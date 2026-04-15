# Hermes OpenCode Bridge

이 폴더는 OpenCode에서 Hermes 에이전트를 제어하기 위한 브리지입니다.

현재 상태:

- 이 폴더는 비어 있었기 때문에 새로 구성했습니다.
- 기본값은 이제 `opencode` 모드입니다. Discord 요청을 OpenCode 실행기로 직접 처리합니다.
- 필요하면 환경변수로 `mock`, `command`, `http` 모드로 바꿀 수 있습니다.
- 현재 활성 연결 방식은 `127.0.0.1:8765`에 띄우는 로컬 HTTP MCP 브리지입니다.
- OpenCode의 Windows 로컬 stdio MCP 시작 방식과 충돌이 있어, 실제 자동 연결은 remote MCP 설정으로 구성했습니다.
- GUI 제어 화면은 `http://127.0.0.1:8765/gui` 에서 열립니다.

## 구성 파일

- `hermes_bridge.py`: OpenCode가 연결할 MCP 서버
- `hermes_bridge_http.py`: 현재 활성화된 HTTP MCP 브리지
- `run_bridge.ps1`: Windows PowerShell 실행 스크립트
- `start_hermes_bridge_http.ps1`: HTTP 브리지를 백그라운드로 시작하는 스크립트
- `discord_hermes_bot.py`: Discord 메시지를 Hermes 브리지로 전달하는 봇
- `calendar_manager_agent.py`: Google Calendar 전용 에이전트 프롬프트/라우팅 정의
- `google_calendar_integration.py`: calendar_manager_agent의 tool_request를 실제 Google Calendar API 호출로 실행
- `run_discord_hermes_bot.ps1`: Discord 봇 실행 스크립트
- `install_discord_bot_boot_task.ps1`: Discord 봇 자동 시작 작업 등록 스크립트
- `check_hermes_startup_tasks.ps1`: Hermes 시작 작업 상태 확인 스크립트
- `.env.example`: 실제 Hermes 연결 예시
- `opencode.mcp.example.json`: OpenCode 등록 예시

## 노출되는 도구

- `hermes_status`: 현재 상태 확인
- `hermes_start`: Hermes 시작 요청
- `hermes_send`: Hermes에 명령 전달
- `hermes_stop`: Hermes 중지 요청
- `hermes_execute_discord_task`: Discord 입력을 Hermes 작업 프롬프트로 변환해 실행

## 빠른 시작

PowerShell에서:

```powershell
cd "E:\Hermes Agent Setting"
.\run_bridge.ps1
```

`run_bridge.ps1`는 이제 과거 stdio bridge가 아니라 `start_hermes_bridge_http.ps1`를 호출하는 래퍼입니다.
즉, 브리지 표준 시작 경로는 HTTP bridge 하나로 통일됩니다.

기본은 `mock` 모드라서 OpenCode에서 붙인 뒤 다음처럼 테스트하면 됩니다.

- `hermes_status`
- `hermes_start`
- `hermes_send` with `prompt`
- `hermes_stop`

## Hermes 연결 방식

## Discord -> Hermes -> 작업 실행

이제 브리지는 Discord 봇 입력을 바로 Hermes 작업으로 전달하는 전용 경로도 제공합니다.

입력 형식:

```json
{
  "user": "discord_username",
  "channel": "ops",
  "message": "!task 배포 상태 확인해줘"
}
```

동작 방식:

1. `!task`, `!run`, `!agent` prefix가 있는 메시지만 처리합니다.
2. prefix를 제거하고 실제 작업 의도를 추출합니다.
3. 정제된 작업을 `{ "task": "...", "user": "..." }` 형태로 Hermes Agent에 전달합니다.
4. Discord 봇이 그대로 돌려주기 쉬운 JSON으로 정규화합니다.

반환 형식:

```json
{
  "action": "execute | reply | ignore",
  "task": "<parsed task>",
  "response": "<message to send back>",
  "visibility": "public | ephemeral"
}
```

HTTP API 예시:

```powershell
$body = @{
    user = "KitchenGun"
    channel = "ops"
    message = "!task 서버 상태 확인해줘"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/discord/execute" -Method Post -ContentType "application/json" -Body $body
```

OpenCode MCP 도구 예시:

- `hermes_execute_discord_task` with `user`, `channel`, `message`

## Discord 봇 생성과 서버 추가

직접 해야 하는 단계는 Discord Developer Portal에서 앱을 만들고 서버 초대를 승인하는 부분입니다.

1. Discord Developer Portal에서 `New Application`을 만듭니다.
2. `Bot` 탭에서 봇을 생성합니다.
3. `Privileged Gateway Intents`에서 `Message Content Intent`를 켭니다.
4. `Reset Token` 또는 `Copy Token`으로 토큰을 발급받아 `.env`에 넣습니다.
5. `OAuth2 > URL Generator`에서 `bot`, `applications.commands`를 체크합니다.
6. 권한은 최소한 아래를 체크합니다.

```text
View Channels
Send Messages
Read Message History
```

7. 생성된 OAuth2 링크를 열고, Hermes 봇을 넣을 서버를 선택해 승인합니다.

직접 초대 링크를 만들고 싶으면 아래 형식입니다.

```text
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot%20applications.commands&permissions=3072
```

`3072`는 `View Channels` + `Send Messages` 조합입니다. `Read Message History`까지 포함하려면 Portal에서 체크해서 생성하는 편이 안전합니다.

## Discord 봇 실행

1. `.env.example`를 참고해 같은 폴더에 `.env`를 만듭니다.
2. `DISCORD_BOT_TOKEN`을 넣습니다.
3. 필요하면 `HERMES_BRIDGE_URL`을 바꿉니다.
4. Discord 라이브러리를 설치합니다.

```powershell
cd "E:\Hermes Agent Setting"
python -m pip install -r requirements-discord.txt
```

5. Hermes HTTP 브리지가 켜져 있는지 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\start_hermes_bridge_http.ps1"
```

6. Discord 봇을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\run_discord_hermes_bot.ps1"
```

`run_discord_hermes_bot.ps1`는 이제 봇 실행 전에 `http://127.0.0.1:8765/api/status`를 확인하고,
응답이 없으면 `start_hermes_bridge_http.ps1`를 먼저 실행한 뒤 bridge가 준비될 때까지 대기합니다.

부팅 시 자동으로 실행하려면 관리자 PowerShell에서 아래 스크립트를 실행해 `HermesDiscordBot` 작업을 등록합니다.

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\install_discord_bot_boot_task.ps1"
```

메시지 예시:

```text
!task 서버 상태 확인해줘
!run 최신 로그 분석해줘
!agent 이 에러 원인 찾고 요약해줘
@Agent-Hermes 메시지가 확인되나
```

멘션으로 시작한 메시지는 내부적으로 `!agent`로 변환해서 처리합니다.

같은 사용자와 같은 채널에서 이어지는 최근 대화 몇 턴은 자동으로 OpenCode 실행 문맥에 함께 전달합니다.

캘린더 관련 요청은 일반 Hermes 실행 프롬프트 대신 `calendar_manager_agent` 전용 프롬프트로 라우팅됩니다.

예시:

```text
@Agent-Hermes 오늘 일정 보여줘
@Agent-Hermes 내일 오후 3시에 팀 미팅 추가해줘
@Agent-Hermes 금요일 미팅을 4시로 옮겨줘
@Agent-Hermes 내일 2시간 비는 시간 찾아줘
```

이 경우 OpenCode는 사용자에게 바로 보여줄 일반 문장 대신, 우선 Google Calendar 실행 계획용 JSON을 생성하도록 유도됩니다.

이제 브리지는 그 JSON의 `tool_request`를 후처리해 실제 Google Calendar API를 호출하고, 가능하면 최종 결과를 Discord에 바로 반환합니다.

필요한 환경 변수:

```env
GOOGLE_CALENDAR_ACCESS_TOKEN=...
```

또는 refresh token 방식:

```env
GOOGLE_CALENDAR_REFRESH_TOKEN=...
GOOGLE_CALENDAR_CLIENT_ID=...
GOOGLE_CALENDAR_CLIENT_SECRET=...
```

현재 연결된 실행 작업:

- `search`
- `create`
- `update`
- `delete`
- `freebusy`

제한:

- `respond`(초대 응답)는 아직 보류 상태로 반환됩니다.

주의:

- Discord 일반 메시지 기반 봇은 진짜 `ephemeral` 메시지를 보낼 수 없습니다.
- 이 구현에서는 `visibility=ephemeral`이면 우선 DM으로 보내고, 실패하면 채널 reply로 대체합니다.

Hermes에 전달되는 내부 실행 프롬프트 입력은 아래 형식입니다.

```json
{
  "task": "서버 상태 확인해줘",
  "user": "KitchenGun"
}
```

Hermes는 아래 형식으로 응답하면 됩니다.

```json
{
  "status": "success | error",
  "result": "<final output>",
  "log": ["step1", "step2"],
  "error": ""
}
```

### 1. opencode 모드

기본값입니다. Discord 요청을 OpenCode `serve` + `run --attach` 경로로 실행합니다.

```powershell
$env:HERMES_MODE="opencode"
.
\run_bridge.ps1
```

추가 환경변수:

- `HERMES_OPENCODE_MODEL` 기본값 `openai/gpt-5.4`
- `HERMES_OPENCODE_VARIANT` 기본값 `medium`
- `HERMES_OPENCODE_PORT` 기본값 `4096`

### 2. mock 모드

실행기 없이 연결만 확인할 때 사용합니다.

```powershell
$env:HERMES_MODE="mock"
.\run_bridge.ps1
```

### 3. command 모드

로컬 스크립트나 실행 파일로 Hermes를 제어할 때 사용합니다.

지원 환경변수:

- `HERMES_MODE=command`
- `HERMES_START_COMMAND`
- `HERMES_STATUS_COMMAND`
- `HERMES_STOP_COMMAND`
- `HERMES_SEND_COMMAND`

`HERMES_SEND_COMMAND`는 전달받은 프롬프트를 끝에 붙여 실행합니다.

예시:

```powershell
$env:HERMES_MODE="command"
$env:HERMES_START_COMMAND="python E:\somewhere\hermes.py --start"
$env:HERMES_STATUS_COMMAND="python E:\somewhere\hermes.py --status"
$env:HERMES_STOP_COMMAND="python E:\somewhere\hermes.py --stop"
$env:HERMES_SEND_COMMAND="python E:\somewhere\hermes.py --prompt"
.\run_bridge.ps1
```

실제로 실행되는 형태는 대략 아래와 같습니다.

```powershell
python E:\somewhere\hermes.py --prompt "OpenCode에서 보낸 프롬프트"
```

### 4. http 모드

Hermes가 로컬 HTTP API를 갖고 있다면 사용합니다.

지원 환경변수:

- `HERMES_MODE=http`
- `HERMES_HTTP_BASE_URL`
- `HERMES_HTTP_TOKEN` 선택

기본 엔드포인트:

- `GET /status`
- `POST /start`
- `POST /send`
- `POST /stop`

`/send` 요청 바디:

```json
{
  "prompt": "OpenCode에서 전달한 명령",
  "context": "선택값"
}
```

## OpenCode에 연결하기

현재 적용된 방식은 OpenCode 설정에서 `remote MCP`로 `http://127.0.0.1:8765/mcp`를 등록하는 것입니다.

전역 설정 파일:

```text
C:\Users\kang9\.config\opencode\opencode.json
```

현재 설정 개념:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "hermes": {
      "type": "remote",
      "url": "http://127.0.0.1:8765/mcp",
      "enabled": true,
      "oauth": false
    }
  }
}
```

부팅 직후 자동 시작이 필요하면 관리자 PowerShell에서 아래 스크립트로 `HermesOpenCodeBridge` 작업을 등록할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\install_hermes_boot_task.ps1"
```

기본값은 현재 사용자 컨텍스트로 `AtStartup` 트리거를 등록합니다.

## 시작 작업 상태 확인

상태 확인:

```powershell
powershell -ExecutionPolicy Bypass -File "E:\Hermes Agent Setting\check_hermes_startup_tasks.ps1"
```

로그 위치:

- `bridge.log`
- `discord_bot_restart.log`

추가로 Windows 작업 스케줄러의 `History` 탭에서도 실행 기록을 확인할 수 있습니다.

## 공급자 메모

- OpenAI는 OpenCode 설정에 모델 구성을 넣어두었습니다.
- 실제 인증은 OpenCode의 provider 로그인 또는 API 키 입력이 한 번 필요합니다.
- Anthropic 모델 목록도 설정에 넣어두었지만, 최신 OpenCode 문서 기준으로 `Claude Pro/Max` 구독 연결은 더 이상 지원되지 않습니다.
- Claude를 OpenCode에서 쓰려면 Anthropic API 키 방식으로 연결하는 것이 안전합니다.

예전 stdio 방식 예시는 참고용으로만 아래에 남겨둡니다.

개념적으로는 아래와 같습니다.

```json
{
  "mcpServers": {
    "hermes": {
      "command": "powershell",
      "args": [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "E:\\Hermes Agent Setting\\run_bridge.ps1"
      ]
    }
  }
}
```

OpenCode 설정 파일 위치나 JSON 구조는 사용 중인 OpenCode 버전에 따라 조금 다를 수 있지만, 핵심은 `run_bridge.ps1`를 stdio MCP 서버로 등록하는 것입니다.

## 실제 연결 전 점검 순서

1. `mock` 모드로 MCP 연결이 되는지 확인
2. OpenCode에서 `hermes_status` 호출
3. `command` 또는 `http` 모드 환경변수 설정
4. 실제 Hermes 제어 명령으로 교체
5. `hermes_send`로 왕복 테스트

## 다음 단계 추천

실제 Hermes 실행 파일이나 API 주소를 알게 되면 아래 둘 중 하나로 바로 맞춰드릴 수 있습니다.

1. 로컬 실행형이면 `command` 모드에 맞게 명령어를 고정
2. API형이면 `http` 모드 엔드포인트에 맞게 요청 포맷 조정
