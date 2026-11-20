"""Router layer - routes plans to agents.

Chain position: (planner) -> (router) -> (admin agent) -> (agent) -> (worker A, Worker B)

The router:
  - Receives plans from the planner
  - Routes each plan step to the appropriate agent
  - Tracks agent availability and load
  - Forwards routed plans to the admin agent for execution
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Route:
    """A route from a plan step to an agent."""

    route_id: str
    plan_id: str
    step_id: str
    agent: str
    action: str
    mcp_tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    worker_count: int = 1
    state: str = "routed"  # routed, assigned, executing, completed, failed, suspended

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_id": self.route_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "agent": self.agent,
            "action": self.action,
            "mcp_tools": self.mcp_tools,
            "skills": self.skills,
            "worker_count": self.worker_count,
            "state": self.state,
        }


@dataclass
class RoutedPlan:
    """A plan that has been routed to agents."""

    plan_id: str
    goal: str
    routes: List[Route] = field(default_factory=list)
    state: str = "routed"  # routed, executing, completed, failed, suspended
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "routes": [r.to_dict() for r in self.routes],
            "state": self.state,
            "created_at": self.created_at,
        }


class Router:
    """Routes plan steps to agents."""

    def __init__(self):
        self.routes: Dict[str, Route] = {}
        self.routed_plans: Dict[str, RoutedPlan] = {}
        self.agent_load: Dict[str, int] = {}  # agent -> active route count

    def route_plan(self, plan) -> RoutedPlan:
        """Route all steps of a plan to agents."""
        routed_plan = RoutedPlan(
            plan_id=plan.plan_id,
            goal=plan.goal,
        )

        for step in plan.steps:
            agent = step.agent
            self.agent_load[agent] = self.agent_load.get(agent, 0) + 1

            route = Route(
                route_id=f"route-{uuid.uuid4().hex[:12]}",
                plan_id=plan.plan_id,
                step_id=step.step_id,
                agent=agent,
                action=step.action,
                mcp_tools=step.mcp_tools,
                skills=step.skills,
                worker_count=step.worker_count,
            )
            self.routes[route.route_id] = route
            routed_plan.routes.append(route)

        self.routed_plans[plan.plan_id] = routed_plan
        return routed_plan

    def get_route(self, route_id: str) -> Optional[Route]:
        return self.routes.get(route_id)

    def get_routed_plan(self, plan_id: str) -> Optional[RoutedPlan]:
        return self.routed_plans.get(plan_id)

    def list_routes_for_agent(self, agent: str) -> List[Route]:
        return [r for r in self.routes.values() if r.agent == agent]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_routes": len(self.routes),
            "total_routed_plans": len(self.routed_plans),
            "agent_load": dict(self.agent_load),
            "routes_by_state": {
                s: len([r for r in self.routes.values() if r.state == s])
                for s in ("routed", "assigned", "executing", "completed", "failed", "suspended")
            },
        }


_router = Router()


def get_router() -> Router:
    return _router