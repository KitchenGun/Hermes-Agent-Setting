import asyncio
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

import discord

from env_loader import load_env_file


BRIDGE_URL = "http://127.0.0.1:8765/api/discord/execute"
BRIDGE_TIMEOUT_SECONDS = 180
LONG_RUNNING_BRIDGE_TIMEOUT_SECONDS = 1800
PREPARING_DELAY_SECONDS = 10
MAX_CONTEXT_TURNS = 6
CONVERSATION_HISTORY: dict[tuple[int, int], list[dict[str, str]]] = {}
BRIDGE_STATUS_URL = "http://127.0.0.1:8765/api/status"
BRIDGE_STARTER = os.path.join(os.path.dirname(__file__), "start_hermes_bridge_http.ps1")
BOT_SINGLETON_HOST = "127.0.0.1"
BOT_SINGLETON_PORT = 8766
PROCESSING_MESSAGE_IDS: set[int] = set()
COMPLETED_MESSAGE_IDS: dict[int, float] = {}
MESSAGE_DEDUPE_WINDOW_SECONDS = 300
LONG_RUNNING_PATTERNS = (
    "공고 리스트 업데이트",
    "공고리스트 업데이트",
    "공고 리스트 갱신",
    "공고리스트 갱신",
    "로우데이터 업데이트",
    "채용공고 업데이트",
    "채용 공고 업데이트",
    "공고 업데이트",
)


def normalize_message_content(message: discord.Message, bot_user_id: int) -> str:
    raw = message.content.strip()
    mention_prefixes = (f"<@{bot_user_id}>", f"<@!{bot_user_id}>")

    for prefix in mention_prefixes:
        if raw == prefix:
            return "!agent"
        if raw.startswith(prefix + " "):
            return "!agent " + raw[len(prefix) :].strip()

    role_prefixes = [f"<@&{role.id}>" for role in message.role_mentions]
    for prefix in role_prefixes:
        if raw == prefix:
            return "!agent"
        if raw.startswith(prefix + " "):
            return "!agent " + raw[len(prefix) :].strip()

    return raw


def build_payload(message: discord.Message, bot_user_id: int) -> dict[str, str]:
    channel_name = getattr(message.channel, "name", "dm") or "dm"
    return {
        "user": message.author.name,
        "channel": channel_name,
        "message": normalize_message_content(message, bot_user_id),
        "context": "",
    }


def conversation_key(message: discord.Message) -> tuple[int, int]:
    return (message.channel.id, message.author.id)


def build_conversation_context(message: discord.Message) -> str:
    history = CONVERSATION_HISTORY.get(conversation_key(message), [])
    if not history:
        return ""

    lines: list[str] = []
    for turn in history[-MAX_CONTEXT_TURNS:]:
        user_text = turn.get("user", "").strip()
        bot_text = turn.get("bot", "").strip()
        hidden_context = turn.get("context", "").strip()
        if user_text:
            lines.append(f"User: {user_text}")
        if bot_text:
            lines.append(f"Agent: {bot_text}")
        if hidden_context:
            lines.append(f"System memory: {hidden_context}")
    return "\n".join(lines)


def remember_turn(message: discord.Message, user_text: str, bot_text: str, hidden_context: str = "") -> None:
    key = conversation_key(message)
    history = CONVERSATION_HISTORY.setdefault(key, [])
    history.append({"user": user_text.strip(), "bot": bot_text.strip(), "context": hidden_context.strip()})
    del history[:-MAX_CONTEXT_TURNS]


def _bridge_status_url() -> str:
    return os.getenv("HERMES_BRIDGE_STATUS_URL", BRIDGE_STATUS_URL).strip() or BRIDGE_STATUS_URL


def _is_long_running_request(message: str) -> bool:
    normalized = " ".join(str(message or "").strip().lower().split())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in LONG_RUNNING_PATTERNS)


def _bridge_is_ready() -> bool:
    try:
        with urllib.request.urlopen(_bridge_status_url(), timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def ensure_bridge_ready() -> bool:
    if _bridge_is_ready():
        return True

    if not os.path.exists(BRIDGE_STARTER):
        return False

    try:
        subprocess.Popen(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                BRIDGE_STARTER,
            ],
            cwd=os.path.dirname(__file__),
        )
    except Exception:
        return False

    for _ in range(15):
        time.sleep(1)
        if _bridge_is_ready():
            return True

    return False


def prune_completed_message_ids() -> None:
    cutoff = time.time() - MESSAGE_DEDUPE_WINDOW_SECONDS
    expired_ids = [
        message_id
        for message_id, completed_at in COMPLETED_MESSAGE_IDS.items()
        if completed_at < cutoff
    ]
    for message_id in expired_ids:
        COMPLETED_MESSAGE_IDS.pop(message_id, None)


def acquire_singleton_lock() -> socket.socket:
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        lock_socket.bind((BOT_SINGLETON_HOST, BOT_SINGLETON_PORT))
        lock_socket.listen(1)
    except OSError as exc:
        lock_socket.close()
        raise SystemExit(
            f"Another Hermes Discord bot instance is already running ({BOT_SINGLETON_HOST}:{BOT_SINGLETON_PORT}): {exc}"
        )
    return lock_socket


def post_to_bridge(payload: dict[str, str]) -> dict[str, Any]:
    bridge_url = os.getenv("HERMES_BRIDGE_URL", BRIDGE_URL).strip() or BRIDGE_URL
    timeout_seconds = int(os.getenv("HERMES_BRIDGE_TIMEOUT", str(BRIDGE_TIMEOUT_SECONDS)).strip() or str(BRIDGE_TIMEOUT_SECONDS))
    if _is_long_running_request(payload.get("message", "")):
        timeout_seconds = int(
            os.getenv("HERMES_LONG_RUNNING_BRIDGE_TIMEOUT", str(LONG_RUNNING_BRIDGE_TIMEOUT_SECONDS)).strip()
            or str(LONG_RUNNING_BRIDGE_TIMEOUT_SECONDS)
        )
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_exception: Exception | None = None

    for attempt in range(2):
        request = urllib.request.Request(
            bridge_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            return {
                "action": "reply",
                "task": "",
                "response": detail or f"Hermes bridge returned HTTP {exc.code}",
                "visibility": "ephemeral",
            }
        except Exception as exc:  # noqa: BLE001
            last_exception = exc
            if attempt == 0 and ensure_bridge_ready():
                continue
            return {
                "action": "reply",
                "task": "",
                "response": f"Hermes bridge request failed: {last_exception}",
                "visibility": "ephemeral",
            }

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "action": "reply",
            "task": "",
            "response": "Hermes bridge returned invalid JSON",
            "visibility": "ephemeral",
        }

    return parsed if isinstance(parsed, dict) else {
        "action": "reply",
        "task": "",
        "response": "Hermes bridge returned an unexpected payload",
        "visibility": "ephemeral",
    }


async def deliver_response(message: discord.Message, result: dict[str, Any]) -> None:
    action = str(result.get("action", "reply") or "reply").strip().lower()
    if action == "ignore":
        return

    text = str(result.get("response", "") or "").strip()
    if not text:
        return

    await message.reply(text, mention_author=False)


class HermesDiscordBot(discord.Client):
    async def on_ready(self) -> None:
        print(f"Hermes Discord bot logged in as {self.user}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if self.user is None:
            return

        prune_completed_message_ids()
        if message.id in PROCESSING_MESSAGE_IDS or message.id in COMPLETED_MESSAGE_IDS:
            return

        PROCESSING_MESSAGE_IDS.add(message.id)

        try:
            payload = build_payload(message, self.user.id)
            payload["context"] = build_conversation_context(message)
            normalized_input = payload["message"]
            result_task = asyncio.create_task(asyncio.to_thread(post_to_bridge, payload))

            try:
                result = await asyncio.wait_for(asyncio.shield(result_task), timeout=PREPARING_DELAY_SECONDS)
            except asyncio.TimeoutError:
                await message.reply("답변 준비중입니다", mention_author=False)
                result = await result_task

            await deliver_response(message, result)
            if str(result.get("action", "")).strip().lower() != "ignore":
                remember_turn(
                    message,
                    normalized_input,
                    str(result.get("response", "") or ""),
                    str(result.get("context_update", "") or ""),
                )
        finally:
            PROCESSING_MESSAGE_IDS.discard(message.id)
            COMPLETED_MESSAGE_IDS[message.id] = time.time()


def main() -> int:
    load_env_file()
    singleton_lock = acquire_singleton_lock()

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        singleton_lock.close()
        raise SystemExit("DISCORD_BOT_TOKEN is not set")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.guilds = True

    client = HermesDiscordBot(intents=intents)
    try:
        client.run(token)
    finally:
        singleton_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
