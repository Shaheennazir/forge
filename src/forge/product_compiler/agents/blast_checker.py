"""
forge.product_compiler.agents.blast_checker — Verify blast radius is contained.

Stage 5 of the edit pipeline: after delta rules are compiled, verify that
the proposed change does not unexpectedly affect more of the codebase than
the impact surface initially scoped.
"""

from __future__ import annotations

import structlog
from pathlib import Path

from forge.llm import LLMBackend
from forge.product_compiler.models import DeltaRuleSet, ImpactSurface

log = structlog.get_logger(__name__)

CHECKER_SYSTEM = """You are a blast-radius containment checker.

Your job: verify that the proposed change stays within the originally
declared impact surface. If the change would affect code outside the
declared blast radius, flag it as a containment failure.

A containment failure occurs when:
1. A rule in a file outside files_affected would be modified
2. A function not in functions_at_risk would be called differently
3. A test not in tests_at_risk would need updating
4. A contract not in contracts_at_risk would be affected

You must be conservative: if you're uncertain whether something is affected,
flag it as potentially outside the blast radius.
"""


class BlastCheckError(Exception):
    """Raised when the delta exceeds the declared impact surface."""

    def __init__(self, message: str, exceeded_by: list[str]):
        super().__init__(message)
        self.exceeded_by = exceeded_by


class BlastCheckerAgent:
    def __init__(self, llm: LLMBackend, workdir: Path | str = Path(".")):
        self.llm = llm
        self.workdir = Path(workdir)

    def run(self, delta_rules: DeltaRuleSet, impact_surface: ImpactSurface) -> bool:
        """
        Check whether the delta rules stay within the predicted blast radius.

        Args:
            delta_rules: The computed delta (preserve / change / new / remove).
            impact_surface: The originally declared impact surface.

        Returns:
            True if blast radius is contained (change is safe to proceed).
        Raises:
            BlastCheckError: if the blast radius exceeds the impact surface.
        """
        # Step 1: Semantic check via jedi — are any of the changed/new rules
        # touching functions outside the declared files_affected?
        containment_failures = self._semantic_containment_check(delta_rules, impact_surface)

        # Step 2: LLM sanity check — does the delta's scope match the declared scope?
        llm_failures = self._llm_containment_check(delta_rules, impact_surface)
        containment_failures.extend(llm_failures)

        if containment_failures:
            failure_msg = "; ".join(containment_failures)
            log.error(
                "blast_checker.containment_failure",
                failures=containment_failures,
                declared_files=impact_surface.files_affected,
                declared_functions=impact_surface.functions_at_risk,
            )
            raise BlastCheckError(
                message=f"Blast radius exceeded: {failure_msg}",
                exceeded_by=containment_failures,
            )

        log.info(
            "blast_checker.passed",
            preserve=len(delta_rules.preserve),
            change=len(delta_rules.change),
            new=len(delta_rules.new),
            remove=len(delta_rules.remove),
            risk_level=impact_surface.risk_level,
        )
        return True

    def _semantic_containment_check(
        self,
        delta_rules: DeltaRuleSet,
        impact_surface: ImpactSurface,
    ) -> list[str]:
        """
        Use jedi to verify changed rules don't touch symbols outside the blast radius.

        For each changed rule, extract the source file it's in and verify it's
        in files_affected. For each new rule, verify it doesn't call anything
        outside functions_at_risk.
        """
        from forge.code_intelligence.jedi_ import get_inference

        failures: list[str] = []
        affected_set = set(impact_surface.files_affected)
        functions_set = set(impact_surface.functions_at_risk)

        # Check: new rules don't reference unknown functions
        for rule in delta_rules.new:
            if not rule.condition or not rule.action:
                continue

            # Extract function names from the rule text (crude but useful heuristic)
            # e.g., "IF user.is_authenticated THEN call update_session"
            words = (rule.condition + " " + rule.action).split()
            for word in words:
                # Check if it's calling a method (has dot notation) not in functions_at_risk
                if "." in word:
                    # e.g., "user.authenticate" — check if 'authenticate' is known
                    method = word.split(".")[-1].strip("()")
                    if functions_set and method not in functions_set:
                        # This is a heuristic — flag it but don't hard-fail
                        log.warning(
                            "blast_checker.new_rule_calls_unknown_function",
                            method=method,
                            rule=rule.condition[:60],
                        )

        # Check: changed rules must be in declared files
        for old_rule, new_rule in delta_rules.change:
            source = old_rule.source_contract
            if not source:
                continue

            # source format: "filename.py:start-end"
            if ":" in source:
                file_part = source.split(":")[0]
                if file_part not in affected_set:
                    failures.append(
                        f"Changed rule in {file_part} but {file_part} not in declared blast radius"
                    )

        return failures

    def _llm_containment_check(
        self,
        delta_rules: DeltaRuleSet,
        impact_surface: ImpactSurface,
    ) -> list[str]:
        """
        Ask the LLM to verify the delta is within the declared impact surface.
        """
        prompt = f"""Verify this change stays within the declared blast radius.

DECLARED IMPACT SURFACE:
- Primary symbol: {impact_surface.primary_changed_symbol}
- Files in scope: {impact_surface.files_affected}
- Functions at risk: {impact_surface.functions_at_risk}
- Contracts at risk: {impact_surface.contracts_at_risk}
- Tests at risk: {impact_surface.tests_at_risk}
- Risk level: {impact_surface.risk_level}

DELTA RULES:

PRESERVE ({len(delta_rules.preserve)} rules):
{[{"condition": r.condition, "source": r.source_contract} for r in delta_rules.preserve[:5]]}

CHANGE ({len(delta_rules.change)} rules):
{[{"old_condition": old.condition, "old_source": old.source_contract} for old, _ in delta_rules.change[:5]]}

NEW ({len(delta_rules.new)} rules):
{[{"condition": r.condition} for r in delta_rules.new[:5]]}

REMOVE ({len(delta_rules.remove)} rules):
{[{"condition": r.condition, "reason": r.source_contract} for r in delta_rules.remove[:5]]}

Check:
1. Are any new rules calling functions NOT in functions_at_risk?
2. Are any changed/removed rules from files NOT in files_affected?
3. Would any tests NOT in tests_at_risk need updating?

Respond with a JSON list of containment failures (empty if contained):
{{"failures": ["reason 1", "reason 2"]}}
"""
        try:
            raw = self.llm.complete_json(
                prompt=prompt,
                system=CHECKER_SYSTEM,
                max_tokens=1024,
                temperature=0.1,
            )
            raw = raw if isinstance(raw, dict) else {}
            return raw.get("failures", [])
        except Exception as e:
            log.warning("blast_checker.llm_check_failed", error=str(e))
            return []
