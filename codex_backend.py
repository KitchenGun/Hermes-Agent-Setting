import json
import os
import urllib.error
import urllib.request
from typing import Any


_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# LM Studio / Ollama 등 로컬 서버: /chat/completions 사용
# OpenAI 공식 API: /responses 사용 (기본값)
_IS_LOCAL = any(h in _BASE_URL for h in ("localhost", "127.0.0.1", "0.0.0.0"))
_API_MODE = os.getenv("HERMES_API_MODE", "chat" if _IS_LOCAL else "responses")

API_URL = _BASE_URL + ("/chat/completions" if _API_MODE == "chat" else "/responses")
DEFAULT_MODEL = os.getenv("HERMES_CODEX_MODEL", "qwen3-coder-30b" if _IS_LOCAL else "gpt-5.4").strip()
DEFAULT_VARIANT = os.getenv("HERMES_CODEX_VARIANT", "medium").strip() or "medium"
DEFAULT_TIMEOUT = int(os.getenv("HERMES_TASK_TIMEOUT", "120").strip() or "120")


def _normalize_model(model: str | None) -> str:
    raw = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if "/" in raw:
        _, _, raw = raw.partition("/")
    return raw


def _reasoning_effort(variant: str | None) -> str:
    raw = (variant or DEFAULT_VARIANT).strip().lower()
    if raw in {"minimal", "low", "medium", "high"}:
        return raw
    if raw in {"xhigh", "very-high", "very_high"}:
        return "high"
    return "medium"


def _headers() -> dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY", "lm-studio").strip() or "lm-studio"
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _build_body(prompt: str, model: str, variant: str | None) -> dict[str, Any]:
    if _API_MODE == "chat":
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 4096,
        }
    return {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": _reasoning_effort(variant)},
    }


def _extract_output_text(payload: dict[str, Any]) -> str:
    # Chat Completions 응답 형식
    choices = payload.get("choices")
    if choices and isinstance(choices, list):
        msg = choices[0].get("message", {})
        return str(msg.get("content") or "").strip()

    # OpenAI Responses API 형식
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct

    collected: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or "").strip()
            if text:
                collected.append(text)
    return "\n".join(part for part in collected if part).strip()


def status(**_: Any) -> dict[str, Any]:
    return {
        "mode": "codex",
        "running": True,
        "ok": True,
        "model": DEFAULT_MODEL,
        "variant": DEFAULT_VARIANT,
        "api_mode": _API_MODE,
        "api_url": API_URL,
        "message": f"Backend ready ({_API_MODE} mode → {API_URL})",
    }


def start(**_: Any) -> dict[str, Any]:
    return {**status(), "started": True}


def stop(**_: Any) -> dict[str, Any]:
    return {"mode": "codex", "stopped": True, "ok": True, "message": "Codex backend is stateless"}


def send(
    prompt: str,
    context: str = "",
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | None = None,
    log_file: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    del host, port, pid_file, log_file

    resolved_model = _normalize_model(model)
    resolved_timeout = int(timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT)

    full_prompt = prompt.strip()
    if context.strip():
        full_prompt += "\n\nAdditional context:\n" + context.strip()

    body = _build_body(full_prompt, resolved_model, variant)
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=resolved_timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "mode": "codex",
            "ok": False,
            "status": exc.code,
            "stderr": raw,
            "stdout": "",
            "result_text": "",
            "model": resolved_model,
            "variant": variant or DEFAULT_VARIANT,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "mode": "codex",
            "ok": False,
            "stderr": str(exc),
            "stdout": "",
            "result_text": "",
            "model": resolved_model,
            "variant": variant or DEFAULT_VARIANT,
        }

    result_text = _extract_output_text(payload)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "mode": "codex",
        "ok": bool(result_text),
        "stdout": result_text,
        "stderr": "",
        "result_text": result_text,
        "model": resolved_model,
        "variant": variant or DEFAULT_VARIANT,
        "usage": usage,
        "response_id": payload.get("id"),
    }
