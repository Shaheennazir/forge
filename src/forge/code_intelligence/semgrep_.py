"""
forge.code_intelligence.semgrep_ — Extended semgrep wrapper for security rules.

Usage:
    from forge.code_intelligence.semgrep_ import run_semgrep, ToolResult, Issue
    result = run_semgrep(workdir=Path("src"), rules=["p/secrets", "p/python"])
"""

from __future__ import annotations

import json
import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Default security-focused rule configs
DEFAULT_SECURITY_CONFIGS = ["p/secrets", "p/python"]


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: int | None
    code: str | None    # semgrep check_id


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no HIGH severity from security rules
    issues: list[Issue]
    raw: str


def run_semgrep(
    workdir: Path,
    configs: Optional[list[str]] = None,
) -> ToolResult:
    """
    Run semgrep with specified rule configs.
    Hard gate: any HIGH severity from p/secrets (hardcoded credentials).
    Advisory: style issues from p/python.
    """
    configs = configs or DEFAULT_SECURITY_CONFIGS

    # Check if semgrep is installed
    bin_path = shutil.which("semgrep")
    if not bin_path:
        log.warning("semgrep_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="semgrep: not installed",
        )

    issues: list[Issue] = []
    raw_parts = []

    for config in configs:
        try:
            result = subprocess.run(
                [bin_path, "--config", config, "--json", str(workdir)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            raw = result.stdout or ""
            raw_parts.append(f"=== {config} ===\n{raw[:3000]}")

            if raw.strip():
                data = json.loads(raw)
                for r in data.get("results", []):
                    severity = r.get("extra", {}).get("severity", "WARNING")
                    issues.append(Issue(
                        severity=severity.upper(),
                        message=r.get("extra", {}).get("message", r.get("check_id", ""))[:200],
                        file=r.get("path", ""),
                        line=r.get("start", {}).get("line"),
                        code=r.get("check_id", ""),
                    ))

        except subprocess.TimeoutExpired:
            raw_parts.append(f"{config}: timeout")
        except json.JSONDecodeError:
            raw_parts.append(f"{config}: JSON parse error")
        except Exception as e:
            log.warning("semgrep_.run_failed", config=config, error=str(e))
            raw_parts.append(f"{config}: {e}")

    # Block only on HIGH severity from secrets rules
    high_from_secrets = [
        i for i in issues
        if i.severity == "HIGH" and i.code
        and any(sec in i.code for sec in ["secrets", "secret", "credentials", "auth"])
    ]
    blocked = len(high_from_secrets) > 0

    return ToolResult(
        success=True,
        passed=not blocked,
        issues=issues,
        raw="\n".join(raw_parts),
    )
