import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_+\-./]*", re.IGNORECASE)


@dataclass(slots=True)
class AgentDefinition:
    id: str
    name: str
    status: str
    role: str
    goal: str
    backstory: str
    skills: list[str]
    model: str
    variant: str
    port: int | None = None
    allowed_delegations: list[str] | None = None
    max_concurrent_tasks: int = 1
    capabilities: dict[str, Any] | None = None
    health_check: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentDefinition":
        return cls(
            id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            status=str(payload.get("status") or "active").strip().lower(),
            role=str(payload.get("role") or "").strip(),
            goal=str(payload.get("goal") or "").strip(),
            backstory=str(payload.get("backstory") or "").strip(),
            skills=[str(item).strip() for item in payload.get("skills", []) if str(item).strip()],
            model=str(payload.get("model") or "openai/gpt-5.4").strip(),
            variant=str(payload.get("variant") or "medium").strip(),
            port=int(payload["port"]) if payload.get("port") not in (None, "") else None,
            allowed_delegations=[str(item).strip() for item in payload.get("allowed_delegations", []) if str(item).strip()],
            max_concurrent_tasks=int(payload.get("max_concurrent_tasks", 1) or 1),
            capabilities=payload.get("capabilities", {}) if isinstance(payload.get("capabilities"), dict) else {},
            health_check=payload.get("health_check", {}) if isinstance(payload.get("health_check"), dict) else {},
            created_at=str(payload.get("created_at") or "").strip(),
            updated_at=str(payload.get("updated_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "role": self.role,
            "goal": self.goal,
            "backstory": self.backstory,
            "skills": self.skills,
            "model": self.model,
            "variant": self.variant,
            "port": self.port,
            "allowed_delegations": self.allowed_delegations or [],
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "capabilities": self.capabilities or {},
            "health_check": self.health_check or {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class ImplementationRequest:
    request_type: str = "agent_implementation_needed"
    task_description: str = ""
    missing_skills: list[str] | None = None
    suggested_agent: dict[str, Any] | None = None
    suggested_skill: dict[str, Any] | None = None
    setup_guide: str = ""
    similar_agents: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_type": self.request_type,
            "task_description": self.task_description,
            "missing_skills": self.missing_skills or [],
            "suggested_agent": self.suggested_agent or {},
            "suggested_skill": self.suggested_skill or {},
            "setup_guide": self.setup_guide,
            "similar_agents": self.similar_agents or [],
        }


@dataclass(slots=True)
class AgentMatchResult:
    status: str
    matched_agents: list[AgentDefinition]
    missing_skills: list[str]
    implementation_request: ImplementationRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "matched_agents": [agent.to_dict() for agent in self.matched_agents],
            "missing_skills": self.missing_skills,
            "implementation_request": self.implementation_request.to_dict() if self.implementation_request else None,
        }


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text or "")}


class AgentRegistry:
    def __init__(self, agents_dir: str | Path = "agents") -> None:
        self.agents_dir = Path(agents_dir)
        self.agents: dict[str, AgentDefinition] = {}

    def load_from_directory(self, path: str | Path | None = None) -> None:
        base_dir = Path(path) if path else self.agents_dir
        self.agents_dir = base_dir
        self.agents = {}
        if not base_dir.exists():
            return

        for agent_file in sorted(base_dir.glob("*/agent.json")):
            try:
                payload = json.loads(agent_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            agent = AgentDefinition.from_dict(payload)
            if agent.id:
                self.agents[agent.id] = agent

        self._write_registry_file()

    def register(self, agent: AgentDefinition) -> None:
        self.agents[agent.id] = agent
        self._write_agent_file(agent)
        self._write_registry_file()

    def unregister(self, agent_id: str) -> None:
        self.agents.pop(agent_id, None)
        target = self.agents_dir / agent_id / "agent.json"
        if target.exists():
            target.unlink()
        self._write_registry_file()

    def update(self, agent_id: str, updates: dict[str, Any]) -> AgentDefinition | None:
        existing = self.get(agent_id)
        if existing is None:
            return None
        payload = existing.to_dict()
        payload.update(updates)
        payload["updated_at"] = _utc_now()
        updated = AgentDefinition.from_dict(payload)
        self.register(updated)
        return updated

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self.agents.get(agent_id)

    def list_all(self) -> list[AgentDefinition]:
        return sorted(self.agents.values(), key=lambda item: item.id)

    def search(self, query: str) -> list[AgentDefinition]:
        tokens = _tokenize(query)
        if not tokens:
            return self.list_all()

        scored: list[tuple[int, AgentDefinition]] = []
        for agent in self.list_all():
            corpus = " ".join(
                [
                    agent.id,
                    agent.name,
                    agent.role,
                    agent.goal,
                    agent.backstory,
                    " ".join(agent.skills),
                    " ".join(str(item) for item in (agent.capabilities or {}).get("domains", [])),
                ]
            )
            score = len(tokens & _tokenize(corpus))
            if score:
                scored.append((score, agent))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [item[1] for item in scored]

    def find_by_skills(self, required_skills: list[str]) -> list[AgentDefinition]:
        required = {item for item in required_skills if item}
        matches = []
        for agent in self.list_all():
            if agent.status != "active":
                continue
            if required.issubset(set(agent.skills)):
                matches.append(agent)
        return matches

    def find_by_capabilities(self, domain: str) -> list[AgentDefinition]:
        needle = str(domain or "").strip().lower()
        if not needle:
            return []
        matches = []
        for agent in self.list_all():
            domains = [str(item).strip().lower() for item in (agent.capabilities or {}).get("domains", [])]
            if needle in domains:
                matches.append(agent)
        return matches

    def find_best_match(self, task: str, required_skills: list[str]) -> AgentMatchResult:
        active_agents = [agent for agent in self.list_all() if agent.status == "active"]
        if not active_agents:
            missing = [skill for skill in required_skills if skill]
            return AgentMatchResult(
                status="not_found",
                matched_agents=[],
                missing_skills=missing,
                implementation_request=self.generate_implementation_request(task, missing, []),
            )

        required = {item for item in required_skills if item}
        scored: list[tuple[int, int, AgentDefinition]] = []
        task_tokens = _tokenize(task)
        for agent in active_agents:
            skill_overlap = len(required & set(agent.skills))
            corpus = " ".join(
                [
                    agent.id,
                    agent.name,
                    agent.role,
                    agent.goal,
                    agent.backstory,
                    " ".join(agent.skills),
                    " ".join(str(item) for item in (agent.capabilities or {}).get("domains", [])),
                    " ".join(str(item) for item in (agent.capabilities or {}).get("languages", [])),
                ]
            )
            semantic_overlap = len(task_tokens & _tokenize(corpus))
            scored.append((skill_overlap, semantic_overlap, agent))

        scored.sort(key=lambda item: (-item[0], -item[1], item[2].id))
        matched_agents = [item[2] for item in scored if item[0] > 0 or item[1] > 0]

        if matched_agents and required and required.issubset(set(matched_agents[0].skills)):
            return AgentMatchResult(status="matched", matched_agents=matched_agents[:3], missing_skills=[])

        partial_skills = set()
        for _, _, agent in scored:
            partial_skills.update(required & set(agent.skills))
        missing = sorted(required - partial_skills)

        if matched_agents:
            return AgentMatchResult(
                status="partial",
                matched_agents=matched_agents[:3],
                missing_skills=missing,
                implementation_request=self.generate_implementation_request(task, missing, matched_agents[:3]),
            )

        return AgentMatchResult(
            status="not_found",
            matched_agents=[],
            missing_skills=sorted(required),
            implementation_request=self.generate_implementation_request(task, sorted(required), []),
        )

    def generate_implementation_request(
        self,
        task: str,
        missing_skills: list[str],
        similar_agents: list[AgentDefinition],
    ) -> ImplementationRequest:
        primary_skill = missing_skills[0] if missing_skills else "custom-skill"
        agent_id = primary_skill.lower().replace(" ", "-")
        similar = [agent.id for agent in similar_agents if agent.id]
        suggested_agent = {
            "id": f"{agent_id}-specialist",
            "name": f"{primary_skill.title()} Specialist",
            "status": "active",
            "role": f"{primary_skill.title()} execution specialist",
            "goal": f"Handle tasks related to {primary_skill} safely and accurately.",
            "backstory": f"Specialized worker focused on {primary_skill}.",
            "skills": missing_skills or [primary_skill],
            "model": "openai/gpt-5.4",
            "variant": "medium",
            "port": None,
            "allowed_delegations": ["generalist", "doc-writer", "code-reviewer"],
            "max_concurrent_tasks": 1,
            "capabilities": {
                "domains": missing_skills or [primary_skill],
                "tools": [],
                "mcp_servers": [],
            },
            "health_check": {"enabled": True, "interval_s": 30, "max_failures": 3},
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        suggested_skill = {
            "name": primary_skill,
            "display_name": primary_skill.title(),
            "description": f"Skill bundle for tasks related to {primary_skill}.",
            "role": f"{primary_skill.title()} specialist skill",
            "goal": f"Execute {primary_skill} work with clear outputs.",
            "keywords": list(dict.fromkeys(missing_skills or [primary_skill])),
            "trigger_when": f"Use when the task explicitly requires {primary_skill}.",
            "skip_when": "Skip when a more precise existing specialist already covers the task.",
            "system_prompt_path": f"skills/{primary_skill}/system_prompt.md",
            "reference_paths": [],
            "mcp_servers": [],
            "required_tools": [],
            "expected_output": "Concrete execution result or implementation guidance.",
            "priority": 8,
        }
        setup_guide = (
            f"1. Create agents/{suggested_agent['id']}/agent.json\n"
            f"2. Create skills/{primary_skill}/skill.json\n"
            f"3. Create skills/{primary_skill}/system_prompt.md\n"
            "4. Reload the Hermes registry"
        )
        return ImplementationRequest(
            task_description=task,
            missing_skills=missing_skills,
            suggested_agent=suggested_agent,
            suggested_skill=suggested_skill,
            setup_guide=setup_guide,
            similar_agents=similar,
        )

    def _write_agent_file(self, agent: AgentDefinition) -> None:
        target_dir = self.agents_dir / agent.id
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "agent.json").write_text(json.dumps(agent.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_registry_file(self) -> None:
        try:
            self.agents_dir.mkdir(parents=True, exist_ok=True)
            registry_path = self.agents_dir / "registry.json"
            payload = [agent.to_dict() for agent in self.list_all()]
            registry_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
