import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import discord


BRIDGE_URL = "http://127.0.0.1:8765/api/discord/execute"
ENV_FILE = Path(__file__).with_name(".env")
BRIDGE_TIMEOUT_SECONDS = 180
PREPARING_DELAY_SECONDS = 10
MAX_CONTEXT_TURNS = 6
CONVERSATION_HISTORY: dict[tuple[int, int], list[dict[str, str]]] = {}


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_message_content(message: discord.Message, bot_user_id: int) -> str:
    raw = message.content.strip()
    mention_prefixes = (f"<@{bot_user_id}>", f"<@!{bot_user_id}>")

    for prefix in mention_prefixes:
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
        if user_text:
            lines.append(f"User: {user_text}")
        if bot_text:
            lines.append(f"Agent: {bot_text}")
    return "\n".join(lines)


def remember_turn(message: discord.Message, user_text: str, bot_text: str) -> None:
    key = conversation_key(message)
    history = CONVERSATION_HISTORY.setdefault(key, [])
    history.append({"user": user_text.strip(), "bot": bot_text.strip()})
    del history[:-MAX_CONTEXT_TURNS]


def post_to_bridge(payload: dict[str, str]) -> dict[str, Any]:
    bridge_url = os.getenv("HERMES_BRIDGE_URL", BRIDGE_URL).strip() or BRIDGE_URL
    timeout_seconds = int(os.getenv("HERMES_BRIDGE_TIMEOUT", str(BRIDGE_TIMEOUT_SECONDS)).strip() or str(BRIDGE_TIMEOUT_SECONDS))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        bridge_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "action": "reply",
            "task": "",
            "response": detail or f"Hermes bridge returned HTTP {exc.code}",
            "visibility": "ephemeral",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "action": "reply",
            "task": "",
            "response": f"Hermes bridge request failed: {exc}",
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
            remember_turn(message, normalized_input, str(result.get("response", "") or ""))


def main() -> int:
    load_env_file()

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.guilds = True

    client = HermesDiscordBot(intents=intents)
    client.run(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
