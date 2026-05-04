"""
forge.code_intelligence.pyupgrade_ — pyupgrade syntax auto-fixer.

Usage:
    from forge.code_intelligence.pyupgrade_ import run_pyupgrade, ToolResult, Issue
    result = run_pyupgrade(workdir=Path("src"))
"""

from __future__ import annotations

import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = structlog.get_logger(__name__)

# Target: upgrade to Python 3.11+ idioms
PYTHON_TARGET = "py311-plus"


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: int | None
    code: str | None


@dataclass
class ToolResult:
    success: bool
    passed: bool        # always True for pyupgrade (it's a fixer)
    issues: list[Issue]
    raw: str            # stdout — what was changed
    files_modified: int = 0


def run_pyupgrade(workdir: Path) -> ToolResult:
    """
    Run pyupgrade to auto-upgrade Python syntax to the latest idioms.
    Modifies files in-place. Called BEFORE test runs in the Coder TDD loop.
    """
    bin_path = shutil.which("pyupgrade")
    if not bin_path:
        log.warning("pyupgrade_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="pyupgrade: not installed",
        )

    try:
        # --exit-zero-even-if-changed: don't fail if files were modified
        result = subprocess.run(
            [bin_path, f"--{PYTHON_TARGET}", "--exit-zero-even-if-changed", str(workdir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw = (result.stdout or "") + (result.stderr or "")

        # Count files modified (pyupgrade prints nothing by default,
        # prints filenames if --with options enabled)
        files_modified = 0
        if "error" not in raw.lower():
            # pyupgrade modifies in-place; count Python files in workdir
            py_files = list(workdir.rglob("*.py"))
            files_modified = len(py_files)

        return ToolResult(
            success=True,
            passed=True,
            issues=[],
            raw=raw or f"pyupgrade: processed {files_modified} files",
            files_modified=files_modified,
        )

    except subprocess.TimeoutExpired:
        return ToolResult(success=False, passed=True, issues=[], raw="pyupgrade: timeout")
    except Exception as e:
        log.warning("pyupgrade_.run_failed", error=str(e))
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))
