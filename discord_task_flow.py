import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone
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
        prompt += f"\n\nRelevant conversation context:\n{str(context).strip()}"
    return prompt


def _default_response(action: str, task: str, response: str, visibility: str = "public") -> dict[str, str]:
    return {
        "action": action,
        "task": task,
        "response": response,
        "visibility": visibility,
    }


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
        adapter_result = adapter.send(prompt, payload["context"])
    elif use_orchestrator:
        from orchestrator import get_default_orchestrator

        adapter_result = get_default_orchestrator().orchestrate(parsed_task, payload["user"], payload["context"])
    else:
        prompt = build_hermes_task_prompt(parsed_task, payload["user"], payload["context"])
        adapter_result = adapter.send(prompt, payload["context"])
    normalized = normalize_discord_execution(adapter_result, parsed_task)
    if not normalized["task"]:
        normalized["task"] = parsed_task
    return normalized
