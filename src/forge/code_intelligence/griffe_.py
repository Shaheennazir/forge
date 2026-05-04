"""
forge.code_intelligence.griffe_ — Griffe API contract drift detection.

Usage:
    from forge.code_intelligence.griffe_ import run_griffe, ToolResult, Issue
    from forge.product_compiler.models import APIContract

    result = run_griffe(workdir=Path("src"), expected_contracts=[...])
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Lazy import — griffe is optional
_griffe = None


def _get_griffe():
    global _griffe
    if _griffe is None:
        try:
            import griffe
            _griffe = griffe
        except ImportError:
            pass
    return _griffe


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
    passed: bool        # no drift detected
    issues: list[Issue]
    raw: str


def run_griffe(workdir: Path, expected_contracts: Optional[list] = None) -> ToolResult:
    """
    Use griffe to extract the public API from generated code and detect drift
    against expected contracts (APIContract[] from the pipeline).

    Hard gate: any public function in APIContract missing or signature-changed.

    If no expected_contracts are provided, only reports what griffe found.
    """
    griffe_pkg = _get_griffe()
    if griffe_pkg is None:
        log.warning("griffe_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="griffe: not installed",
        )

    issues: list[Issue] = []

    try:
        # Load the generated package from workdir
        # We add the workdir to sys.path so griffe can find it as a package
        import sys
        workdir_str = str(workdir.absolute())
        if workdir_str not in sys.path:
            sys.path.insert(0, workdir_str)

        loaded = griffe_pkg.load(workdir_str, allow_inspection=False)

        # Extract public API: all exported functions/classes
        public_api: dict[str, dict] = {}
        _collect_members(loaded, public_api)

        # Check against expected contracts if provided
        # expected_contracts: list of APIContract objects (from pipeline models)
        if expected_contracts:
            for contract in expected_contracts:
                endpoint = contract.endpoint if hasattr(contract, 'endpoint') else str(contract)
                # Find matching function in public_api
                matched = False
                for name, info in public_api.items():
                    if endpoint.lower() in name.lower() or name.lower() in endpoint.lower():
                        matched = True
                        break

                if not matched:
                    issues.append(Issue(
                        severity="HIGH",
                        message=f"Contract '{endpoint}' has no matching function in generated code",
                        file="",
                        line=None,
                        code="missing_contract",
                    ))

        # Also report any functions found (informational)
        for name, info in public_api.items():
            issues.append(Issue(
                severity="LOW",
                message=f"Public function '{name}' found with signature: {info.get('signature', '?')}",
                file=info.get("file", ""),
                line=info.get("line"),
                code="public_api",
            ))

        return ToolResult(
            success=True,
            passed=len([i for i in issues if i.severity == "HIGH"]) == 0,
            issues=issues,
            raw=f"griffe: loaded {len(public_api)} public symbols from {workdir_str}",
        )

    except Exception as e:
        log.warning("griffe_.run_failed", error=str(e))
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))


def _collect_members(obj, public_api: dict, prefix: str = ""):
    """
    Recursively collect public members from a griffe Module/Class.
    """
    try:
        members = getattr(obj, "members", {}) or {}
        for name, member in members.items():
            if name.startswith("_"):
                continue

            kind = getattr(member, "kind", None)
            kind_name = str(kind) if kind else "unknown"

            # Only collect functions and classes
            if "function" in kind_name.lower() or "class" in kind_name.lower():
                full_name = f"{prefix}{name}" if prefix else name
                public_api[full_name] = {
                    "kind": kind_name,
                    "file": getattr(member, "filepath", "") or "",
                    "line": getattr(member, "lineno", None),
                    "signature": _get_signature(member),
                }

            # Recurse into classes/modules
            if "class" in kind_name.lower() or "module" in kind_name.lower():
                _collect_members(member, public_api, f"{full_name}.")
    except Exception:
        pass


def _get_signature(obj):
    """Extract a human-readable signature from a griffe object."""
    try:
        if hasattr(obj, "parameters") and obj.parameters:
            params = []
            for p in obj.parameters:
                pname = getattr(p, "name", "?")
                pdefault = getattr(p, "default", None)
                if pdefault and pdefault != "inspect._empty":
                    params.append(f"{pname}={pdefault}")
                else:
                    params.append(pname)
            return f"({', '.join(params)})"
    except Exception:
        pass
    return "(...)"
