param(
    [string]$PythonExe = "",
    [int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonExe) {
    $localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $workspacePython = Join-Path $PSScriptRoot '..\..\work\venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { $PythonExe = $localPython }
    elseif (Test-Path -LiteralPath $workspacePython) { $PythonExe = $workspacePython }
    else { throw 'No virtual environment found. Follow README.md setup, or pass -PythonExe.' }
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
    Push-Location frontend
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
Write-Host "Lexi: http://127.0.0.1:$Port (Ctrl+C to stop)"
& $PythonExe -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
