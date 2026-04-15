param(
    [string]$TaskName = "HermesOpenCodeBridge",
    [string]$UserId = "${env:USERDOMAIN}\${env:USERNAME}"
)

$ErrorActionPreference = "Stop"

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    throw "관리자 권한 PowerShell에서 실행해야 합니다. PowerShell을 '관리자 권한으로 실행'한 뒤 다시 시도하세요."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupScript = Join-Path $scriptDir "start_hermes_bridge_http.ps1"

if (-not (Test-Path $startupScript)) {
    throw "시작 스크립트를 찾을 수 없습니다: $startupScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startupScript`""

$trigger = New-ScheduledTaskTrigger -AtStartup

$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType S4U `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances Ignore `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 72)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $taskPrincipal `
    -Settings $settings `
    -Description "Start the local Hermes OpenCode MCP bridge at system startup" `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for startup as $UserId"
