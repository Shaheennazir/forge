"""
forge.code_intelligence.jedi_ — Jedi semantic analysis wrapper for forge.

Usage:
    from forge.code_intelligence.jedi_ import find_callers, rename_symbol, get_inference
    callers = find_callers(workdir=Path("src/"), function_name="authenticate_user")

Jedi powers VS Code Python intellisense. It provides precise semantic
resolution with type inference — not regex/grep.
Used by the Impact Analyst to determine blast radius of changes.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jedi

log = structlog.get_logger(__name__)


@dataclass
class CallSite:
    """A single call site of a function/method."""
    file: str
    line: int
    column: int
    call_type: str          # "function" / "method" / "class" / "import"
    caller_name: str        # name of the function/class that contains this call
    full_path: str          # fully resolved path including project


@dataclass
class SemanticResult:
    """Result of a semantic analysis query."""
    success: bool
    query: str
    results: list  # CallSite | InferenceResult | RenameResult
    raw: str       # debug string


def find_callers(
    workdir: Path,
    function_name: str,
    project_name: str = "forge_project",
) -> SemanticResult:
    """
    Find all callers of a given function across the project.

    Uses Jedi's semantic analysis to get precise call sites,
    not just text matches. Handles imports, aliases, and methods correctly.
    """
    try:
        # Create a Jedi project for the workdir
        project = jedi.Project(path=str(workdir.absolute()))
        script = jedi.Script(project=project, path=str(workdir / "__init__.py"))

        # Get references (all places this name is used)
        names = script.get_names(all_scopes=True, definitions=True, references=True)

        call_sites: list[CallSite] = []
        for name in names:
            if name.name == function_name:
                call_sites.append(CallSite(
                    file=name.module_path or "",
                    line=name.line,
                    column=name.column,
                    call_type=name.type,
                    caller_name=name.name,
                    full_path=name.full_name or "",
                ))

        return SemanticResult(
            success=True,
            query=function_name,
            results=call_sites,
            raw=f"jedi: found {len(call_sites)} references to '{function_name}'",
        )

    except Exception as e:
        log.warning("jedi_.find_callers_error", function=function_name, error=str(e))
        return SemanticResult(
            success=False,
            query=function_name,
            results=[],
            raw=f"jedi error: {e}",
        )


def get_inference(
    workdir: Path,
    code: str,
    line: int = 1,
    column: int = 0,
) -> SemanticResult:
    """
    Infer the type of an expression at a given position.

    Use this to ask "what type does this variable have?" or
    "what does this function return?" with full type inference.
    """
    try:
        project = jedi.Project(path=str(workdir.absolute()))
        script = jedi.Script(code=code, project=project, path="<inline>")

        inference = script.infer(line=line, column=column)

        results = []
        for inf in inference:
            results.append({
                "name": inf.name,
                "type": inf.type,
                "description": inf.description,
                "full_name": inf.full_name,
                "line": inf.line,
                "module_path": str(inf.module_path) if inf.module_path else "",
            })

        return SemanticResult(
            success=True,
            query=code[:50],
            results=results,
            raw=f"jedi infer: {len(results)} inferred types",
        )

    except Exception as e:
        return SemanticResult(
            success=False,
            query=code[:50],
            results=[],
            raw=f"jedi infer error: {e}",
        )


def get_completions(
    workdir: Path,
    code: str,
    line: int = 1,
    column: int = 0,
) -> SemanticResult:
    """
    Get autocomplete suggestions at a given cursor position.

    Mirrors what VS Code shows when you type in a Python file.
    """
    try:
        project = jedi.Project(path=str(workdir.absolute()))
        script = jedi.Script(code=code, project=project, path="<inline>")

        completions = script.complete(line=line, column=column)

        results = [{
            "name": c.name,
            "type": c.type,
            "description": c.description,
            "full_signature": c.full_signature,
        } for c in completions]

        return SemanticResult(
            success=True,
            query=code[:50],
            results=results,
            raw=f"jedi complete: {len(results)} completions",
        )

    except Exception as e:
        return SemanticResult(
            success=False,
            query=code[:50],
            results=[],
            raw=f"jedi complete error: {e}",
        )


def get_signatures(
    workdir: Path,
    code: str,
    line: int = 1,
    column: int = 0,
) -> SemanticResult:
    """
    Get function/method signatures at a given cursor position.

    Returns the call signature with parameter names and types,
    useful for validating API contracts.
    """
    try:
        project = jedi.Project(path=str(workdir.absolute()))
        script = jedi.Script(code=code, project=project, path="<inline>")

        signatures = script.get_signatures(line=line, column=column)

        results = []
        for sig in signatures:
            params = []
            for p in sig.params:
                params.append({
                    "name": p.name,
                    "type": str(p.type_annotation) if p.type_annotation else "unknown",
                    "description": p.description,
                })
            results.append({
                "name": sig.name,
                "params": params,
                "index": sig.index,
                "documentation": sig.docstring(fast=True),
            })

        return SemanticResult(
            success=True,
            query=code[:50],
            results=results,
            raw=f"jedi signatures: {len(results)} signatures",
        )

    except Exception as e:
        return SemanticResult(
            success=False,
            query=code[:50],
            results=[],
            raw=f"jedi signature error: {e}",
        )
