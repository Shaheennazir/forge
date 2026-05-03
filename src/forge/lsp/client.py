"""LSP JSON-RPC client over stdio."""

from __future__ import annotations
import json
import structlog
import subprocess
from pathlib import Path
from typing import Any, Optional

log = structlog.get_logger(__name__)


class LSPError(Exception):
    """LSP-specific errors."""
    pass


class LSPClient:
    """
    Minimal LSP client using stdio transport.
    Communicates via JSON-RPC 2.0 over subprocess stdin/stdout.
    """

    def __init__(self, server_command: list[str], workdir: Path):
        self.workdir = workdir
        self._proc = subprocess.Popen(
            server_command,
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        self._request_id = 0
        self._initialize()

    def _initialize(self):
        """Send LSP initialize request."""
        result = self.send_request("initialize", {
            "processId": None,
            "rootUri": self.workdir.as_uri(),
            "capabilities": {},
        })
        self.send_notification("initialized", {})

    def send_request(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and wait for response."""
        req_id = self._request_id
        self._request_id += 1
        message = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._proc.stdin.write(message.encode() + b"\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise LSPError(f"No response for {method}")
        response = json.loads(line.decode())
        if "error" in response:
            raise LSPError(f"LSP error: {response['error']}")
        return response.get("result")

    def send_notification(self, method: str, params: dict):
        """Fire-and-forget notification (no response)."""
        message = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(message.encode() + b"\n")
        self._proc.stdin.flush()

    def shutdown(self):
        """Send shutdown and terminate."""
        try:
            self.send_request("shutdown", {})
        except Exception:
            pass
        self._proc.terminate()

    def hover(self, file: str, line: int, character: int) -> Optional[dict]:
        """Get hover info at a position."""
        try:
            return self.send_request("textDocument/hover", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
            })
        except LSPError:
            return None

    def definition(self, file: str, line: int, character: int) -> list[dict]:
        """Go to definition."""
        try:
            result = self.send_request("textDocument/definition", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
            })
            return result if isinstance(result, list) else [result] if result else []
        except LSPError:
            return []

    def references(self, file: str, line: int, character: int) -> list[dict]:
        """Find all references."""
        try:
            result = self.send_request("textDocument/references", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            })
            return result if isinstance(result, list) else []
        except LSPError:
            return []