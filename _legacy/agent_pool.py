import os
import threading
from dataclasses import dataclass, field
from typing import Any

from agent_registry import AgentDefinition
from hermes_backend import send as hermes_send
from hermes_backend import status as hermes_status


DEFAULT_MAX_WORKERS = int(os.getenv("HERMES_MAX_WORKERS", "4").strip() or "4")
DEFAULT_MODEL = os.getenv("HERMES_MODEL", os.getenv("HERMES_OPENCODE_MODEL", "openai/gpt-5.4")).strip() or "openai/gpt-5.4"
DEFAULT_VARIANT = os.getenv("HERMES_VARIANT", os.getenv("HERMES_OPENCODE_VARIANT", "medium")).strip() or "medium"


@dataclass(slots=True)
class WorkerAgent:
    name: str
    model: str
    variant: str
    assigned_agent_id: str | None = None
    skills: list[str] = field(default_factory=list)
    active_tasks: int = 0

    # 하위 호환성: orchestrator 등에서 worker dict["host"], worker dict["port"] 참조
    host: str = "127.0.0.1"
    port: int = 0


class AgentPool:
    """Hermes sub-agent 기반 워커 풀.

    OpenCode subprocess 없이 hermes_backend 를 통해 LLM 태스크를 실행한다.
    워커는 동시 실행 수를 추적하는 논리적 슬롯이며, 실제 프로세스를 관리하지 않는다.
    """

    FORCED_MODEL_SKILLS = {"unreal-mcp", "unreal"}
    UNREAL_TASK_TIMEOUT = int(os.getenv("HERMES_UNREAL_TIMEOUT", "300").strip() or "300")

    def __init__(
        self,
        max_workers: int = DEFAULT_MAX_WORKERS,
        default_model: str = DEFAULT_MODEL,
        default_variant: str = DEFAULT_VARIANT,
        # 하위 호환성: 기존 코드가 host/base_port 를 넘기는 경우 무시
        host: str = "127.0.0.1",
        base_port: int = 0,
    ) -> None:
        self.max_workers = max_workers
        self.default_model = default_model
        self.default_variant = default_variant
        self._lock = threading.Lock()
        self.workers: list[WorkerAgent] = [
            WorkerAgent(
                name=f"worker_{i}",
                model=self.default_model,
                variant=self.default_variant,
                host=host,
                port=base_port + i if base_port else i,
            )
            for i in range(self.max_workers)
        ]

    def list_workers(self) -> list[dict[str, Any]]:
        state = hermes_status()
        with self._lock:
            workers = list(self.workers)
        return [
            {
                "name": w.name,
                "host": w.host,
                "port": w.port,
                "backend": "hermes",
                "model": w.model,
                "variant": w.variant,
                "assigned_agent_id": w.assigned_agent_id,
                "skills": w.skills,
                "active_tasks": w.active_tasks,
                "running": bool(state.get("running")),
                "attach_url": None,
            }
            for w in workers
        ]

    def run_task(
        self,
        prompt: str,
        context: str = "",
        agent: AgentDefinition | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """hermes_backend.send() 로 태스크를 실행한다.

        skills 인수는 모델 선택 및 타임아웃 결정에만 사용한다.
        스킬 Python 함수 직접 실행은 하지 않는다 (orchestrator 가 별도 처리).
        """
        worker = self._acquire_worker(agent, skills or [])
        effective_skills = set(skills or [])

        # UnrealMCP/Unreal: gpt-5.4 강제 + 타임아웃 300s
        if effective_skills & self.FORCED_MODEL_SKILLS:
            resolved_model = "gpt-5.4"
            resolved_variant = agent.variant if agent else worker.variant
            resolved_timeout = self.UNREAL_TASK_TIMEOUT
        else:
            resolved_model = (agent.model if agent else worker.model) or self.default_model
            resolved_variant = (agent.variant if agent else worker.variant) or self.default_variant
            resolved_timeout = None  # hermes_backend 기본값 사용

        try:
            return hermes_send(
                prompt,
                context,
                model=resolved_model,
                variant=resolved_variant,
                timeout_seconds=resolved_timeout,
                # skills 를 넘기지 않음 → orchestrator 경로에서 Python 스킬 이중 실행 방지
            )
        finally:
            self._release_worker(worker)

    def _acquire_worker(self, agent: AgentDefinition | None, skills: list[str]) -> WorkerAgent:
        with self._lock:
            target: WorkerAgent | None = None

            if agent and agent.port is not None:
                target = self._worker_by_port(agent.port)

            if target is None and agent:
                reusable = [
                    w for w in self.workers
                    if w.assigned_agent_id == agent.id and w.active_tasks < max(1, agent.max_concurrent_tasks)
                ]
                if reusable:
                    target = min(reusable, key=lambda w: w.active_tasks)

            if target is None:
                candidates = [w for w in self.workers if w.active_tasks == 0] or list(self.workers)
                target = min(candidates, key=lambda w: w.active_tasks)

            target.active_tasks += 1
            if agent:
                target.assigned_agent_id = agent.id
                target.model = agent.model or self.default_model
                target.variant = agent.variant or self.default_variant
            target.skills = list(dict.fromkeys(skills))
            return target

    def _release_worker(self, worker: WorkerAgent) -> None:
        with self._lock:
            worker.active_tasks = max(0, worker.active_tasks - 1)

    def _worker_by_port(self, port: int) -> WorkerAgent | None:
        for w in self.workers:
            if w.port == port:
                return w
        return None
