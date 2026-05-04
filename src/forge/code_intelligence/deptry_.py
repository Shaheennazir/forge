"""
forge.code_intelligence.deptry_ — Deptry unused/missing dependency detection.

Usage:
    from forge.code_intelligence.deptry_ import run_deptry, ToolResult, Issue
    result = run_deptry(workdir=Path("src"))
"""

from __future__ import annotations

import json
import shutil
import structlog
import subprocess
import os
import sys
from dataclasses import dataclass
from pathlib import Path

log = structlog.get_logger(__name__)


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: int | None
    code: str | None    # "missing" / "unused" / "transitive"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no MISSING dependencies
    issues: list[Issue]
    raw: str


def run_deptry(workdir: Path) -> ToolResult:
    """
    Run deptry to detect missing, unused, and transitive dependencies.
    Hard gate: missing dependencies block.
    Advisory: unused and transitive are warnings.
    """
    bin_path = shutil.which("deptry")
    if not bin_path:
        log.warning("deptry_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="deptry: not installed",
        )

    json_output = workdir / "deptry_result.json"

    issues: list[Issue] = []

    try:
        result = subprocess.run(
            [bin_path, str(workdir), "--json-output", str(json_output)],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(workdir)},
        )
        raw = (result.stdout or "") + (result.stderr or "")

        if json_output.exists():
            try:
                data = json.loads(json_output.read_text())
                json_output.unlink()

                # Deptry JSON: list of {error: {code, message}, module, location: {file, line}}
                # Codes: DEP001=missing, DEP002=unused, DEP003=transitive
                code_to_severity = {
                    "DEP001": "HIGH",   # missing dependency
                    "DEP002": "MEDIUM", # unused dependency
                    "DEP003": "LOW",    # transitive (advisory)
                }

                for item in data:
                    if not isinstance(item, dict):
                        continue
                    error_info = item.get("error", {})
                    code = error_info.get("code", "")
                    message = error_info.get("message", "")
                    location = item.get("location", {})
                    file_path = location.get("file", "")
                    line_no = location.get("line")

                    severity = code_to_severity.get(code, "MEDIUM")

                    issues.append(Issue(
                        severity=severity,
                        message=message,  # message already includes the code in deptry JSON
                        file=str(file_path),
                        line=line_no,
                        code=code,
                    ))

            except json.JSONDecodeError:
                raw += f"\n[deptry: could not parse JSON output]"

        # If deptry returned non-zero but no JSON (older version)
        if result.returncode != 0 and not issues:
            raw += f"\n[deptry: exit code {result.returncode}]"

        missing = any(i.code == "missing" for i in issues)
        return ToolResult(
            success=True,
            passed=not missing,
            issues=issues,
            raw=raw,
        )

    except subprocess.TimeoutExpired:
        return ToolResult(success=False, passed=True, issues=[], raw="deptry: timeout")
    except Exception as e:
        log.warning("deptry_.run_failed", error=str(e))
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))
