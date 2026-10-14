param([ValidateRange(1024,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$address = "http://127.0.0.1:$Port"
$statePath = Join-Path $PSScriptRoot 'backend\data\launcher.json'

function Test-LexiReady {
    try {
        $health = Invoke-RestMethod "$address/api/health" -TimeoutSec 2
        if ($health.status -ne 'ok' -or -not $health.ocr_provider -or -not $health.llm_provider) { return $false }
        $page = Invoke-WebRequest $address -UseBasicParsing -TimeoutSec 2
        return $page.StatusCode -eq 200 -and $page.Content -match '<title>[^<]*Lexi'
    } catch { return $false }
}

if (Test-LexiReady) {
    Write-Host "Lexi is already running: $address"
    Start-Process -FilePath $address
    exit 0
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is occupied by another service. Run open.ps1 -Port 8002 instead."
}

$logFolder = Join-Path $PSScriptRoot 'backend\data\logs'
New-Item -ItemType Directory -Force -Path $logFolder | Out-Null
$stdoutPath = Join-Path $logFolder 'startup.log'
$stderrPath = Join-Path $logFolder 'server.log'
$startupPath = Join-Path $PSScriptRoot 'start.ps1'
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Port {1}' -f $startupPath, $Port
$launchProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Write-Host 'Starting Lexi. The first launch may take longer while dependencies are prepared...'
for ($attempt = 0; $attempt -lt 180; $attempt++) {
    if (Test-LexiReady) {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1
        $serverProcess = Get-Process -Id $listener.OwningProcess
        @{ pid = $serverProcess.Id; started_at = $serverProcess.StartTime.ToUniversalTime().ToString('o'); port = $Port } |
            ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
        Write-Host "Lexi is ready: $address"
        Start-Process -FilePath $address
        exit 0
    }
    $launchProcess.Refresh()
    if ($launchProcess.HasExited) {
        if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Tail 20 | Write-Host }
        throw "Lexi could not start. Logs: $logFolder"
    }
    Start-Sleep -Seconds 1
}
throw "Lexi is still starting. Retry this launcher shortly. Logs: $logFolder"
