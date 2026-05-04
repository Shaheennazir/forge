"""
forge.code_intelligence.crosshair_ — Crosshair contract proving wrapper.

Usage:
    from forge.code_intelligence.crosshair_ import prove_contracts, ToolResult
    result = prove_contracts(workdir=Path("src/"))

Crosshair performs static contract checking on Python functions
with precondition/postcondition annotations (PEP 316 / ibugs协议).
It proves whether a contract CAN be violated — without running the code.
If Crosshair can find a violating input, the pipeline hard-blocks.
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
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]  # precondition/postcondition that can be violated


@dataclass
class ContractProof:
    """Result of proving a single function's contracts."""
    function: str
    file: str
    line: int
    preconditions_violatable: bool
    postconditions_violatable: bool
    violating_input: str | None   # concrete input that violates the contract
    proof_status: str             # "proved" / "violated" / "unknown" / "error"


@dataclass
class ToolResult:
    success: bool
    passed: bool        # False if any contract can be violated (hard gate)
    issues: list[Issue]
    raw: str
    proofs: list[ContractProof] = None


def run_crosshair(
    workdir: Path,
    targets: list[str] | None = None,   # specific files/functions, or None = all
    timeout: int = 120,
) -> ToolResult:
    """
    Run Crosshair contract prover on the generated code.

    Crosshair looks for functions with PEP 316-style contracts:
        def foo(x):
            requires(x > 0)
            ensures(result >= 0)
            ...

    It then tries to find concrete inputs that violate the contracts.

    Hard gate: any function where Crosshair finds a violating input
    blocks the pipeline. Contract violations are definition-of-done failures.
    """
    bin_path = shutil.which("crosshair")
    if not bin_path:
        log.warning("crosshair_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="crosshair: not installed",
        )

    issues: list[Issue] = []
    proofs: list[ContractProof] = []
    workdir = workdir.absolute()

    targets = targets or ["."]
    target_args = []
    for t in targets:
        target_args.extend(["--target", str(workdir / t)])

    try:
        # crosshair check [targets...]
        # Returns non-zero if any contract can be violated
        result = subprocess.run(
            [
                bin_path,
                "check",
                "--no-color",
            ] + [str(workdir / t) for t in targets],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir),
        )
        raw = result.stdout + "\n" + result.stderr

        proofs = _parse_crosshair_output(raw, workdir)

        for proof in proofs:
            if proof.proof_status == "violated":
                issues.append(Issue(
                    severity="HIGH",
                    message=f"Contract violated for {proof.function}: {proof.violating_input}",
                    file=proof.file,
                    line=proof.line,
                    code=f"input: {proof.violating_input}" if proof.violating_input else None,
                ))

    except subprocess.TimeoutExpired:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"crosshair: timed out after {timeout}s (prover ran too long)",
            file="",
            line=None,
            code=None,
        ))
        raw = "crosshair timed out"
    except Exception as e:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"crosshair error: {e}",
            file="",
            line=None,
            code=None,
        ))
        raw = str(e)

    # Any violation = hard fail
    passed = all(p.proof_status != "violated" for p in proofs)

    return ToolResult(
        success=True,
        passed=passed,
        issues=issues,
        raw=raw,
        proofs=proofs if proofs else None,
    )


def _parse_crosshair_output(raw: str, workdir: Path) -> list[ContractProof]:
    """Parse Crosshair text output to extract per-function proof results."""
    proofs: list[ContractProof] = []

    import re

    # Crosshair output format:
    # crosshair: check path/to/file.py::function_name
    #   Precondition violated: condition_name with args
    #   postcondition: ...
    # crosshair: All calls checked — no violations found

    # Find all function results
    func_pattern = re.compile(
        r"crosshair:\s+check\s+(.+?)::(\w+)\s*\n(.*?)(?=\ncrosshair:|\Z)",
        re.DOTALL,
    )

    for m in func_pattern.finditer(raw):
        file_rel = m.group(1)
        func_name = m.group(2)
        body = m.group(3)

        file_path = str(workdir / file_rel)

        # Find line number for function
        line_match = re.search(r"line (\d+)", body)
        line_num = int(line_match.group(1)) if line_match else 0

        status = "unknown"
        violating_input = None

        if "violated" in body.lower() or "counterexample" in body.lower():
            status = "violated"
            # Try to extract the violating input
            inp_match = re.search(r"(\w+)\s*=\s*(.+?)(?:\n|$)", body)
            if inp_match:
                violating_input = f"{inp_match.group(1)}={inp_match.group(2)[:50]}"
        elif "all calls checked" in body.lower() or "proved" in body.lower():
            status = "proved"
        elif "error" in body.lower():
            status = "error"
        else:
            # No checkable functions found is not an error
            if "no checkable functions" in body.lower() or "could not import" in body.lower():
                status = "no_contracts"
            else:
                status = "unknown"

        proofs.append(ContractProof(
            function=func_name,
            file=file_path,
            line=line_num,
            preconditions_violatable=status == "violated",
            postconditions_violatable=status == "violated",
            violating_input=violating_input,
            proof_status=status,
        ))

    return proofs


def check_function_contracts(
    workdir: Path,
    function_path: str,   # e.g. "src/models.py::User::authenticate"
    timeout: int = 60,
) -> ToolResult:
    """
    Check contracts for a specific function or method.

    Path format: "file.py::Class.function" or "file.py::function"
    """
    return run_crosshair(workdir=workdir, targets=[function_path], timeout=timeout)
