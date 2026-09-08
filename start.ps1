$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$daylightPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $daylightPython)) {
    throw 'Run .\run.ps1 once to install the Python environment.'
}
$daylightPort = if ($env:PORT) { [int]$env:PORT } else { 8765 }
$daylightUrl = "http://127.0.0.1:$daylightPort"
try {
    $daylightExisting = Invoke-RestMethod "$daylightUrl/api/state" -TimeoutSec 2
} catch { $daylightExisting = $null }
if ($daylightExisting) {
    Write-Host "A dashboard is already running at $daylightUrl. Use manage.py shutdown before starting updated code."
    exit
}
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
$daylightProcess = Start-Process -FilePath $daylightPython -ArgumentList 'app.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data/server.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data/server-error.log') -PassThru
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data/server.pid') -Value $daylightProcess.Id
for ($daylightAttempt=0; $daylightAttempt -lt 20; $daylightAttempt++) {
    try {
        $daylightState = Invoke-RestMethod "$daylightUrl/api/state" -TimeoutSec 1
        if ($daylightState.application -eq 'daylight') {
            Write-Host "Daylight $($daylightState.version) ready at $daylightUrl. Session is stopped until Go."
            exit
        }
    } catch { Start-Sleep -Milliseconds 250 }
}
throw 'Dashboard did not become ready. Check data/server-error.log.'
