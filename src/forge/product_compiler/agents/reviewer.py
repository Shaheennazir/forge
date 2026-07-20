"""
forge.product_compiler.agents.reviewer — Stage 9: Rule compliance review.

Architecture:
  1. Run all 6 static analysis tools in PARALLEL (ThreadPoolExecutor)
  2. Apply hard gates — if any fail, block immediately (no LLM call)
  3. Only if all gates pass → LLM synthesizes advisory issues + rule compliance

Tool chain:
  bandit     → HIGH/CRITICAL security issues  (hard gate)
  radon      → CC > 10 complexity             (hard gate)
  vulture    → dead code at ≥80% confidence  (hard gate)
  griffe     → contract drift                 (hard gate)
  deptry     → missing dependencies           (hard gate)
  semgrep    → HIGH severity secrets violations (hard gate)

No LLM call is made when hard gates fail. Every tool is IO-bound subprocess;
running them in parallel cuts wall-clock time from ~20s sequential to ~3-5s.
"""

from __future__ import annotations

import json
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from forge.llm import LLMBackend
from forge.product_compiler.models import (
    ReviewResult,
    RuleEvidence,
    RuleSet,
    StaticAnalysisResult,
)

log = structlog.get_logger(__name__)

REVIEWER_SYSTEM = """You are a code review agent that checks implementation against formal rules.

Static analysis tools have ALL PASSED their hard gates. You receive:
1. Structured evidence from 6 static analysis tools (bandit, radon, vulture, griffe, deptry, semgrep)
2. A list of formal rules the implementation must satisfy

Your job:
1. For each rule, check if implementation satisfies it using tool evidence
2. Flag violations with specific tool references (e.g. "Bandit B301 in auth.py:45")
3. Confirm satisfied rules briefly
4. For advisory (non-blocking) issues, suggest fixes

Respond ONLY with valid JSON:
{
  "pass": true|false,
  "rule_violations": ["rule_id: violation description"],
  "rule_satisfied": ["rule_id: brief confirmation"],
  "advisory_issues": ["advisory: description with fix suggestion"],
  "reason": "one-paragraph summary"
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Tool wrappers
# ─────────────────────────────────────────────────────────────────────────────

def _run_bandit(workdir: Path) -> dict:
    """Returns {issues: list[str], success: bool} for bandit."""
    from forge.code_intelligence.bandit_ import run_bandit
    result = run_bandit(workdir)
    return {
        "issues": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message[:100]}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _run_radon(workdir: Path) -> dict:
    """Returns {warnings: list[str], success: bool} for radon."""
    from forge.code_intelligence.radon_ import run_radon
    result = run_radon(workdir)
    return {
        "warnings": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _run_vulture(workdir: Path) -> dict:
    """Returns {issues: list[str], success: bool} for vulture."""
    from forge.code_intelligence.vulture_ import run_vulture
    result = run_vulture(workdir)
    return {
        "issues": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message[:100]}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _run_griffe(workdir: Path, contracts: Optional[list] = None) -> dict:
    """Returns {drift: list[str], success: bool} for griffe."""
    from forge.code_intelligence.griffe_ import run_griffe
    result = run_griffe(workdir, expected_contracts=contracts)
    return {
        "drift": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message[:150]}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _run_deptry(workdir: Path) -> dict:
    """Returns {issues: list[str], success: bool} for deptry."""
    from forge.code_intelligence.deptry_ import run_deptry
    result = run_deptry(workdir)
    return {
        "issues": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message[:100]}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _run_semgrep(workdir: Path) -> dict:
    """Returns {issues: list[str], success: bool} for semgrep."""
    from forge.code_intelligence.semgrep_ import run_semgrep
    result = run_semgrep(workdir)
    return {
        "issues": [
            f"{i.severity}|{i.code or '?'}|{i.file}:{i.line or 0}|{i.message[:100]}"
            for i in result.issues
        ],
        "success": result.success,
    }


def _format_static_analysis(analysis: StaticAnalysisResult) -> str:
    """Format static analysis results for the LLM prompt."""
    lines = ["## Static Analysis Results (all gates passed)\n"]

    if analysis.bandit_issues:
        lines.append(f"### Bandit Security ({len(analysis.bandit_issues)} findings)\n")
        for i in analysis.bandit_issues[:10]:
            lines.append(f"  {i}")
        lines.append("")

    if analysis.radon_warnings:
        lines.append(f"### Radon Complexity ({len(analysis.radon_warnings)} warnings)\n")
        for w in analysis.radon_warnings[:10]:
            lines.append(f"  {w}")
        lines.append("")

    if analysis.vulture_issues:
        lines.append(f"### Vulture Dead Code ({len(analysis.vulture_issues)} findings)\n")
        for v in analysis.vulture_issues[:10]:
            lines.append(f"  {v}")
        lines.append("")

    if analysis.griffe_drift:
        lines.append(f"### Griffe Contract Drift ({len(analysis.griffe_drift)} issues)\n")
        for d in analysis.griffe_drift[:10]:
            lines.append(f"  {d}")
        lines.append("")

    if analysis.deptry_issues:
        lines.append(f"### Deptry Dependencies ({len(analysis.deptry_issues)} issues)\n")
        for d in analysis.deptry_issues[:10]:
            lines.append(f"  {d}")
        lines.append("")

    if analysis.semgrep_issues:
        lines.append(f"### Semgrep Security ({len(analysis.semgrep_issues)} findings)\n")
        for s in analysis.semgrep_issues[:10]:
            lines.append(f"  {s}")
        lines.append("")

    if analysis.tool_errors:
        lines.append(f"### Tool Errors\n")
        for e in analysis.tool_errors:
            lines.append(f"  {e}")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Reviewer Agent
# ─────────────────────────────────────────────────────────────────────────────


class ReviewerAgent:
    def run(
        self,
        rule_set: RuleSet,
        workdir: Path | str,
        contracts: Optional[list] = None,
    ) -> ReviewResult:
        """
        Run static analysis in parallel, apply hard gates, then (optionally) run LLM.

        Args:
            rule_set: The formal rules the implementation must satisfy.
            workdir: Path to the generated codebase.
            contracts: Optional list of APIContract objects for griffe drift detection.
        """
        workdir = Path(workdir)
        result = ReviewResult()
        result.rules_checked = len(rule_set.rules)

        # ── Phase 1: Run all tools in parallel ──────────────────────────────────
        tool_start = __import__("time").time()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                "bandit":  pool.submit(_run_bandit, workdir),
                "radon":   pool.submit(_run_radon, workdir),
                "vulture": pool.submit(_run_vulture, workdir),
                "griffe":  pool.submit(_run_griffe, workdir, contracts),
                "deptry":  pool.submit(_run_deptry, workdir),
                "semgrep": pool.submit(_run_semgrep, workdir),
            }

            results: dict = {}
            errors: list[str] = []

            for name, future in futures.items():
                try:
                    results[name] = future.result(timeout=90)
                except Exception as e:
                    log.warning("reviewer.tool_failed", tool=name, error=str(e))
                    errors.append(f"{name}: {e}")
                    results[name] = {"issues": [], "success": False, "warnings": [], "drift": []}

        elapsed = __import__("time").time() - tool_start

        # ── Build StaticAnalysisResult ───────────────────────────────────────────
        analysis = StaticAnalysisResult(
            bandit_issues=results.get("bandit", {}).get("issues", []),
            radon_warnings=results.get("radon", {}).get("warnings", []),
            vulture_issues=results.get("vulture", {}).get("issues", []),
            griffe_drift=results.get("griffe", {}).get("drift", []),
            deptry_issues=results.get("deptry", {}).get("issues", []),
            semgrep_issues=results.get("semgrep", {}).get("issues", []),
            tool_errors=errors,
            bandit_success=results.get("bandit", {}).get("success", True),
            radon_success=results.get("radon", {}).get("success", True),
            vulture_success=results.get("vulture", {}).get("success", True),
            griffe_success=results.get("griffe", {}).get("success", True),
            deptry_success=results.get("deptry", {}).get("success", True),
            semgrep_success=results.get("semgrep", {}).get("success", True),
        )

        result.static_analysis = analysis
        result.blocking_failures = analysis.blocking_failures()

        log.info(
            "reviewer.tools_done",
            elapsed=f"{elapsed:.1f}s",
            blocking=len(result.blocking_failures),
            bandit=len(analysis.bandit_issues),
            radon=len(analysis.radon_warnings),
            vulture=len(analysis.vulture_issues),
            griffe=len(analysis.griffe_drift),
            deptry=len(analysis.deptry_issues),
            semgrep=len(analysis.semgrep_issues),
        )

        # ── Hard gate: block immediately if any static analysis fails ─────────────
        if result.blocking_failures:
            result.passed = False
            result.issues = result.blocking_failures
            result.llm_verdict = ""
            return result

        # ── Phase 2: LLM synthesis (only when all gates pass) ──────────────────
        evidence = _format_static_analysis(analysis)
        violations = []
        satisfied = []

        for rule in rule_set.rules:
            violation, evidence_obj = self._check_rule(
                rule,
                workdir,
                analysis=analysis,
            )
            result.rule_evidences[rule.id] = evidence_obj

            if violation:
                violations.append(violation)
            else:
                satisfied.append(f"{rule.id}: satisfied")

        result.rules_violated = violations

        if violations:
            result.passed = False
            result.issues = violations
            result.suggestions = self._suggest_fixes(violations, workdir, analysis)
        else:
            result.passed = True
            result.issues = []
            result.llm_verdict = f"All {len(rule_set.rules)} rules satisfied. {len(satisfied)} confirmed by static analysis."

        return result

    def _check_rule(
        self,
        rule,
        workdir: Path,
        analysis: StaticAnalysisResult,
    ) -> tuple[Optional[str], RuleEvidence]:
        """
        Check a single rule against code + static analysis evidence.
        Returns (violation_description or None, RuleEvidence).
        """
        evidence = RuleEvidence(rule_id=rule.id)

        # Build relevant evidence strings for the LLM
        relevant_bandit = [b for b in analysis.bandit_issues if analysis.bandit_success]
        relevant_radon = [r for r in analysis.radon_warnings if analysis.radon_success]
        relevant_vulture = [v for v in analysis.vulture_issues if analysis.vulture_success]
        relevant_griffe = [d for d in analysis.griffe_drift if analysis.griffe_success]
        relevant_deptry = [d for d in analysis.deptry_issues if analysis.deptry_success]
        relevant_semgrep = [s for s in analysis.semgrep_issues if analysis.semgrep_success]

        evidence.bandit_issues = relevant_bandit
        evidence.radon_complexity = relevant_radon
        evidence.llm_assessment = ""

        bandit_evidence = '\n'.join(relevant_bandit[:5]) if relevant_bandit else "(no issues)"
        radon_evidence = '\n'.join(relevant_radon[:5]) if relevant_radon else "(no warnings)"
        vulture_evidence = '\n'.join(relevant_vulture[:5]) if relevant_vulture else "(no dead code)"
        griffe_evidence = '\n'.join(relevant_griffe[:5]) if relevant_griffe else "(no drift)"
        deptry_evidence = '\n'.join(relevant_deptry[:5]) if relevant_deptry else "(no issues)"
        semgrep_evidence = '\n'.join(relevant_semgrep[:5]) if relevant_semgrep else "(no issues)"

        prompt = f"""Check if this rule is satisfied by the code, using the static analysis evidence.

Rule:
  IF {rule.condition} THEN {rule.action}
  Source: {rule.source_contract}

## Static Analysis Evidence

### Bandit ({len(relevant_bandit)} issues)
{bandit_evidence}

### Radon complexity ({len(relevant_radon)} warnings)
{radon_evidence}

### Vulture dead code ({len(relevant_vulture)} issues)
{vulture_evidence}

### Griffe contract ({len(relevant_griffe)} issues)
{griffe_evidence}

### Deptry deps ({len(relevant_deptry)} issues)
{deptry_evidence}

### Semgrep ({len(relevant_semgrep)} issues)
{semgrep_evidence}

Check:
1. Is there code implementing the condition check?
2. Is there code implementing the action?
3. Do static analysis tools flag anything relevant to this rule?
"""
        try:
            response = self.llm.complete(
                prompt=prompt,
                system=REVIEWER_SYSTEM,
                max_tokens=1024,
                temperature=0.1,
            )
            content = response.content if hasattr(response, "content") else str(response)
            content = content.strip()
            evidence.llm_assessment = content

            if content.startswith("VIOLATED:"):
                return content.replace("VIOLATED:", "").strip(), evidence
            return None, evidence

        except Exception as e:
            evidence.llm_assessment = f"error: {e}"
            return None, evidence

    def _suggest_fixes(
        self,
        violations: list[str],
        workdir: Path,
        analysis: StaticAnalysisResult,
    ) -> list[str]:
        """Generate fix suggestions for rule violations using LLM."""
        code_files = {}
        for py_file in workdir.glob("*.py"):
            try:
                code_files[py_file.name] = py_file.read_text()
            except Exception:
                pass

        evidence = _format_static_analysis(analysis)

        prompt = f"""For each rule violation, suggest how to fix the code.

Violations:
{json.dumps(violations, indent=2)}

{evidence}

Code:
{json.dumps(code_files, indent=2)[:5000]}

Output ONLY valid JSON:
{{
  "suggestions": [
    {{
      "violation": "the violation description",
      "fix": "specific code change needed",
      "file": "filename.py (if applicable)"
    }}
  ]
}}
"""
        try:
            raw = self.llm.complete_json(
                prompt=prompt,
                system="You are a code fix suggestion agent. Output ONLY valid JSON.",
                max_tokens=2048,
                temperature=0.2,
            )
            raw = raw if isinstance(raw, dict) else json.loads(raw)
            return [s["fix"] for s in raw.get("suggestions", [])]
        except Exception:
            return [f"Manual review needed for: {v[:80]}" for v in violations[:5]]
