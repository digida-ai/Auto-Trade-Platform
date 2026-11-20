"""Skills repository: discovery, parsing, and registry (ported/adapted from musashaban skills).

Skills live in <platform>/skills/<name>/SKILL.md with YAML frontmatter + Markdown body.
This module loads them for surfacing in API/UI and for operational guidance (including
sentry/monitoring and live problem resolution). No execution of untrusted code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SKILLS_DIR, ensure_dirs

MAX_TOTAL_INSTRUCTIONS = 120_000


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _strip_quotes(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
        return val[1:-1]
    return val


def parse_frontmatter(text: str) -> Dict[str, Any]:
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {"meta": {}, "body": text.strip()}
    # find closing ---
    m = re.search(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.DOTALL)
    if not m:
        return {"meta": {}, "body": text.strip()}
    fm_raw, body = m.group(1), m.group(2)
    meta: Dict[str, Any] = {}
    for line in fm_raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # simple list: [a, b]
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            if not inner:
                meta[k] = []
            else:
                meta[k] = [_strip_quotes(x) for x in inner.split(",") if x.strip()]
        else:
            meta[k] = _strip_quotes(v)
    return {"meta": meta, "body": body.strip()}


def _skill_dir_to_name(p: Path) -> str:
    return p.name


def discover_skills(skills_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    ensure_dirs()
    root = Path(skills_dir) if skills_dir else SKILLS_DIR
    skills: List[Dict[str, Any]] = []
    if not root.exists():
        return skills
    total = 0
    for child in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            raw = skill_md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        parsed = parse_frontmatter(raw)
        meta = parsed.get("meta") or {}
        body = parsed.get("body") or ""
        name = str(meta.get("name") or _skill_dir_to_name(child)).strip()
        desc = str(meta.get("description") or "").strip()
        tags = meta.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        # cap total instructions surfaced
        remaining = max(0, MAX_TOTAL_INSTRUCTIONS - total)
        instructions = body[:remaining]
        total += len(instructions)
        skills.append({
            "id": name,
            "name": name,
            "description": desc,
            "version": meta.get("version", 1),
            "tags": tags,
            "path": str(skill_md),
            "dir": str(child),
            "instructions": instructions,
            "meta": meta,
        })
        if total >= MAX_TOTAL_INSTRUCTIONS:
            break
    return skills


def get_skill(skill_id: str, skills_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    sid = (skill_id or "").strip().lower()
    for s in discover_skills(skills_dir=skills_dir):
        if s.get("id", "").lower() == sid or s.get("name", "").lower() == sid:
            return s
    return None


def skills_summary(skills_dir: Optional[Path] = None) -> Dict[str, Any]:
    skills = discover_skills(skills_dir=skills_dir)
    return {
        "count": len(skills),
        "skills": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "description": s.get("description"),
                "version": s.get("version"),
                "tags": s.get("tags", []),
                "path": s.get("path"),
            }
            for s in skills
        ],
    }




# ============================================================================
# Agent-Contributable Skills Catalogue
# ============================================================================
#
# Every agent utilizes and adds skills to the skills catalogue.
# Skills are shared across agents; when an agent develops a new capability,
# it contributes it back to the catalogue for other agents to use.


@dataclass
class AgentSkillContribution:
    """A skill contributed by an agent to the catalogue."""

    skill_id: str
    name: str
    description: str
    contributed_by: str  # agent_id
    tags: List[str]
    mcp_tools: List[str]  # MCP tool names this skill uses
    version: int = 1
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    usage_count: int = 0
    last_used_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SkillCatalogue:
    """Skills catalogue that supports agent contributions.

    Every agent can:
      - Use skills from the catalogue (discover, reference by MCP tools)
      * Contribute new skills to the catalogue
    """

    def __init__(self):
        self.skills: Dict[str, AgentSkillContribution] = {}
        self.agent_contributions: Dict[str, List[str]] = {}  # agent_id -> [skill_id, ...]
        self.skill_usage: Dict[str, List[str]] = {}  # skill_id -> [agent_id, ...]

    def contribute_skill(
        self,
        agent_id: str,
        skill_id: str,
        name: str,
        description: str,
        tags: Optional[List[str]] = None,
        mcp_tools: Optional[List[str]] = None,
    ) -> AgentSkillContribution:
        """Agent contributes a new skill to the catalogue."""
        tags = tags or []
        mcp_tools = mcp_tools or []
        contribution = AgentSkillContribution(
            skill_id=skill_id,
            name=name,
            description=description,
            contributed_by=agent_id,
            tags=tags,
            mcp_tools=mcp_tools,
        )
        self.skills[skill_id] = contribution
        self.agent_contributions.setdefault(agent_id, []).append(skill_id)
        return contribution

    def get_skill(self, skill_id: str) -> Optional[AgentSkillContribution]:
        return self.skills.get(skill_id)

    def list_skills(self, agent_id: Optional[str] = None) -> List[AgentSkillContribution]:
        if agent_id:
            ids = self.agent_contributions.get(agent_id, [])
            return [self.skills[sid] for sid in ids if sid in self.skills]
        return list(self.skills.values())

    def use_skill(self, skill_id: str, agent_id: str) -> bool:
        """Record that an agent used a skill."""
        skill = self.skills.get(skill_id)
        if not skill:
            return False
        skill.usage_count += 1
        skill.last_used_by = agent_id
        skill.updated_at = _now()
        self.skill_usage.setdefault(skill_id, []).append(agent_id)
        return True

    def discover_by_mcp_tool(self, mcp_tool_name: str) -> List[AgentSkillContribution]:
        """Find skills that use a given MCP tool."""
        return [
            s for s in self.skills.values() if mcp_tool_name in s.mcp_tools
        ]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_skills": len(self.skills),
            "contributing_agents": len(self.agent_contributions),
            "skills": [
                {
                    "skill_id": s.skill_id,
                    "name": s.name,
                    "contributed_by": s.contributed_by,
                    "tags": s.tags,
                    "mcp_tools": s.mcp_tools,
                    "usage_count": s.usage_count,
                    "last_used_by": s.last_used_by,
                }
                for s in self.skills.values()
            ],
        }


_skill_catalogue = SkillCatalogue()


def get_skill_catalogue() -> SkillCatalogue:
    return _skill_catalogue


def skills_for_context(tag: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    skills = discover_skills()
    if tag:
        t = tag.lower()
        skills = [s for s in skills if t in [str(x).lower() for x in (s.get("tags") or [])] or t in (s.get("name","").lower())]
    return skills[:limit]
