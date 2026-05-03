"""LSP server spawning for pyright and tsserver."""

from __future__ import annotations
import shutil
import structlog
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


class LSPServer:
    """Describes how to spawn a specific LSP server."""

    def __init__(self, id: str, command: list[str], extensions: list[str]):
        self.id = id
        self.command = command
        self.extensions = extensions  # file extensions this server handles

    def available(self) -> bool:
        """Check if the server binary is installed."""
        if not self.command:
            return False
        binary = Path(self.command[0]).name
        return shutil.which(binary) is not None


# Known LSP servers
PYRIGHT = LSPServer(
    id="pyright",
    command=["pyright", "--stdio"],
    extensions=[".py"],
)

TSSERVER = LSPServer(
    id="tsserver",
    command=["typescript-language-server", "--stdio"],
    extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs"],
)


def detect_servers(workdir: Path) -> list[LSPServer]:
    """Detect which LSP servers are available and applicable for the project."""
    servers = []

    has_ts = any(workdir.rglob(f"*.{ext}") for ext in [".ts", ".tsx", ".js", ".jsx"])
    if has_ts and TSSERVER.available():
        servers.append(TSSERVER)

    has_py = list(workdir.rglob("*.py"))
    if has_py and PYRIGHT.available():
        servers.append(PYRIGHT)

    return servers