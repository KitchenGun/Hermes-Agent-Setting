import json
import os
import subprocess
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from calendar_manager_agent import DEFAULT_TIMEZONE
from calendar_manager_agent import build_calendar_manager_execution_prompt
from calendar_manager_agent import extract_calendar_user_message
from calendar_manager_agent import is_calendar_request
from calendar_manager_agent import now_iso
from google_calendar_integration import execute_calendar_plan
from google_calendar_integration import parse_calendar_plan


COMMAND_PREFIXES = ("!task", "!run", "!agent")
DEFAULT_GAMEJOB_AGENT_ROOT = os.getenv("HERMES_GAMEJOB_AGENT_ROOT", r"D:\game-job-agent\Agent_GameJob").strip() or r"D:\game-job-agent\Agent_GameJob"
GAMEJOB_UPDATE_PATTERNS = (
    "공고 리스트 업데이트",
    "공고리스트 업데이트",
    "공고 리스트 갱신",
    "공고리스트 갱신",
    "로우데이터 업데이트",
    "채용공고 업데이트",
    "채용 공고 업데이트",
    "공고 업데이트",
)
GAMEJOB_MATCH_PATTERNS = (
    "공고 맞춤",
    "공고맞춤",
    "맞춤 공고",
    "맞춤공고",
    "공고 추천",
    "공고추천",
    "채용 추천",
    "채용추천",
    "맞춤으로 찾아",
    "포트폴리오 기반",
    "포트폴리오기반",
    "내 스펙에 맞는",
    "나한테 맞는 공고",
    "맞는 공고",
)

DISCORD_BOT_SYSTEM_PROMPT = """You are a Discord-integrated task execution agent.

Your role is to:

* Listen to Discord messages
* Detect commands from users
* Convert them into executable tasks
* Execute them via connected agent tools (Hermes)
* Return results back to Discord clearly

Command rules:
- Only respond to messages that start with one of these prefixes: !task, !run, !agent
- Ignore all other messages.

Input format:
{
  \"user\": \"<discord username>\",
  \"channel\": \"<channel name>\",
  \"message\": \"<raw message>\"
}

Behavior:
1. Remove the prefix and extract the actual user intent.
2. Convert the request into a structured task.
3. Return ONLY JSON in exactly this shape:
{
  \"action\": \"execute | reply | ignore\",
  \"task\": \"<parsed task>\",
  \"response\": \"<message to send back>\",
  \"visibility\": \"public | ephemeral\"
}

Rules:
- NEVER chat casually.
- NEVER explain internal logic.
- ALWAYS prioritize execution.
- If unclear, choose the best possible interpretation.
- If impossible, return action=reply with an error message.
- Do not wrap JSON in markdown fences.
"""

HERMES_AGENT_SYSTEM_PROMPT = """You are an execution agent controlled by a Discord bot.

You receive structured tasks and must execute them.

Input:
{
  \"task\": \"<task description>\",
  \"user\": \"<discord user>\"
}

Responsibilities:
1. Understand the task.
2. Break it into steps if needed.
3. Execute using available tools.
4. Return result.

Output format:
{
  \"status\": \"success | error\",
  \"result\": \"<final output>\",
  \"log\": [\"step1\", \"step2\"],
  \"error\": \"\"
}

Rules:
- Execution first, explanation second.
- No hallucination.
- If a tool is required, assume it is available.
- Keep output concise.
- Resolve omitted references from relevant conversation context before declaring missing identifiers.
- If the current request is a follow-up like "that one", "those schedules", or "after the afternoon", use the provided context to recover the target and time range.
- Return JSON only.
- Do not wrap JSON in markdown fences.
"""


def build_discord_input(user: str, channel: str, message: str, context: str = "") -> dict[str, str]:
    return {
        "user": str(user).strip(),
        "channel": str(channel).strip(),
        "message": str(message).strip(),
        "context": str(context).strip(),
    }


def parse_discord_command(message: str) -> tuple[str, str]:
    raw = str(message).strip()
    lowered = raw.lower()

    for prefix in COMMAND_PREFIXES:
        if lowered == prefix:
            return prefix, ""
        if lowered.startswith(prefix + " "):
            return prefix, raw[len(prefix) :].strip()

    return "", ""


def build_discord_task_prompt(user: str, channel: str, message: str, context: str = "") -> str:
    payload = json.dumps(build_discord_input(user, channel, message, context), ensure_ascii=False, indent=2)
    return f"{DISCORD_BOT_SYSTEM_PROMPT}\n\nProcess this Discord message now:\n{payload}"


def build_hermes_task_prompt(task: str, user: str, context: str = "") -> str:
    payload = json.dumps({"task": str(task).strip(), "user": str(user).strip()}, ensure_ascii=False, indent=2)
    prompt = f"{HERMES_AGENT_SYSTEM_PROMPT}\n\nExecute this task now:\n{payload}"
    if str(context).strip():
        trimmed = str(context).strip()[:2000]
        prompt += f"\n\nRelevant conversation context:\n{trimmed}"
    return prompt


def _default_response(action: str, task: str, response: str, visibility: str = "public") -> dict[str, str]:
    return {
        "action": action,
        "task": task,
        "response": response,
        "visibility": visibility,
    }


def _is_gamejob_update_request(task: str) -> bool:
    normalized = " ".join(str(task or "").strip().lower().split())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in GAMEJOB_UPDATE_PATTERNS)


def _is_gamejob_match_request(task: str) -> bool:
    normalized = " ".join(str(task or "").strip().lower().split())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in GAMEJOB_MATCH_PATTERNS)


def _resolve_gamejob_python(project_root: Path) -> list[str]:
    env_python = os.getenv("HERMES_GAMEJOB_PYTHON", "").strip()
    if env_python and Path(env_python).exists():
        return [env_python]

    venv_python = project_root / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return [str(venv_python)]

    return [os.sys.executable]


def _run_gamejob_rawdata_update() -> dict[str, Any]:
    project_root = Path(DEFAULT_GAMEJOB_AGENT_ROOT)
    if not project_root.exists():
        return {
            "ok": False,
            "stderr": f"Agent_GameJob 경로를 찾을 수 없습니다: {project_root}",
        }

    main_py = project_root / "main.py"
    if not main_py.exists():
        return {
            "ok": False,
            "stderr": f"Agent_GameJob 실행 파일을 찾을 수 없습니다: {main_py}",
        }

    command = [*_resolve_gamejob_python(project_root), str(main_py), "--crawl-only"]

    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(os.getenv("HERMES_GAMEJOB_CRAWL_TIMEOUT", "1800").strip() or "1800"),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "ok": False,
            "stderr": (stderr or stdout or "Agent_GameJob 공고 업데이트가 제한 시간 내에 끝나지 않았습니다.").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "stderr": f"Agent_GameJob 공고 업데이트 실행 실패: {exc}",
        }

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        return {
            "ok": False,
            "stderr": stderr or stdout or f"Agent_GameJob 공고 업데이트가 실패했습니다. (code={completed.returncode})",
        }

    response = "Agent_GameJob 공고 로우데이터 업데이트를 완료했습니다."
    if stdout:
        response = f"{response}\n\n{stdout}"

    return {
        "ok": True,
        "result_text": response,
        "stdout": stdout,
        "stderr": stderr,
    }


JOB_MATCH_SYSTEM_PROMPT = """당신은 게임 프로그래머 채용 공고 매칭 전문가입니다.

입력으로 지원자의 이력서 데이터와 채용 공고 후보 목록이 주어집니다.
이력서를 꼼꼼히 읽고, 각 공고와의 적합도를 직접 판단하여 최적의 공고를 추천하십시오.

판단 기준:
1. 기술 스택 일치도 (언어, 엔진, 플랫폼)
2. 경력 수준 적합성 (요구 경력 vs 보유 경력)
3. 프로젝트 경험과의 연관성
4. 회사/프로젝트 특성과 지원자 배경의 부합 여부

출력 규칙:
- 추천 공고 최대 8개 선정
- 각 공고마다 "왜 이 공고를 추천하는지" 2~3문장으로 설명
- 선정되지 않은 공고군을 한 줄로 요약 설명
- Discord 마크다운 형식 사용 (**, >, ⏰ 등)
- 전체 응답은 1900자 이내
- JSON 없이 순수 텍스트로만 응답

출력 형식 예시:
**강건님 맞춤 채용 공고 추천**
*C++ / Unreal 3년 경력 기준*

**1. [회사명]** — [공고 제목] ⏰마감일
> 추천 이유: ...
> 링크: <URL>

**2. [회사명]** ...

---
*제외된 공고: [간략 이유]*
"""


def _extract_match_data_from_sheets() -> dict[str, Any] | None:
    """match_discord.py 실행해 시트 데이터를 JSON으로 추출."""
    project_root = Path(DEFAULT_GAMEJOB_AGENT_ROOT)
    match_script = project_root / "match_discord.py"

    if not project_root.exists() or not match_script.exists():
        return None

    try:
        completed = subprocess.run(
            [*_resolve_gamejob_python(project_root), str(match_script)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception:
        return None

    if completed.returncode != 0 or not completed.stdout.strip():
        return None

    try:
        return json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return None


def _build_job_match_prompt(data: dict[str, Any]) -> str:
    """추출된 시트 데이터로 LLM 판단 프롬프트 생성."""
    resume = data.get("resume", {})
    candidates = data.get("candidates", [])
    total = data.get("total_jobs", 0)

    resume_text = "\n".join(f"  {k}: {v}" for k, v in resume.items() if v)
    candidates_text = json.dumps(candidates, ensure_ascii=False, indent=2)

    return (
        f"{JOB_MATCH_SYSTEM_PROMPT}\n\n"
        f"=== 지원자 이력서 ===\n{resume_text}\n\n"
        f"=== 채용 공고 후보 ({len(candidates)}건 / 전체 신규 {total}건 중 1차 필터 통과) ===\n"
        f"{candidates_text}\n\n"
        "위 이력서와 공고를 분석하여 맞춤 추천을 작성하십시오."
    )


def _run_gamejob_match(adapter: Any) -> dict[str, Any]:
    """시트 데이터 추출 → LLM 판단 → 결과 반환."""
    data = _extract_match_data_from_sheets()

    if data is None:
        return {"ok": False, "stderr": "시트에서 데이터를 가져오지 못했습니다."}

    error = data.get("error")
    if error:
        return {"ok": False, "stderr": error}

    if not data.get("candidates"):
        return {"ok": False, "stderr": "추천 가능한 공고 후보가 없습니다."}

    prompt = _build_job_match_prompt(data)
    adapter_result = adapter.send(prompt, "")

    # adapter 결과에서 텍스트 추출
    response = adapter_result.get("response") or {}
    result_text = ""
    if isinstance(response, dict):
        result_text = response.get("result") or response.get("raw") or ""
    if not result_text:
        for key in ("stdout", "result_text", "echo", "message"):
            val = adapter_result.get(key)
            if isinstance(val, str) and val.strip():
                result_text = val.strip()
                break

    if not result_text:
        return {"ok": False, "stderr": "에이전트 응답을 받지 못했습니다."}

    return {"ok": True, "result_text": result_text}


def _calendar_context_update(plan: dict[str, Any], execution: dict[str, Any] | None = None) -> str:
    entities = plan.get("entities", {}) if isinstance(plan.get("entities"), dict) else {}
    normalized_time = plan.get("normalized_time", {}) if isinstance(plan.get("normalized_time"), dict) else {}
    tool_request = plan.get("tool_request", {}) if isinstance(plan.get("tool_request"), dict) else {}

    summary: dict[str, Any] = {
        "kind": "calendar_memory",
        "intent": str(plan.get("intent") or "").strip(),
        "status": str(plan.get("status") or "").strip(),
        "user_request_summary": str(plan.get("user_request_summary") or "").strip(),
        "title": entities.get("title"),
        "event_reference": entities.get("event_reference"),
        "calendar_target": entities.get("calendar_target"),
        "normalized_time": {
            "timezone": normalized_time.get("timezone"),
            "start": normalized_time.get("start"),
            "end": normalized_time.get("end"),
            "date_text_resolution": normalized_time.get("date_text_resolution"),
        },
        "required_data": plan.get("required_data", []),
        "tool_request": tool_request,
    }
    if execution is not None:
        summary["executed"] = bool(execution.get("executed"))
        summary["execution_status"] = str(execution.get("status") or "").strip()
        summary["user_message"] = str(execution.get("user_message") or "").strip()
    return json.dumps(summary, ensure_ascii=False)


def _normalize_visibility(value: Any) -> str:
    visibility = str(value or "").strip().lower()
    if visibility in {"public", "ephemeral"}:
        return visibility
    return "public"


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _looks_like_bot_response(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"action", "task", "response", "visibility"}
    return required.issubset(value.keys())


def _looks_like_hermes_response(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"status", "result", "log", "error"}
    return required.issubset(value.keys())


def _coerce_bot_response(value: dict[str, Any]) -> dict[str, str]:
    action = str(value.get("action", "reply") or "reply").strip().lower()
    if action not in {"execute", "reply", "ignore"}:
        action = "reply"

    return {
        "action": action,
        "task": str(value.get("task", "") or "").strip(),
        "response": str(value.get("response", "") or "").strip(),
        "visibility": _normalize_visibility(value.get("visibility")),
    }


def _coerce_hermes_response(value: dict[str, Any]) -> dict[str, Any]:
    status = str(value.get("status", "error") or "error").strip().lower()
    if status not in {"success", "error"}:
        status = "error"

    log = value.get("log", [])
    normalized_log: list[str] = []
    if isinstance(log, list):
        normalized_log = [str(step).strip() for step in log if str(step).strip()]

    return {
        "status": status,
        "result": str(value.get("result", "") or "").strip(),
        "log": normalized_log,
        "error": str(value.get("error", "") or "").strip(),
    }


def _parse_json_text(text: str) -> dict[str, str] | None:
    candidate = _strip_code_fences(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if _looks_like_bot_response(parsed):
        return _coerce_bot_response(parsed)
    return None


def _parse_hermes_json_text(text: str) -> dict[str, Any] | None:
    candidate = _strip_code_fences(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if _looks_like_hermes_response(parsed):
        return _coerce_hermes_response(parsed)
    return None


def _discord_reply_from_hermes(parsed_task: str, hermes_result: dict[str, Any]) -> dict[str, str]:
    calendar_execution = execute_calendar_plan(hermes_result["result"])
    if calendar_execution is not None:
        return _default_response("reply", parsed_task, str(calendar_execution.get("user_message", "") or "캘린더 요청을 처리했습니다."), "public")

    calendar_message = extract_calendar_user_message(hermes_result["result"])
    if calendar_message:
        return _default_response("reply", parsed_task, calendar_message, "public")

    if hermes_result["status"] == "success":
        response = hermes_result["result"] or "작업이 완료되었습니다"
        return _default_response("reply", parsed_task, response, "public")

    error_text = hermes_result["error"] or hermes_result["result"] or "요청을 실행할 수 없습니다"
    return _default_response("reply", parsed_task, error_text, "ephemeral")


def normalize_discord_execution(adapter_result: dict[str, Any], parsed_task: str) -> dict[str, str]:
    if adapter_result.get("mode") == "mock" and adapter_result.get("accepted"):
        return {
            "action": "execute",
            "task": parsed_task,
            "response": "작업 실행을 시작합니다",
            "visibility": "public",
        }

    response = adapter_result.get("response")
    if _looks_like_bot_response(response):
        return _coerce_bot_response(response)
    if _looks_like_hermes_response(response):
        return _discord_reply_from_hermes(parsed_task, _coerce_hermes_response(response))

    if isinstance(response, dict):
        for key in ("result", "raw"):
            value = response.get(key)
            if isinstance(value, str):
                calendar_execution = execute_calendar_plan(value)
                if calendar_execution is not None:
                    result = _default_response("reply", parsed_task, str(calendar_execution.get("user_message", "") or "캘린더 요청을 처리했습니다."), "public")
                    plan = calendar_execution.get("plan")
                    if isinstance(plan, dict):
                        result["context_update"] = _calendar_context_update(plan, calendar_execution)
                    return result
                calendar_message = extract_calendar_user_message(value)
                if calendar_message:
                    result = _default_response("reply", parsed_task, calendar_message, "public")
                    plan = parse_calendar_plan(value)
                    if isinstance(plan, dict):
                        result["context_update"] = _calendar_context_update(plan)
                    return result
                hermes_parsed = _parse_hermes_json_text(value)
                if hermes_parsed is not None:
                    return _discord_reply_from_hermes(parsed_task, hermes_parsed)
                parsed = _parse_json_text(value)
                if parsed is not None:
                    return parsed

    for key in ("stdout", "echo", "message", "error"):
        value = adapter_result.get(key)
        if isinstance(value, str) and value.strip():
            calendar_execution = execute_calendar_plan(value)
            if calendar_execution is not None:
                result = _default_response("reply", parsed_task, str(calendar_execution.get("user_message", "") or "캘린더 요청을 처리했습니다."), "public")
                plan = calendar_execution.get("plan")
                if isinstance(plan, dict):
                    result["context_update"] = _calendar_context_update(plan, calendar_execution)
                return result
            calendar_message = extract_calendar_user_message(value)
            if calendar_message:
                result = _default_response("reply", parsed_task, calendar_message, "public")
                plan = parse_calendar_plan(value)
                if isinstance(plan, dict):
                    result["context_update"] = _calendar_context_update(plan)
                return result
            hermes_parsed = _parse_hermes_json_text(value)
            if hermes_parsed is not None:
                return _discord_reply_from_hermes(parsed_task, hermes_parsed)
            parsed = _parse_json_text(value)
            if parsed is not None:
                return parsed

    result_text = adapter_result.get("result_text")
    if isinstance(result_text, str) and result_text.strip():
        calendar_execution = execute_calendar_plan(result_text)
        if calendar_execution is not None:
            result = _default_response("reply", parsed_task, str(calendar_execution.get("user_message", "") or "캘린더 요청을 처리했습니다."), "public")
            plan = calendar_execution.get("plan")
            if isinstance(plan, dict):
                result["context_update"] = _calendar_context_update(plan, calendar_execution)
            return result
        calendar_message = extract_calendar_user_message(result_text)
        if calendar_message:
            result = _default_response("reply", parsed_task, calendar_message, "public")
            plan = parse_calendar_plan(result_text)
            if isinstance(plan, dict):
                result["context_update"] = _calendar_context_update(plan)
            return result
        hermes_parsed = _parse_hermes_json_text(result_text)
        if hermes_parsed is not None:
            return _discord_reply_from_hermes(parsed_task, hermes_parsed)
        parsed = _parse_json_text(result_text)
        if parsed is not None:
            return parsed

    ok = bool(adapter_result.get("ok", adapter_result.get("accepted", False)))
    if ok:
        return {
            "action": "reply",
            "task": parsed_task,
            "response": adapter_result.get("result_text", "") or "작업이 완료되었습니다",
            "visibility": "public",
        }

    error_text = str(adapter_result.get("stderr", "") or adapter_result.get("error", "") or adapter_result.get("message", "")).strip()
    if not error_text:
        error_text = "요청을 실행할 수 없습니다"
    return _default_response("reply", parsed_task, error_text, "ephemeral")


def execute_discord_task(adapter: Any, user: str, channel: str, message: str, context: str = "") -> dict[str, str]:
    payload = build_discord_input(user, channel, message, context)
    if not payload["user"]:
        return _default_response("reply", "", "user is required", "ephemeral")
    if not payload["message"]:
        return _default_response("reply", "", "message is required", "ephemeral")

    _, parsed_task = parse_discord_command(payload["message"])
    if not parsed_task:
        raw = payload["message"].strip()
        if raw.lower() not in COMMAND_PREFIXES:
            return _default_response("ignore", "", "", "public")
        return _default_response("reply", "", "실행할 작업을 입력해 주세요", "ephemeral")

    if ZoneInfo is not None:
        try:
            current_datetime = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        except Exception:
            current_datetime = now_iso(DEFAULT_TIMEZONE)
    else:
        current_datetime = now_iso(DEFAULT_TIMEZONE)
    use_orchestrator = getattr(adapter, "mode", "").strip().lower() == "opencode" and not is_calendar_request(parsed_task)

    if is_calendar_request(parsed_task):
        prompt = build_calendar_manager_execution_prompt(
            user_input=parsed_task,
            discord_user=payload["user"],
            discord_channel=payload["channel"],
            user_id=payload["user"],
            current_datetime=current_datetime,
            context=payload["context"],
            timezone_name=DEFAULT_TIMEZONE,
        )
        adapter_result = adapter.send(prompt, "")
    elif _is_gamejob_update_request(parsed_task):
        adapter_result = _run_gamejob_rawdata_update()
    elif _is_gamejob_match_request(parsed_task):
        adapter_result = _run_gamejob_match(adapter)
    elif use_orchestrator:
        from orchestrator import get_default_orchestrator

        adapter_result = get_default_orchestrator().orchestrate(parsed_task, payload["user"], payload["context"])
    else:
        prompt = build_hermes_task_prompt(parsed_task, payload["user"], payload["context"])
        adapter_result = adapter.send(prompt, "")
    normalized = normalize_discord_execution(adapter_result, parsed_task)
    if not normalized["task"]:
        normalized["task"] = parsed_task
    return normalized
