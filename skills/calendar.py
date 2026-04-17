"""Google Calendar 스킬.

Hermes skill 인터페이스를 구현한다:
    run(prompt, context, skill, **kwargs) -> dict

처리 흐름:
1. LLM(codex)으로 캘린더 실행 plan JSON 생성
2. google_calendar_integration.execute_calendar_plan() 으로 실제 API 호출
3. 결과를 Hermes 표준 포맷으로 반환
"""
from typing import Any


def run(
    prompt: str,
    context: str = "",
    skill: str = "google-calendar",
    **_: Any,
) -> dict[str, Any]:
    """캘린더 관련 요청을 처리한다.

    LLM이 캘린더 작업 plan을 생성하고, Google Calendar API가 실제로 실행한다.
    """
    from calendar_manager_agent import (  # type: ignore[import]
        DEFAULT_TIMEZONE,
        build_calendar_manager_execution_prompt,
        now_iso,
    )
    from codex_backend import send as codex_send  # type: ignore[import]
    from google_calendar_integration import execute_calendar_plan  # type: ignore[import]

    # 1. 캘린더 전용 프롬프트로 LLM plan 생성
    cal_prompt = build_calendar_manager_execution_prompt(
        user_input=prompt,
        discord_user="hermes-skill",
        discord_channel="skill",
        user_id="hermes-skill",
        current_datetime=now_iso(DEFAULT_TIMEZONE),
        context=context,
        timezone_name=DEFAULT_TIMEZONE,
    )
    llm_result = codex_send(cal_prompt, context)
    plan_text = str(llm_result.get("result_text") or "").strip()

    if not plan_text:
        return {
            "ok": False,
            "result_text": "캘린더 plan을 생성하지 못했습니다.",
            "stderr": "empty plan from LLM",
            "stdout": "",
            "mode": f"hermes-skill:{skill}",
        }

    # 2. Google Calendar API 실행
    cal_result = execute_calendar_plan(plan_text)
    if cal_result is None:
        # plan은 생성됐지만 실행 대상 작업이 없는 경우 (조회 전용 응답 등)
        return {
            "ok": True,
            "result_text": plan_text,
            "stdout": plan_text,
            "stderr": "",
            "mode": f"hermes-skill:{skill}",
        }

    return {
        "ok": cal_result.get("status") == "success",
        "result_text": str(cal_result.get("user_message") or plan_text).strip(),
        "stdout": str(cal_result.get("user_message") or plan_text).strip(),
        "stderr": str(cal_result.get("error") or ""),
        "calendar_execution": cal_result,
        "mode": f"hermes-skill:{skill}",
    }
