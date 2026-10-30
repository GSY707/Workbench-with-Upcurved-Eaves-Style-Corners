param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$generatorDir = [System.IO.Path]::GetFullPath($PSScriptRoot)
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $generatorDir '..\..'))
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$serverScript = Join-Path $generatorDir 'workbench_server.py'
$stateDir = Join-Path $generatorDir 'ui_state'
$stdoutLog = Join-Path $stateDir 'server.stdout.log'
$stderrLog = Join-Path $stateDir 'server.stderr.log'
$baseUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "找不到工作台 Python runtime：$python"
}
if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    throw "找不到本地工作台后端：$serverScript"
}
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

function Test-WorkbenchIdentity {
    try {
        $payload = Invoke-RestMethod -Uri "$baseUrl/api/bootstrap" -Method Get -TimeoutSec 2
        if ($payload.version -ne '0.4.0' -or $payload.app_id -ne 'mu-gouzao-corner-workbench') {
            return $false
        }
        $reportedRoot = [System.IO.Path]::GetFullPath([string]$payload.workspace)
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($reportedRoot, $workspaceRoot)) {
            return $false
        }
        if (-not $payload.schema -or -not $payload.defaults -or
            $null -eq $payload.examples -or $null -eq $payload.cases -or
            $null -eq $payload.reference_runtime_seconds) {
            return $false
        }
        return $true
    } catch {
        return $false
    }
}

if (Test-WorkbenchIdentity) {
    Write-Output "复用本项目工作台：$baseUrl/"
    if (-not $NoBrowser) {
        Start-Process -FilePath "$baseUrl/"
    }
    exit 0
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($owner) { $owner.ProcessName } else { '未知进程' }
    throw "端口 $Port 已被 $ownerName 占用，且响应不是本项目工作台；未结束该进程。请指定 -Port 使用其他端口。"
}

if (Test-Path -LiteralPath $stdoutLog) { Remove-Item -LiteralPath $stdoutLog -Force }
if (Test-Path -LiteralPath $stderrLog) { Remove-Item -LiteralPath $stderrLog -Force }
$arguments = @('-X', 'utf8', "`"$serverScript`"", '--port', [string]$Port)
$serviceProcess = Start-Process -FilePath $python -ArgumentList $arguments `
    -WorkingDirectory $generatorDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-WorkbenchIdentity) {
        $ready = $true
        break
    }
    $serviceProcess.Refresh()
    if ($serviceProcess.HasExited) {
        $tail = if (Test-Path -LiteralPath $stderrLog) {
            (Get-Content -LiteralPath $stderrLog -Tail 24 -ErrorAction SilentlyContinue) -join "`n"
        } else { '' }
        throw "工作台进程启动失败，退出码 $($serviceProcess.ExitCode)。日志：$stderrLog`n$tail"
    }
}

if (-not $ready) {
    throw "工作台在20秒内未通过版本、项目身份和API检查。进程未被结束，日志：$stderrLog"
}

Write-Output "本地工作台已启动：$baseUrl/"
Write-Output "服务日志：$stdoutLog；错误日志：$stderrLog"
if (-not $NoBrowser) {
    Start-Process -FilePath "$baseUrl/"
}
