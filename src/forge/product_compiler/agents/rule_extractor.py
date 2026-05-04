"""
forge.product_compiler.agents.rule_extractor — Extract rules from existing code for edit mode.

Stage 3 of the edit pipeline: given the impact surface, extract the behavioral
rules that govern the code under change so they can be preserved or mutated.
"""

from __future__ import annotations

from forge.llm import LLMBackend
from forge.product_compiler.models import ImpactSurface, Rule


class RuleExtractorAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, impact_surface: ImpactSurface, code: str) -> list[Rule]:
        """
        Extract behavioral rules from the impacted code.

        Args:
            impact_surface: The blast-radius surface from ImpactAnalystAgent.
            code: The source code content of files in scope.

        Returns:
            List of Rule objects representing the current behavior of the impacted code.
        """
        raise NotImplementedError("RuleExtractorAgent.run() not yet implemented")
