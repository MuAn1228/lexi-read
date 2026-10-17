$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot 'backend\data\launcher.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host 'No background service started by this launcher was found.'
    exit 0
}
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$serverProcess = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if ($serverProcess) {
    # A PID may have been reused. Only stop the exact process saved at launch.
    # ConvertFrom-Json returns DateTime in PowerShell 7 and a string in 5.1.
    $savedStart = ([DateTimeOffset]$state.started_at).UtcDateTime
    if ($serverProcess.StartTime.ToUniversalTime().Ticks -ne $savedStart.Ticks) {
        throw 'Saved process no longer matches. Nothing was stopped.'
    }
    $command = (Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)").CommandLine
    if ($command -notmatch 'uvicorn backend\.main:app') { throw 'Process is not the Lexi server. Nothing was stopped.' }
    Stop-Process -Id $serverProcess.Id
    Write-Host 'Lexi has stopped. Your reading data is preserved.'
}
Remove-Item -LiteralPath $statePath
