import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_registry import AgentDefinition
from opencode_backend import send as opencode_send
from opencode_backend import status as opencode_status


DEFAULT_HOST = os.getenv("HERMES_OPENCODE_HOST", "127.0.0.1").strip() or "127.0.0.1"
DEFAULT_BASE_PORT = int(os.getenv("HERMES_BASE_PORT", "4096").strip() or "4096")
DEFAULT_MAX_WORKERS = int(os.getenv("HERMES_MAX_WORKERS", "4").strip() or "4")
DEFAULT_MODEL = os.getenv("HERMES_OPENCODE_MODEL", "openai/gpt-5.4").strip() or "openai/gpt-5.4"
DEFAULT_VARIANT = os.getenv("HERMES_OPENCODE_VARIANT", "medium").strip() or "medium"
STATE_DIR = Path(os.getenv("HERMES_OPENCODE_STATE_DIR", str(Path.home() / ".config" / "opencode")))


@dataclass(slots=True)
class WorkerAgent:
    name: str
    host: str
    port: int
    model: str
    variant: str
    assigned_agent_id: str | None = None
    skills: list[str] | None = None
    active_tasks: int = 0

    @property
    def pid_file(self) -> Path:
        return STATE_DIR / f"opencode_{self.name}.pid"

    @property
    def log_file(self) -> Path:
        return STATE_DIR / f"opencode_{self.name}.log"


class AgentPool:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        base_port: int = DEFAULT_BASE_PORT,
        max_workers: int = DEFAULT_MAX_WORKERS,
        default_model: str = DEFAULT_MODEL,
        default_variant: str = DEFAULT_VARIANT,
    ) -> None:
        self.host = host
        self.base_port = base_port
        self.max_workers = max_workers
        self.default_model = default_model
        self.default_variant = default_variant
        self._lock = threading.Lock()
        self.workers: list[WorkerAgent] = [
            WorkerAgent(
                name=f"worker_{index}",
                host=self.host,
                port=self.base_port + index,
                model=self.default_model,
                variant=self.default_variant,
            )
            for index in range(self.max_workers)
        ]

    def list_workers(self) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        with self._lock:
            workers = list(self.workers)
        for worker in workers:
            state = opencode_status(
                host=worker.host,
                port=worker.port,
                model=worker.model,
                variant=worker.variant,
                pid_file=worker.pid_file,
                log_file=worker.log_file,
            )
            snapshot.append(
                {
                    "name": worker.name,
                    "host": worker.host,
                    "port": worker.port,
                    "model": worker.model,
                    "variant": worker.variant,
                    "assigned_agent_id": worker.assigned_agent_id,
                    "skills": worker.skills or [],
                    "active_tasks": worker.active_tasks,
                    "running": bool(state.get("running")),
                    "attach_url": state.get("attach_url"),
                }
            )
        return snapshot

    def run_task(
        self,
        prompt: str,
        context: str = "",
        agent: AgentDefinition | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        worker = self._acquire_worker(agent, skills or [])
        try:
            return opencode_send(
                prompt,
                context,
                host=worker.host,
                port=worker.port,
                model=agent.model if agent else worker.model,
                variant=agent.variant if agent else worker.variant,
                pid_file=worker.pid_file,
                log_file=worker.log_file,
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
                    worker
                    for worker in self.workers
                    if worker.assigned_agent_id == agent.id and worker.active_tasks < max(1, agent.max_concurrent_tasks)
                ]
                if reusable:
                    target = min(reusable, key=lambda item: item.active_tasks)

            if target is None:
                candidates = [worker for worker in self.workers if worker.active_tasks == 0]
                if not candidates:
                    candidates = list(self.workers)
                target = min(candidates, key=lambda item: item.active_tasks)

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
        for worker in self.workers:
            if worker.port == port:
                return worker
        return None
