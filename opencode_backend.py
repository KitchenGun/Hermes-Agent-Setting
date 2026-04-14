import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


HOST = os.getenv("HERMES_OPENCODE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("HERMES_OPENCODE_PORT", "4096").strip() or "4096")
ATTACH_URL = f"http://{HOST}:{PORT}"
PID_FILE = Path(os.getenv("HERMES_OPENCODE_PID_FILE", str(Path.home() / ".config" / "opencode" / "opencode_serve.pid")))
LOG_FILE = Path(os.getenv("HERMES_OPENCODE_LOG_FILE", str(Path.home() / ".config" / "opencode" / "opencode_serve.log")))
DEFAULT_MODEL = os.getenv("HERMES_OPENCODE_MODEL", "openai/gpt-5.4").strip() or "openai/gpt-5.4"
DEFAULT_VARIANT = os.getenv("HERMES_OPENCODE_VARIANT", "medium").strip() or "medium"


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_opencode_exe() -> str:
    env_value = os.getenv("OPENCODE_EXE", "").strip()
    if env_value and Path(env_value).exists():
        return env_value

    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidate = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages" / "SST.opencode_Microsoft.Winget.Source_8wekyb3d8bbwe" / "opencode.exe"
        if candidate.exists():
            return str(candidate)

    resolved = shutil.which("opencode")
    if resolved:
        return resolved

    raise FileNotFoundError("opencode executable not found")


def _server_reachable() -> bool:
    request = urllib.request.Request(ATTACH_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code in {200, 401, 404}
    except Exception:
        return False


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def status() -> dict[str, Any]:
    return {
        "mode": "opencode",
        "running": _server_reachable(),
        "attach_url": ATTACH_URL,
        "pid": _read_pid(),
        "model": DEFAULT_MODEL,
        "variant": DEFAULT_VARIANT,
    }


def start() -> dict[str, Any]:
    if _server_reachable():
        return {"mode": "opencode", "started": True, "existing": True, "attach_url": ATTACH_URL, "pid": _read_pid()}

    executable = find_opencode_exe()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [executable, "serve", "--port", str(PORT), "--hostname", HOST],
            stdout=handle,
            stderr=handle,
            creationflags=_creationflags(),
        )
    _write_pid(process.pid)

    for _ in range(40):
        if _server_reachable():
            return {"mode": "opencode", "started": True, "attach_url": ATTACH_URL, "pid": process.pid}
        time.sleep(0.5)

    return {"mode": "opencode", "started": False, "attach_url": ATTACH_URL, "pid": process.pid, "error": "OpenCode server did not become ready in time"}


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


def send(prompt: str, context: str = "") -> dict[str, Any]:
    started = start()
    if not started.get("started"):
        return {"mode": "opencode", "ok": False, "error": started.get("error", "Failed to start OpenCode server")}

    executable = find_opencode_exe()
    full_prompt = prompt.strip()
    if context.strip():
        full_prompt += "\n\nAdditional context:\n" + context.strip()

    command = [
        executable,
        "run",
        "--attach",
        ATTACH_URL,
        "--format",
        "json",
        "--model",
        DEFAULT_MODEL,
        "--variant",
        DEFAULT_VARIANT,
        "--dangerously-skip-permissions",
        full_prompt,
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    final_text = _extract_final_text(completed.stdout)
    return {
        "mode": "opencode",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "result_text": final_text,
        "ok": completed.returncode == 0,
    }


def stop() -> dict[str, Any]:
    pid = _read_pid()
    if not pid:
        return {"mode": "opencode", "stopped": False, "message": "No managed OpenCode server PID found"}

    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode == 0:
        _clear_pid()

    return {
        "mode": "opencode",
        "stopped": completed.returncode == 0,
        "pid": pid,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "ok": completed.returncode == 0,
    }
