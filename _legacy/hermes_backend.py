"""Hermes 네이티브 실행 엔진.

OpenCode subprocess 의존성 없이 동작한다.
- 단순/전문 작업: Python skill 함수 직접 실행 (LLM 불필요)
- LLM 필요 작업: codex_backend 위임 (OpenAI API 직접 호출)
- 실패 시: retry 구조 (HERMES_MAX_RETRIES)
- 모든 결과: knowledge/memory.jsonl 에 히스토리 저장

사용법:
    from hermes_backend import send, register_skill, load_recent_memory
    result = send(prompt, context, skills=["google-calendar"])
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_backend import send as codex_send
from codex_backend import start as codex_start
from codex_backend import status as codex_status
from codex_backend import stop as codex_stop


MEMORY_PATH = Path(os.getenv("HERMES_MEMORY_PATH", "knowledge/memory.jsonl"))
DEFAULT_MAX_RETRIES = int(os.getenv("HERMES_MAX_RETRIES", "2").strip() or "2")
DEFAULT_RETRY_DELAY = float(os.getenv("HERMES_RETRY_DELAY", "1.0").strip() or "1.0")

_memory_lock = threading.Lock()

# ── Skill 레지스트리 ──────────────────────────────────────────────────────────
# 스킬명 → callable(prompt, context, skill, **kwargs) → dict
_SKILL_REGISTRY: dict[str, Any] = {}


def register_skill(name: str, fn: Any) -> None:
    """Hermes 스킬을 런타임에 등록한다.

    fn 시그니처: fn(prompt: str, context: str = "", skill: str = "", **kwargs) -> dict
    반환값은 반드시 {"ok": bool, "result_text": str, ...} 형태여야 한다.
    """
    _SKILL_REGISTRY[name] = fn


def unregister_skill(name: str) -> bool:
    """등록된 스킬을 제거한다. 존재하지 않으면 False 반환."""
    if name in _SKILL_REGISTRY:
        del _SKILL_REGISTRY[name]
        return True
    return False


def list_registered_skills() -> list[str]:
    """현재 등록된 스킬 이름 목록을 반환한다."""
    return list(_SKILL_REGISTRY.keys())


def _load_builtin_skills() -> None:
    """내장 Python 스킬을 자동 등록한다. 임포트 실패 시 조용히 무시한다."""
    try:
        from skills.calendar import run as calendar_run  # type: ignore[import]
        register_skill("google-calendar", calendar_run)
    except ImportError:
        pass

    try:
        from skills.unreal import run as unreal_run  # type: ignore[import]
        register_skill("unreal-mcp", unreal_run)
        register_skill("unreal", unreal_run)
    except ImportError:
        pass

    try:
        from skills.discord import run as discord_run  # type: ignore[import]
        register_skill("discord", discord_run)
    except ImportError:
        pass


_load_builtin_skills()


# ── Memory ────────────────────────────────────────────────────────────────────

def _append_memory(record: dict[str, Any]) -> None:
    """실행 결과를 knowledge/memory.jsonl 에 추가한다."""
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = dict(record)
        record["timestamp"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        with _memory_lock:
            with open(MEMORY_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_recent_memory(limit: int = 20) -> list[dict[str, Any]]:
    """최근 실행 히스토리를 반환한다. 반복 작업 최적화에 활용한다."""
    if not MEMORY_PATH.exists():
        return []
    try:
        lines = MEMORY_PATH.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-limit * 3:]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        return records[-limit:]
    except Exception:
        return []


# ── Skill 디스패치 ────────────────────────────────────────────────────────────

def _try_skill_dispatch(
    prompt: str,
    skills: list[str],
    context: str = "",
) -> dict[str, Any] | None:
    """등록된 스킬 중 우선순위 순서로 매칭되는 것을 찾아 직접 실행한다.

    매칭되는 스킬이 없으면 None 반환.
    스킬 실행 중 예외가 발생하면 오류 결과 dict 반환.
    """
    for skill_name in skills:
        fn = _SKILL_REGISTRY.get(skill_name)
        if fn is None:
            continue
        try:
            result = fn(prompt=prompt, context=context, skill=skill_name)
            if isinstance(result, dict):
                result.setdefault("mode", f"hermes-skill:{skill_name}")
                result.setdefault("skill", skill_name)
                return result
        except Exception as exc:  # noqa: BLE001
            return {
                "mode": f"hermes-skill:{skill_name}",
                "skill": skill_name,
                "ok": False,
                "result_text": f"스킬 실행 오류: {exc}",
                "stderr": str(exc),
                "stdout": "",
            }
    return None


# ── 메인 실행 엔진 ────────────────────────────────────────────────────────────

def send(
    prompt: str,
    context: str = "",
    model: str | None = None,
    variant: str | None = None,
    skills: list[str] | None = None,
    max_retries: int | None = None,
    timeout_seconds: int | None = None,
    # 하위 호환성: agent_pool.py 에서 넘어오는 opencode 전용 인수 무시
    host: str | None = None,
    port: int | None = None,
    pid_file: Any = None,
    log_file: Any = None,
) -> dict[str, Any]:
    """Hermes 통합 실행 함수.

    우선순위:
    1. skills 리스트가 주어지면 Python 스킬 직접 실행 (LLM 호출 없음)
    2. 스킬 없거나 미등록이면 codex_backend → OpenAI API 직접 호출
    3. 실패 시 max_retries 횟수만큼 재시도 (지수 백오프)
    4. 결과를 memory.jsonl 에 저장
    """
    del host, port, pid_file, log_file  # opencode 전용 인수 — 사용하지 않음

    resolved_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES

    # 1. Python 스킬 직접 실행 경로
    if skills:
        skill_result = _try_skill_dispatch(prompt, skills, context)
        if skill_result is not None:
            _append_memory({
                "path": "skill",
                "skills": skills,
                "prompt": prompt[:200],
                "ok": skill_result.get("ok"),
                "result": str(skill_result.get("result_text") or "")[:200],
            })
            return skill_result

    # 2. LLM 실행 경로 (codex_backend → OpenAI API)
    last_result: dict[str, Any] = {
        "mode": "hermes",
        "ok": False,
        "result_text": "",
        "stdout": "",
        "stderr": "실행되지 않음",
    }
    attempts = 0
    for attempt in range(max(1, resolved_retries + 1)):
        attempts = attempt + 1
        last_result = codex_send(
            prompt,
            context,
            model=model,
            variant=variant,
            timeout_seconds=timeout_seconds,
        )
        if last_result.get("ok"):
            break
        if attempt < resolved_retries:
            time.sleep(DEFAULT_RETRY_DELAY * (attempt + 1))

    last_result["mode"] = "hermes"
    last_result["hermes_attempts"] = attempts

    _append_memory({
        "path": "llm",
        "skills": skills or [],
        "prompt": prompt[:200],
        "ok": last_result.get("ok"),
        "attempts": attempts,
        "result": str(last_result.get("result_text") or "")[:200],
    })
    return last_result


# ── 상태 관리 (stateless — codex_backend 위임) ────────────────────────────────

def status(**_: Any) -> dict[str, Any]:
    base = codex_status()
    return {
        **base,
        "mode": "hermes",
        "skill_count": len(_SKILL_REGISTRY),
        "registered_skills": list(_SKILL_REGISTRY.keys()),
    }


def start(**_: Any) -> dict[str, Any]:
    base = codex_start()
    return {
        **base,
        "mode": "hermes",
        "started": base.get("started", base.get("ok", False)),
    }


def stop(**_: Any) -> dict[str, Any]:
    base = codex_stop()
    return {**base, "mode": "hermes"}
