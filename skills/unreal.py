"""UnrealMCP 스킬.

Hermes skill 인터페이스를 구현한다:
    run(prompt, context, skill, **kwargs) -> dict

처리 흐름:
1. LLM(codex/gpt-5.4)에게 intent JSON만 출력하도록 요청
2. unreal_adapter.execute_unreal_intent() 로 UE5 TCP 직접 전송
3. MCP 타임아웃(-32001) 없이 UE5 명령 실행

⚠️ orchestrator._execute_unreal_via_adapter() 는 이 스킬을 사용하지 않는다.
    orchestrator는 자체 경로(agent_pool → LLM intent → unreal_adapter)를 사용한다.
    이 스킬은 hermes_backend.send(skills=["unreal-mcp"]) 직접 호출 경로 전용이다.
"""
from typing import Any


def run(
    prompt: str,
    context: str = "",
    skill: str = "unreal-mcp",
    **_: Any,
) -> dict[str, Any]:
    """Unreal Engine 요청을 처리한다.

    GPT에게 intent JSON만 생성하도록 요청한 뒤 unreal_adapter로 UE5 TCP 직접 호출.
    """
    from codex_backend import send as codex_send  # type: ignore[import]
    from unreal_adapter import execute_unreal_intent  # type: ignore[import]

    # 1. GPT intent JSON 생성 (MCP tool 직접 호출 금지)
    intent_prompt = _build_intent_prompt(prompt, context)
    llm_result = codex_send(
        intent_prompt,
        "",
        model="gpt-5.4",
        variant="medium",
        timeout_seconds=300,
    )
    intent_text = str(llm_result.get("result_text") or "").strip()

    if not intent_text:
        return {
            "ok": False,
            "result_text": "GPT에서 intent를 받지 못했습니다.",
            "stderr": "empty intent from LLM",
            "stdout": "",
            "mode": f"hermes-skill:{skill}",
        }

    # 2. UE5 TCP 직접 실행
    try:
        result = execute_unreal_intent(intent_text)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "result_text": f"UE5 어댑터 오류: {exc}",
            "stderr": str(exc),
            "stdout": "",
            "mode": f"hermes-skill:{skill}",
        }

    result.setdefault("mode", f"hermes-skill:{skill}")
    return result


def _build_intent_prompt(task: str, context: str) -> str:
    lines = [
        "You are preparing a Unreal MCP intent.",
        "Output ONLY one JSON object. Do not call tools. Do not explain.",
        "The system will execute after receiving your JSON.",
        "",
        'Actor format:  {"action":"create|delete|transform|query|find|get_props|set_prop|duplicate", ...}',
        'Tool format:   {"tool":"search_assets|get_asset_details|get_blueprint_graph|tail_editor_log", "params":{...}}',
        "",
        'Examples:',
        '  {"action":"create","class":"PointLight","location":[1000,1000,300]}',
        '  {"action":"query"}',
        '  {"tool":"tail_editor_log","params":{"tail_lines":80,"contains":"Error"}}',
        "",
    ]
    if context.strip():
        lines.append(f"Context: {context.strip()[:500]}")
    lines.append(f"Task: {task}")
    lines.append("Output ONLY the JSON object, nothing else.")
    return "\n".join(lines)
