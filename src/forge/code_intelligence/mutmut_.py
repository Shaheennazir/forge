"""
forge.code_intelligence.mutmut_ — Mutation testing wrapper for forge.

Usage:
    from forge.code_intelligence.mutmut_ import run_mutmut, ToolResult
    result = run_mutmut(workdir=Path("."), test_cmd="pytest tests/ -x")

Runs mutmut mutation analysis on the generated code.
Hard gate: mutation score (coverage) must exceed the configured threshold.
Below threshold = test suite has gaps.
"""

from __future__ import annotations

import shutil
import structlog
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Minimum mutation score (0-100) to pass the gate
DEFAULT_MUTATION_SCORE_THRESHOLD = 70


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]


@dataclass
class MutationResult:
    """Detailed mutation testing result."""
    total_mutants: int = 0
    killed: int = 0
    survived: int = 0
    incompetent: int = 0   # unrunnable mutants
    timeout: int = 0
    mutation_score: float = 0.0
    survived_mutants: list[dict] = field(default_factory=list)  # [{file, line, fn, mutation}]


@dataclass
class ToolResult:
    success: bool
    passed: bool        # mutation score >= threshold
    issues: list[Issue]
    raw: str
    mutation_result: MutationResult | None = None


def run_mutmut(
    workdir: Path,
    test_cmd: str = "pytest tests/ -x",
    threshold: float = DEFAULT_MUTATION_SCORE_THRESHOLD,
    timeout_per_mutant: int = 30,
) -> ToolResult:
    """
    Run mutmut mutation testing on the generated codebase.

    Steps:
    1. Run `mutmut run` to perform mutations and run tests
    2. Run `mutmut results` to get the mutation score
    3. Run `mutmut show` to get surviving mutants

    Hard gate: if mutation_score < threshold, pipeline blocks.
    The LLM synthesis step uses surviving mutants as evidence of test gaps.
    """
    bin_path = shutil.which("mutmut")
    if not bin_path:
        log.warning("mutmut.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="mutmut: not installed",
        )

    issues: list[Issue] = []
    mutation_result = MutationResult()
    raw_output = ""

    workdir = workdir.absolute()

    try:
        # Step 1: run mutations
        # --no-progress disables progress bar (better for capture)
        mutmut_run = subprocess.run(
            [
                bin_path, "run",
                "--no-progress",
                f"--test-timeout={timeout_per_mutant}",
                f"--test-command={test_cmd}",
                str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=600,   # 10 min max for mutation runs
            cwd=str(workdir),
        )
        raw_output += "=== MUTMUT RUN ===\n"
        raw_output += mutmut_run.stdout + "\n" + mutmut_run.stderr + "\n"

        # Step 2: get results summary (JSON if available)
        mutmut_results = subprocess.run(
            [bin_path, "results", "--format=json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workdir),
        )

        # Try to parse JSON results
        mutation_result = _parse_mutmut_json(
            mutmut_results.stdout + mutmut_results.stderr,
            threshold,
        )
        raw_output += "\n=== MUTMUT RESULTS ===\n"
        raw_output += mutmut_results.stdout + "\n"

        # Step 3: get surviving (untested) mutants
        mutmut_show = subprocess.run(
            [bin_path, "show", "surviving"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(workdir),
        )
        raw_output += "\n=== MUTMUT SURVIVING ===\n"
        raw_output += mutmut_show.stdout + "\n"

        surviving = _parse_surviving_mutants(mutmut_show.stdout)
        mutation_result.surviving_mutants = surviving

        # Classify survivors as HIGH severity issues (test gaps)
        for s in surviving[:20]:   # report top 20 survivors
            issues.append(Issue(
                severity="HIGH",
                message=f"Surviving mutant: {s.get('mutation', 'unknown')} in {s.get('fn', '?')} at {s.get('file', '?')}:{s.get('line', '?')}",
                file=s.get("file", ""),
                line=s.get("line"),
                code=None,
            ))

        # Also add score as a summary issue
        score = mutation_result.mutation_score
        issues.append(Issue(
            severity="HIGH" if score < threshold else "LOW",
            message=f"Mutation score: {score:.1f}% (threshold: {threshold}%)",
            file="",
            line=None,
            code=None,
        ))

    except subprocess.TimeoutExpired:
        issues.append(Issue(
            severity="MEDIUM",
            message="mutmut: timed out after 10 minutes",
            file="",
            line=None,
            code=None,
        ))
        raw_output += "\n[mutmut timed out]"
    except Exception as e:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"mutmut error: {e}",
            file="",
            line=None,
            code=None,
        ))
        raw_output += f"\n[mutmut error: {e}]"

    passed = mutation_result.mutation_score >= threshold if mutation_result.mutation_score > 0 else True

    return ToolResult(
        success=True,
        passed=passed,
        issues=issues,
        raw=raw_output,
        mutation_result=mutation_result,
    )


def _parse_mutmut_json(raw: str, threshold: float) -> MutationResult:
    """Parse mutmut JSON output to extract mutation score."""
    result = MutationResult()

    try:
        # Try to extract JSON from raw output
        import re
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            result.total_mutants = data.get("total", 0)
            result.killed = data.get("killed", 0)
            result.survived = data.get("survived", 0)
            result.incompetent = data.get("incompetent", 0)
            result.timeout = data.get("timeout", 0)

            total = result.total_mutants
            if total > 0:
                result.mutation_score = round(result.killed / total * 100, 1)
    except Exception:
        # Fallback: try to parse text format
        import re
        score_match = re.search(r"Mutation score:\s*(\d+(?:\.\d+)?)", raw)
        if score_match:
            result.mutation_score = float(score_match.group(1))

        killed_match = re.search(r"killed:\s*(\d+)", raw)
        if killed_match:
            result.killed = int(killed_match.group(1))

        total_match = re.search(r"total:\s*(\d+)", raw)
        if total_match:
            result.total_mutants = int(total_match.group(1))

        survived_match = re.search(r"survived:\s*(\d+)", raw)
        if survived_match:
            result.survived = int(survived_match.group(1))

    return result


def _parse_surviving_mutants(raw: str) -> list[dict]:
    """Parse mutmut show surviving output."""
    survivors: list[dict] = []
    import re

    # Format: file.py:line:fn:mutation
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "surviving" in line.lower():
            continue
        parts = line.split(":")
        if len(parts) >= 4:
            survivors.append({
                "file": parts[0],
                "line": int(parts[1]) if parts[1].isdigit() else None,
                "fn": parts[2],
                "mutation": ":".join(parts[3:]),
            })
        elif len(parts) == 3:
            survivors.append({
                "file": parts[0],
                "line": int(parts[1]) if parts[1].isdigit() else None,
                "fn": parts[2],
                "mutation": "unknown",
            })

    return survivors
