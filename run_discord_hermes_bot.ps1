$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Resolve-PythonExecutable {
    if ($env:HERMES_PYTHON -and (Test-Path $env:HERMES_PYTHON)) {
        return [pscustomobject]@{
            Command = $env:HERMES_PYTHON
            Arguments = @()
        }
    }

    $preferredPython = "C:\Users\kang9\AppData\Local\Programs\Python\Python313\python.exe"
    if (Test-Path $preferredPython) {
        return [pscustomobject]@{
            Command = $preferredPython
            Arguments = @()
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source) {
        return [pscustomobject]@{
            Command = $pythonCommand.Source
            Arguments = @()
        }
    }

    $pyCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCommand -and $pyCommand.Source) {
        return [pscustomobject]@{
            Command = $pyCommand.Source
            Arguments = @("-3")
        }
    }

    throw "Python executable not found. Set HERMES_PYTHON or install Python for the scheduled task environment."
}

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

$bridgeBaseUrl = if ($env:HERMES_BRIDGE_BASE_URL) {
    $env:HERMES_BRIDGE_BASE_URL.TrimEnd('/')
}
else {
    "http://127.0.0.1:8765"
}

$bridgeStatusUrl = if ($env:HERMES_BRIDGE_STATUS_URL) {
    $env:HERMES_BRIDGE_STATUS_URL
}
else {
    "$bridgeBaseUrl/status"
}

$bridgeStarter = Join-Path $scriptDir "start_hermes_bridge_http.ps1"
$pythonExecutable = Resolve-PythonExecutable

if (-not (Test-HermesBridge -Url $bridgeStatusUrl)) {
    Write-Host "Hermes HTTP bridge is not responding. Starting bridge..."

    if (-not (Test-Path $bridgeStarter)) {
        throw "Hermes bridge starter script not found: $bridgeStarter"
    }

    powershell -ExecutionPolicy Bypass -File $bridgeStarter

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
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

& $pythonExecutable.Command @($pythonExecutable.Arguments + @("$scriptDir\discord_hermes_bot.py"))
exit $LASTEXITCODE
