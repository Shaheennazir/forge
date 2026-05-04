"""
forge.product_compiler.agents.delta_compiler — Build a DeltaRuleSet from extracted rules.

Stage 4 of the edit pipeline: given the change intent and extracted rules,
produce a DeltaRuleSet that describes what rules are preserved, changed, added, or removed.
"""

from __future__ import annotations

from forge.llm import LLMBackend
from forge.product_compiler.models import ChangeIntent, DeltaRuleSet, Rule


class DeltaCompilerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, extracted_rules: list[Rule], change_intent: ChangeIntent) -> DeltaRuleSet:
        """
        Diff the extracted rules against the proposed change intent.

        Args:
            extracted_rules: Rules currently governing the impacted code.
            change_intent: The desired change behavior.

        Returns:
            DeltaRuleSet classifying each rule as preserve / change / new / remove.
        """
        raise NotImplementedError("DeltaCompilerAgent.run() not yet implemented")
