# Hermes Agent Setting

## 아키텍처 (2026-04-17 전환 완료)

```
Discord → Hermes Gateway (E:\hermes-agent, :8642)
        → LM Studio (:1234, qwen/qwen2.5-coder-14b)
Dashboard → http://127.0.0.1:9119
```

**단일 경로** — 구 브릿지(8765) / OpenCode / codex_backend 완전 제거됨

## 핵심 실행 파일

| 파일 | 역할 |
|------|------|
| `start_gateway_full.ps1` | Gateway + Discord 플랫폼 시작 (유일한 진입점) |
| `E:\hermes-agent\` | NousResearch Hermes Agent 본체 |
| `calendar_manager_agent.py` | Google Calendar 전용 에이전트 (독립) |
| `unreal_adapter.py` | Unreal Engine MCP 어댑터 (독립) |

## Gateway 설정 (~/.hermes/)

| 파일 | 내용 |
|------|------|
| `config.yaml` | 모델: custom/qwen2.5-coder-14b, ctx 65536, discord+api_server 플랫폼 |
| `.env` | OPENAI_BASE_URL, DISCORD_BOT_TOKEN, DISCORD_DISABLE_SKILL_COMMANDS=true |

## 실행 방법

```powershell
# Gateway 시작 (Discord + API Server)
powershell ./start_gateway_full.ps1

# Dashboard (별도 실행)
powershell E:\hermes-agent\start_dashboard.ps1
```

## 적용된 패치 (E:\hermes-agent)

- `gateway/status.py` — Windows os.kill 버그 수정 (2곳)
- `gateway/platforms/discord.py` — DISCORD_DISABLE_SKILL_COMMANDS 추가
- `gateway/run.py` — LM Studio 헬스 체크 + 타임아웃(180s) 추가
- `gateway/lmstudio_health.py` — 헬스 체크 모듈 (신규)
- `agent/prompt_builder.py` — AGENTS.md 2000자 제한

## 하네스 규칙

- 죽은 코드 → 즉시 `_legacy/` 이동
- CLAUDE.md : 60줄 이하 유지
- 실패 로그: harness/AGENTS.md
- 레거시 파일 위치: `_legacy/`
