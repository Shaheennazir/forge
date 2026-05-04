"""
forge.product_compiler.sandbox — E2B sandbox integration stub.

This module provides an E2B sandbox wrapper for code execution
during the Product Compiler pipeline. Currently a stub — swap in
real E2B SDK calls when an API key is configured.

Usage:
    sandbox = E2BSandbox(enabled=True)
    async with sandbox:
        result = await sandbox.run("python", ["-c", "print('hello')"])
"""

from __future__ import annotations

import json
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = structlog.get_logger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandboxed execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: list[str] = field(default_factory=list)  # paths to generated files

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "artifacts": self.artifacts,
        }


class E2BSandbox:
    """
    E2B sandbox wrapper (stub implementation).

    When `enabled=False` or `e2b_api_key` is None, executes code locally
    as a fallback (no sandbox isolation).

    Real implementation will use:
      from e2b import Sandbox

    Environment variables:
      E2B_API_KEY — API token for E2B cloud (or set via constructor)
    """

    def __init__(
        self,
        enabled: bool = False,
        e2b_api_key: str | None = None,
        template: str = "python3",
        timeout_secs: int = 120,
    ):
        self.enabled = enabled
        self.e2b_api_key = e2b_api_key or _load_e2b_api_key()
        self.template = template
        self.timeout_secs = timeout_secs
        self._sandbox = None

    # ── Context manager ─────────────────────────────────────────────────────────

    def __enter__(self) -> "E2BSandbox":
        if not self.enabled:
            log.info("sandbox.disabled", reason="disabled flag or no API key")
            return self

        if not self.e2b_api_key:
            log.warning("sandbox.disabled", reason="no E2B_API_KEY set — falling back to local exec")
            self.enabled = False
            return self

        # Real E2B SDK:
        # from e2b import Sandbox
        # self._sandbox = Sandbox(template=self.template)
        log.info("sandbox.starting", template=self.template, enabled=self.enabled)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sandbox:
            # self._sandbox.close()
            log.info("sandbox.closed")
        return False

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(self, cmd: str, args: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None) -> SandboxResult:
        """
        Run a command inside the sandbox.

        Currently a stub — returns a successful dummy result.
        Replace with real E2B execution:
            execution = await self._sandbox.run(cmd, args, cwd=cwd, env=env, timeout=self.timeout_secs)
            return SandboxResult(success=execution.success, stdout=execution.stdout, ...)
        """
        if not self.enabled:
            return await self._run_local(cmd, args, cwd=cwd, env=env)

        # Stub: simulate a successful execution
        log.info("sandbox.run_stub", cmd=cmd, args=args)
        return SandboxResult(success=True, stdout=f"[sandbox stub] ran: {cmd} {' '.join(args)}")

    async def _run_local(self, cmd: str, args: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None) -> SandboxResult:
        """Fallback local execution when sandbox is disabled."""
        import asyncio
        import os

        full_cmd = [cmd] + args
        log.info("sandbox.local_exec", cmd=full_cmd, cwd=cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, **(env or {})},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_secs)
            return SandboxResult(
                success=proc.returncode == 0,
                stdout=stdout.decode() if stdout else "",
                stderr=stderr.decode() if stderr else "",
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            log.error("sandbox.local_timeout", cmd=full_cmd, timeout=self.timeout_secs)
            return SandboxResult(success=False, stderr=f"Command timed out after {self.timeout_secs}s", exit_code=-1)
        except Exception as e:
            log.error("sandbox.local_error", cmd=full_cmd, error=str(e))
            return SandboxResult(success=False, stderr=str(e), exit_code=-1)

    # ── File operations ────────────────────────────────────────────────────────

    async def write_file(self, path: str, content: str) -> None:
        """Write a file inside the sandbox."""
        if self._sandbox:
            # await self._sandbox.write_file(path, content)
            pass
        else:
            (Path(path) if not self.enabled else Path("/tmp") / path).write_text(content)
        log.debug("sandbox.write_file", path=path, size=len(content))

    async def read_file(self, path: str) -> str:
        """Read a file from inside the sandbox."""
        if self._sandbox:
            # return await self._sandbox.read_file(path)
            pass
        return (Path(path) if not self.enabled else Path("/tmp") / path).read_text()

    async def list_artifacts(self, dir_path: str = ".") -> list[str]:
        """List files generated by the sandbox."""
        if self._sandbox:
            # return await self._sandbox.list_files(dir_path)
            pass
        return []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_e2b_api_key() -> str | None:
    """Load E2B API key from environment."""
    import os
    return os.environ.get("E2B_API_KEY") or None
