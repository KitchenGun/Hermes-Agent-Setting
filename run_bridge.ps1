$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (-not $env:HERMES_MODE) {
    $env:HERMES_MODE = "opencode"
}

python "$scriptDir\hermes_bridge.py"
