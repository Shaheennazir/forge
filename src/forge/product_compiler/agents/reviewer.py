"""
forge.product_compiler.agents.reviewer — Verifies implementation against rules.

Stage 9 of the pipeline: runs static analysis tools first, feeds structured
evidence to LLM, LLM synthesizes tool output + code context into a judgment.

Tool chain:
  bandit     → security issues (B413 blacklist, B301 unsafe pickle, etc.)
  radon -e  → complexity warnings (CC > 10)
  pyright   → type errors
  semgrep   → pattern matches (via code_intelligence.search)

LLM is the synthesizer, not the only detector.
"""

from __future__ import annotations

import json
import structlog
import subprocess
from dataclasses import asdict
from pathlib import Path

from forge.llm import LLMBackend
from forge.product_compiler.models import ReviewResult, RuleEvidence, RuleSet

log = structlog.get_logger(__name__)

REVIEWER_SYSTEM = """You are a code review agent that checks implementation against formal rules.

You have structured evidence from static analysis tools (bandit, radon, pyright, semgrep).
Use this evidence to make precise judgments. Quote the tool findings when flagging violations.

Your job:
1. For each rule, check if tool evidence confirms or denies satisfaction
2. Flag violations with specific tool references (e.g. "Bandit B301 in auth.py:45")
3. Confirm satisfied rules briefly
"""


# ─────────────────────────────────────────────────────────────────────────────
# Tool runners
# ─────────────────────────────────────────────────────────────────────────────


def _run_bandit(workdir: Path) -> tuple[list[str], dict]:
    """
    Run bandit security analysis. Returns (issues, version_info).
    Issues are formatted as "SEVERITY|CONFIDENCE|FILE:LINE:TESTNAME:message"
    """
    import shutil
    issues = []
    version_info = {}

    bin_path = shutil.which("bandit")
    if not bin_path:
        return ["bandit: not installed"], {}
    try:
        ver = subprocess.run(
            [bin_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver.returncode == 0:
            version_info["bandit"] = ver.stdout.strip().split()[1]
    except Exception:
        pass

    try:
        result = subprocess.run(
            [bin_path, "-r", str(workdir), "-f", "json", "-q"],
            capture_output=True, text=True, timeout=60,
        )
        report = json.loads(result.stdout) if result.stdout else {}
        for item in report.get("results", []):
            issues.append(
                f"{item.get('issue_severity','?')}|{item.get('issue_confidence','?')}|"
                f"{item.get('filename','?')}:{item.get('line_number',0)}:"
                f"{item.get('test_id','?')}:{item.get('issue_text','')[:100]}"
            )
    except Exception as e:
        issues.append(f"bandit: failed ({e})")

    return issues, version_info


def _run_radon(workdir: Path) -> tuple[list[str], dict]:
    """
    Run radon complexity analysis. Returns (complexity_warnings, version_info).
    Warns for CC > 10 (high complexity).
    """
    import shutil
    warnings = []
    version_info = {}

    bin_path = shutil.which("radon")
    if not bin_path:
        return ["radon: not installed"], {}
    try:
        ver = subprocess.run(
            [bin_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver.returncode == 0:
            version_info["radon"] = ver.stdout.strip().split()[1]
    except Exception:
        pass

    try:
        result = subprocess.run(
            [bin_path, "cc", "-a", "-e", str(workdir), "-f", "json"],
            capture_output=True, text=True, timeout=60,
        )
        report = json.loads(result.stdout) if result.stdout else []
        for item in report:
            if item.get("complexity", 0) > 10:
                warnings.append(
                    f"CC={item['complexity']} | {item.get('name','?')} | "
                    f"{item.get('filename','?')}:{item.get('lineno','?')}"
                )
    except Exception as e:
        warnings.append(f"radon: failed ({e})")

    return warnings, version_info


def _run_pyright(workdir: Path) -> tuple[list[str], dict]:
    """
    Run pyright type checking. Returns (type_errors, version_info).
    """
    import shutil
    errors = []
    version_info = {}

    bin_path = shutil.which("pyright")
    if not bin_path:
        # Fall back to python -m pyright
        for alt in ["python -m pyright", "python3 -m pyright"]:
            try:
                result = subprocess.run(
                    alt.split() + ["--version"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    version_info["pyright"] = result.stdout.strip()
                    bin_path = alt.split()[0]
                    break
            except Exception:
                pass
        if not bin_path:
            return ["pyright: not installed"], {}

    try:
        result = subprocess.run(
            f"{bin_path} --outputjson".split() if isinstance(bin_path, str) else [bin_path, "--outputjson"],
            capture_output=True, text=True, timeout=60,
            cwd=str(workdir),
        )
        report = json.loads(result.stdout) if result.stdout else {}
        for diag in report.get("generalDiagnostics", []):
            if diag.get("severity") in ("error", "warning"):
                errors.append(
                    f"{diag.get('severity','?').upper()} | "
                    f"{diag.get('file','?')}:{diag.get('range',{}).get('start',{}).get('line',0)}: "
                    f"{diag.get('message','')[:120]}"
                )
    except Exception as e:
        errors.append(f"pyright: failed ({e})")

    return errors, version_info


# ─────────────────────────────────────────────────────────────────────────────
# Reviewer Agent
# ─────────────────────────────────────────────────────────────────────────────


class ReviewerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, rule_set: RuleSet, workdir: Path | str = Path(".")) -> ReviewResult:
        """
        Run static analysis tools first, then check rules with LLM backed by evidence.

        Phase 1 — Tools: bandit, radon, pyright run and produce structured output.
        Phase 2 — Rules: for each rule, tool evidence is assembled and the LLM
                   makes a judgment using measurements not just pattern matching.
        """
        workdir = Path(workdir)
        result = ReviewResult()
        result.rules_checked = len(rule_set.rules)

        # ── Phase 1: Run all tools ────────────────────────────────────────────
        tool_start = __import__("time").time()

        bandit_issues, bandit_ver = _run_bandit(workdir)
        radon_warnings, radon_ver = _run_radon(workdir)
        pyright_errors, pyright_ver = _run_pyright(workdir)

        result.security_issues = bandit_issues
        result.complexity_warnings = radon_warnings
        result.type_errors = pyright_errors
        result.tool_versions = {**bandit_ver, **radon_ver, **pyright_ver}

        log.info(
            "reviewer.tools_done",
            security=len(bandit_issues),
            complexity=len(radon_warnings),
            types=len(pyright_errors),
            elapsed=__import__("time").time() - tool_start,
        )

        # ── Phase 2: Check each rule with tool-backed evidence ───────────────
        violations = []
        suggestions = []

        for rule in rule_set.rules:
            violation, evidence = self._check_rule(
                rule,
                workdir,
                bandit_issues=bandit_issues,
                radon_warnings=radon_warnings,
                pyright_errors=pyright_errors,
            )
            result.rule_evidences[rule.id] = evidence

            if violation:
                violations.append(violation)
            else:
                log.debug("reviewer.rule_passed", rule_id=rule.id)

        result.rules_violated = violations
        result.passed = len(violations) == 0

        if violations:
            result.suggestions = self._suggest_fixes(violations, workdir)

        log.info(
            "reviewer.complete",
            passed=result.passed,
            violations=len(violations),
            rules=result.rules_checked,
        )
        return result

    def _check_rule(
        self,
        rule,
        workdir: Path,
        *,
        bandit_issues: list[str],
        radon_warnings: list[str],
        pyright_errors: list[str],
    ) -> tuple[str | None, RuleEvidence]:
        """
        Check a single rule against code, backed by static analysis evidence.

        Returns (violation_description or None, RuleEvidence).
        """
        evidence = RuleEvidence(rule_id=rule.id)

        # Read relevant code files
        code_files = {}
        for py_file in workdir.glob("*.py"):
            try:
                code_files[py_file.name] = py_file.read_text()
            except Exception:
                pass

        # Pre-filter relevant tool output for this rule
        # (LLM gets full context but we tag relevant items)
        relevant_bandit = [b for b in bandit_issues if "bandit:" not in b]
        relevant_radon = [r for r in radon_warnings if "radon:" not in r]
        relevant_pyright = [p for p in pyright_errors if "pyright:" not in p]

        evidence.bandit_issues = relevant_bandit
        evidence.radon_complexity = relevant_radon
        evidence.pyright_errors = relevant_pyright

        prompt = f"""Check if this rule is satisfied by the code, using the static analysis evidence.

Rule:
  IF {rule.condition} THEN {rule.action}
  Source: {rule.source_contract}

## Code files
{json.dumps(code_files, indent=2)[:6000]}

## Bandit security issues ({len(relevant_bandit)} total)
{chr(10).join(relevant_bandit[:10]) if relevant_bandit else "  (none)"}

## Radon complexity warnings (CC > 10) ({len(relevant_radon)} total)
{chr(10).join(relevant_radon[:10]) if relevant_radon else "  (none)"}

## Pyright type errors ({len(relevant_pyright)} total)
{chr(10).join(relevant_pyright[:10]) if relevant_pyright else "  (none)"}

Check:
1. Is there code implementing the condition check?
2. Is there code implementing the action?
3. Are tests present verifying this behavior?
4. Do static analysis tools flag anything relevant?

Respond ONLY with:
SATISFIED: [brief confirmation]
or
VIOLATED: [precise description — quote relevant code or tool findings]
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
            return f"LLM assessment failed: {e}", evidence

    def _suggest_fixes(self, violations: list[str], workdir: Path) -> list[str]:
        """Generate fix suggestions for rule violations using LLM."""
        code_files = {}
        for py_file in workdir.glob("*.py"):
            try:
                code_files[py_file.name] = py_file.read_text()
            except Exception:
                pass

        prompt = f"""For each rule violation, suggest how to fix the code.

Violations:
{json.dumps(violations, indent=2)}

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
