"""Discord 유틸리티 스킬.

Hermes skill 인터페이스를 구현한다:
    run(prompt, context, skill, **kwargs) -> dict

Discord 관련 텍스트 생성, 메시지 포맷팅, 응답 작성 등을 LLM으로 처리한다.
Discord API 직접 호출은 이 스킬의 범위가 아니다 (discord_hermes_bot.py 담당).
"""
from typing import Any


def run(
    prompt: str,
    context: str = "",
    skill: str = "discord",
    **_: Any,
) -> dict[str, Any]:
    """Discord 관련 텍스트/응답 작업을 처리한다."""
    from codex_backend import send as codex_send  # type: ignore[import]

    # Discord 컨텍스트에 맞는 시스템 프롬프트 추가
    discord_prompt = (
        "You are a Discord assistant. "
        "Keep responses concise and formatted for Discord (markdown supported, max 2000 chars).\n\n"
        f"{prompt}"
    )
    result = codex_send(discord_prompt, context)
    result.setdefault("mode", f"hermes-skill:{skill}")
    return result
