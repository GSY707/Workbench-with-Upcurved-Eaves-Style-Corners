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
$portRecord = Join-Path $stateDir 'server.port'
$explicitPort = $PSBoundParameters.ContainsKey('Port')

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
        if ($payload.version -ne '0.4.2' -or $payload.app_id -ne 'mu-gouzao-corner-workbench') {
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

if (-not $explicitPort -and (Test-Path -LiteralPath $portRecord)) {
    $savedPort = 0
    if ([int]::TryParse((Get-Content -LiteralPath $portRecord -Raw).Trim(), [ref]$savedPort) -and
        $savedPort -ge 1 -and $savedPort -le 65535) {
        $Port = $savedPort
        $baseUrl = "http://127.0.0.1:$Port"
    }
}

if (Test-WorkbenchIdentity) {
    Write-Output "复用本项目工作台：$baseUrl/"
    if (-not $NoBrowser) {
        Start-Process -FilePath "$baseUrl/"
    }
    exit 0
}

$Port = if ($explicitPort) { $Port } else { 8765 }
$baseUrl = "http://127.0.0.1:$Port"
if (-not $explicitPort -and (Test-WorkbenchIdentity)) {
    Set-Content -LiteralPath $portRecord -Value $Port -Encoding ASCII
    if (-not $NoBrowser) { Start-Process -FilePath "$baseUrl/" }
    Write-Output "复用本项目工作台：$baseUrl/"
    exit 0
}

function Get-AvailablePort([int]$PreferredPort) {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $PreferredPort)
    try {
        $probe.Start()
        return $PreferredPort
    } catch [System.Net.Sockets.SocketException] {
        if ($explicitPort) { throw "端口 $PreferredPort 无法使用，请指定其他 -Port。" }
    } finally {
        $probe.Stop()
    }
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $probe.Start()
        return $probe.LocalEndpoint.Port
    } finally {
        $probe.Stop()
    }
}

$Port = Get-AvailablePort $Port
$baseUrl = "http://127.0.0.1:$Port"
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
            (Get-Content -LiteralPath $stderrLog -Encoding UTF8 -Tail 24 -ErrorAction SilentlyContinue) -join "`n"
        } else { '' }
        throw "工作台进程启动失败，退出码 $($serviceProcess.ExitCode)。日志：$stderrLog`n$tail"
    }
}

if (-not $ready) {
    throw "工作台在20秒内未通过版本、项目身份和API检查。进程未被结束，日志：$stderrLog"
}

Set-Content -LiteralPath $portRecord -Value $Port -Encoding ASCII
Write-Output "本地工作台已启动：$baseUrl/"
Write-Output "服务日志：$stdoutLog；错误日志：$stderrLog"
if (-not $NoBrowser) {
    Start-Process -FilePath "$baseUrl/"
}
