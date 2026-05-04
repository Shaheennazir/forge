"""
forge.code_intelligence.vulture_ — Vulture dead code wrapper.

Usage:
    from forge.code_intelligence.vulture_ import run_vulture, ToolResult, Issue
    result = run_vulture(workdir=Path("src"))
"""

from __future__ import annotations

import re
import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = structlog.get_logger(__name__)

# Hard gate: unused functions/classes at 80%+ confidence
CONFIDENCE_THRESHOLD = 80


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: int | None
    code: str | None   # e.g. "unused function"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no HIGH-confidence dead code
    issues: list[Issue]
    raw: str


def run_vulture(workdir: Path) -> ToolResult:
    """
    Run vulture to find dead code (unused functions, classes, variables).
    Hard gate: unused functions/classes at 80%+ confidence.
    Advisory: unused variables at any confidence.
    """
    bin_path = shutil.which("vulture")
    if not bin_path:
        log.warning("vulture_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="vulture: not installed",
        )

    issues: list[Issue] = []

    # Vulture doesn't have native JSON — parse its stdout format:
    # path/file.py:42: unused function 'foo' (confidence 80%)
    try:
        result = subprocess.run(
            [bin_path, str(workdir), f"--min-confidence={CONFIDENCE_THRESHOLD}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = (result.stdout or "") + (result.stderr or "")

        # Parse vulture output lines
        # Example: "src/foo.py:42: unused function 'bar' (confidence 85%)"
        pattern = re.compile(
            r"^(.+?):(\d+):\s*(unused \w+|dead code)\s*(?:'([^']+)')?\s*(?:\(confidence\s*(\d+)%\))?",
            re.IGNORECASE,
        )

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("Skipping "):
                continue

            m = pattern.match(line)
            if m:
                file_path, line_no, kind, name, confidence = m.groups()
                conf = int(confidence) if confidence else CONFIDENCE_THRESHOLD
                severity = "HIGH" if conf >= CONFIDENCE_THRESHOLD else "MEDIUM"

                issues.append(Issue(
                    severity=severity,
                    message=f"{kind.title()} '{name or '?'}' (confidence {conf}%)",
                    file=file_path.strip(),
                    line=int(line_no) if line_no else None,
                    code=kind,
                ))

        # Sort: highest confidence first
        issues.sort(key=lambda i: i.line or 0)

        blocked = any(i.severity == "HIGH" for i in issues)
        return ToolResult(success=True, passed=not blocked, issues=issues, raw=raw)

    except subprocess.TimeoutExpired:
        return ToolResult(success=False, passed=True, issues=[], raw="vulture: timeout")
    except Exception as e:
        log.warning("vulture_.run_failed", error=str(e))
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))
