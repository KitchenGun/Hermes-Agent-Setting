import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_+\-./]*", re.IGNORECASE)


@dataclass(slots=True)
class SkillDefinition:
    name: str
    display_name: str
    description: str
    keywords: list[str]
    role: str = ""
    goal: str = ""
    trigger_when: str = ""
    skip_when: str = ""
    system_prompt_path: str = ""
    reference_paths: list[str] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    required_tools: list[str] | None = None
    expected_output: str = ""
    priority: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SkillDefinition":
        return cls(
            name=str(payload.get("name") or "").strip(),
            display_name=str(payload.get("display_name") or payload.get("name") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            keywords=[str(item).strip() for item in payload.get("keywords", []) if str(item).strip()],
            role=str(payload.get("role") or "").strip(),
            goal=str(payload.get("goal") or "").strip(),
            trigger_when=str(payload.get("trigger_when") or "").strip(),
            skip_when=str(payload.get("skip_when") or "").strip(),
            system_prompt_path=str(payload.get("system_prompt_path") or "").strip(),
            reference_paths=[str(item).strip() for item in payload.get("reference_paths", []) if str(item).strip()],
            mcp_servers=[item for item in payload.get("mcp_servers", []) if isinstance(item, dict)],
            required_tools=[str(item).strip() for item in payload.get("required_tools", []) if str(item).strip()],
            expected_output=str(payload.get("expected_output") or "").strip(),
            priority=int(payload.get("priority", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "keywords": self.keywords,
            "role": self.role,
            "goal": self.goal,
            "trigger_when": self.trigger_when,
            "skip_when": self.skip_when,
            "system_prompt_path": self.system_prompt_path,
            "reference_paths": self.reference_paths or [],
            "mcp_servers": self.mcp_servers or [],
            "required_tools": self.required_tools or [],
            "expected_output": self.expected_output,
            "priority": self.priority,
        }


@dataclass(slots=True)
class AgentConfig:
    system_prompt: str
    mcp_servers: list[dict[str, Any]]
    references: list[dict[str, str]]
    skills: list[str]
    required_tools: list[str]


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text or "")}


class SkillRegistry:
    def __init__(self, skills_dir: str | Path = "skills") -> None:
        self.skills_dir = Path(skills_dir)
        self.skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        if skill.name:
            self.skills[skill.name] = skill

    def get_skill(self, name: str) -> SkillDefinition | None:
        return self.skills.get(name)

    def list_skills(self) -> list[SkillDefinition]:
        return sorted(self.skills.values(), key=lambda item: (-item.priority, item.name))

    def load_from_directory(self, path: str | Path | None = None) -> None:
        base_dir = Path(path) if path else self.skills_dir
        self.skills_dir = base_dir
        self.skills = {}
        if not base_dir.exists():
            return

        for skill_file in sorted(base_dir.glob("*/skill.json")):
            try:
                payload = json.loads(skill_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            skill = SkillDefinition.from_dict(payload)
            if skill.name:
                self.register(skill)

        self._write_registry_file()

    def match_skills(self, task_text: str, limit: int = 3) -> list[SkillDefinition]:
        task = str(task_text or "").strip()
        if not task:
            return self._fallback_skills(limit)

        scored: list[tuple[int, int, int, SkillDefinition]] = []
        task_tokens = _tokenize(task)
        task_lower = task.lower()
        for skill in self.skills.values():
            keyword_score = self._keyword_score(skill, task_lower)
            semantic_score = self._semantic_score(skill, task_tokens)
            total = keyword_score * 10 + semantic_score + skill.priority
            if keyword_score > 0 or semantic_score >= 2:
                scored.append((keyword_score, semantic_score, total, skill))

        if not scored:
            return self._fallback_skills(limit)

        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3].name))
        selected = [item[3] for item in scored[: max(1, limit)]]

        fallback = self.get_skill("code-general")
        if fallback and fallback not in selected and len(selected) < limit:
            selected.append(fallback)
        return selected

    def build_agent_config(self, skills: list[SkillDefinition]) -> AgentConfig:
        prompt_parts: list[str] = []
        references: list[dict[str, str]] = []
        mcp_servers: list[dict[str, Any]] = []
        required_tools: list[str] = []
        skill_names: list[str] = []

        for skill in skills:
            if skill.name in skill_names:
                continue
            skill_names.append(skill.name)

            prompt_text = self._read_relative_text(skill.system_prompt_path)
            if prompt_text:
                prompt_parts.append(f"# Skill: {skill.display_name}\n{prompt_text}")

            for reference_path in skill.reference_paths or []:
                reference_text = self._read_relative_text(reference_path)
                if reference_text:
                    references.append({"path": reference_path, "content": reference_text})

            for server in skill.mcp_servers or []:
                if server not in mcp_servers:
                    mcp_servers.append(server)

            for tool in skill.required_tools or []:
                if tool not in required_tools:
                    required_tools.append(tool)

        return AgentConfig(
            system_prompt="\n\n".join(part for part in prompt_parts if part.strip()),
            mcp_servers=mcp_servers,
            references=references,
            skills=skill_names,
            required_tools=required_tools,
        )

    def _keyword_score(self, skill: SkillDefinition, task_lower: str) -> int:
        score = 0
        candidates = [skill.name, skill.display_name, *skill.keywords]
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if value and value in task_lower:
                score += 1
        return score

    def _semantic_score(self, skill: SkillDefinition, task_tokens: set[str]) -> int:
        description_text = " ".join(
            [
                skill.description,
                skill.role,
                skill.goal,
                skill.trigger_when,
                skill.expected_output,
                " ".join(skill.keywords),
            ]
        )
        if not description_text.strip():
            return 0
        description_tokens = _tokenize(description_text)
        return len(task_tokens & description_tokens)

    def _fallback_skills(self, limit: int) -> list[SkillDefinition]:
        preferred = ["code-general", "research", "document"]
        selected: list[SkillDefinition] = []
        for name in preferred:
            skill = self.get_skill(name)
            if skill:
                selected.append(skill)
        return selected[: max(1, limit)]

    def _read_relative_text(self, relative_path: str) -> str:
        candidate = (self.skills_dir.parent / relative_path).resolve()
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _write_registry_file(self) -> None:
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            registry_path = self.skills_dir / "registry.json"
            payload = [skill.to_dict() for skill in self.list_skills()]
            registry_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
