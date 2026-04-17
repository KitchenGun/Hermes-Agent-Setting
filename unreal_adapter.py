"""unreal_adapter — Hermes ↔ UE5 어댑터 (독립형)

역할:
    1. Orchestrator가 GPT에서 받은 intent JSON 텍스트를 파싱
    2. UE5 TCP(127.0.0.1:13377)에 직접 명령 전송
    3. 결과를 Hermes 기대 포맷으로 변환하여 반환

기존 OpenCode MCP 타임아웃(-32001) 문제 우회:
    기존: Orchestrator → OpenCode → GPT(MCP tool 호출) → stdio → UE5 TCP
         └─ GPT가 MCP tool을 직접 실행 → 타임아웃 발생 → 중복 재시도
    신규: Orchestrator → GPT(intent JSON만 출력) → 이 어댑터 → UE5 TCP 직접
         └─ GPT는 JSON만 출력, 이 모듈이 TCP 처리 → 타임아웃 안전

의존성: 표준 라이브러리만 사용 (외부 패키지 불필요)

반환 포맷 (Hermes 기대값 ❗변경 금지❗):
    {"ok": bool, "result_text": str, "mode": "unreal-mcp-adapter",
     "stderr": str, "returncode": None, "stdout": str}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
import concurrent.futures
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# UE5 TCP 설정
# ---------------------------------------------------------------------------

_UE5_HOST = "127.0.0.1"
_UE5_PORT = 13377
_UE5_TIMEOUT = 30.0
_SUMMARY_LIMIT = 10
_GRAPH_NODE_LIMIT = 20
_LOG_TAIL_LIMIT = 200
_DEFAULT_PROJECT_DIR = Path(os.getenv("HERMES_UNREAL_PROJECT_DIR", r"D:\PanicRoom"))
_DEFAULT_LOG_DIR = Path(os.getenv("HERMES_UNREAL_LOG_DIR", str(_DEFAULT_PROJECT_DIR / "Saved" / "Logs")))

# ---------------------------------------------------------------------------
# 멱등성 캐시 (프로세스 내 전역)
# ---------------------------------------------------------------------------

_IDEM_CACHE: dict[str, tuple[float, str]] = {}
_IDEM_WINDOW = 60  # seconds

_IDEMPOTENT_TOOLS = {
    "create_actor",
    "duplicate_actor",
    "spawn_blueprint_actor",
    "create_blueprint",
    "create_material",
    "create_behavior_tree",
    "create_blackboard",
    "create_niagara_system",
    "create_animation_blueprint",
    "create_widget_blueprint",
}


def _idem_key(tool_name: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"tool": tool_name, "params": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _idem_prune() -> None:
    cutoff = time.time() - _IDEM_WINDOW
    expired = [k for k, (ts, _) in _IDEM_CACHE.items() if ts < cutoff]
    for k in expired:
        del _IDEM_CACHE[k]


def _idem_is_duplicate(key: str) -> bool:
    _idem_prune()
    return key in _IDEM_CACHE


def _idem_get(key: str) -> str | None:
    entry = _IDEM_CACHE.get(key)
    return entry[1] if entry else None


def _idem_record(key: str, result: str) -> None:
    _IDEM_CACHE[key] = (time.time(), result)


# ---------------------------------------------------------------------------
# UE5 TCP 클라이언트 (비동기)
# ---------------------------------------------------------------------------

async def _ue5_send_async(
    tool_name: str,
    params: dict[str, Any],
    timeout: float = _UE5_TIMEOUT,
) -> dict[str, Any]:
    """UE5 플러그인에 명령 전송 (비동기)."""
    command = {
        "id": str(uuid.uuid4()),
        "type": tool_name,
        "params": params,
    }
    payload = json.dumps(command, ensure_ascii=False) + "\n"

    reader, writer = await asyncio.open_connection(_UE5_HOST, _UE5_PORT)
    try:
        writer.write(payload.encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    if not raw:
        raise ConnectionError("UE5 plugin이 연결을 닫았습니다.")

    return json.loads(raw.decode("utf-8").strip())


def _ue5_send_sync(
    tool_name: str,
    params: dict[str, Any],
    timeout: float = _UE5_TIMEOUT,
) -> dict[str, Any]:
    """UE5 플러그인에 명령 전송 (동기 래퍼)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_ue5_send_async(tool_name, params, timeout))

    # 이미 루프가 실행 중인 경우 → 새 스레드에서 실행
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _ue5_send_async(tool_name, params, timeout))
        return future.result()


# ---------------------------------------------------------------------------
# Intent 파싱 (GPT 출력 → ToolCall)
# ---------------------------------------------------------------------------

_ACTION_ALIASES: dict[str, str] = {
    "create": "create", "spawn": "create", "add": "create",
    "place": "create", "생성": "create", "배치": "create", "추가": "create",
    "delete": "delete", "remove": "delete", "destroy": "delete", "삭제": "delete",
    "move": "transform", "set_transform": "transform", "transform": "transform",
    "이동": "transform", "위치": "transform",
    "list": "query", "get_all": "query", "query": "query", "목록": "query",
    "find": "find", "search": "find", "찾기": "find",
    "get_props": "get_props", "properties": "get_props", "get": "get_props", "속성": "get_props",
    "set_prop": "set_prop", "set_property": "set_prop", "set": "set_prop", "설정": "set_prop",
    "duplicate": "duplicate", "copy": "duplicate", "복제": "duplicate",
}

_CLASS_ALIASES: dict[str, str] = {
    "pointlight": "PointLight", "point_light": "PointLight", "포인트라이트": "PointLight",
    "spotlight": "SpotLight", "spot_light": "SpotLight",
    "directionallight": "DirectionalLight", "directional_light": "DirectionalLight",
    "skylight": "SkyLight", "sky_light": "SkyLight",
    "rectlight": "RectLight", "rect_light": "RectLight",
    "staticmeshactor": "StaticMeshActor", "static_mesh_actor": "StaticMeshActor",
    "staticmesh": "StaticMeshActor",
    "cameraactor": "CameraActor", "camera": "CameraActor",
    "fog": "ExponentialHeightFog", "heightfog": "ExponentialHeightFog",
}


def _normalize_class(raw: str) -> str:
    return _CLASS_ALIASES.get(raw.lower().strip(), raw.strip())


def _normalize_vector(raw: Any, default: list[float] | None = None) -> list[float]:
    default = default or [0.0, 0.0, 0.0]
    if raw is None:
        return default
    if isinstance(raw, (list, tuple)):
        nums = [float(v) for v in raw]
        while len(nums) < 3:
            nums.append(0.0)
        return nums[:3]
    if isinstance(raw, dict):
        return [
            float(raw.get("x", raw.get("X", 0.0))),
            float(raw.get("y", raw.get("Y", 0.0))),
            float(raw.get("z", raw.get("Z", 0.0))),
        ]
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw.strip())
        try:
            nums = [float(p) for p in parts if p]
            while len(nums) < 3:
                nums.append(0.0)
            return nums[:3]
        except ValueError:
            pass
    return default


def _extract_json(text: str) -> dict[str, Any] | None:
    """텍스트에서 JSON 딕셔너리 추출."""
    if not text:
        return None
    text = text.strip()

    # 마크다운 코드 블록
    md = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if md:
        try:
            parsed = json.loads(md.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # 직접 JSON 파싱
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 첫 번째 { ... } 추출
    brace = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\})", text, re.DOTALL)
    if brace:
        try:
            parsed = json.loads(brace.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


class _ParseError(ValueError):
    pass


def _limit_int(value: Any, default: int, minimum: int = 1, maximum: int = _SUMMARY_LIMIT) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _tail_log_lines(log_path: Path, tail_lines: int, contains: str = "") -> dict[str, Any]:
    if not log_path.exists():
        raise FileNotFoundError(f"log file not found: {log_path}")

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if contains.strip():
        needle = contains.strip().lower()
        lines = [line for line in lines if needle in line.lower()]
    selected = lines[-tail_lines:]
    return {
        "log_path": str(log_path),
        "tail_lines": tail_lines,
        "contains": contains.strip(),
        "line_count": len(selected),
        "lines": selected,
    }


def _execute_local_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name != "tail_editor_log":
        return None

    tail_lines = _limit_int(params.get("tail_lines"), default=80, maximum=_LOG_TAIL_LIMIT)
    raw_path = str(params.get("log_path", "")).strip()
    contains = str(params.get("contains", "")).strip()

    if raw_path:
        log_path = Path(raw_path)
    else:
        candidates = sorted(_DEFAULT_LOG_DIR.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"no log files found in {_DEFAULT_LOG_DIR}")
        log_path = candidates[0]

    return {
        "success": True,
        "result": _tail_log_lines(log_path, tail_lines=tail_lines, contains=contains),
        "error": None,
    }


def _validate_tool_request(tool_name: str, params: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if tool_name == "get_actors_in_level" and not str(params.get("actor_class_filter", "")).strip():
        errors.append("actor_class_filter is required for actor list queries")
    if tool_name == "search_assets" and not str(params.get("query", "")).strip() and not str(params.get("asset_class_filter", "")).strip():
        errors.append("query or asset_class_filter is required for asset search")
    if tool_name == "get_asset_details" and not str(params.get("asset_path", "")).strip():
        errors.append("asset_path is required")
    if tool_name == "get_blueprint_graph":
        if not str(params.get("blueprint_name", "")).strip():
            errors.append("blueprint_name is required")
        if not str(params.get("graph_name", "")).strip():
            errors.append("graph_name is required")
    if tool_name == "tail_editor_log":
        if "tail_lines" in params and _limit_int(params.get("tail_lines"), 0, minimum=0, maximum=_LOG_TAIL_LIMIT) <= 0:
            errors.append("tail_lines must be between 1 and 200")
    if tool_name == "inspect_uobject" and not str(params.get("object_path", "")).strip():
        errors.append("object_path is required")

    return errors


def _intent_to_tool(intent: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """intent dict → (tool_name, params).

    Raises:
        _ParseError: 파싱/매핑 실패
    """
    # 직접 tool 지정
    if "tool" in intent:
        tool_name = str(intent["tool"]).strip()
        raw_params = intent.get("params", {})
        params = raw_params if isinstance(raw_params, dict) else {}
        return tool_name, params

    raw_action = str(intent.get("action", "")).strip().lower()
    if not raw_action:
        raise _ParseError(f"intent에 'action' 또는 'tool' 필드가 없습니다: {intent}")

    action = _ACTION_ALIASES.get(raw_action)
    if not action:
        raise _ParseError(f"알 수 없는 action: {raw_action!r}")

    if action == "create":
        cls = _normalize_class(str(intent.get("class", intent.get("actor_class", "StaticMeshActor"))))
        return "create_actor", {
            "actor_class": cls,
            "name": str(intent.get("name", "")),
            "location": _normalize_vector(intent.get("location")),
            "rotation": _normalize_vector(intent.get("rotation")),
            "scale": _normalize_vector(intent.get("scale"), default=[1.0, 1.0, 1.0]),
        }

    if action == "delete":
        name = str(intent.get("name", "")).strip()
        if not name:
            raise _ParseError("delete 명령에 'name' 필드가 필요합니다.")
        return "delete_actor", {"name": name}

    if action == "transform":
        name = str(intent.get("name", "")).strip()
        if not name:
            raise _ParseError("transform 명령에 'name' 필드가 필요합니다.")
        params: dict[str, Any] = {"name": name}
        if "location" in intent:
            params["location"] = _normalize_vector(intent["location"])
        if "rotation" in intent:
            params["rotation"] = _normalize_vector(intent["rotation"])
        if "scale" in intent:
            params["scale"] = _normalize_vector(intent["scale"], default=[1.0, 1.0, 1.0])
        return "set_actor_transform", params

    if action == "query":
        return "get_actors_in_level", {
            "actor_class_filter": str(intent.get("filter", intent.get("class", "")))
        }

    if action == "find":
        pattern = str(intent.get("pattern", intent.get("name", ""))).strip()
        if not pattern:
            raise _ParseError("find 명령에 'pattern' 필드가 필요합니다.")
        return "find_actors_by_name", {"pattern": pattern}

    if action == "get_props":
        name = str(intent.get("name", "")).strip()
        if not name:
            raise _ParseError("get_props 명령에 'name' 필드가 필요합니다.")
        return "get_actor_properties", {"name": name}

    if action == "set_prop":
        name = str(intent.get("name", "")).strip()
        prop = str(intent.get("property", intent.get("property_name", ""))).strip()
        value = intent.get("value", intent.get("property_value", ""))
        if not name or not prop:
            raise _ParseError("set_prop 명령에 'name', 'property', 'value' 필드가 필요합니다.")
        return "set_actor_property", {
            "name": name,
            "property_name": prop,
            "property_value": json.dumps(value, ensure_ascii=False),
        }

    if action == "duplicate":
        name = str(intent.get("name", "")).strip()
        if not name:
            raise _ParseError("duplicate 명령에 'name' 필드가 필요합니다.")
        return "duplicate_actor", {
            "name": name,
            "new_name": str(intent.get("new_name", "")),
            "offset": _normalize_vector(intent.get("offset"), default=[100.0, 0.0, 0.0]),
        }

    raise _ParseError(f"지원하지 않는 action: {action!r}")


# ---------------------------------------------------------------------------
# 결과 요약 (사람 친화적 텍스트)
# ---------------------------------------------------------------------------

def _summarize(tool_name: str, result: dict[str, Any]) -> str:
    if tool_name in ("create_actor", "get_actor_properties", "duplicate_actor", "set_actor_property"):
        name = result.get("name", "")
        cls = result.get("actor_class", "")
        loc = result.get("location", {})
        if isinstance(loc, dict):
            loc_str = f"({loc.get('x', 0)}, {loc.get('y', 0)}, {loc.get('z', 0)})"
        elif isinstance(loc, list) and len(loc) >= 3:
            loc_str = f"({loc[0]}, {loc[1]}, {loc[2]})"
        else:
            loc_str = str(loc)
        parts = []
        if name:
            parts.append(f"name={name!r}")
        if cls:
            parts.append(f"class={cls}")
        if loc_str and loc_str not in ("(0, 0, 0)", "(0.0, 0.0, 0.0)"):
            parts.append(f"location={loc_str}")
        return ", ".join(parts) if parts else json.dumps(result, ensure_ascii=False)

    if tool_name in ("get_actors_in_level", "find_actors_by_name"):
        actors = result.get("actors", [])
        count = result.get("count", len(actors))
        if not actors:
            return f"레벨에 해당 액터 없음 (total={count})"
        names = [a.get("name", "?") for a in actors[:10]]
        suffix = f" ... 외 {count - 10}개" if count > 10 else ""
        return f"총 {count}개: {', '.join(names)}{suffix}"

    if tool_name == "delete_actor":
        deleted = result.get("deleted", "")
        return f"삭제 완료: {deleted!r}" if deleted else "삭제 완료"

    if tool_name == "set_actor_transform":
        return f"트랜스폼 업데이트: {_summarize('create_actor', result)}"

    if tool_name == "search_assets":
        assets = result.get("assets", [])
        count = int(result.get("count", len(assets)))
        head = assets[:_SUMMARY_LIMIT]
        listed = [
            f"{item.get('name', '?')}<{item.get('asset_class', '?')}>"
            for item in head
            if isinstance(item, dict)
        ]
        suffix = f" (+{count - len(head)})" if count > len(head) else ""
        return f"assets={count}: {', '.join(listed)}{suffix}" if listed else f"assets={count}"

    if tool_name == "get_asset_details":
        tags = result.get("tags", {})
        keys = list(tags.keys())[:_SUMMARY_LIMIT] if isinstance(tags, dict) else []
        parts = [
            f"name={result.get('name', '')}",
            f"class={result.get('asset_class', '')}",
            f"path={result.get('object_path', '')}",
        ]
        if keys:
            parts.append("tags=" + ", ".join(keys))
        return ", ".join(part for part in parts if part and not part.endswith("="))

    if tool_name == "get_blueprint_graph":
        nodes = result.get("nodes", [])
        limit = _limit_int(result.get("node_limit"), default=_GRAPH_NODE_LIMIT, maximum=_GRAPH_NODE_LIMIT)
        head = nodes[:limit]
        listed = [
            str(node.get("node_title") or node.get("node_class") or "?")
            for node in head
            if isinstance(node, dict)
        ]
        suffix = f" (+{len(nodes) - len(head)} more)" if len(nodes) > len(head) else ""
        return (
            f"{result.get('blueprint_name', '')}:{result.get('graph_name', '')} "
            f"nodes={result.get('node_count', len(nodes))} "
            f"{', '.join(listed)}{suffix}"
        ).strip()

    if tool_name == "inspect_uobject":
        properties = result.get("properties", [])
        functions = result.get("functions", [])
        parts = [
            f"object={result.get('object_path', result.get('class_name', ''))}",
            f"properties={len(properties) if isinstance(properties, list) else 0}",
            f"functions={len(functions) if isinstance(functions, list) else 0}",
        ]
        return ", ".join(parts)

    if tool_name == "tail_editor_log":
        lines = result.get("lines", [])
        preview = " | ".join(str(line) for line in lines[-5:])
        return f"log={result.get('line_count', len(lines))}: {preview}".strip()

    return json.dumps(result, ensure_ascii=False, indent=None)


# ---------------------------------------------------------------------------
# Hermes 포맷 생성
# ---------------------------------------------------------------------------

def _make_hermes(
    ok: bool,
    result_text: str,
    stderr: str = "",
    returncode: int | None = None,
) -> dict[str, Any]:
    """❗ 이 포맷은 절대 변경하지 않는다 ❗"""
    return {
        "ok": ok,
        "result_text": result_text,
        "mode": "unreal-mcp-adapter",
        "stderr": stderr,
        "returncode": returncode,
        "stdout": result_text,
    }


def _hermes_from_ue5_response(
    tool_name: str,
    response: dict[str, Any],
    idempotency_skipped: bool = False,
) -> dict[str, Any]:
    """UE5 TCP 응답 → Hermes 포맷."""
    # KitchenGun 표준: {"success": bool, "result": {...}, "error": {...}}
    if "success" in response:
        success = bool(response.get("success", False))
        result = response.get("result") or {}
        error = response.get("error") or {}

        if success:
            summary = _summarize(tool_name, result) if isinstance(result, dict) else str(result)
            prefix = "[중복 방지: 캐시 결과] " if idempotency_skipped else ""
            return _make_hermes(ok=True, result_text=f"{prefix}{summary}")

        if isinstance(error, dict):
            code = error.get("code", "UNKNOWN")
            message = error.get("message", "알 수 없는 오류")
            err_text = f"[{code}] {message}"
        else:
            err_text = str(error) or "요청을 실행할 수 없습니다"
        return _make_hermes(ok=False, result_text=err_text, stderr=err_text)

    # chongdashu 레거시: {"status": "success"|"error", ...}
    if "status" in response:
        status = str(response.get("status", "")).lower()
        if status == "success":
            result = response.get("result", response)
            summary = _summarize(tool_name, result) if isinstance(result, dict) else str(result)
            return _make_hermes(ok=True, result_text=summary)
        err_msg = str(response.get("error", response.get("message", "요청을 실행할 수 없습니다")))
        return _make_hermes(ok=False, result_text=err_msg, stderr=err_msg)

    # 알 수 없는 포맷
    raw = json.dumps(response, ensure_ascii=False)[:2000]
    return _make_hermes(ok=True, result_text=raw)


# ---------------------------------------------------------------------------
# 메인 공개 함수
# ---------------------------------------------------------------------------

def execute_unreal_intent(
    intent_json: str,
    ue5_timeout: float = _UE5_TIMEOUT,
) -> dict[str, Any]:
    """GPT intent JSON → UE5 실행 → Hermes 포맷 반환.

    Args:
        intent_json:  GPT가 출력한 텍스트 (JSON 포함)
        ue5_timeout:  UE5 TCP 응답 대기 시간 (초)

    Returns:
        Hermes 기대 포맷 dict
    """
    # 1. Intent 추출
    intent = _extract_json(intent_json)
    if intent is None:
        return _make_hermes(
            ok=False,
            result_text="Intent JSON을 추출할 수 없습니다",
            stderr=f"파싱 실패: {intent_json[:200]}",
        )

    # 2. Intent → ToolCall
    if bool(intent.get("invalid")):
        reason = str(intent.get("reason", "")).strip() or "insufficient information"
        missing = intent.get("missing", [])
        suffix = f" missing={', '.join(str(item) for item in missing)}" if isinstance(missing, list) and missing else ""
        return _make_hermes(ok=False, result_text=f"{reason}{suffix}", stderr=f"{reason}{suffix}")

    try:
        tool_name, params = _intent_to_tool(intent)
    except _ParseError as exc:
        return _make_hermes(ok=False, result_text=str(exc), stderr=str(exc))

    # 3. 멱등성 체크
    if bool(intent.get("invalid")):
        reason = str(intent.get("reason", "")).strip() or "insufficient information"
        missing = intent.get("missing", [])
        suffix = f" missing={', '.join(str(item) for item in missing)}" if isinstance(missing, list) and missing else ""
        return _make_hermes(ok=False, result_text=f"{reason}{suffix}", stderr=f"{reason}{suffix}")

    validation_errors = _validate_tool_request(tool_name, params)
    if validation_errors:
        message = "insufficient information: " + "; ".join(validation_errors)
        return _make_hermes(ok=False, result_text=message, stderr=message)

    try:
        local_response = _execute_local_tool(tool_name, params)
    except Exception as exc:  # noqa: BLE001
        message = f"local tool error: {exc}"
        return _make_hermes(ok=False, result_text=message, stderr=message)
    if local_response is not None:
        return _hermes_from_ue5_response(tool_name, local_response)

    idem_key: str | None = None
    if tool_name in _IDEMPOTENT_TOOLS:
        idem_key = _idem_key(tool_name, params)
        if _idem_is_duplicate(idem_key):
            cached_raw = _idem_get(idem_key)
            if cached_raw:
                try:
                    cached_dict = json.loads(cached_raw)
                    return _hermes_from_ue5_response(tool_name, cached_dict, idempotency_skipped=True)
                except Exception:
                    pass

    # 4. UE5 TCP 실행
    try:
        response = _ue5_send_sync(tool_name, params, timeout=ue5_timeout)
    except ConnectionError as exc:
        msg = f"UE5 연결 실패: {exc}"
        return _make_hermes(ok=False, result_text=msg, stderr=msg)
    except TimeoutError as exc:
        msg = f"UE5 응답 타임아웃: {exc}"
        return _make_hermes(ok=False, result_text=msg, stderr=msg)
    except Exception as exc:  # noqa: BLE001
        msg = f"실행 오류: {exc}"
        return _make_hermes(ok=False, result_text=msg, stderr=msg)

    # 5. 멱등성 결과 기록
    if idem_key is not None:
        _idem_record(idem_key, json.dumps(response, ensure_ascii=False))

    return _hermes_from_ue5_response(tool_name, response)

    # 6. Hermes 포맷 변환
    return _hermes_from_ue5_response(tool_name, response)
