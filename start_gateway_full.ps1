# Hermes Gateway (Discord + API Server) 통합 실행 스크립트
# 실행: powershell -ExecutionPolicy Bypass -File start_gateway_full.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# LM Studio 확인
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 3 -UseBasicParsing
    Write-Host "[OK] LM Studio 확인됨 (포트 1234)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] LM Studio 응답 없음. 먼저 LM Studio를 실행하세요." -ForegroundColor Yellow
}

# 환경 변수 (.env 파일 로드)
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[^#]\S+=\S*' } | ForEach-Object {
        $k, $v = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
    }
    Write-Host "[OK] .env 로드됨" -ForegroundColor Green
} else {
    Write-Host "[WARN] .env 파일 없음: $envFile" -ForegroundColor Yellow
}

$env:API_SERVER_ENABLED              = 'true'
$env:API_SERVER_PORT                 = '8642'
$env:API_SERVER_HOST                 = '127.0.0.1'
$env:DISCORD_ALLOW_ALL_USERS         = 'true'
$env:DISCORD_DISABLE_SKILL_COMMANDS  = 'true'
$env:GATEWAY_ALLOW_ALL_USERS         = 'true'

# 기존 Gateway 종료
$old = (Get-NetTCPConnection -LocalPort 8642 -ErrorAction SilentlyContinue).OwningProcess
if ($old) { Stop-Process -Id $old -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }

# 스테일 잠금 파일 정리
Remove-Item "$env:USERPROFILE\.hermes\gateway.pid" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\.hermes\gateway_state.json" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\.local\state\hermes\gateway-locks\*.lock" -ErrorAction SilentlyContinue

# Gateway 시작
$logOut = "E:\hermes-agent\gateway.log"
$logErr = "E:\hermes-agent\gateway_err.log"

$proc = Start-Process `
    -FilePath 'E:\hermes-agent\venv\Scripts\python.exe' `
    -ArgumentList '-m', 'hermes_cli.main', 'gateway', 'run' `
    -WorkingDirectory 'E:\hermes-agent' `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError  $logErr `
    -PassThru -WindowStyle Hidden

Write-Host "[>>] Gateway 시작 중 (PID $($proc.Id))..." -ForegroundColor Cyan
Start-Sleep 12

# 상태 확인
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8642/health" -TimeoutSec 5
    Write-Host "[OK] Gateway Running — discord+api_server 연결됨" -ForegroundColor Green
    Write-Host "     Dashboard: http://127.0.0.1:9119" -ForegroundColor DarkCyan
} catch {
    Write-Host "[ERR] Gateway 응답 없음. 로그 확인: $logErr" -ForegroundColor Red
}
