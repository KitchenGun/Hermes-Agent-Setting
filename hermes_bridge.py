import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

from discord_task_flow import execute_discord_task
from env_loader import load_env_file
from opencode_backend import send as opencode_send
from opencode_backend import start as opencode_start
from opencode_backend import status as opencode_status
from opencode_backend import stop as opencode_stop
from orchestrator import get_default_orchestrator


SERVER_NAME = "hermes-opencode-bridge"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-03-26"
LOG_PATH = r"C:\Users\kang9\.config\opencode\hermes_bridge.log"


def log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


class HermesAdapter:
    def __init__(self) -> None:
        self.mode = os.getenv("HERMES_MODE", "opencode").strip().lower()
        self.mock_running = False
        self.mock_last_prompt = ""

    def status(self) -> dict[str, Any]:
        if self.mode == "mock":
            return {
                "mode": self.mode,
                "running": self.mock_running,
                "last_prompt": self.mock_last_prompt,
            }

        if self.mode == "command":
            command = os.getenv("HERMES_STATUS_COMMAND", "").strip()
            if not command:
                return {
                    "mode": self.mode,
                    "running": False,
                    "message": "HERMES_STATUS_COMMAND is not set",
                }
            return self._run_command(command)

        if self.mode == "opencode":
            return opencode_status()

        if self.mode == "http":
            return self._http_request("GET", "/status")

        return {"error": f"Unsupported HERMES_MODE: {self.mode}"}

    def start(self) -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_running = True
            return {"mode": self.mode, "started": True}

        if self.mode == "command":
            command = os.getenv("HERMES_START_COMMAND", "").strip()
            if not command:
                return {"mode": self.mode, "started": False, "message": "HERMES_START_COMMAND is not set"}
            return self._run_command(command)

        if self.mode == "opencode":
            return opencode_start()

        if self.mode == "http":
            return self._http_request("POST", "/start", {})

        return {"error": f"Unsupported HERMES_MODE: {self.mode}"}

    def send(self, prompt: str, context: str = "") -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_last_prompt = prompt
            return {
                "mode": self.mode,
                "accepted": True,
                "echo": prompt,
                "context": context,
            }

        if self.mode == "command":
            command = os.getenv("HERMES_SEND_COMMAND", "").strip()
            if not command:
                return {"mode": self.mode, "accepted": False, "message": "HERMES_SEND_COMMAND is not set"}

            full_command = command
            full_command += " " + shlex.quote(prompt)
            if context:
                full_command += " " + shlex.quote(context)
            return self._run_command(full_command)

        if self.mode == "opencode":
            return opencode_send(prompt, context)

        if self.mode == "http":
            return self._http_request("POST", "/send", {"prompt": prompt, "context": context})

        return {"error": f"Unsupported HERMES_MODE: {self.mode}"}

    def stop(self) -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_running = False
            return {"mode": self.mode, "stopped": True}

        if self.mode == "command":
            command = os.getenv("HERMES_STOP_COMMAND", "").strip()
            if not command:
                return {"mode": self.mode, "stopped": False, "message": "HERMES_STOP_COMMAND is not set"}
            return self._run_command(command)

        if self.mode == "opencode":
            return opencode_stop()

        if self.mode == "http":
            return self._http_request("POST", "/stop", {})

        return {"error": f"Unsupported HERMES_MODE: {self.mode}"}

    def _run_command(self, command: str) -> dict[str, Any]:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return {
            "mode": self.mode,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "ok": completed.returncode == 0,
        }

    def _http_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        base_url = os.getenv("HERMES_HTTP_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            return {"mode": self.mode, "ok": False, "message": "HERMES_HTTP_BASE_URL is not set"}

        headers = {"Content-Type": "application/json"}
        token = os.getenv("HERMES_HTTP_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url=f"{base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8").strip()
                if not body:
                    return {"mode": self.mode, "ok": True, "status": response.status}
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = {"raw": body}
                return {"mode": self.mode, "ok": True, "status": response.status, "response": parsed}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            return {"mode": self.mode, "ok": False, "status": exc.code, "error": body}
        except Exception as exc:  # noqa: BLE001
            return {"mode": self.mode, "ok": False, "error": str(exc)}


ADAPTER = HermesAdapter()


def get_orchestrator():
    return get_default_orchestrator()


TOOLS = [
    {
        "name": "hermes_status",
        "description": "Return the current Hermes agent status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_start",
        "description": "Start the Hermes agent.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_send",
        "description": "Send a prompt or instruction to the Hermes agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Instruction for Hermes."},
                "context": {"type": "string", "description": "Optional context."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_stop",
        "description": "Stop the Hermes agent.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_execute_discord_task",
        "description": "Execute a Discord message through the Hermes task runner.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Discord username."},
                "channel": {"type": "string", "description": "Discord channel name."},
                "message": {"type": "string", "description": "Discord user message."},
                "context": {"type": "string", "description": "Optional prior conversation context."},
            },
            "required": ["user", "channel", "message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_orchestrate",
        "description": "Execute a routed Hermes task through the multi-agent orchestrator.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task to execute."},
                "user": {"type": "string", "description": "Optional user identifier."},
                "context": {"type": "string", "description": "Optional prior context."},
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_register_agent",
        "description": "Register an agent definition in the Hermes registry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_json": {"type": "string", "description": "agent.json content as a JSON string."}
            },
            "required": ["agent_json"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_list_agents",
        "description": "List registered Hermes agents.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "hermes_agent_status",
        "description": "Get one Hermes agent definition.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Registered agent id."}
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_list_suggestions",
        "description": "List pending specialist implementation suggestions.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "hermes_approve_suggestion",
        "description": "Approve a pending specialist suggestion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "suggestion_id": {"type": "string", "description": "Pending suggestion id."}
            },
            "required": ["suggestion_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hermes_reject_suggestion",
        "description": "Reject a pending specialist suggestion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "suggestion_id": {"type": "string", "description": "Pending suggestion id."}
            },
            "required": ["suggestion_id"],
            "additionalProperties": False,
        },
    },
]


def encode_message(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_message() -> dict[str, Any] | None:
    content_length = None

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break

        header = line.decode("ascii", errors="replace").strip()
        if ":" not in header:
            continue

        name, value = header.split(":", 1)
        if name.lower() == "content-length":
            content_length = int(value.strip())

    if content_length is None:
        return None

    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    decoded = body.decode("utf-8")
    log(f"received: {decoded[:500]}")
    return json.loads(decoded)


def send_response(message_id: Any, result: dict[str, Any]) -> None:
    response = {"jsonrpc": "2.0", "id": message_id, "result": result}
    log(f"response id={message_id}")
    sys.stdout.buffer.write(encode_message(response))
    sys.stdout.buffer.flush()


def send_error(message_id: Any, code: int, message: str) -> None:
    response = {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }
    log(f"error id={message_id} code={code} message={message}")
    sys.stdout.buffer.write(encode_message(response))
    sys.stdout.buffer.flush()


def tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def handle_request(message: dict[str, Any]) -> None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params", {})
    log(f"method={method}")

    if method == "initialize":
        send_response(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        send_response(message_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})

        if name == "hermes_status":
            send_response(message_id, tool_result(ADAPTER.status()))
            return

        if name == "hermes_start":
            send_response(message_id, tool_result(ADAPTER.start()))
            return

        if name == "hermes_send":
            prompt = arguments.get("prompt", "")
            context = arguments.get("context", "")
            if not prompt:
                send_response(message_id, tool_result({"error": "prompt is required"}, is_error=True))
                return
            send_response(message_id, tool_result(ADAPTER.send(prompt, context)))
            return

        if name == "hermes_stop":
            send_response(message_id, tool_result(ADAPTER.stop()))
            return

        if name == "hermes_execute_discord_task":
            user = arguments.get("user", "")
            channel = arguments.get("channel", "")
            prompt = arguments.get("message", "")
            context = arguments.get("context", "")
            send_response(message_id, tool_result(execute_discord_task(ADAPTER, user, channel, prompt, context)))
            return

        if name == "hermes_orchestrate":
            task = str(arguments.get("task", "")).strip()
            if not task:
                send_response(message_id, tool_result({"error": "task is required"}, is_error=True))
                return
            send_response(
                message_id,
                tool_result(
                    get_orchestrator().orchestrate(
                        task,
                        str(arguments.get("user", "")).strip(),
                        str(arguments.get("context", "")).strip(),
                    )
                ),
            )
            return

        if name == "hermes_register_agent":
            agent_json = str(arguments.get("agent_json", "")).strip()
            if not agent_json:
                send_response(message_id, tool_result({"error": "agent_json is required"}, is_error=True))
                return
            try:
                result = get_orchestrator().register_agent_json(agent_json)
            except Exception as exc:  # noqa: BLE001
                send_response(message_id, tool_result({"error": str(exc)}, is_error=True))
                return
            send_response(message_id, tool_result(result))
            return

        if name == "hermes_list_agents":
            send_response(message_id, tool_result({"agents": get_orchestrator().list_agents()}))
            return

        if name == "hermes_agent_status":
            agent_id = str(arguments.get("agent_id", "")).strip()
            agent = get_orchestrator().get_agent(agent_id)
            if agent is None:
                send_response(message_id, tool_result({"error": f"Unknown agent: {agent_id}"}, is_error=True))
                return
            send_response(message_id, tool_result(agent))
            return

        if name == "hermes_list_suggestions":
            send_response(message_id, tool_result({"suggestions": get_orchestrator().list_suggestions()}))
            return

        if name == "hermes_approve_suggestion":
            suggestion_id = str(arguments.get("suggestion_id", "")).strip()
            try:
                result = get_orchestrator().approve_suggestion(suggestion_id)
            except KeyError:
                send_response(message_id, tool_result({"error": f"Unknown suggestion: {suggestion_id}"}, is_error=True))
                return
            send_response(message_id, tool_result(result))
            return

        if name == "hermes_reject_suggestion":
            suggestion_id = str(arguments.get("suggestion_id", "")).strip()
            try:
                result = get_orchestrator().reject_suggestion(suggestion_id)
            except KeyError:
                send_response(message_id, tool_result({"error": f"Unknown suggestion: {suggestion_id}"}, is_error=True))
                return
            send_response(message_id, tool_result(result))
            return

        send_response(message_id, tool_result({"error": f"Unknown tool: {name}"}, is_error=True))
        return

    send_error(message_id, -32601, f"Method not found: {method}")


def main() -> int:
    load_env_file()
    log("bridge started")
    while True:
        message = read_message()
        if message is None:
            log("stdin closed")
            return 0
        handle_request(message)


if __name__ == "__main__":
    raise SystemExit(main())
