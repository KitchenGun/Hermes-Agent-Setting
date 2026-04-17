import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HOST = os.getenv("HERMES_OPENCODE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("HERMES_OPENCODE_PORT", "4096").strip() or "4096")
DEFAULT_MODEL = os.getenv("HERMES_OPENCODE_MODEL", "openai/gpt-5.4").strip() or "openai/gpt-5.4"
DEFAULT_VARIANT = os.getenv("HERMES_OPENCODE_VARIANT", "medium").strip() or "medium"
DEFAULT_TIMEOUT = int(os.getenv("HERMES_TASK_TIMEOUT", "120").strip() or "120")
STATE_DIR = Path(os.getenv("HERMES_OPENCODE_STATE_DIR", str(Path.home() / ".config" / "opencode")))


@dataclass(slots=True)
class OpenCodeConfig:
    host: str = HOST
    port: int = PORT
    model: str = DEFAULT_MODEL
    variant: str = DEFAULT_VARIANT
    pid_file: Path | None = None
    log_file: Path | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT

    @property
    def attach_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _config_name(config: OpenCodeConfig) -> str:
    return f"{config.host.replace('.', '_')}_{config.port}"


def _with_defaults(
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> OpenCodeConfig:
    resolved_host = (host or HOST).strip() or HOST
    resolved_port = int(port if port is not None else PORT)
    resolved_model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    resolved_variant = (variant or DEFAULT_VARIANT).strip() or DEFAULT_VARIANT
    resolved_timeout = int(timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT)
    name = f"{resolved_host.replace('.', '_')}_{resolved_port}"
    resolved_pid = Path(pid_file) if pid_file else STATE_DIR / f"opencode_{name}.pid"
    resolved_log = Path(log_file) if log_file else STATE_DIR / f"opencode_{name}.log"
    return OpenCodeConfig(
        host=resolved_host,
        port=resolved_port,
        model=resolved_model,
        variant=resolved_variant,
        pid_file=resolved_pid,
        log_file=resolved_log,
        timeout_seconds=resolved_timeout,
    )


def find_opencode_exe() -> str:
    env_value = os.getenv("OPENCODE_EXE", "").strip()
    if env_value and Path(env_value).exists():
        return env_value

    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidate = (
            Path(local_appdata)
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "SST.opencode_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "opencode.exe"
        )
        if candidate.exists():
            return str(candidate)

    resolved = shutil.which("opencode")
    if resolved:
        return resolved

    raise FileNotFoundError("opencode executable not found")


def _server_reachable(config: OpenCodeConfig) -> bool:
    request = urllib.request.Request(config.attach_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code in {200, 401, 404}
    except Exception:
        return False


def _read_pid(config: OpenCodeConfig) -> int | None:
    if not config.pid_file or not config.pid_file.exists():
        return None
    try:
        return int(config.pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_pid(config: OpenCodeConfig, pid: int) -> None:
    if not config.pid_file:
        return
    config.pid_file.parent.mkdir(parents=True, exist_ok=True)
    config.pid_file.write_text(str(pid), encoding="utf-8")


def _clear_pid(config: OpenCodeConfig) -> None:
    if not config.pid_file:
        return
    try:
        config.pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def status(
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    config = _with_defaults(host, port, model, variant, pid_file, log_file, timeout_seconds)
    return {
        "mode": "opencode",
        "worker": _config_name(config),
        "running": _server_reachable(config),
        "attach_url": config.attach_url,
        "pid": _read_pid(config),
        "model": config.model,
        "variant": config.variant,
        "timeout_seconds": config.timeout_seconds,
    }


def start(
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    config = _with_defaults(host, port, model, variant, pid_file, log_file, timeout_seconds)
    if _server_reachable(config):
        return {
            "mode": "opencode",
            "started": True,
            "existing": True,
            "attach_url": config.attach_url,
            "pid": _read_pid(config),
            "worker": _config_name(config),
        }

    executable = find_opencode_exe()
    if config.log_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config.log_file, "a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [executable, "serve", "--port", str(config.port), "--hostname", config.host],
            stdout=handle,
            stderr=handle,
            creationflags=_creationflags(),
        )
    _write_pid(config, process.pid)

    for _ in range(40):
        if _server_reachable(config):
            return {
                "mode": "opencode",
                "started": True,
                "attach_url": config.attach_url,
                "pid": process.pid,
                "worker": _config_name(config),
            }
        time.sleep(0.5)

    return {
        "mode": "opencode",
        "started": False,
        "attach_url": config.attach_url,
        "pid": process.pid,
        "worker": _config_name(config),
        "error": "OpenCode server did not become ready in time",
    }


def _extract_final_text(output: str) -> str:
    final_text = ""
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("type") == "text":
            part = parsed.get("part", {})
            text = part.get("text")
            if isinstance(text, str):
                final_text = text.strip()
    return final_text


def send(
    prompt: str,
    context: str = "",
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    config = _with_defaults(host, port, model, variant, pid_file, log_file, timeout_seconds)
    started = start(
        host=config.host,
        port=config.port,
        model=config.model,
        variant=config.variant,
        pid_file=config.pid_file,
        log_file=config.log_file,
        timeout_seconds=config.timeout_seconds,
    )
    if not started.get("started"):
        return {
            "mode": "opencode",
            "worker": _config_name(config),
            "ok": False,
            "error": started.get("error", "Failed to start OpenCode server"),
        }

    executable = find_opencode_exe()
    full_prompt = prompt.strip()
    if context.strip():
        full_prompt += "\n\nAdditional context:\n" + context.strip()

    command = [
        executable,
        "run",
        "--attach",
        config.attach_url,
        "--format",
        "json",
        "--model",
        config.model,
        "--variant",
        config.variant,
        "--dangerously-skip-permissions",
        full_prompt,
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        # 타임아웃이더라도 stdout에서 텍스트 추출 시도
        final_text = _extract_final_text(stdout)
        # 타임아웃 시 stdout 원문도 fallback으로 보존
        if not final_text and stdout:
            # JSON 파싱 실패한 경우 raw stdout 사용
            final_text = stdout[:2000]
        return {
            "mode": "opencode",
            "worker": _config_name(config),
            "command": command,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "result_text": final_text,
            # 타임아웃은 응답 수신 실패이지 작업 실패가 아님
            # (UnrealMCP 등 부수효과는 이미 실행됐을 수 있음)
            "ok": bool(final_text),
            "timed_out": True,
            "error": f"OpenCode task timed out after {config.timeout_seconds}s",
        }

    final_text = _extract_final_text(completed.stdout)
    # JSON 파싱 실패 시 stdout 원문 fallback
    if not final_text and completed.stdout.strip():
        final_text = completed.stdout.strip()[:2000]
    return {
        "mode": "opencode",
        "worker": _config_name(config),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "result_text": final_text,
        "ok": completed.returncode == 0,
        "attach_url": config.attach_url,
        "model": config.model,
        "variant": config.variant,
    }


def stop(
    host: str | None = None,
    port: int | None = None,
    model: str | None = None,
    variant: str | None = None,
    pid_file: str | Path | None = None,
    log_file: str | Path | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    config = _with_defaults(host, port, model, variant, pid_file, log_file, timeout_seconds)
    pid = _read_pid(config)
    if not pid:
        return {
            "mode": "opencode",
            "worker": _config_name(config),
            "stopped": False,
            "message": "No managed OpenCode server PID found",
        }

    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode == 0:
        _clear_pid(config)

    return {
        "mode": "opencode",
        "worker": _config_name(config),
        "stopped": completed.returncode == 0,
        "pid": pid,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }
