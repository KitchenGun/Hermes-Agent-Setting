# Hermes Agent Setting

## 프로젝트
Discord->Hermes->OpenCode 멀티에이전트 오케스트레이션.
HTTP 브릿지: 127.0.0.1:8765 | MCP 엔드포인트: /mcp | GUI: /gui

## 핵심 파일
| 파일 | 역할 |
|------|------|
| hermes_bridge_http.py  | HTTP MCP 서버 (진입점) |
| orchestrator.py        | 스킬 매칭으로 에이전트에 태스크 라우팅 |
| agent_pool.py          | OpenCode 워커 프로세스 관리 |
| discord_hermes_bot.py  | Discord 메시지 -> 태스크 포맷 변환 |
| discord_task_flow.py   | 의도 파싱 + 컨텍스트 윈도우 관리 |
| calendar_manager_agent.py | Google Calendar 전문 에이전트 |

## 에이전트 & 스킬 레지스트리
- agents/registry.json  : 에이전트 역할, 도메인, 동시실행 한도
- skills/registry.json  : 스킬 우선순위(p), 트리거 조건
- agents/suggestions.json : 구현 백로그

## 에이전트 목록
| 에이전트            | 스킬            | 최대 |
|---------------------|-----------------|------|
| calendar-manager    | google-calendar | 1    |
| ue5-developer       | unreal          | 1    |
| unreal-mcp-operator | unreal-mcp      | 1    |
| generalist          | code-general    | 2    |
| code-reviewer       | (코드 리뷰)     | 1    |
| doc-writer          | document        | 1    |

## 스킬 우선순위
unreal-mcp(p11) > google-calendar(p10) = unreal(p10) > code-general(p9) > research(p7) > document(p6) > google-docs(p5)

## 실행/테스트
- 브릿지 시작: `powershell ./run_bridge.ps1`
- 봇 시작:     `powershell ./run_discord_hermes_bot.ps1`
- 테스트:      `POST http://127.0.0.1:8765/api/discord/execute`
- 부팅 등록:   `powershell ./install_hermes_boot_task.ps1`

## 하네스 규칙
- 죽은 코드 -> 즉시 삭제
- skills/system_prompt.md : 40줄 이하 유지
- CLAUDE.md : 60줄 이하 유지, 세부사항은 harness/rules/ 에
- 실패 로그: harness/AGENTS.md
