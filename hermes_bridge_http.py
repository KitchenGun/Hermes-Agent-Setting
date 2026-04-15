import json
import mimetypes
import os
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from discord_task_flow import execute_discord_task
from env_loader import load_env_file
from opencode_backend import send as opencode_send
from opencode_backend import start as opencode_start
from opencode_backend import status as opencode_status
from opencode_backend import stop as opencode_stop
from orchestrator import get_default_orchestrator


HOST = "127.0.0.1"
PORT = 8765
MCP_PATH = "/mcp"
LOG_PATH = r"C:\Users\kang9\.config\opencode\hermes_bridge_http.log"
SERVER_NAME = "hermes-opencode-http-bridge"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-03-26"
GUI_DIR = Path(__file__).parent / "gui"


def log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


EVENTS: list[dict[str, Any]] = []


def add_event(kind: str, payload: dict[str, Any]) -> None:
    EVENTS.append({"kind": kind, "payload": payload})
    del EVENTS[:-100]


class HermesAdapter:
    def __init__(self) -> None:
        self.mode = os.getenv("HERMES_MODE", "opencode").strip().lower()
        self.mock_running = False
        self.mock_last_prompt = ""

    def status(self) -> dict[str, Any]:
        if self.mode == "mock":
            result = {
                "mode": self.mode,
                "running": self.mock_running,
                "last_prompt": self.mock_last_prompt,
            }
            add_event("status", result)
            return result

        if self.mode == "command":
            command = os.getenv("HERMES_STATUS_COMMAND", "").strip()
            if not command:
                result = {"mode": self.mode, "running": False, "message": "HERMES_STATUS_COMMAND is not set"}
                add_event("status", result)
                return result
            result = self._run_command(command)
            add_event("status", result)
            return result

        if self.mode == "opencode":
            result = opencode_status()
            add_event("status", result)
            return result

        if self.mode == "http":
            result = self._http_request("GET", "/status")
            add_event("status", result)
            return result

        result = {"error": f"Unsupported HERMES_MODE: {self.mode}"}
        add_event("status", result)
        return result

    def start(self) -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_running = True
            result = {"mode": self.mode, "started": True}
            add_event("start", result)
            return result

        if self.mode == "command":
            command = os.getenv("HERMES_START_COMMAND", "").strip()
            if not command:
                result = {"mode": self.mode, "started": False, "message": "HERMES_START_COMMAND is not set"}
                add_event("start", result)
                return result
            result = self._run_command(command)
            add_event("start", result)
            return result

        if self.mode == "opencode":
            result = opencode_start()
            add_event("start", result)
            return result

        if self.mode == "http":
            result = self._http_request("POST", "/start", {})
            add_event("start", result)
            return result

        result = {"error": f"Unsupported HERMES_MODE: {self.mode}"}
        add_event("start", result)
        return result

    def send(self, prompt: str, context: str = "") -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_last_prompt = prompt
            result = {"mode": self.mode, "accepted": True, "echo": prompt, "context": context}
            add_event("send", result)
            return result

        if self.mode == "command":
            command = os.getenv("HERMES_SEND_COMMAND", "").strip()
            if not command:
                result = {"mode": self.mode, "accepted": False, "message": "HERMES_SEND_COMMAND is not set"}
                add_event("send", result)
                return result
            full_command = command + " " + shlex.quote(prompt)
            if context:
                full_command += " " + shlex.quote(context)
            result = self._run_command(full_command)
            add_event("send", result)
            return result

        if self.mode == "opencode":
            result = opencode_send(prompt, context)
            add_event("send", result)
            return result

        if self.mode == "http":
            result = self._http_request("POST", "/send", {"prompt": prompt, "context": context})
            add_event("send", result)
            return result

        result = {"error": f"Unsupported HERMES_MODE: {self.mode}"}
        add_event("send", result)
        return result

    def stop(self) -> dict[str, Any]:
        if self.mode == "mock":
            self.mock_running = False
            result = {"mode": self.mode, "stopped": True}
            add_event("stop", result)
            return result

        if self.mode == "command":
            command = os.getenv("HERMES_STOP_COMMAND", "").strip()
            if not command:
                result = {"mode": self.mode, "stopped": False, "message": "HERMES_STOP_COMMAND is not set"}
                add_event("stop", result)
                return result
            result = self._run_command(command)
            add_event("stop", result)
            return result

        if self.mode == "opencode":
            result = opencode_stop()
            add_event("stop", result)
            return result

        if self.mode == "http":
            result = self._http_request("POST", "/stop", {})
            add_event("stop", result)
            return result

        result = {"error": f"Unsupported HERMES_MODE: {self.mode}"}
        add_event("stop", result)
        return result

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
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "hermes_start",
        "description": "Start the Hermes agent.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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


def tool_result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params", {})
    log(f"rpc method={method}")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})

        if name == "hermes_status":
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(ADAPTER.status())}

        if name == "hermes_start":
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(ADAPTER.start())}

        if name == "hermes_send":
            prompt = arguments.get("prompt", "")
            context = arguments.get("context", "")
            if not prompt:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": "prompt is required"}, is_error=True),
                }
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(ADAPTER.send(prompt, context))}

        if name == "hermes_stop":
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(ADAPTER.stop())}

        if name == "hermes_execute_discord_task":
            user = arguments.get("user", "")
            channel = arguments.get("channel", "")
            prompt = arguments.get("message", "")
            context = arguments.get("context", "")
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": tool_result(execute_discord_task(ADAPTER, user, channel, prompt, context)),
            }

        if name == "hermes_orchestrate":
            task = str(arguments.get("task", "")).strip()
            if not task:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": "task is required"}, is_error=True),
                }
            result = get_orchestrator().orchestrate(
                task,
                str(arguments.get("user", "")).strip(),
                str(arguments.get("context", "")).strip(),
            )
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(result)}

        if name == "hermes_register_agent":
            agent_json = str(arguments.get("agent_json", "")).strip()
            if not agent_json:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": "agent_json is required"}, is_error=True),
                }
            try:
                result = get_orchestrator().register_agent_json(agent_json)
            except Exception as exc:  # noqa: BLE001
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": str(exc)}, is_error=True),
                }
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(result)}

        if name == "hermes_list_agents":
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result({"agents": get_orchestrator().list_agents()})}

        if name == "hermes_agent_status":
            agent_id = str(arguments.get("agent_id", "")).strip()
            agent = get_orchestrator().get_agent(agent_id)
            if agent is None:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": f"Unknown agent: {agent_id}"}, is_error=True),
                }
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(agent)}

        if name == "hermes_list_suggestions":
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": tool_result({"suggestions": get_orchestrator().list_suggestions()}),
            }

        if name == "hermes_approve_suggestion":
            suggestion_id = str(arguments.get("suggestion_id", "")).strip()
            try:
                result = get_orchestrator().approve_suggestion(suggestion_id)
            except KeyError:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": f"Unknown suggestion: {suggestion_id}"}, is_error=True),
                }
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(result)}

        if name == "hermes_reject_suggestion":
            suggestion_id = str(arguments.get("suggestion_id", "")).strip()
            try:
                result = get_orchestrator().reject_suggestion(suggestion_id)
            except KeyError:
                return {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": tool_result({"error": f"Unknown suggestion: {suggestion_id}"}, is_error=True),
                }
            return {"jsonrpc": "2.0", "id": message_id, "result": tool_result(result)}

        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": tool_result({"error": f"Unknown tool: {name}"}, is_error=True),
        }

    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesOpenCodeBridge/0.1.0"

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        return json.loads(raw)

    def do_GET(self) -> None:
        if self.path in ("/", "/gui"):
            self._serve_file("index.html")
            return

        if self.path.startswith("/gui/"):
            self._serve_file(self.path.removeprefix("/gui/"))
            return

        if self.path == "/status":
            self._send_json(200, {"ok": True, "bridge": SERVER_NAME, "mode": ADAPTER.mode})
            return

        if self.path == "/api/status":
            self._send_json(
                200,
                {
                    "bridge": SERVER_NAME,
                    "mode": ADAPTER.mode,
                    "status": ADAPTER.status(),
                    "events": EVENTS[-20:],
                    "orchestrator_workers": get_orchestrator().agent_pool.list_workers(),
                },
            )
            return

        if self.path == "/api/registry/agents":
            self._send_json(200, {"agents": get_orchestrator().list_agents()})
            return

        if self.path.startswith("/api/registry/agents/"):
            agent_id = self.path.removeprefix("/api/registry/agents/").strip("/")
            agent = get_orchestrator().get_agent(agent_id)
            if agent is None:
                self._send_json(404, {"error": f"Unknown agent: {agent_id}"})
                return
            self._send_json(200, agent)
            return

        if self.path == "/api/registry/suggestions":
            self._send_json(200, {"suggestions": get_orchestrator().list_suggestions()})
            return

        if self.path.startswith("/api/registry/search"):
            _, _, query = self.path.partition("?q=")
            q = urllib.parse.unquote_plus(query) if query else ""
            self._send_json(200, {"agents": get_orchestrator().search_agents(q), "query": q})
            return

        if self.path == MCP_PATH:
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "GET not supported for this bridge"}).encode("utf-8"))
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/api/start":
            self._send_json(200, ADAPTER.start())
            return

        if self.path == "/api/stop":
            self._send_json(200, ADAPTER.stop())
            return

        if self.path == "/api/send":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            prompt = str(payload.get("prompt", "")).strip()
            context = str(payload.get("context", "")).strip()
            if not prompt:
                self._send_json(400, {"error": "prompt is required"})
                return
            self._send_json(200, ADAPTER.send(prompt, context))
            return

        if self.path == "/api/discord/execute":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            user = str(payload.get("user", "")).strip()
            channel = str(payload.get("channel", "")).strip()
            prompt = str(payload.get("message", "")).strip()
            context = str(payload.get("context", "")).strip()
            self._send_json(200, execute_discord_task(ADAPTER, user, channel, prompt, context))
            return

        if self.path == "/api/orchestrate":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            task = str(payload.get("task", "")).strip()
            if not task:
                self._send_json(400, {"error": "task is required"})
                return
            result = get_orchestrator().orchestrate(
                task,
                str(payload.get("user", "")).strip(),
                str(payload.get("context", "")).strip(),
            )
            self._send_json(200, result)
            return

        if self.path == "/api/registry/agents":
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            agent_json = payload.get("agent_json")
            if isinstance(agent_json, dict):
                agent_json = json.dumps(agent_json, ensure_ascii=False)
            try:
                result = get_orchestrator().register_agent_json(str(agent_json or ""))
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, result)
            return

        if self.path == "/api/registry/reload":
            get_orchestrator().reload()
            self._send_json(200, {"ok": True})
            return

        if self.path.startswith("/api/registry/suggestions/") and self.path.endswith("/approve"):
            suggestion_id = self.path.removeprefix("/api/registry/suggestions/").removesuffix("/approve").strip("/")
            try:
                result = get_orchestrator().approve_suggestion(suggestion_id)
            except KeyError:
                self._send_json(404, {"error": f"Unknown suggestion: {suggestion_id}"})
                return
            self._send_json(200, result)
            return

        if self.path.startswith("/api/registry/suggestions/") and self.path.endswith("/reject"):
            suggestion_id = self.path.removeprefix("/api/registry/suggestions/").removesuffix("/reject").strip("/")
            try:
                result = get_orchestrator().reject_suggestion(suggestion_id)
            except KeyError:
                self._send_json(404, {"error": f"Unknown suggestion: {suggestion_id}"})
                return
            self._send_json(200, result)
            return

        if self.path != MCP_PATH:
            self._send_json(404, {"error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        log(f"http body={raw[:1000]}")

        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return

        response = handle_rpc(message)
        if response is None:
            self.send_response(202)
            self.end_headers()
            return

        self._send_json(200, response)

    def do_PUT(self) -> None:
        if not self.path.startswith("/api/registry/agents/"):
            self._send_json(404, {"error": "Not found"})
            return
        agent_id = self.path.removeprefix("/api/registry/agents/").strip("/")
        try:
            payload = self._read_json_body()
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return
        updated = get_orchestrator().update_agent(agent_id, payload)
        if updated is None:
            self._send_json(404, {"error": f"Unknown agent: {agent_id}"})
            return
        self._send_json(200, updated)

    def do_DELETE(self) -> None:
        if not self.path.startswith("/api/registry/agents/"):
            self._send_json(404, {"error": "Not found"})
            return
        agent_id = self.path.removeprefix("/api/registry/agents/").strip("/")
        deleted = get_orchestrator().delete_agent(agent_id)
        if not deleted:
            self._send_json(404, {"error": f"Unknown agent: {agent_id}"})
            return
        self._send_json(200, {"ok": True, "agent_id": agent_id})

    def log_message(self, format: str, *args: Any) -> None:
        log("http " + format % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, relative_path: str) -> None:
        target = (GUI_DIR / relative_path).resolve()
        if not str(target).startswith(str(GUI_DIR.resolve())) or not target.exists() or not target.is_file():
            self._send_json(404, {"error": "Not found"})
            return

        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    load_env_file()
    log(f"starting http bridge on {HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
