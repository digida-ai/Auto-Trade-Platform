"""Planner layer - produces execution plans from goals/tasks.

Chain position: (planner) -> (router) -> (admin agent) -> (agent) -> (worker A, Worker B)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanStep:
    """A single step in an execution plan."""

    step_id: str
    agent: str
    action: str
    purpose: str
    mcp_tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    worker_count: int = 1
    depends_on: List[str] = field(default_factory=list)
    state: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "agent": self.agent,
            "action": self.action,
            "purpose": self.purpose,
            "mcp_tools": self.mcp_tools,
            "skills": self.skills,
            "worker_count": self.worker_count,
            "depends_on": self.depends_on,
            "state": self.state,
        }


class Planner:
    """Produces execution plans from goals.

    Given a goal, the planner:
      1. Decomposes it into steps
      2. Assigns each step to the appropriate agent (based on role/capability)
      3. Identifies required MCP tools and skills
      4. Estimates worker count per step
    """

    def create_plan(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Plan:
        plan_id = f"plan-{uuid.uuid4().hex[:12]}"
        context = context or {}

        steps = self._decompose(goal, context)

        required_caps = set()
        total_workers = 0
        for step in steps:
            required_caps.update(step.mcp_tools)
            required_caps.update(step.skills)
            total_workers += step.worker_count

        plan = Plan(
            plan_id=plan_id,
            goal=goal,
            steps=steps,
            required_capabilities=sorted(required_caps),
            estimated_workers=total_workers,
        )
        return plan

    def _decompose(
        self,
        goal: str,
        context: Dict[str, Any],
    ) -> List[PlanStep]:
        """Decompose a goal into execution steps."""
        steps: List[PlanStep] = []
        goal_lower = goal.lower()

        agent_map = [
            ("freellmapi", ["inference", "llm", "model", "generate text", "completion"],
             ["inference"], [], 1),
            ("agent-desktop", ["desktop", "automation", "ui", "window", "click", "screen"],
             ["accessibility_tree", "desktop_automation"], [], 1),
            ("agentic-inbox", ["email", "inbox", "message", "route", "notify"],
             ["email_routing", "internal_comms"], [], 1),
            ("AutoHedge", ["hedge", "arbitrage", "spread", "trade"],
             ["market_data", "trade_execution"], [], 1),
            ("Pix-Payment-System", ["payment", "pix", "mobile money", "transfer", "settle"],
             ["payment_processing", "settlement"], [], 1),
            ("Scrapling", ["scrape", "web", "extract", "crawl"],
             ["web_scraping", "data_extraction"], [], 1),
            ("claude-ads", ["ad", "campaign", "advertising", "copy"],
             ["ad_generation", "campaign_management"], [], 1),
            ("hyperframes", ["video", "render", "frame", "generate video"],
             ["video_generation"], [], 1),
            ("LibreChat", ["chat", "conversation", "chatbot"],
             ["chat_hosting"], [], 1),
            ("Open-Higgsfield-AI", ["image", "generate image", "media"],
             ["image_generation"], [], 1),
            ("Open-LLM-VTuber", ["avatar", "voice", "vtuber", "streaming"],
             ["avatar", "voice_synthesis"], [], 1),
            ("grok-jev-router", ["decision", "route", "irreversible", "approve"],
             ["decision_routing"], [], 1),
            ("FinceptTerminal", ["terminal", "research", "broker", "enterprise"],
             ["research", "broker_integration"], [], 1),
            ("spider-ai--trading-bot", ["copy trading", "latency", "snipe"],
             ["copy_trading", "arbitrage"], [], 1),
            ("vibetrading", ["signal", "trading strategy", "vibe"],
             ["trading_signals"], [], 1),
        ]

        for agent_name, keywords, tools, skills, worker_count in agent_map:
            if any(kw in goal_lower for kw in keywords):
                step = PlanStep(
                    step_id=f"step-{uuid.uuid4().hex[:8]}",
                    agent=agent_name,
                    action=f"Execute {agent_name} capability for: {goal[:60]}",
                    purpose=f"Support goal: {goal}",
                    mcp_tools=tools,
                    skills=skills,
                    worker_count=worker_count,
                )
                steps.append(step)

        if not steps:
            steps.append(PlanStep(
                step_id=f"step-{uuid.uuid4().hex[:8]}",
                agent="admin",
                action=f"Admin review required for: {goal[:60]}",
                purpose="No specific agent matched; admin must decide routing",
                mcp_tools=[],
                skills=[],
                worker_count=1,
            ))

        return steps


@dataclass
class Plan:
    """Execution plan produced by the planner."""

    plan_id: str
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    estimated_workers: int = 0
    created_at: str = field(default_factory=_now)
    state: str = "draft"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "required_capabilities": self.required_capabilities,
            "estimated_workers": self.estimated_workers,
            "created_at": self.created_at,
            "state": self.state,
        }