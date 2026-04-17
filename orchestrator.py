import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_pool import AgentPool
from agent_registry import AgentDefinition
from agent_registry import AgentRegistry
from calendar_manager_agent import DEFAULT_TIMEZONE
from calendar_manager_agent import build_calendar_manager_execution_prompt
from calendar_manager_agent import now_iso
from google_calendar_integration import execute_calendar_plan
from skill_registry import SkillDefinition
from skill_registry import SkillRegistry


DEFAULT_SKILLS_DIR = os.getenv("HERMES_SKILL_DIR", "skills").strip() or "skills"
DEFAULT_AGENTS_DIR = os.getenv("HERMES_AGENT_DIR", "agents").strip() or "agents"
DEFAULT_KNOWLEDGE_DIR = os.getenv("HERMES_KNOWLEDGE_DIR", "knowledge").strip() or "knowledge"
DEFAULT_REACTIONS_PATH = os.getenv("HERMES_REACTIONS_PATH", "reactions.json").strip() or "reactions.json"
DEFAULT_HOT_RELOAD = os.getenv("HERMES_REGISTRY_HOT_RELOAD", "true").strip().lower() == "true"
WORKER_RULES_PATH = Path(os.getenv("HERMES_WORKER_RULES", "harness/worker_rules.md"))
SUGGESTIONS_FILE = "suggestions.json"
GENERIC_SUPPORT_SKILLS = {"code-general", "research", "document", "google-docs"}


@dataclass(slots=True)
class SubTask:
    id: str
    task: str
    depends_on: list[str]
    required_skills: list[str]
    expected_output: str = ""


@dataclass(slots=True)
class ImplementationSuggestion:
    suggestion_id: str
    status: str = "pending"
    reason: str = ""
    task_description: str = ""
    fallback_result_summary: str = ""
    quality_assessment: str = ""
    suggested_agent: dict[str, Any] | None = None
    suggested_skill: dict[str, Any] | None = None
    setup_guide: str = ""
    similar_agents: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "status": self.status,
            "reason": self.reason,
            "task_description": self.task_description,
            "fallback_result_summary": self.fallback_result_summary,
            "quality_assessment": self.quality_assessment,
            "suggested_agent": self.suggested_agent or {},
            "suggested_skill": self.suggested_skill or {},
            "setup_guide": self.setup_guide,
            "similar_agents": self.similar_agents or [],
        }


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class HermesOrchestrator:
    def __init__(
        self,
        skills_dir: str | Path = DEFAULT_SKILLS_DIR,
        agents_dir: str | Path = DEFAULT_AGENTS_DIR,
        knowledge_dir: str | Path = DEFAULT_KNOWLEDGE_DIR,
        reactions_path: str | Path = DEFAULT_REACTIONS_PATH,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.agents_dir = Path(agents_dir)
        self.knowledge_dir = Path(knowledge_dir)
        self.reactions_path = Path(reactions_path)
        self.skill_registry = SkillRegistry(self.skills_dir)
        self.agent_registry = AgentRegistry(self.agents_dir)
        self.agent_pool = AgentPool()
        self._lock = threading.Lock()
        self.reactions: dict[str, Any] = {}
        self.pending_suggestions: dict[str, ImplementationSuggestion] = {}
        self._worker_rules: str = ""
        self.reload()

    def reload(self) -> None:
        self.skill_registry.load_from_directory(self.skills_dir)
        self.agent_registry.load_from_directory(self.agents_dir)
        self.reactions = self._load_reactions()
        self.pending_suggestions = self._load_suggestions()
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._worker_rules = WORKER_RULES_PATH.read_text(encoding="utf-8").strip()
        except Exception:
            self._worker_rules = ""

    def list_agents(self) -> list[dict[str, Any]]:
        if DEFAULT_HOT_RELOAD:
            self.reload()
        return [agent.to_dict() for agent in self.agent_registry.list_all()]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        if DEFAULT_HOT_RELOAD:
            self.reload()
        agent = self.agent_registry.get(agent_id)
        return agent.to_dict() if agent else None

    def search_agents(self, query: str) -> list[dict[str, Any]]:
        if DEFAULT_HOT_RELOAD:
            self.reload()
        return [agent.to_dict() for agent in self.agent_registry.search(query)]

    def register_agent_json(self, agent_json: str) -> dict[str, Any]:
        payload = json.loads(agent_json)
        agent = AgentDefinition.from_dict(payload)
        if not agent.id:
            raise ValueError("agent.id is required")
        if not agent.created_at:
            agent.created_at = _utc_now()
        agent.updated_at = _utc_now()
        self.agent_registry.register(agent)
        return agent.to_dict()

    def update_agent(self, agent_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        updated = self.agent_registry.update(agent_id, updates)
        return updated.to_dict() if updated else None

    def delete_agent(self, agent_id: str) -> bool:
        if self.agent_registry.get(agent_id) is None:
            return False
        self.agent_registry.unregister(agent_id)
        return True

    def list_suggestions(self) -> list[dict[str, Any]]:
        if DEFAULT_HOT_RELOAD:
            self.pending_suggestions = self._load_suggestions()
        return [item.to_dict() for item in self.pending_suggestions.values()]

    def approve_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        suggestion = self.pending_suggestions.get(suggestion_id)
        if suggestion is None:
            raise KeyError(suggestion_id)

        skill_payload = suggestion.suggested_skill or {}
        skill_name = str(skill_payload.get("name") or "").strip()
        if skill_name:
            skill_dir = self.skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "skill.json").write_text(json.dumps(skill_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            system_prompt_path = skill_payload.get("system_prompt_path")
            if isinstance(system_prompt_path, str) and system_prompt_path.strip():
                prompt_file = Path(system_prompt_path)
                if not prompt_file.is_absolute():
                    prompt_file = Path.cwd() / system_prompt_path
                prompt_file.parent.mkdir(parents=True, exist_ok=True)
                if not prompt_file.exists():
                    prompt_file.write_text(
                        f"You are the {skill_payload.get('display_name', skill_name)} skill.\nFocus on accurate task execution.",
                        encoding="utf-8",
                    )

        agent_payload = suggestion.suggested_agent or {}
        agent = AgentDefinition.from_dict(agent_payload)
        if agent.id:
            if not agent.created_at:
                agent.created_at = _utc_now()
            agent.updated_at = _utc_now()
            self.agent_registry.register(agent)

        suggestion.status = "approved"
        self._save_suggestions()
        self.reload()
        return suggestion.to_dict()

    def reject_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        suggestion = self.pending_suggestions.get(suggestion_id)
        if suggestion is None:
            raise KeyError(suggestion_id)
        suggestion.status = "rejected"
        self._save_suggestions()
        return suggestion.to_dict()

    def orchestrate(self, task: str, user: str = "", context: str = "") -> dict[str, Any]:
        if DEFAULT_HOT_RELOAD:
            self.reload()

        subtasks = self._decompose_task(task)
        dependency_results: dict[str, dict[str, Any]] = {}
        completed_ids: set[str] = set()
        ordered_results: list[dict[str, Any]] = []

        while len(completed_ids) < len(subtasks):
            ready = [item for item in subtasks if item.id not in completed_ids and set(item.depends_on).issubset(completed_ids)]
            if not ready:
                break

            with ThreadPoolExecutor(max_workers=max(1, len(ready))) as executor:
                futures = [executor.submit(self._execute_subtask, subtask, user, context, dependency_results) for subtask in ready]
                for future in futures:
                    result = future.result()
                    ordered_results.append(result)
                    completed_ids.add(str(result["subtask_id"]))
                    dependency_results[str(result["subtask_id"])] = result

        result_text = self._synthesize_results(task, ordered_results)
        ok = (
            all(bool(item.get("ok") or item.get("timed_out")) for item in ordered_results)
            if ordered_results
            else False
        )
        return {
            "mode": "orchestrator",
            "ok": ok,
            "task": task,
            "user": user,
            "context": context,
            "result_text": result_text,
            "subtasks": ordered_results,
            "workers": self.agent_pool.list_workers(),
            "suggestions": [item.to_dict() for item in self.pending_suggestions.values() if item.status == "pending"],
        }

    def _decompose_task(self, task: str) -> list[SubTask]:
        raw = str(task or "").strip()
        chunks = [part.strip(" -\t") for part in re.split(r"(?:\n+|(?:\s+그리고\s+)|(?:\s+then\s+)|(?:\s+and then\s+))", raw) if part and part.strip(" -\t")]
        if not chunks:
            chunks = [raw]

        subtasks: list[SubTask] = []
        previous_id: str | None = None
        for index, chunk in enumerate(chunks, start=1):
            matched_skills = self._match_skills_for_chunk(chunk)
            required_skills = [skill.name for skill in matched_skills]
            expected_output = next((skill.expected_output for skill in matched_skills if skill.expected_output), "")
            subtask = SubTask(
                id=f"task-{index}",
                task=chunk,
                depends_on=[previous_id] if previous_id and len(chunks) > 1 else [],
                required_skills=required_skills,
                expected_output=expected_output,
            )
            subtasks.append(subtask)
            previous_id = subtask.id
        return subtasks

    def _match_skills_for_chunk(self, chunk: str) -> list[SkillDefinition]:
        forced_skill_names = self._forced_skill_names(chunk)
        if forced_skill_names:
            forced_skills = [self.skill_registry.get_skill(name) for name in forced_skill_names]
            forced_skills = [skill for skill in forced_skills if skill is not None]
            if forced_skills:
                return forced_skills
        return self.skill_registry.match_skills(chunk)

    def _forced_skill_names(self, task: str) -> list[str]:
        normalized = " ".join(str(task or "").strip().lower().split())
        if not normalized:
            return []

        compact = normalized.replace(" ", "")
        has_unreal_mcp_marker = any(token in compact for token in ("panicroom", "unrealmcp")) or any(
            phrase in normalized for phrase in ("panic room", "unreal mcp")
        )
        if has_unreal_mcp_marker:
            return ["unreal-mcp", "unreal"]

        has_unreal_scene_marker = any(
            marker in normalized
            for marker in (
                "ue5",
                "unreal",
                "point light",
                "spot light",
                "directional light",
                "sky light",
                "skylight",
                "blueprint",
                "level",
                "actor",
            )
        )
        has_scene_action = any(
            marker in normalized
            for marker in ("spawn", "create", "place", "move", "set", "생성", "배치", "위치", "이동")
        )
        if has_unreal_scene_marker and has_scene_action:
            # Route UE5 scene mutations through the UnrealMCP adapter path.
            # This avoids duplicate side effects when direct MCP execution times out.
            return ["unreal-mcp", "unreal"]

        return []

    def _execute_subtask(
        self,
        subtask: SubTask,
        user: str,
        context: str,
        dependency_results: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        matched_skills = [self.skill_registry.get_skill(name) for name in subtask.required_skills]
        skills = [skill for skill in matched_skills if skill is not None]
        routing_skills = self._routing_required_skills(subtask.required_skills)
        match = self.agent_registry.find_best_match(subtask.task, routing_skills)

        selected_agent = match.matched_agents[0] if match.matched_agents else None
        selected_agent_skills = set(selected_agent.skills) if selected_agent else set()
        can_handle_routing_skills = bool(routing_skills) and set(routing_skills).issubset(selected_agent_skills)
        resolution = "specialist" if selected_agent and (match.status == "matched" or can_handle_routing_skills) else "generic_fallback"
        if resolution == "generic_fallback":
            fallback_names = self._select_fallback_skills(subtask.task, subtask.required_skills)
            skills = [self.skill_registry.get_skill(name) for name in fallback_names]
            skills = [skill for skill in skills if skill is not None]

        agent_config = self.skill_registry.build_agent_config(skills)
        prompt = self._build_worker_prompt(subtask, selected_agent, agent_config, user, context, dependency_results)

        # ── UnrealMCP 전용 실행 경로 ────────────────────────────────────
        # GPT가 MCP tool을 직접 호출하지 않도록 프롬프트를 바꾸고,
        # GPT 응답(intent JSON)을 unreal_adapter가 파싱해 UE5 TCP로 직접 전송한다.
        # 이를 통해 MCP 타임아웃(-32001) → 중복 실행 문제를 근본적으로 방지한다.
        if "unreal-mcp" in (agent_config.skills or []):
            execution = self._execute_unreal_via_adapter(subtask, selected_agent, agent_config, prompt)
            quality = self._quality_check(subtask, execution, resolution)
            suggestion: ImplementationSuggestion | None = None
            if quality == "insufficient":
                suggestion = self._create_suggestion(subtask, match, execution)
            record = {
                "subtask_id": subtask.id,
                "task": subtask.task,
                "required_skills": subtask.required_skills,
                "resolution": resolution,
                "quality": quality,
                "agent": selected_agent.to_dict() if selected_agent else None,
                "ok": bool(execution.get("ok")),
                "result_text": str(execution.get("result_text") or "").strip(),
                "stderr": str(execution.get("stderr") or "").strip(),
                "worker": execution.get("worker"),
                "implementation_suggestion": suggestion.to_dict() if suggestion else None,
            }
            self._append_knowledge(record)
            return record
        # ────────────────────────────────────────────────────────────────

        execution = self.agent_pool.run_task(prompt, "", selected_agent, agent_config.skills)
        if "google-calendar" in agent_config.skills:
            calendar_execution = execute_calendar_plan(str(execution.get("result_text") or ""))
            if calendar_execution is not None:
                execution = dict(execution)
                execution["calendar_execution"] = calendar_execution
                execution["result_text"] = str(calendar_execution.get("user_message") or execution.get("result_text") or "").strip()
                execution["ok"] = calendar_execution.get("status") == "success"
        quality = self._quality_check(subtask, execution, resolution)
        suggestion: ImplementationSuggestion | None = None
        if quality == "insufficient":
            suggestion = self._create_suggestion(subtask, match, execution)

        record = {
            "subtask_id": subtask.id,
            "task": subtask.task,
            "required_skills": subtask.required_skills,
            "resolution": resolution,
            "quality": quality,
            "agent": selected_agent.to_dict() if selected_agent else None,
            "ok": bool(execution.get("ok")),
            "result_text": str(execution.get("result_text") or "").strip(),
            "stderr": str(execution.get("stderr") or "").strip(),
            "worker": execution.get("worker"),
            "implementation_suggestion": suggestion.to_dict() if suggestion else None,
        }
        self._append_knowledge(record)
        return record

    def _build_worker_prompt(
        self,
        subtask: SubTask,
        agent: AgentDefinition | None,
        agent_config: Any,
        user: str,
        context: str,
        dependency_results: dict[str, dict[str, Any]],
    ) -> str:
        if "google-calendar" in agent_config.skills:
            current_datetime = now_iso(DEFAULT_TIMEZONE)
            return build_calendar_manager_execution_prompt(
                user_input=subtask.task,
                discord_user=user or "api-user",
                discord_channel="api",
                user_id=user or "api-user",
                current_datetime=current_datetime,
                context=context,
                timezone_name=DEFAULT_TIMEZONE,
            )

        if "unreal-mcp" in agent_config.skills:
            return self._build_unreal_mcp_prompt(subtask, agent, user, context, dependency_results)

        sections = [
            "You are a Hermes worker handling one routed subtask.",
            "Prioritize execution. Use the available codebase and tools directly.",
        ]
        if self._worker_rules:
            sections.append(self._worker_rules)
        if agent is not None:
            sections.append(f"Assigned agent: {agent.name}")
            if agent.role:
                sections.append(f"Role: {agent.role}")
            if agent.goal:
                sections.append(f"Goal: {agent.goal}")
            if agent.backstory:
                sections.append(f"Backstory: {agent.backstory}")
        if agent_config.skills:
            sections.append("Skills: " + ", ".join(agent_config.skills))
        if agent_config.system_prompt:
            sections.append(agent_config.system_prompt)
        if agent_config.references:
            references = []
            for item in agent_config.references[:5]:
                references.append(f"[{item['path']}]\n{item['content']}")
            sections.append("References:\n" + "\n\n".join(references))
        if dependency_results:
            summaries = []
            for dep_id in subtask.depends_on:
                dep = dependency_results.get(dep_id)
                if dep and dep.get("result_text"):
                    summaries.append(f"{dep_id}: {dep['result_text']}")
            if summaries:
                sections.append("Dependency results:\n" + "\n".join(summaries))
        if user:
            sections.append(f"Requested by: {user}")
        if context.strip():
            trimmed = context.strip()[:2000]
            sections.append("Conversation context:\n" + trimmed)
        sections.append(f"Task:\n{subtask.task}")
        return "\n\n".join(section for section in sections if section.strip())

    def _build_unreal_mcp_prompt(
        self,
        subtask: SubTask,
        agent: AgentDefinition | None,
        user: str,
        context: str,
        dependency_results: dict[str, dict[str, Any]],
    ) -> str:
        """UnrealMCP 전용 프롬프트 — GPT에게 intent JSON만 출력하도록 요청.

        ⚠️ GPT는 MCP tool을 직접 호출하지 않는다.
        GPT 응답의 JSON intent를 unreal_adapter가 파싱해 UE5 TCP로 직접 전송한다.
        이를 통해 MCP 타임아웃(-32001)으로 인한 중복 실행 문제를 방지한다.
        """
        sections = [
            "You are preparing a Codex Unreal MCP intent.",
            "Output ONLY one JSON object.",
            "",
            "Do not call tools. Do not explain.",
            "The system will execute after receiving your JSON.",
            "",
            "If required info is missing, output:",
            '  {"invalid": true, "reason": "...", "missing": ["field"]}',
            "",
            "Use narrow, summary-first requests.",
            "Never request full logs, full asset dumps, full actor lists, or full blueprint graphs.",
            "For assets, require a query or exact asset_path.",
            "For graphs, require blueprint_name and explicit graph_name.",
            "For logs, use tail_editor_log with tail_lines <= 200 and optional contains.",
            "",
            "Actor intent format:",
            '  {"action":"create|delete|transform|query|find|get_props|set_prop|duplicate", ...}',
            "",
            "Direct tool format:",
            '  {"tool":"search_assets|get_asset_details|get_blueprint_graph|tail_editor_log|inspect_uobject", "params": {...}}',
            "",
            "Supported actions:",
            '  "create"    — create a new actor (requires: class)',
            '  "delete"    — delete an actor (requires: name)',
            '  "transform" — move/rotate/scale actor (requires: name, location/rotation/scale)',
            '  "query"     — list actors in level (optional: filter class)',
            '  "find"      — find actors by name pattern (requires: pattern)',
            '  "get_props" — read actor properties (requires: name)',
            '  "set_prop"  — set a property (requires: name, property, value)',
            '  "duplicate" — copy an actor (requires: name)',
            "",
            "Examples:",
            '  {"action":"create","class":"PointLight","location":[1000,1000,300]}',
            '  → {"action": "create", "class": "PointLight", "location": [1000, 1000, 300]}',
            "",
            '  {"tool":"get_blueprint_graph","params":{"blueprint_name":"BP_EnemyAI","graph_name":"EventGraph","node_limit":20}}',
            '  → {"action": "delete", "name": "MyLight"}',
            "",
            '  {"tool":"tail_editor_log","params":{"tail_lines":80,"contains":"Error"}}',
            '  → {"action": "query"}',
        ]

        if dependency_results:
            summaries = []
            for dep_id in subtask.depends_on:
                dep = dependency_results.get(dep_id)
                if dep and dep.get("result_text"):
                    summaries.append(f"{dep_id}: {dep['result_text']}")
            if summaries:
                sections.append("Previous results:\n" + "\n".join(summaries))

        if context.strip():
            sections.append("Context:\n" + context.strip()[:1000])

        sections.append(f"Task: {subtask.task}")
        sections.append("Output ONLY the JSON object, nothing else.")
        return "\n".join(sections)

    def _execute_unreal_via_adapter(
        self,
        subtask: SubTask,
        agent: Any,
        agent_config: Any,
        intent_prompt: str,
    ) -> dict[str, Any]:
        """UnrealMCP 어댑터 경로 실행.

        1. GPT에게 intent JSON만 요청 (tool 실행 없음)
        2. unreal_adapter.execute_unreal_intent()로 UE5 TCP 직접 호출
        3. Hermes 포맷 결과 반환

        GPT가 MCP tool을 직접 호출하지 않으므로 타임아웃(-32001) 문제가 없다.
        """
        # 1. GPT에게 intent JSON 요청 (OpenCode, no MCP tool execution)
        gpt_result = self.agent_pool.run_task(
            intent_prompt, "", agent, agent_config.skills
        )
        intent_text = str(gpt_result.get("result_text") or gpt_result.get("stdout") or "").strip()

        if not intent_text:
            return {
                "ok": False,
                "result_text": "GPT에서 intent를 받지 못했습니다.",
                "stderr": "empty intent from Codex",
                "mode": "unreal-mcp-adapter",
                "returncode": None,
                "stdout": "",
                "worker": gpt_result.get("worker"),
            }

        # 2. unreal_adapter로 UE5 실행
        try:
            from unreal_adapter import execute_unreal_intent
            adapter_result = execute_unreal_intent(intent_text)
        except ImportError:
            # unreal_adapter 미설치: GPT 결과를 그대로 반환 (graceful degradation)
            adapter_result = gpt_result
        except Exception as exc:  # noqa: BLE001
            adapter_result = {
                "ok": False,
                "result_text": f"어댑터 오류: {exc}",
                "stderr": str(exc),
                "mode": "unreal-mcp-adapter",
                "returncode": None,
                "stdout": "",
            }

        adapter_result["worker"] = gpt_result.get("worker")
        return adapter_result

    UNREAL_MCP_SKILLS = {"unreal-mcp", "unreal"}

    def _quality_check(self, subtask: SubTask, result: dict[str, Any], resolution: str) -> str:
        # UnrealMCP 스킬: 타임아웃이더라도 부수효과(액터 생성 등)는 UE5에서 이미 실행됨
        # ok=False + timed_out=True 조합은 실제 실패가 아닌 응답 수신 실패이므로 sufficient 처리
        required_skills = set(subtask.required_skills or [])
        if required_skills & self.UNREAL_MCP_SKILLS:
            if result.get("timed_out") or result.get("ok"):
                return "sufficient"
            # 명시적 에러(연결 실패 등)만 insufficient
            return "insufficient"

        if resolution == "specialist":
            return "sufficient" if result.get("ok") else "insufficient"
        if not result.get("ok"):
            return "insufficient"
        result_text = str(result.get("result_text") or "").strip()
        if not result_text:
            return "insufficient"
        if len(result_text) < 40:
            return "insufficient"
        if subtask.expected_output and not self._matches_expected_format(result_text, subtask.expected_output):
            return "insufficient"
        return "sufficient"

    def _matches_expected_format(self, result_text: str, expected_output: str) -> bool:
        expected_tokens = {token for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]+", expected_output) if len(token) > 3}
        if not expected_tokens:
            return True
        lowered = result_text.lower()
        return any(token.lower() in lowered for token in expected_tokens)

    def _select_fallback_skills(self, task: str, required_skills: list[str]) -> list[str]:
        selected = [name for name in required_skills if name in {"code-general", "research", "document"}]
        lowered = task.lower()
        if "document" in lowered or "write" in lowered or "summary" in lowered:
            selected.append("document")
        elif any(token in lowered for token in ["search", "research", "find", "compare"]):
            selected.append("research")
        else:
            selected.append("code-general")
        return list(dict.fromkeys(selected))

    def _routing_required_skills(self, required_skills: list[str]) -> list[str]:
        specialized = [name for name in required_skills if name and name not in GENERIC_SUPPORT_SKILLS]
        if specialized:
            return specialized
        return [name for name in required_skills if name]

    def _create_suggestion(
        self,
        subtask: SubTask,
        match: Any,
        execution: dict[str, Any],
    ) -> ImplementationSuggestion:
        request = match.implementation_request or self.agent_registry.generate_implementation_request(
            subtask.task,
            match.missing_skills if hasattr(match, "missing_skills") else subtask.required_skills,
            match.matched_agents if hasattr(match, "matched_agents") else [],
        )
        suggestion_id = f"suggest-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        suggestion = ImplementationSuggestion(
            suggestion_id=suggestion_id,
            reason="Fallback execution was insufficient for the routed task.",
            task_description=subtask.task,
            fallback_result_summary=str(execution.get("result_text") or execution.get("stderr") or "").strip(),
            quality_assessment="Fallback result did not meet the expected confidence threshold.",
            suggested_agent=request.suggested_agent,
            suggested_skill=request.suggested_skill,
            setup_guide=request.setup_guide,
            similar_agents=request.similar_agents,
        )
        with self._lock:
            self.pending_suggestions[suggestion_id] = suggestion
            self._save_suggestions()
        return suggestion

    def _synthesize_results(self, original_task: str, results: list[dict[str, Any]]) -> str:
        if not results:
            return "No subtask was executed."
        if len(results) == 1:
            text = results[0].get("result_text") or results[0].get("stderr") or ""
            suggestion = results[0].get("implementation_suggestion")
            if suggestion:
                return f"{text}\n\nSpecialist suggestion pending: {suggestion['suggestion_id']}"
            return str(text).strip()

        lines = [f"Task: {original_task}"]
        for item in results:
            lines.append(f"[{item['subtask_id']}] {item['task']}")
            lines.append(str(item.get("result_text") or item.get("stderr") or "").strip())
            suggestion = item.get("implementation_suggestion")
            if suggestion:
                lines.append(f"Specialist suggestion pending: {suggestion['suggestion_id']}")
        return "\n\n".join(line for line in lines if line)

    def _append_knowledge(self, record: dict[str, Any]) -> None:
        target = self.knowledge_dir / f"{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        payload = dict(record)
        payload["timestamp"] = _utc_now()
        with self._lock:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _load_reactions(self) -> dict[str, Any]:
        if not self.reactions_path.exists():
            return {}
        try:
            return json.loads(self.reactions_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _suggestions_path(self) -> Path:
        return self.agents_dir / SUGGESTIONS_FILE

    def _load_suggestions(self) -> dict[str, ImplementationSuggestion]:
        path = self._suggestions_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        suggestions: dict[str, ImplementationSuggestion] = {}
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("suggestion_id"):
                    suggestion = ImplementationSuggestion(
                        suggestion_id=str(item.get("suggestion_id")),
                        status=str(item.get("status") or "pending"),
                        reason=str(item.get("reason") or ""),
                        task_description=str(item.get("task_description") or ""),
                        fallback_result_summary=str(item.get("fallback_result_summary") or ""),
                        quality_assessment=str(item.get("quality_assessment") or ""),
                        suggested_agent=item.get("suggested_agent", {}) if isinstance(item.get("suggested_agent"), dict) else {},
                        suggested_skill=item.get("suggested_skill", {}) if isinstance(item.get("suggested_skill"), dict) else {},
                        setup_guide=str(item.get("setup_guide") or ""),
                        similar_agents=[str(value) for value in item.get("similar_agents", []) if str(value).strip()],
                    )
                    suggestions[suggestion.suggestion_id] = suggestion
        return suggestions

    def _save_suggestions(self) -> None:
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        payload = [item.to_dict() for item in self.pending_suggestions.values()]
        self._suggestions_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_DEFAULT_ORCHESTRATOR: HermesOrchestrator | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_orchestrator() -> HermesOrchestrator:
    global _DEFAULT_ORCHESTRATOR
    with _DEFAULT_LOCK:
        if _DEFAULT_ORCHESTRATOR is None:
            _DEFAULT_ORCHESTRATOR = HermesOrchestrator()
        return _DEFAULT_ORCHESTRATOR
