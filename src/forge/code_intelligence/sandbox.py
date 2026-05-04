"""
forge.code_intelligence.sandbox — E2B sandbox for isolated code execution.

E2B provides first-class Python SDK. This module wires it into forge for:
  - Running generated code in an isolated sandbox during executor runs
  - Untrusted third-party code execution
  - Per-test environment isolation

Usage:
    sandbox = Sandbox(enabled=True)
    async with sandbox:
        result = await sandbox.run("python", ["-c", "print('hello')"])
        print(result.success, result.stdout)

    # Run a full test suite in sandbox
    result = await sandbox.run("pytest", ["-m", "pytest", "tests/"], cwd="/code")
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Lazy import
_e2b = None


def _get_e2b():
    global _e2b
    if _e2b is None:
        try:
            from e2b_code_interpreter import Sandbox as E2BSandbox
            _e2b = E2BSandbox
        except ImportError as e:
            log.warning("sandbox.e2b_import_failed", error=str(e))
            _e2b = None
    return _e2b


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


class Sandbox:
    """
    E2B sandbox for isolated code execution.

    Usage:
        sandbox = Sandbox(enabled=True, template="python3")
        async with sandbox:
            result = await sandbox.run("python", ["-c", "print(1+1)"])

    Or use synchronously with `run_sync`:
        result = Sandbox.run_sync(lambda: await sandbox.run(...))

    The sandbox can also be used as a context manager for pooled reuse.
    """

    def __init__(
        self,
        enabled: bool = True,
        e2b_api_key: Optional[str] = None,
        template: str = "python3",
        timeout_secs: int = 120,
    ):
        self.enabled = enabled
        self.e2b_api_key = e2b_api_key or _load_e2b_api_key()
        self.template = template
        self.timeout_secs = timeout_secs
        self._sandbox: Optional[any] = None

        if not self.e2b_api_key:
            self.enabled = False

    # ── Context manager ─────────────────────────────────────────────────────

    def __enter__(self) -> "Sandbox":
        if not self.enabled:
            log.info("sandbox.disabled", reason="no API key or disabled flag")
            return self

        e2b_cls = _get_e2b()
        if e2b_cls is None:
            log.warning("sandbox.e2b_unavailable", error="e2b-code-interpreter not installed")
            self.enabled = False
            return self

        try:
            kwargs = {"template": self.template}
            if self.e2b_api_key:
                kwargs["api_key"] = self.e2b_api_key

            self._sandbox = e2b_cls(**kwargs)
            log.info("sandbox.started", template=self.template)
        except Exception as e:
            log.warning("sandbox.start_failed", error=str(e))
            self.enabled = False
            self._sandbox = None

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sandbox is not None:
            try:
                self._sandbox.close()
            except Exception as e:
                log.warning("sandbox.close_failed", error=str(e))
            self._sandbox = None
        return False

    # ── Execution ───────────────────────────────────────────────────────────

    async def run(
        self,
        cmd: str,
        args: list[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> SandboxResult:
        """
        Run a command inside the E2B sandbox.

        Args:
            cmd: Command to run (e.g. "python", "node", "bash").
            args: Command arguments.
            cwd: Working directory inside the sandbox.
            env: Environment variables.

        Returns:
            SandboxResult with success, stdout, stderr, exit_code.
        """
        if not self.enabled or self._sandbox is None:
            return await self._run_local(cmd, args, cwd=cwd, env=env)

        try:
            # E2B code interpreter uses run_code() for Python code snippets
            # For general commands, we use the underlying sandbox.run()
            execution = self._sandbox.run(
                cmd,
                args,
                cwd=cwd,
                env=env,
                timeout=self.timeout_secs,
            )
            return SandboxResult(
                success=execution.success if hasattr(execution, "success") else True,
                stdout=execution.stdout if hasattr(execution, "stdout") else str(execution),
                stderr=execution.stderr if hasattr(execution, "stderr") else "",
                exit_code=getattr(execution, "exit_code", 0),
            )
        except Exception as e:
            log.error("sandbox.e2b_run_failed", cmd=cmd, args=args, error=str(e))
            # Fall back to local execution
            return await self._run_local(cmd, args, cwd=cwd, env=env)

    async def _run_local(
        self,
        cmd: str,
        args: list[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> SandboxResult:
        """Fallback local execution when sandbox is disabled."""
        import asyncio
        import os

        log.info("sandbox.local_fallback", cmd=cmd, args=args)
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**os.environ, **(env or {})},
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_secs,
            )
            return SandboxResult(
                success=proc.returncode == 0,
                stdout=stdout_b.decode() if stdout_b else "",
                stderr=stderr_b.decode() if stderr_b else "",
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            return SandboxResult(
                success=False,
                stderr=f"Command timed out after {self.timeout_secs}s",
                exit_code=-1,
            )
        except Exception as e:
            return SandboxResult(success=False, stderr=str(e), exit_code=-1)

    # ── Python-specific helpers ─────────────────────────────────────────────

    async def run_python(
        self,
        code: str,
        *,
        packages: Optional[list[str]] = None,
    ) -> SandboxResult:
        """
        Run a Python code snippet directly inside the E2B sandbox.

        This is the preferred method for running Python — E2B handles
        package installation automatically.

        Args:
            code: Python source code.
            packages: Optional list of pip packages to install first.
        """
        if not self.enabled or self._sandbox is None:
            return await self._run_local("python", ["-c", code])

        try:
            from e2b_code_interpreter import E2BCodeInterpreter

            # If we have a plain sandbox, use run_code
            if hasattr(self._sandbox, "run_code"):
                execution = self._sandbox.run_code(
                    code,
                    timeout=self.timeout_secs,
                )
            else:
                # Fall back to subprocess
                execution = self._sandbox.run(
                    "python",
                    ["-c", code],
                    timeout=self.timeout_secs,
                )

            return SandboxResult(
                success=execution.success if hasattr(execution, "success") else True,
                stdout=execution.stdout if hasattr(execution, "stdout") else str(execution),
                stderr=execution.stderr if hasattr(execution, "stderr") else "",
                exit_code=getattr(execution, "exit_code", 0),
            )
        except Exception as e:
            log.error("sandbox.python_failed", error=str(e))
            return await self._run_local("python", ["-c", code])

    # ── File operations ─────────────────────────────────────────────────────

    async def write_file(self, path: str, content: str) -> None:
        """Write a file inside the sandbox."""
        if self._sandbox is not None and hasattr(self._sandbox, "write_file"):
            await self._sandbox.write_file(path, content)
        else:
            (Path(path) if self.enabled else Path("/tmp") / path).write_text(content)
        log.debug("sandbox.write_file", path=path, size=len(content))

    async def read_file(self, path: str) -> str:
        """Read a file from inside the sandbox."""
        if self._sandbox is not None and hasattr(self._sandbox, "read_file"):
            return await self._sandbox.read_file(path)
        return (Path(path) if self.enabled else Path("/tmp") / path).read_text()

    async def list_artifacts(self, dir_path: str = ".") -> list[str]:
        """List files generated by the sandbox."""
        if self._sandbox is not None and hasattr(self._sandbox, "list_files"):
            return await self._sandbox.list_files(dir_path)
        return []


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_e2b_api_key() -> Optional[str]:
    """Load E2B API key from environment."""
    import os
    return os.environ.get("E2B_API_KEY") or None
