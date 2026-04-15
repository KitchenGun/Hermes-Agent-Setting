$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Test-HermesBridge {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

$bridgeStatusUrl = if ($env:HERMES_BRIDGE_STATUS_URL) {
    $env:HERMES_BRIDGE_STATUS_URL
}
else {
    "http://127.0.0.1:8765/api/status"
}

$bridgeStarter = Join-Path $scriptDir "start_hermes_bridge_http.ps1"

if (-not (Test-HermesBridge -Url $bridgeStatusUrl)) {
    Write-Host "Hermes HTTP bridge is not responding. Starting bridge..."

    if (-not (Test-Path $bridgeStarter)) {
        throw "Hermes bridge starter script not found: $bridgeStarter"
    }

    powershell -ExecutionPolicy Bypass -File $bridgeStarter

    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-HermesBridge -Url $bridgeStatusUrl) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        throw "Hermes HTTP bridge did not become ready at $bridgeStatusUrl"
    }

    Write-Host "Hermes HTTP bridge is ready."
}

python "$scriptDir\discord_hermes_bot.py"
