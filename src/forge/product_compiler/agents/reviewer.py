"""
forge.product_compiler.agents.reviewer — Verifies implementation against rules.

Stage 8 of the pipeline: reads the generated code, checks every rule
is satisfied, and returns a compliance report.
"""

from __future__ import annotations

import json
import structlog
from pathlib import Path

from forge.llm import LLMBackend
from forge.product_compiler.models import ReviewResult, RuleSet

log = structlog.get_logger(__name__)

REVIEWER_SYSTEM = """You are a code review agent that checks implementation against formal rules.

Your job: given a rule set and the actual code, identify every rule violation.
Be precise. Quote the relevant code when flagging violations.
If a rule is satisfied, confirm it briefly.
"""


class ReviewerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, rule_set: RuleSet, workdir: Path | str = Path(".")) -> ReviewResult:
        """
        Check all rules against the generated code in workdir.
        Returns a ReviewResult with violations and suggestions.
        """
        workdir = Path(workdir)
        result = ReviewResult()
        result.rules_checked = len(rule_set.rules)

        # Read all generated Python files
        code_files = {}
        for py_file in workdir.glob("*.py"):
            try:
                code_files[py_file.name] = py_file.read_text()
            except Exception:
                pass

        if not code_files:
            result.issues.append("No Python files found in workdir")
            result.passed = False
            return result

        # Check each rule
        violations = []
        suggestions = []

        for rule in rule_set.rules:
            rule_violation = self._check_rule(rule, code_files)
            if rule_violation:
                violations.append(rule_violation)
            else:
                # Confirm it passes
                log.debug("reviewer.rule_passed", rule_id=rule.id)

        result.rules_violated = violations
        result.passed = len(violations) == 0
        result.issues = suggestions

        if violations:
            result.suggestions = self._suggest_fixes(violations, code_files)

        log.info("reviewer.complete", passed=result.passed, violations=len(violations))
        return result

    def _check_rule(self, rule, code_files: dict[str, str]) -> str | None:
        """
        Check a single rule against the code.
        Returns violation description or None if satisfied.
        """
        prompt = f"""Check if this rule is satisfied by the code.

Rule:
  IF {rule.condition} THEN {rule.action}
  Source: {rule.source_contract}

Code files:
{json.dumps(code_files, indent=2)}

Check:
1. Is there code that implements the condition check?
2. Is there code that implements the action?
3. Are there tests that verify this behavior?

Respond ONLY with:
SATISFIED: [brief confirmation]
or
VIOLATED: [precise description of what's missing or wrong — quote the relevant code]
"""
        response = self.llm.complete(
            prompt=prompt,
            system=REVIEWER_SYSTEM,
            max_tokens=1024,
            temperature=0.1,
        )
        content = response.content if hasattr(response, "content") else str(response)
        content = content.strip()

        if content.startswith("VIOLATED:"):
            return content.replace("VIOLATED:", "").strip()
        return None

    def _suggest_fixes(self, violations: list[str], code_files: dict[str, str]) -> list[str]:
        """Generate fix suggestions for rule violations."""
        prompt = f"""For each rule violation, suggest how to fix the code.

Violations:
{json.dumps(violations, indent=2)}

Code:
{json.dumps(code_files, indent=2)}

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
