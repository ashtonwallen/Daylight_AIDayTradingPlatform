$ErrorActionPreference = 'Stop'
$daylightPidFile = Join-Path $PSScriptRoot 'data/server.pid'
if (-not (Test-Path -LiteralPath $daylightPidFile)) {
    Write-Host 'No background server recorded. Use Ctrl+C if running in a terminal.'
    exit
}
$daylightServerPid = [int](Get-Content -LiteralPath $daylightPidFile -Raw)
$daylightPythonPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe')).Path
$daylightServer = Get-CimInstance Win32_Process -Filter "ProcessId = $daylightServerPid"
if ($daylightServer) {
    if ($daylightServer.ExecutablePath -ne $daylightPythonPath -or $daylightServer.CommandLine -notmatch '(?i)(^|\s|\")app\.py(\"|\s|$)') {
        throw 'Recorded process does not match this project server; refusing to stop it.'
    }
    # Windows virtualenv python.exe can launch a second interpreter that owns the socket.
    # Stop only matching direct children of the verified project launcher.
    $daylightChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $daylightServerPid"
    foreach ($daylightChild in $daylightChildren) {
        if ($daylightChild.Name -eq 'python.exe' -and $daylightChild.CommandLine -eq $daylightServer.CommandLine) {
            Stop-Process -Id $daylightChild.ProcessId -ErrorAction SilentlyContinue
        }
    }
    Stop-Process -Id $daylightServerPid -ErrorAction SilentlyContinue
    Write-Host 'Daylight background server stopped.'
}
Remove-Item -LiteralPath $daylightPidFile
