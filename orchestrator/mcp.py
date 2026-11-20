"""MCP (Model Context Protocol) client/server infrastructure.

SINGLE SHARED MCP SERVER for the entire platform:
  - One MCP server manages all tools, data resources, and prompts
  - Centralized rate limiting and load management
  - All agents are MCP clients connecting to this single server
  - Chain: (agent -> mcp client) -> (single mcp server -> (tools, data, prompts))
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import PLATFORM_ROOT, SKILLS_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Single shared server ID for the whole platform
SHARED_SERVER_ID = "mcp-platform"


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_id: str = SHARED_SERVER_ID
    agent_id: str = ""
    # Rate limiting metadata
    rate_limit_per_minute: int = 60
    calls_in_window: int = 0
    window_start: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def check_rate_limit(self) -> bool:
        """Check if this tool can be called (centralized rate limiting)."""
        now = datetime.now(timezone.utc)
        window_start_dt = datetime.fromisoformat(self.window_start)
        if (now - window_start_dt).total_seconds() >= 60:
            # Reset window
            self.window_start = _now()
            self.calls_in_window = 0
        if self.calls_in_window >= self.rate_limit_per_minute:
            return False
        self.calls_in_window += 1
        return True


@dataclass
class MCPDataResource:
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    server_id: str = SHARED_SERVER_ID
    agent_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCPPrompt:
    name: str
    description: str
    template: str
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    server_id: str = SHARED_SERVER_ID
    agent_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MCPServerRegistry:
    """Single shared MCP server registry for the entire platform.

    All agents connect to this one server. Centralized rate limiting
    and load management happen here.
    """

    def __init__(self):
        # One shared server for the whole platform
        self.server_id = SHARED_SERVER_ID
        self.server_info: Dict[str, Any] = {
            "server_id": SHARED_SERVER_ID,
            "status": "active",
            "registered_at": _now(),
        }
        self.tools: Dict[str, MCPTool] = {}
        self.data: Dict[str, MCPDataResource] = {}
        self.prompts: Dict[str, MCPPrompt] = {}
        # Track which agents use which capabilities (for observability)
        self.agent_tool_usage: Dict[str, List[str]] = {}  # agent_id -> [tool_names]

    def register_capabilities(
        self,
        capabilities: Dict[str, Any],
    ) -> None:
        """Register all platform capabilities under the single shared server."""
        for t in capabilities.get("tools", []):
            tool = MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_id=SHARED_SERVER_ID,
                agent_id=t.get("agent_id", ""),
                rate_limit_per_minute=t.get("rate_limit_per_minute", 60),
            )
            self.tools[t["name"]] = tool

        for r in capabilities.get("data", []):
            res = MCPDataResource(
                uri=r["uri"],
                name=r["name"],
                description=r.get("description", ""),
                mime_type=r.get("mimeType", "application/json"),
                server_id=SHARED_SERVER_ID,
                agent_id=r.get("agent_id", ""),
            )
            self.data[r["uri"]] = res

        for p in capabilities.get("prompts", []):
            prompt = MCPPrompt(
                name=p["name"],
                description=p.get("description", ""),
                template=p["template"],
                arguments=p.get("arguments", []),
                server_id=SHARED_SERVER_ID,
                agent_id=p.get("agent_id", ""),
            )
            self.prompts[p["name"]] = prompt

    def register_tool_for_agent(
        self,
        tool_name: str,
        agent_id: str,
        description: str,
        input_schema: Optional[Dict[str, Any]] = None,
        rate_limit_per_minute: int = 60,
    ) -> None:
        """Register a single tool (used by an agent) on the shared server."""
        self.tools[tool_name] = MCPTool(
            name=tool_name,
            description=description,
            input_schema=input_schema or {},
            server_id=SHARED_SERVER_ID,
            agent_id=agent_id,
            rate_limit_per_minute=rate_limit_per_minute,
        )

    def get_tool(self, name: str) -> Optional[MCPTool]:
        return self.tools.get(name)

    def list_tools(self, agent_id: Optional[str] = None) -> List[MCPTool]:
        if agent_id:
            return [t for t in self.tools.values() if t.agent_id == agent_id]
        return list(self.tools.values())

    def list_data(self, agent_id: Optional[str] = None) -> List[MCPDataResource]:
        if agent_id:
            return [r for r in self.data.values() if r.agent_id == agent_id]
        return list(self.data.values())

    def list_prompts(self, agent_id: Optional[str] = None) -> List[MCPPrompt]:
        if agent_id:
            return [p for p in self.prompts.values() if p.agent_id == agent_id]
        return list(self.prompts.values())

    def record_tool_usage(self, agent_id: str, tool_name: str) -> None:
        """Record that an agent used a tool (for observability/rate tracking)."""
        self.agent_tool_usage.setdefault(agent_id, []).append(tool_name)

    def summary(self) -> Dict[str, Any]:
        return {
            "server_id": SHARED_SERVER_ID,
            "servers": 1,  # Single shared server
            "tools": len(self.tools),
            "data_resources": len(self.data),
            "prompts": len(self.prompts),
            "servers_detail": [
                {
                    "server_id": SHARED_SERVER_ID,
                    "status": self.server_info["status"],
                    "type": "shared",
                }
            ],
            "agent_tool_usage": {
                agent: len(tools) for agent, tools in self.agent_tool_usage.items()
            },
        }


_mcp_registry = MCPServerRegistry()


def get_mcp_registry() -> MCPServerRegistry:
    return _mcp_registry


class MCPClient:
    """MCP client for an agent connecting to the single shared MCP server.

    All agents use this same client to access the shared MCP server.
    Rate limiting is enforced centrally on the server side.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.server_id = SHARED_SERVER_ID
        self.connected = False

    def connect(self) -> bool:
        """Connect to the single shared MCP server."""
        self.connected = True
        return True

    def discover_tools(self) -> List[MCPTool]:
        """Discover all tools available on the shared server."""
        registry = get_mcp_registry()
        return registry.list_tools()

    def discover_data(self) -> List[MCPDataResource]:
        """Discover all data resources on the shared server."""
        registry = get_mcp_registry()
        return registry.list_data()

    def discover_prompts(self) -> List[MCPPrompt]:
        """Discover all prompts on the shared server."""
        registry = get_mcp_registry()
        return registry.list_prompts()

    def invoke_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke a tool through the shared MCP server with rate limiting."""
        registry = get_mcp_registry()
        tool = registry.get_tool(tool_name)
        if not tool:
            return {"ok": False, "error": f"Tool '{tool_name}' not found"}

        # Centralized rate limiting on the shared server
        if not tool.check_rate_limit():
            return {
                "ok": False,
                "error": f"Rate limit exceeded for tool '{tool_name}'",
                "tool": tool_name,
                "server_id": SHARED_SERVER_ID,
            }

        # Record usage for observability
        registry.record_tool_usage(self.agent_id, tool_name)

        return {
            "ok": True,
            "tool": tool_name,
            "server_id": SHARED_SERVER_ID,
            "agent_id": tool.agent_id,
            "arguments": arguments,
            "invoked_at": _now(),
            "invoked_by": self.agent_id,
        }

    def summary(self) -> Dict[str, Any]:
        registry = get_mcp_registry()
        return {
            "agent_id": self.agent_id,
            "server_id": SHARED_SERVER_ID,
            "connected": self.connected,
            "available_tools": len(registry.list_tools(self.agent_id)),
            "available_data": len(registry.list_data(self.agent_id)),
            "available_prompts": len(registry.list_prompts(self.agent_id)),
            "registry_summary": registry.summary(),
        }


_mcp_clients: Dict[str, MCPClient] = {}


def get_mcp_client(agent_id: str) -> MCPClient:
    if agent_id not in _mcp_clients:
        _mcp_clients[agent_id] = MCPClient(agent_id)
    return _mcp_clients[agent_id]
