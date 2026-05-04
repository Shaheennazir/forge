"""
forge.code_intelligence.bandit_ — Bandit security analysis wrapper.

Usage:
    from forge.code_intelligence.bandit_ import run_bandit, ToolResult, Issue
    result = run_bandit(workdir=Path("src"))
"""

from __future__ import annotations

import json
import shutil
import structlog
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW / CRITICAL
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]  # e.g. "B301"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no HIGH/CRITICAL issues
    issues: list[Issue]
    raw: str            # raw stdout for debugging


def run_bandit(workdir: Path) -> ToolResult:
    """
    Run bandit security analysis on the given directory.
    Hard gate: any HIGH or CRITICAL severity issue blocks the pipeline.
    """
    bin_path = shutil.which("bandit")
    if not bin_path:
        log.warning("bandit_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="bandit: not installed",
        )

    issues: list[Issue] = []
    try:
        result = subprocess.run(
            [bin_path, "-r", str(workdir), "-f", "json", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = result.stdout or ""

        if not raw.strip():
            return ToolResult(success=True, passed=True, issues=[], raw=raw)

        report = json.loads(raw)
        for item in report.get("results", []):
            severity = item.get("issue_severity", "LOW")
            issues.append(Issue(
                severity=severity.upper(),
                message=item.get("issue_text", "")[:200],
                file=item.get("filename", ""),
                line=item.get("line_number"),
                code=item.get("test_id"),
            ))

        blocked = any(i.severity in ("HIGH", "CRITICAL") for i in issues)
        return ToolResult(success=True, passed=not blocked, issues=issues, raw=raw)

    except subprocess.TimeoutExpired:
        return ToolResult(success=False, passed=True, issues=[], raw="bandit: timeout")
    except json.JSONDecodeError as e:
        return ToolResult(success=False, passed=True, issues=[], raw=f"bandit: JSON parse error: {e}")
    except Exception as e:
        log.warning("bandit_.run_failed", error=str(e))
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))
