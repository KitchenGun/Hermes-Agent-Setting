$ErrorActionPreference = "Stop"

$port = 8765
$pythonw = "C:\Users\kang9\AppData\Local\Programs\Python\Python313\pythonw.exe"
$script = "C:\Users\kang9\.config\opencode\hermes_bridge_http_launcher.py"

$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    exit 0
}

if (-not $env:HERMES_MODE) {
    $env:HERMES_MODE = "opencode"
}
Start-Process -FilePath $pythonw -ArgumentList @($script) -WindowStyle Hidden
