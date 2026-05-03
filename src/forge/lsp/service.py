"""LSP service — unified interface to language servers for a project."""

from __future__ import annotations
import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from forge.lsp.client import LSPClient, LSPError
from forge.lsp.server import LSPServer, detect_servers

log = structlog.get_logger(__name__)


@dataclass
class LSPDiagnostic:
    file: str
    line: int
    column: int
    severity: str
    message: str
    source: str


@dataclass
class LSPLocation:
    file: str
    line: int
    column: int
    end_line: Optional[int] = None
    end_column: Optional[int] = None


class LSPService:
    """
    Unified LSP interface for forge.
    Spawns appropriate server(s) based on project language.
    """

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._servers = detect_servers(workdir)
        self._clients: dict[str, LSPClient] = {}

    @property
    def capabilities(self) -> dict:
        return {}

    def _detect_language(self, file: str) -> str:
        ext = Path(file).suffix.lower()
        if ext in (".ts", ".tsx"):
            return "typescript"
        if ext in (".js", ".jsx", ".mjs"):
            return "javascript"
        if ext == ".py":
            return "python"
        return "unknown"

    def _client_for_file(self, file: str) -> Optional[LSPClient]:
        """Get or spawn the appropriate LSP client for a file."""
        ext = Path(file).suffix
        for server in self._servers:
            if ext in server.extensions:
                if server.id not in self._clients:
                    try:
                        self._clients[server.id] = LSPClient(server.command, self.workdir)
                    except Exception as e:
                        log.warning("lsp.spawn_failed", server=server.id, error=str(e))
                        return None
                return self._clients[server.id]
        return None

    def hover(self, file: str, line: int, character: int) -> Optional[str]:
        """Get hover documentation for a position."""
        client = self._client_for_file(file)
        if not client:
            return None
        result = client.hover(file, line, character)
        if result and "contents" in result:
            contents = result["contents"]
            if isinstance(contents, str):
                return contents
            return contents.get("value", str(contents))
        return None

    def definition(self, file: str, line: int, character: int) -> list[LSPLocation]:
        """Find definition(s) of a symbol."""
        client = self._client_for_file(file)
        if not client:
            return []
        results = client.definition(file, line, character)
        return [self._parse_location(r) for r in results]

    def references(self, file: str, line: int, character: int) -> list[LSPLocation]:
        """Find all references to a symbol."""
        client = self._client_for_file(file)
        if not client:
            return []
        results = client.references(file, line, character)
        return [self._parse_location(r) for r in results]

    def _parse_location(self, loc: dict) -> LSPLocation:
        """Parse an LSP Location object into our format."""
        uri = loc.get("uri", "")
        try:
            path = str(Path(uri).path)
        except Exception:
            path = uri
        pos = loc.get("range", {}).get("start", {})
        end = loc.get("range", {}).get("end", {})
        return LSPLocation(
            file=path,
            line=pos.get("line", 0),
            column=pos.get("character", 0),
            end_line=end.get("line"),
            end_column=end.get("character"),
        )

    def shutdown(self):
        """Stop all LSP clients."""
        for client in self._clients.values():
            try:
                client.shutdown()
            except Exception:
                pass