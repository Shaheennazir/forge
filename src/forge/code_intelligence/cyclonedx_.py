"""
forge.code_intelligence.cyclonedx_ — CycloneDX SBOM generation wrapper.

Usage:
    from forge.code_intelligence.cyclonedx_ import run_sbom, ToolResult
    result = run_sbom(workdir=Path("."), req_path=Path("requirements.txt"))

Generates a Software Bill of Materials (SBOM) in CycloneDX JSON format.
Advisory only — SBOM is always generated, never blocks.
Required for enterprise and government compliance.
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
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]


@dataclass
class SBOMResult:
    """Full SBOM metadata."""
    bom_format: str = "CycloneDX"
    spec_version: str = "1.5"
    component_count: int = 0
    dependency_count: int = 0
    licenses: list[str] = field(default_factory=list)
    vulnerabilities: list[str] = field(default_factory=list)  # CVE IDs found in SBOM


@dataclass
class ToolResult:
    success: bool
    passed: bool        # always True (advisory)
    issues: list[Issue]
    raw: str
    sbom: SBOMResult | None = None
    sbom_path: str = ""   # path to generated SBOM file


def run_sbom(
    workdir: Path,
    req_path: Path | None = None,
    output_path: Path | None = None,
) -> ToolResult:
    """
    Generate a CycloneDX SBOM from the project.

    Uses `cyclonedx-py` CLI (cyclonedx-python-lib) to generate from requirements.
    Also supports environment-based SBOM via `cyclonedx-py env`.

    This is advisory — always generated, never blocks.
    """
    bin_path = shutil.which("cyclonedx-py")
    tool_result = ToolResult(success=False, passed=True, issues=[], raw="")

    # Find requirements file
    if req_path is None:
        for candidate in ["requirements.txt", "setup.py", "pyproject.toml"]:
            p = workdir / candidate
            if p.exists():
                req_path = p
                break

    if req_path is None or not req_path.exists():
        tool_result.raw = "No requirements file found"
        return tool_result

    output_path = output_path or (workdir / "sbom.json")

    try:
        # cyclonedx-py requirements -o sbom.json requirements.txt
        result = subprocess.run(
            [
                bin_path,
                "requirements",
                "-o", str(output_path),
                "-F",   # force (overwrite)
                str(req_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = result.stdout + result.stderr
        tool_result.raw = raw

        if result.returncode == 0 and output_path.exists():
            tool_result.success = True
            sbom_meta = _parse_sbom(output_path)
            tool_result.sbom = sbom_meta
            tool_result.sbom_path = str(output_path)

            # If there are known vulnerabilities in SBOM, report as MEDIUM
            if sbom_meta.vulnerabilities:
                for cve in sbom_meta.vulnerabilities:
                    tool_result.issues.append(Issue(
                        severity="MEDIUM",
                        message=f"SBOM vulnerability: {cve}",
                        file=str(output_path),
                        line=None,
                        code=cve,
                    ))
        else:
            tool_result.issues.append(Issue(
                severity="MEDIUM",
                message=f"cyclonedx-py exited with code {result.returncode}",
                file=str(req_path),
                line=None,
                code=None,
            ))

    except subprocess.TimeoutExpired:
        tool_result.issues.append(Issue(
            severity="LOW",
            message="SBOM generation timed out",
            file="",
            line=None,
            code=None,
        ))
        tool_result.raw = "timeout"
    except Exception as e:
        tool_result.issues.append(Issue(
            severity="LOW",
            message=f"SBOM generation error: {e}",
            file="",
            line=None,
            code=None,
        ))
        tool_result.raw = str(e)

    return tool_result


def _parse_sbom(sbom_path: Path) -> SBOMResult:
    """Parse a CycloneDX SBOM JSON to extract metadata."""
    meta = SBOMResult()

    try:
        with open(sbom_path) as f:
            sbom = json.load(f)

        meta.spec_version = sbom.get("specVersion", "1.5")

        # Count components
        components = sbom.get("components", [])
        meta.component_count = len(components)

        # Extract licenses
        for comp in components:
            if "licenses" in comp:
                for lic in comp["licenses"]:
                    if "license" in lic:
                        meta.licenses.append(lic["license"].get("id", ""))

        # Extract vulnerability refs (CycloneDX 1.5+)
        vuln_refs = sbom.get("vulnerabilities", [])
        for vr in vuln_refs:
            cve = vr.get("id", "")
            if cve:
                meta.vulnerabilities.append(cve)

    except Exception as e:
        log.warning("cyclonedx_.parse_error", error=str(e))

    return meta


def run_sbom_env(workdir: Path, output_path: Path | None = None) -> ToolResult:
    """
    Generate SBOM from the current Python environment (all installed packages).
    Use this when there is no requirements.txt but we need an environment SBOM.
    """
    bin_path = shutil.which("cyclonedx-py")
    output_path = output_path or (workdir / "sbom.json")

    tool_result = ToolResult(success=False, passed=True, issues=[], raw="")

    try:
        result = subprocess.run(
            [
                bin_path,
                "environment",
                "-o", str(output_path),
                "-F",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        tool_result.raw = result.stdout + result.stderr

        if result.returncode == 0 and output_path.exists():
            tool_result.success = True
            tool_result.sbom = _parse_sbom(output_path)
            tool_result.sbom_path = str(output_path)

    except Exception as e:
        tool_result.raw = str(e)

    return tool_result
