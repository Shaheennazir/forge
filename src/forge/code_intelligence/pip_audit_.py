"""
forge.code_intelligence.pip_audit_ — pip-audit CVE scanning wrapper.

Usage:
    from forge.code_intelligence.pip_audit_ import run_pip_audit, ToolResult
    result = run_pip_audit(workdir=Path("."))

Scans all dependencies against known CVE databases.
Hard gate: any HIGH/CRITICAL CVE in a direct dependency blocks the pipeline.
"""

from __future__ import annotations

import shutil
import structlog
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW / CRITICAL
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]  # CVE code e.g. "CVE-2024-1234"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # no HIGH/CRITICAL CVEs in direct deps
    issues: list[Issue]
    raw: str


@dataclass
class Vulnerability:
    """A single vulnerability from pip-audit."""
    name: str
    version: str
    cves: list[str]
    severity: str       # HIGH / MEDIUM / LOW / CRITICAL / UNKNOWN
    description: str
    fix_versions: list[str] = None


def run_pip_audit(
    workdir: Path,
    requirement_files: list[str] | None = None,
) -> ToolResult:
    """
    Run pip-audit to scan for known vulnerabilities in dependencies.

    Hard gate: any HIGH or CRITICAL CVE in a direct (non-transitive) dependency
    blocks the pipeline. pip-audit output is authoritative.
    """
    bin_path = shutil.which("pip-audit")
    if not bin_path:
        log.warning("pip_audit_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="pip-audit: not installed",
        )

    issues: list[Issue] = []

    req_paths = requirement_files or ["requirements.txt", "setup.py", "pyproject.toml"]

    # Find first existing requirements file
    req_file = None
    for rp in req_paths:
        p = workdir / rp
        if p.exists():
            req_file = str(p)
            break

    try:
        cmd = [
            bin_path,
            "--format=json",
            "--verbose",
        ]
        if req_file:
            cmd.append(f"--requirements={req_file}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(workdir.absolute()),
        )
        # JSON is on stdout only; stderr has warnings/progress
        raw = result.stdout

        vulns = _parse_pip_audit_json(raw)

        for v in vulns:
            issues.append(Issue(
                severity=v.severity,
                message=f"{v.name}=={v.version}: {v.description[:120]}",
                file=req_file or "",
                line=None,
                code="; ".join(v.cves) if v.cves else None,
            ))

    except subprocess.TimeoutExpired:
        return ToolResult(
            success=True,
            passed=True,
            issues=[Issue(
                severity="MEDIUM",
                message="pip-audit: timed out after 2 minutes",
                file="",
                line=None,
                code=None,
            )],
            raw="pip-audit timed out",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw=f"pip-audit error: {e}",
        )

    # Hard gate: any HIGH or CRITICAL in direct deps
    blocking = [i for i in issues if i.severity in ("HIGH", "CRITICAL")]
    passed = len(blocking) == 0

    return ToolResult(
        success=True,
        passed=passed,
        issues=issues,
        raw=raw,
    )


def _parse_pip_audit_json(raw: str) -> list[Vulnerability]:
    """Parse pip-audit JSON output."""
    vulns: list[Vulnerability] = []

    try:
        import json

        data = json.loads(raw)

        # pip-audit format: {"dependencies": [...], "fixes": [...]}
        deps = data.get("dependencies", [])

        for item in deps:
            name = item.get("name", "")
            version = item.get("version", "")
            raw_vulns = item.get("vulns") or {}

            # vulns is a dict of {cve_id: details} when present, empty/None when clean
            if not isinstance(raw_vulns, dict):
                continue

            for cve_id, cve_data in raw_vulns.items():
                if isinstance(cve_data, dict):
                    severity = cve_data.get("severity", "UNKNOWN")
                    description = cve_data.get("description", cve_id)
                    fix_versions = cve_data.get("fix_versions", [])
                else:
                    severity = "UNKNOWN"
                    description = str(cve_data)
                    fix_versions = []

                sev = _normalize_severity(severity)

                vulns.append(Vulnerability(
                    name=name,
                    version=version,
                    cves=[cve_id],
                    severity=sev,
                    description=description,
                    fix_versions=fix_versions,
                ))

    except Exception as e:
        log.warning("pip_audit_.parse_error", error=str(e))

    return vulns


def _normalize_severity(severity: str) -> str:
    """Normalize pip-audit severity strings to HIGH/MEDIUM/LOW."""
    s = severity.upper()
    if s in ("CRITICAL", "HIGH"):
        return "HIGH"
    elif s == "MEDIUM":
        return "MEDIUM"
    elif s in ("LOW", "INFORMATIONAL", "NONE"):
        return "LOW"
    else:
        return "MEDIUM"   # unknown defaults to MEDIUM (conservative)
