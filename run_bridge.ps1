$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$starter = Join-Path $scriptDir "start_hermes_bridge_http.ps1"

if (-not (Test-Path $starter)) {
    throw "HTTP bridge starter script not found: $starter"
}

Write-Host "run_bridge.ps1 now delegates to the HTTP bridge starter."
powershell -ExecutionPolicy Bypass -File $starter

Write-Host "Hermes HTTP bridge start requested."
