"""
forge.mcp — MCP server discovery and session management.

MCP servers are defined in ~/.forge/mcp_servers.yaml.
Each server is a stdio or HTTP MCP endpoint.
"""

from __future__ import annotations
import yaml
import structlog
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

log = structlog.get_logger(__name__)

DEFAULT_MCP_CONFIG = Path.home() / ".forge" / "mcp_servers.yaml"


@dataclass
class MCPServer:
    name: str
    transport: str  # stdio | http
    command: str
    args: list[str]
    env: dict[str, str]


class MCPConfig:
    """Loaded MCP server definitions."""

    def __init__(self, config_path: Path = DEFAULT_MCP_CONFIG):
        self.config_path = config_path
        self.servers: dict[str, MCPServer] = {}
        self._load()

    def _load(self):
        if not self.config_path.exists():
            return
        with open(self.config_path) as f:
            data = yaml.safe_load(f) or {}
        servers = data.get("mcp_servers", {})
        for name, cfg in servers.items():
            self.servers[name] = MCPServer(
                name=name,
                transport=cfg.get("transport", "stdio"),
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
            )
        log.info("mcp.config_loaded", servers=list(self.servers.keys()))

    def get(self, name: str) -> Optional[MCPServer]:
        return self.servers.get(name)

    def list_all(self) -> list[MCPServer]:
        return list(self.servers.values())


# NOTE: Actual MCP client sessions (connecting to stdio/HTTP servers) require
# the mcp or mcp-server library. This module stores definitions;
# full session management is a later phase (v0.2+).
# For v0.1, MCP is configured but tool calling is stubbed until real session
# support is added.
