# Hermes 자동 시작 작업 등록 스크립트
# 실행: powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\setup_autostart.ps1"
# 관리자 권한 불필요 (현재 사용자 로그인 트리거)

$ErrorActionPreference = "Stop"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# ── 작업 1: Hermes Gateway ───────────────────────────────────────────────────
$gwAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"E:\Hermes Agent Setting\start_gateway_full.ps1`""

$gwTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

$gwSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Hermes Gateway" `
    -TaskPath "\Hermes\" `
    -Action $gwAction `
    -Trigger $gwTrigger `
    -Settings $gwSettings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "[OK] 작업 등록: Hermes Gateway (로그인 시 자동 실행)" -ForegroundColor Green

# ── 작업 2: Hermes Dashboard (Gateway 15초 후) ───────────────────────────────
$dbTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$dbTrigger.Delay = "PT15S"   # 15초 지연 (Gateway 초기화 대기)

$dbAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"E:\hermes-agent\start_dashboard.ps1`""

$dbSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Hermes Dashboard" `
    -TaskPath "\Hermes\" `
    -Action $dbAction `
    -Trigger $dbTrigger `
    -Settings $dbSettings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "[OK] 작업 등록: Hermes Dashboard (로그인 후 15초 뒤 자동 실행)" -ForegroundColor Green

Write-Host ""
Write-Host "등록된 작업 확인: 작업 스케줄러 → \Hermes\" -ForegroundColor Cyan
Write-Host "수동 실행/삭제:   Get-ScheduledTask -TaskPath '\Hermes\'" -ForegroundColor DarkGray
