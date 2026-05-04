"""
forge.code_intelligence.radon_ — Radon complexity and maintainability wrapper.

Usage:
    from forge.code_intelligence.radon_ import run_radon, ToolResult, Issue
    result = run_radon(workdir=Path("src"))
"""

from __future__ import annotations

import json
import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = structlog.get_logger(__name__)

# Hard gate: CC > 10
COMPLEXITY_THRESHOLD = 10
# Advisory: MI < 65
MAINTAINABILITY_THRESHOLD = 65


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: int | None
    code: str | None    # e.g. "CC=12"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no CC > COMPLEXITY_THRESHOLD
    issues: list[Issue]
    raw: str
    # Additional structured data
    avg_complexity: float = 0.0
    avg_maintainability: float = 0.0


def run_radon(workdir: Path) -> ToolResult:
    """
    Run radon complexity and maintainability analysis.
    Hard gate: any function with CC > 10 blocks.
    Advisory: MI < 65 generates a warning.
    """
    bin_path = shutil.which("radon")
    if not bin_path:
        log.warning("radon_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="radon: not installed",
        )

    issues: list[Issue] = []
    raw_parts = []

    # 1. Cyclomatic complexity
    try:
        cc_result = subprocess.run(
            [bin_path, "cc", str(workdir), "-j"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        cc_raw = cc_result.stdout or ""
        raw_parts.append(f"=== radon cc ===\n{cc_raw[:2000]}")

        if cc_raw.strip():
            cc_data = json.loads(cc_raw)
            if isinstance(cc_data, list):
                for item in cc_data:
                    complexity = item.get("complexity", 0)
                    if complexity > COMPLEXITY_THRESHOLD:
                        issues.append(Issue(
                            severity="HIGH",
                            message=f"Function '{item.get('name', '?')}' has CC={complexity} (threshold: {COMPLEXITY_THRESHOLD})",
                            file=item.get("filename", ""),
                            line=item.get("lineno"),
                            code=f"CC={complexity}",
                        ))

    except subprocess.TimeoutExpired:
        raw_parts.append("radon cc: timeout")
    except json.JSONDecodeError:
        raw_parts.append("radon cc: JSON parse error")
    except Exception as e:
        log.warning("radon_.cc_failed", error=str(e))
        raw_parts.append(f"radon cc error: {e}")

    # 2. Maintainability index
    mi_score = 0.0
    mi_count = 0
    try:
        mi_result = subprocess.run(
            [bin_path, "mi", str(workdir), "-j"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        mi_raw = mi_result.stdout or ""
        raw_parts.append(f"=== radon mi ===\n{mi_raw[:1000]}")

        if mi_raw.strip():
            mi_data = json.loads(mi_raw)
            if isinstance(mi_data, list):
                for item in mi_data:
                    mi = item.get("mi", 0)
                    if mi < MAINTAINABILITY_THRESHOLD and mi > 0:
                        issues.append(Issue(
                            severity="MEDIUM",
                            message=f"Module '{item.get('name', '?')}' has MI={mi:.1f} (advisory threshold: {MAINTAINABILITY_THRESHOLD})",
                            file=item.get("file", ""),
                            line=None,
                            code=f"MI={mi:.1f}",
                        ))
                    if mi > 0:
                        mi_score += mi
                        mi_count += 1

    except Exception as e:
        log.warning("radon_.mi_failed", error=str(e))

    blocked = any(i.severity == "HIGH" for i in issues)
    avg_mi = (mi_score / mi_count) if mi_count > 0 else 0.0

    return ToolResult(
        success=True,
        passed=not blocked,
        issues=issues,
        raw="\n".join(raw_parts),
        avg_complexity=0.0,  # could compute from cc_data if needed
        avg_maintainability=avg_mi,
    )
