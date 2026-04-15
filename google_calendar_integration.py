import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from calendar_manager_agent import CALENDAR_AGENT_NAME


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _json_request(url: str, method: str = "GET", body: dict[str, Any] | None = None, token: str = "") -> tuple[int, dict[str, Any] | None, str]:
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return response.status, None, ""
            try:
                return response.status, json.loads(raw), raw
            except json.JSONDecodeError:
                return response.status, None, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), raw
        except json.JSONDecodeError:
            return exc.code, None, raw


def _refresh_access_token() -> str:
    refresh_token = _env("GOOGLE_CALENDAR_REFRESH_TOKEN")
    client_id = _env("GOOGLE_CALENDAR_CLIENT_ID")
    client_secret = _env("GOOGLE_CALENDAR_CLIENT_SECRET")
    if not (refresh_token and client_id and client_secret):
        return ""

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return str(payload.get("access_token", "") or "").strip()
    except Exception:
        return ""


def _access_token() -> str:
    direct = _env("GOOGLE_CALENDAR_ACCESS_TOKEN")
    if direct:
        return direct
    return _refresh_access_token()


def _calendar_id(arguments: dict[str, Any], plan: dict[str, Any]) -> str:
    value = arguments.get("calendar_id") or arguments.get("calendar_target")
    if isinstance(value, str) and value.strip():
        return value.strip()
    entities = plan.get("entities", {})
    target = entities.get("calendar_target") if isinstance(entities, dict) else None
    if isinstance(target, str) and target.strip():
        return target.strip()
    return "primary"


def _event_id(arguments: dict[str, Any], plan: dict[str, Any]) -> str:
    value = arguments.get("event_id") or arguments.get("event_reference")
    if isinstance(value, str) and value.strip():
        return value.strip()
    entities = plan.get("entities", {})
    target = entities.get("event_reference") if isinstance(entities, dict) else None
    if isinstance(target, str) and target.strip():
        return target.strip()
    return ""


def _build_event_body(plan: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entities = plan.get("entities", {}) if isinstance(plan.get("entities"), dict) else {}
    normalized_time = plan.get("normalized_time", {}) if isinstance(plan.get("normalized_time"), dict) else {}
    timezone_name = str(normalized_time.get("timezone") or arguments.get("timezone") or "Asia/Seoul")

    body: dict[str, Any] = {}
    summary = arguments.get("summary") or entities.get("title")
    if summary:
        body["summary"] = summary

    location = arguments.get("location") or entities.get("location")
    if location:
        body["location"] = location

    description = arguments.get("description") or entities.get("description")
    if description:
        body["description"] = description

    start = arguments.get("start") or normalized_time.get("start")
    end = arguments.get("end") or normalized_time.get("end")
    if start:
        body["start"] = {"dateTime": start, "timeZone": timezone_name}
    if end:
        body["end"] = {"dateTime": end, "timeZone": timezone_name}

    attendees = arguments.get("attendees") or entities.get("attendees") or []
    if isinstance(attendees, list) and attendees:
        normalized_attendees = []
        for attendee in attendees:
            if isinstance(attendee, str) and attendee.strip():
                normalized_attendees.append({"email": attendee.strip()})
            elif isinstance(attendee, dict):
                normalized_attendees.append(attendee)
        if normalized_attendees:
            body["attendees"] = normalized_attendees

    for key in ("reminders", "conferenceData", "visibility"):
        if key in arguments:
            body[key] = arguments[key]
    return body


def _format_event_line(item: dict[str, Any]) -> str:
    summary = str(item.get("summary") or "제목 없음")
    start_info = item.get("start", {}) if isinstance(item.get("start"), dict) else {}
    end_info = item.get("end", {}) if isinstance(item.get("end"), dict) else {}
    start = start_info.get("dateTime") or start_info.get("date") or "시간 미정"
    end = end_info.get("dateTime") or end_info.get("date") or ""
    if end:
        return f"- {summary} ({start} ~ {end})"
    return f"- {summary} ({start})"


def _format_search_result(plan: dict[str, Any], payload: dict[str, Any]) -> str:
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not items:
        return "조회된 일정이 없습니다."
    lines = [_format_event_line(item) for item in items[:20] if isinstance(item, dict)]
    return "조회 결과입니다:\n" + "\n".join(lines)


def _format_create_update_result(payload: dict[str, Any], verb: str) -> str:
    summary = str(payload.get("summary") or "일정") if isinstance(payload, dict) else "일정"
    start_info = payload.get("start", {}) if isinstance(payload, dict) else {}
    end_info = payload.get("end", {}) if isinstance(payload, dict) else {}
    start = start_info.get("dateTime") or start_info.get("date") or "시간 미정"
    end = end_info.get("dateTime") or end_info.get("date") or ""
    if end:
        return f"{summary} 일정을 {verb}했습니다. ({start} ~ {end})"
    return f"{summary} 일정을 {verb}했습니다. ({start})"


def _format_freebusy_result(payload: dict[str, Any]) -> str:
    calendars = payload.get("calendars", {}) if isinstance(payload, dict) else {}
    if not isinstance(calendars, dict) or not calendars:
        return "빈 시간 조회 결과를 확인하지 못했습니다."
    lines = []
    for calendar_id, info in calendars.items():
        busy = info.get("busy", []) if isinstance(info, dict) else []
        if not busy:
            lines.append(f"- {calendar_id}: 요청한 범위에 바쁜 시간이 없습니다.")
            continue
        lines.append(f"- {calendar_id} 바쁜 시간:")
        for slot in busy[:20]:
            if isinstance(slot, dict):
                lines.append(f"  • {slot.get('start')} ~ {slot.get('end')}")
    return "\n".join(lines)


def _delete_query_from_arguments(arguments: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    normalized_time = plan.get("normalized_time", {}) if isinstance(plan.get("normalized_time"), dict) else {}
    entities = plan.get("entities", {}) if isinstance(plan.get("entities"), dict) else {}

    query: dict[str, Any] = {}
    if arguments.get("q"):
        query["q"] = arguments.get("q")
    elif arguments.get("summary"):
        query["q"] = arguments.get("summary")
    elif entities.get("title"):
        query["q"] = entities.get("title")

    time_min = arguments.get("timeMin") or arguments.get("time_min") or normalized_time.get("start")
    time_max = arguments.get("timeMax") or arguments.get("time_max") or normalized_time.get("end")
    if time_min:
        query["timeMin"] = time_min
    if time_max:
        query["timeMax"] = time_max

    for key in ("singleEvents", "orderBy", "maxResults"):
        value = arguments.get(key)
        if value not in (None, ""):
            query[key] = value

    if "singleEvents" not in query:
        query["singleEvents"] = True
    if "orderBy" not in query:
        query["orderBy"] = "startTime"
    return query


def _search_events_for_delete(calendar_id: str, token: str, arguments: dict[str, Any], plan: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str]:
    query = urllib.parse.urlencode({k: v for k, v in _delete_query_from_arguments(arguments, plan).items() if v is not None and v != ""}, doseq=True)
    return _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events?{query}", token=token)


def _format_bulk_delete_result(deleted_items: list[dict[str, Any]], failed_items: list[dict[str, Any]]) -> str:
    if not deleted_items and failed_items:
        return "일정 삭제에 실패했습니다."

    lines = [f"{len(deleted_items)}개의 일정을 삭제했습니다."]
    for item in deleted_items[:10]:
        lines.append(_format_event_line(item))
    if len(deleted_items) > 10:
        lines.append(f"... 외 {len(deleted_items) - 10}개")

    if failed_items:
        lines.append(f"삭제하지 못한 일정 {len(failed_items)}개가 있습니다.")
    return "\n".join(lines)


def parse_calendar_plan(json_text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("agent") != CALENDAR_AGENT_NAME:
        return None
    return payload


def execute_calendar_plan(json_text: str) -> dict[str, Any] | None:
    plan = parse_calendar_plan(json_text)
    if plan is None:
        return None

    status = str(plan.get("status") or "").strip().lower()
    user_message = str(plan.get("user_message") or "").strip()
    if status != "ready":
        return {
            "status": status or "blocked",
            "user_message": user_message or "캘린더 요청을 바로 실행할 수 없습니다.",
            "plan": plan,
            "executed": False,
        }

    tool_request = plan.get("tool_request")
    if not isinstance(tool_request, dict):
        return {
            "status": "blocked",
            "user_message": "캘린더 실행 요청 형식이 올바르지 않습니다.",
            "plan": plan,
            "executed": False,
        }

    if str(tool_request.get("tool_name") or "") != "google_calendar":
        return {
            "status": "blocked",
            "user_message": "지원되지 않는 캘린더 도구 요청입니다.",
            "plan": plan,
            "executed": False,
        }

    token = _access_token()
    if not token:
        return {
            "status": "blocked",
            "user_message": "Google Calendar 인증이 필요합니다. GOOGLE_CALENDAR_ACCESS_TOKEN 또는 refresh token 설정이 필요합니다.",
            "plan": plan,
            "executed": False,
        }

    operation = str(tool_request.get("operation") or "").strip().lower()
    arguments = tool_request.get("arguments", {}) if isinstance(tool_request.get("arguments"), dict) else {}
    calendar_id = urllib.parse.quote(_calendar_id(arguments, plan), safe="")

    if operation == "search":
        query = urllib.parse.urlencode({k: v for k, v in arguments.items() if v is not None and v != ""}, doseq=True)
        status_code, payload, raw = _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events?{query}", token=token)
        if 200 <= status_code < 300 and isinstance(payload, dict):
            return {"status": "success", "user_message": _format_search_result(plan, payload), "plan": plan, "executed": True, "raw_result": payload}
        return {"status": "blocked", "user_message": f"Google Calendar 조회에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

    if operation == "create":
        body = _build_event_body(plan, arguments)
        status_code, payload, raw = _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events", method="POST", body=body, token=token)
        if 200 <= status_code < 300 and isinstance(payload, dict):
            return {"status": "success", "user_message": _format_create_update_result(payload, "생성"), "plan": plan, "executed": True, "raw_result": payload}
        return {"status": "blocked", "user_message": f"Google Calendar 일정 생성에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

    if operation == "update":
        event_id = urllib.parse.quote(_event_id(arguments, plan), safe="")
        if not event_id:
            return {"status": "blocked", "user_message": "수정할 일정 식별자가 없습니다.", "plan": plan, "executed": False}
        body = _build_event_body(plan, arguments)
        status_code, payload, raw = _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events/{event_id}", method="PATCH", body=body, token=token)
        if 200 <= status_code < 300 and isinstance(payload, dict):
            return {"status": "success", "user_message": _format_create_update_result(payload, "수정"), "plan": plan, "executed": True, "raw_result": payload}
        return {"status": "blocked", "user_message": f"Google Calendar 일정 수정에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

    if operation == "delete":
        event_id = urllib.parse.quote(_event_id(arguments, plan), safe="")
        if not event_id:
            allow_multiple = bool(arguments.get("allow_multiple"))
            status_code, payload, raw = _search_events_for_delete(calendar_id, token, arguments, plan)
            if not (200 <= status_code < 300) or not isinstance(payload, dict):
                return {"status": "blocked", "user_message": f"삭제 대상 일정 조회에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

            items = payload.get("items", []) if isinstance(payload.get("items"), list) else []
            matched_items = [item for item in items if isinstance(item, dict) and str(item.get("id") or "").strip()]
            if not matched_items:
                return {"status": "blocked", "user_message": "삭제할 일정 식별자가 없습니다.", "plan": plan, "executed": False, "raw_result": payload}
            if len(matched_items) > 1 and not allow_multiple:
                return {
                    "status": "blocked",
                    "user_message": f"삭제 대상이 {len(matched_items)}개라 하나로 특정되지 않았습니다. 더 구체적으로 알려주세요.",
                    "plan": plan,
                    "executed": False,
                    "raw_result": payload,
                }

            deleted_items: list[dict[str, Any]] = []
            failed_items: list[dict[str, Any]] = []
            for item in matched_items:
                candidate_id = urllib.parse.quote(str(item.get("id") or "").strip(), safe="")
                delete_status, delete_payload, delete_raw = _json_request(
                    f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events/{candidate_id}",
                    method="DELETE",
                    token=token,
                )
                if 200 <= delete_status < 300 or delete_status == 204:
                    deleted_items.append(item)
                else:
                    failed_items.append({"item": item, "payload": delete_payload or delete_raw, "status": delete_status})

            if deleted_items and not failed_items:
                return {
                    "status": "success",
                    "user_message": _format_bulk_delete_result(deleted_items, failed_items),
                    "plan": plan,
                    "executed": True,
                    "raw_result": {"deleted": deleted_items, "failed": failed_items},
                }
            if deleted_items:
                return {
                    "status": "success",
                    "user_message": _format_bulk_delete_result(deleted_items, failed_items),
                    "plan": plan,
                    "executed": True,
                    "raw_result": {"deleted": deleted_items, "failed": failed_items},
                }
            return {
                "status": "blocked",
                "user_message": "일정 삭제에 실패했습니다.",
                "plan": plan,
                "executed": False,
                "raw_result": {"deleted": deleted_items, "failed": failed_items},
            }
        status_code, payload, raw = _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/calendars/{calendar_id}/events/{event_id}", method="DELETE", token=token)
        if 200 <= status_code < 300 or status_code == 204:
            return {"status": "success", "user_message": "일정을 삭제했습니다.", "plan": plan, "executed": True, "raw_result": payload or raw}
        return {"status": "blocked", "user_message": f"Google Calendar 일정 삭제에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

    if operation == "freebusy":
        body = {
            "timeMin": arguments.get("timeMin") or arguments.get("time_min") or arguments.get("start") or plan.get("normalized_time", {}).get("start"),
            "timeMax": arguments.get("timeMax") or arguments.get("time_max") or arguments.get("end") or plan.get("normalized_time", {}).get("end"),
            "timeZone": arguments.get("timezone") or plan.get("normalized_time", {}).get("timezone") or "Asia/Seoul",
            "items": arguments.get("items") or [{"id": _calendar_id(arguments, plan)}],
        }
        status_code, payload, raw = _json_request(f"{GOOGLE_CALENDAR_BASE_URL}/freeBusy", method="POST", body=body, token=token)
        if 200 <= status_code < 300 and isinstance(payload, dict):
            return {"status": "success", "user_message": _format_freebusy_result(payload), "plan": plan, "executed": True, "raw_result": payload}
        return {"status": "blocked", "user_message": f"Google Calendar 빈 시간 조회에 실패했습니다. ({status_code})", "plan": plan, "executed": False, "raw_result": payload or raw}

    if operation == "respond":
        return {
            "status": "blocked",
            "user_message": "respond_invitation 실행은 아직 연결되지 않았습니다. event_reference와 attendee 정보가 필요합니다.",
            "plan": plan,
            "executed": False,
        }

    return {
        "status": "blocked",
        "user_message": f"지원되지 않는 Google Calendar 작업입니다: {operation}",
        "plan": plan,
        "executed": False,
    }
