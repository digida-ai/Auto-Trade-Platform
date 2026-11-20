"""Worker lifecycle management.

Agent vs Worker distinction:
  - Agent: long-running entity that creates workers, belongs to a subplatform,
    reports to admin, has MCP client + skills.
  - Worker: ephemeral entity created by an agent to execute a specific job.
    Killed on completion or suspended on job suspension.
    All suspensions reported to the agent, then to the respective admin.

Chain: (planner), (router) -> (admin agent) -> (agent) -> (worker A, Worker B)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Worker:
    """Ephemeral worker created by an agent to execute a specific job."""

    worker_id: str
    agent_id: str
    job_id: str
    purpose: str
    state: str = "created"
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    suspended_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    suspension_reason: Optional[str] = None
    mcp_tools_used: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "agent_id": self.agent_id,
            "job_id": self.job_id,
            "purpose": self.purpose,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "suspended_at": self.suspended_at,
            "result": self.result,
            "error": self.error,
            "suspension_reason": self.suspension_reason,
            "mcp_tools_used": self.mcp_tools_used,
            "skills_used": self.skills_used,
        }


WORKER_STATES = {"created", "running", "suspended", "completed", "killed", "failed"}


class WorkerLifecycle:
    """Manages worker creation, execution, suspension, and completion.

    Workers are created by agents. When a worker completes its job it is killed.
    When a job is suspended, the worker is suspended and the suspension is
    reported to the agent, which then reports to the admin.
    """

    def __init__(self):
        self.workers: Dict[str, Worker] = {}
        self.agent_workers: Dict[str, List[str]] = {}

    def create_worker(
        self,
        agent_id: str,
        job_id: str,
        purpose: str,
        mcp_tools: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
    ) -> Worker:
        worker_id = f"w-{uuid.uuid4().hex[:12]}"
        worker = Worker(
            worker_id=worker_id,
            agent_id=agent_id,
            job_id=job_id,
            purpose=purpose,
            mcp_tools_used=mcp_tools or [],
            skills_used=skills or [],
        )
        self.workers[worker_id] = worker
        self.agent_workers.setdefault(agent_id, []).append(worker_id)
        return worker

    def start_worker(self, worker_id: str) -> Optional[Worker]:
        worker = self.workers.get(worker_id)
        if not worker:
            return None
        worker.state = "running"
        worker.started_at = _now()
        return worker

    def complete_worker(
        self,
        worker_id: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Optional[Worker]:
        worker = self.workers.get(worker_id)
        if not worker:
            return None
        worker.state = "completed"
        worker.completed_at = _now()
        worker.result = result or {}
        worker.state = "killed"
        return worker

    def suspend_worker(
        self,
        worker_id: str,
        reason: str,
    ) -> Optional[Worker]:
        worker = self.workers.get(worker_id)
        if not worker:
            return None
        worker.state = "suspended"
        worker.suspended_at = _now()
        worker.suspension_reason = reason
        return worker

    def kill_worker(self, worker_id: str) -> Optional[Worker]:
        worker = self.workers.get(worker_id)
        if not worker:
            return None
        worker.state = "killed"
        worker.completed_at = _now()
        return worker

    def get_worker(self, worker_id: str) -> Optional[Worker]:
        return self.workers.get(worker_id)

    def list_agent_workers(self, agent_id: str) -> List[Worker]:
        ids = self.agent_workers.get(agent_id, [])
        return [self.workers[wid] for wid in ids if wid in self.workers]

    def list_suspended(self) -> List[Worker]:
        return [w for w in self.workers.values() if w.state == "suspended"]

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {s: 0 for s in WORKER_STATES}
        for w in self.workers.values():
            counts[w.state] = counts.get(w.state, 0) + 1
        return {
            "total_workers": len(self.workers),
            "by_state": counts,
            "suspended_count": len(self.list_suspended()),
            "agents_with_workers": len(self.agent_workers),
        }


_worker_lifecycle = WorkerLifecycle()


def get_worker_lifecycle() -> WorkerLifecycle:
    return _worker_lifecycle