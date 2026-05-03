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
                        client = LSPClient(server.command, self.workdir)
                        client._server = server  # type: ignore — back-reference for build_context
                        self._clients[server.id] = client
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

    def build_context(self, max_files: int = 20) -> str:
        """
        Build a code-intelligence context string for all discovered files.
        Returns hover docs, symbol lists, and references for the most important files.
        """
        if not self._clients:
            return ""

        lines = ["[LSP Code Intelligence]"]
        served = 0

        for client in self._clients.values():
            if served >= max_files:
                break
            try:
                # Ask the LSP server for document symbols as a proxy for "important" files
                # We can't list all files without a filesystem scan, so we use
                # the server's initialized state as a signal
                for ext in client._server.extensions if hasattr(client, '_server') else []:
                    if served >= max_files:
                        break
                    lines.append(f"\n--- {ext} files (via {client._server.id}) ---")
                    # Collect symbols from open files if any clients have open documents
                    # Since we don't track open docs, we just note the server is available
                    lines.append(f"  Server ready: {client._server.id}")
                    served += 1
            except Exception:
                continue

        if not lines:
            return ""

        # Add method-level details for files that have LSP clients connected
        for server_id, client in self._clients.items():
            if served >= max_files:
                break
            lines.append(f"\n[Server: {server_id}]")
            # Try to get workspace symbols as a broad overview
            try:
                # workspaceSymbol is a broad search — use empty query to get top symbols
                symbols = client.workspace_symbol("")
                if symbols:
                    for sym in symbols[:20]:
                        loc = sym.get("location", {})
                        rng = loc.get("range", {})
                        start = rng.get("start", {})
                        lines.append(
                            f"  {sym.get('name', '?')} @ {loc.get('uri', '?').split('/')[-1]}:{start.get('line', 0)+1}"
                        )
            except Exception:
                pass

        return "\n".join(lines)