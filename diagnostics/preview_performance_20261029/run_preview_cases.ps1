$ErrorActionPreference = 'Stop'

$diag = Split-Path -Parent $MyInvocation.MyCommand.Path
$generator = (Resolve-Path (Join-Path $diag '..\..')).Path
$python = 'C:\Users\24408\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$probe = Join-Path $diag 'preview_probe.py'
$configs = @(
    @{ label = 'angle_120_B400'; path = (Join-Path $generator 'configs\angle_120.json') },
    @{ label = 'angle_90_B400'; path = (Join-Path $diag 'angle_90_B400.json') },
    @{ label = 'angle_60_B400'; path = (Join-Path $generator 'configs\angle_60.json') }
)
$budgetMs = 180000
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$startedUtc = [DateTime]::UtcNow.ToString('o')
$records = @()
$supervisorPath = Join-Path $diag 'supervisor.json'

function Save-Supervisor([string]$status) {
    $body = [ordered]@{
        status = $status
        started_utc = $startedUtc
        updated_utc = [DateTime]::UtcNow.ToString('o')
        wall_seconds = [Math]::Round($clock.Elapsed.TotalSeconds, 3)
        budget_seconds = 180
        order = @('angle_120_B400', 'angle_90_B400', 'angle_60_B400')
        probe_path = $probe
        probe_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $probe).Hash.ToLowerInvariant()
        cases = @($records)
    }
    $tmp = $supervisorPath + '.tmp'
    $body | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $supervisorPath -Force
}

Save-Supervisor 'running'
foreach ($case in $configs) {
    $remaining = $budgetMs - [int]$clock.Elapsed.TotalMilliseconds
    if ($remaining -le 0) { break }
    $configPath = (Resolve-Path -LiteralPath $case.path).Path
    $caseDir = Join-Path $diag $case.label
    New-Item -ItemType Directory -Path $caseDir -Force | Out-Null
    $stdout = Join-Path $caseDir 'stdout.log'
    $stderr = Join-Path $caseDir 'stderr.log'
    $caseStart = [DateTime]::UtcNow.ToString('o')
    $caseWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $args = @('-X', 'utf8', '-u', "`"$probe`"", '--config', "`"$configPath`"", '--output', "`"$caseDir`"")
    $process = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $generator `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $remaining = [Math]::Max(1, $budgetMs - [int]$clock.Elapsed.TotalMilliseconds)
    $finished = $process.WaitForExit($remaining)
    $timedOut = -not $finished
    if ($timedOut) {
        try { $process.Kill() } catch {}
        $process.WaitForExit()
    }
    $caseWatch.Stop()
    $record = [ordered]@{
        case_id = $case.label
        config_path = $configPath
        config_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash.ToLowerInvariant()
        output_dir = $caseDir
        started_utc = $caseStart
        finished_utc = [DateTime]::UtcNow.ToString('o')
        process_id = $process.Id
        wall_seconds = [Math]::Round($caseWatch.Elapsed.TotalSeconds, 3)
        timed_out = $timedOut
        exit_code = if ($timedOut) { $null } else { $process.ExitCode }
        stdout_path = $stdout
        stderr_path = $stderr
        summary_present = (Test-Path -LiteralPath (Join-Path $caseDir 'summary.json'))
        progress_present = (Test-Path -LiteralPath (Join-Path $caseDir 'progress.json'))
    }
    $records += $record
    Save-Supervisor $(if ($timedOut) { 'timed_out' } elseif ($record.exit_code -eq 0) { 'running' } else { 'case_failed' })
    Write-Output ("CASE_DONE {0} wall={1}s timeout={2} exit={3}" -f $case.label, $record.wall_seconds, $record.timed_out, $record.exit_code)
    if ($timedOut) { break }
}
$clock.Stop()
$finalStatus = if ($records.Count -eq 3 -and @($records | Where-Object { $_.exit_code -ne 0 -or $_.timed_out }).Count -eq 0) {
    'completed_all_three'
} elseif (@($records | Where-Object { $_.timed_out }).Count -gt 0) {
    'timed_out'
} elseif ($records.Count -eq 0) {
    'no_case_started'
} else {
    'completed_or_failed_within_budget'
}
Save-Supervisor $finalStatus
Write-Output ("SUPERVISOR_DONE status={0} wall={1}s cases={2}" -f $finalStatus, [Math]::Round($clock.Elapsed.TotalSeconds, 3), $records.Count)
