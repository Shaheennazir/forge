"""
forge.mcp — MCP server discovery and session management.

MCP servers are defined in ~/.forge/mcp_servers.yaml.
Each server is a stdio or HTTP MCP endpoint.

v0.1: stdio transport with JSON-RPC 2.0.
"""

from __future__ import annotations
import json
import subprocess
import threading
import time
import uuid
import sys
import os
import yaml
import structlog
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field
from enum import Enum

log = structlog.get_logger(__name__)

DEFAULT_MCP_CONFIG = Path.home() / ".forge" / "mcp_servers.yaml"


class MCPTransport(Enum):
    STDIO = "stdio"
    HTTP = "http"


@dataclass
class MCPServer:
    name: str
    transport: MCPTransport
    command: str
    args: list[str]
    env: dict[str, str]
    base_url: Optional[str] = None


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict


@dataclass
class MCPToolResult:
    content: list[dict]
    is_error: bool = False


class MCPClient:
    """
    JSON-RPC 2.0 MCP client over stdio.

    Usage:
        client = MCPClient(server_config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("bash", {"command": "ls"})
    """

    def __init__(self, server: MCPServer):
        self.server = server
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._stdout_buffer = ""
        self._connected = False
        self._tools: list[MCPTool] = []

    def connect(self) -> list[MCPTool]:
        """Start the MCP server process and initialize."""
        if self._connected:
            return self._tools

        env = {**os.environ, **self.server.env}
        try:
            self._proc = subprocess.Popen(
                [self.server.command] + self.server.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
            )
        except FileNotFoundError as e:
            log.error("mcp.server_not_found", command=self.server.command, error=str(e))
            raise RuntimeError(f"MCP server not found: {self.server.command}")

        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()

        # Send initialize
        init_id = str(uuid.uuid4())
        init_result = self._send_request_sync("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
            "clientInfo": {"name": "forge", "version": "0.1.0"},
        }, req_id=init_id)

        if init_result.get("error"):
            log.error("mcp.init_failed", error=init_result["error"])
            raise RuntimeError(f"MCP init failed: {init_result['error']}")

        # Send initialized notification (no response expected)
        self._send_notification("notifications/initialized", {})

        # List tools
        tools_result = self._send_request_sync("tools/list", {})
        self._tools = []
        for t in tools_result.get("result", {}).get("tools", []):
            self._tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))

        self._connected = True
        log.info("mcp.connected", server=self.server.name, tools=len(self._tools))
        return self._tools

    def _read_stdout(self):
        """Background thread: read stdout, split into JSON-RPC messages."""
        import select
        while self._proc and self._proc.stdout:
            try:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                self._stdout_buffer += chunk.decode("utf-8", errors="replace")
                self._process_buffer()
            except Exception as e:
                log.error("mcp.read_error", error=str(e))
                break

    def _process_buffer(self):
        """Split buffer into complete JSON-RPC messages."""
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.warning("mcp.bad_json", raw=line[:100])
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict):
        """Route a received JSON-RPC message."""
        msg_id = msg.get("id")
        if msg.get("method") in ("notifications/initialized", "notifications/cancelled"):
            return
        if msg_id and msg_id in self._pending:
            event, cached = self._pending.pop(msg_id)
            cached["result"] = msg.get("result", {})
            if msg.get("error"):
                cached["error"] = msg["error"]
            event.set()
        else:
            log.warning("mcp.unsolicited_message", msg=msg)

    def _send_request_sync(self, method: str, params: dict, req_id: str) -> dict:
        """Send a JSON-RPC request and wait for response."""
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError(f"MCP server {self.server.name} is not running")

        event = threading.Event()
        with self._lock:
            self._pending[req_id] = (event, {})
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, IOError) as e:
            log.error("mcp.stdin_error", error=str(e))
            raise RuntimeError(f"MCP server stdin error: {e}")
        event.wait(timeout=30)
        with self._lock:
            cached = self._pending.pop(req_id, (None, {}))[1]
        return cached.get("result", {}) or cached.get("error", {})

    def _send_notification(self, method: str, params: dict):
        """Fire-and-forget JSON-RPC notification."""
        if not self._proc or self._proc.poll() is not None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, IOError):
            pass

    def list_tools(self) -> list[MCPTool]:
        """Return cached list of available tools."""
        return self._tools

    def is_alive(self) -> bool:
        """Return True if the MCP server process is running."""
        return self._proc is not None and self._proc.poll() is None

    def call_tool(self, tool_name: str, arguments: dict) -> MCPToolResult:
        """
        Call an MCP tool by name with arguments.
        Returns MCPToolResult with content list.

        Raises RuntimeError if the server process is not alive.
        """
        if not self._connected:
            raise RuntimeError("MCP client not connected. Call connect() first.")

        if not self.is_alive():
            raise RuntimeError(
                f"MCP server {self.server.name} is not alive (process exited). "
                "Restart the server before calling tools."
            )

        req_id = str(uuid.uuid4())
        result = self._send_request_sync("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, req_id=req_id)

        if "error" in result:
            log.error("mcp.tool_error", tool=tool_name, error=result["error"])
            return MCPToolResult(content=[{"type": "text", "text": f"Error: {result['error']}"}], is_error=True)

        content = result.get("content", [])
        return MCPToolResult(content=content, is_error=False)

    def disconnect(self):
        """Stop the MCP server process."""
        self._connected = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        log.info("mcp.disconnected", server=self.server.name)


class MCPConfig:
    """Loaded MCP server definitions from YAML."""

    def __init__(self, config_path: Path = DEFAULT_MCP_CONFIG):
        self.config_path = config_path
        self.servers: dict[str, MCPServer] = {}
        self._clients: dict[str, MCPClient] = {}
        self._load()

    def _load(self):
        if not self.config_path.exists():
            log.info("mcp.no_config", path=str(self.config_path))
            return
        with open(self.config_path) as f:
            data = yaml.safe_load(f) or {}
        servers = data.get("mcp_servers", {})
        for name, cfg in servers.items():
            transport_str = cfg.get("transport", "stdio")
            transport = MCPTransport.HTTP if transport_str == "http" else MCPTransport.STDIO
            self.servers[name] = MCPServer(
                name=name,
                transport=transport,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                base_url=cfg.get("base_url"),
            )
        log.info("mcp.config_loaded", servers=list(self.servers.keys()))

    def get(self, name: str) -> Optional[MCPServer]:
        return self.servers.get(name)

    def list_all(self) -> list[MCPServer]:
        return list(self.servers.values())

    def create_client(self, name: str) -> Optional[MCPClient]:
        """Create and connect an MCP client for a server."""
        server = self.servers.get(name)
        if not server:
            return None
        if name in self._clients:
            return self._clients[name]
        client = MCPClient(server)
        try:
            client.connect()
            self._clients[name] = client
            return client
        except Exception as e:
            log.error("mcp.client_connect_failed", server=name, error=str(e))
            return None

    def disconnect_all(self):
        """Stop all connected MCP clients."""
        for client in self._clients.values():
            client.disconnect()
        self._clients.clear()
