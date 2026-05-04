"""
forge.product_compiler.agents.blast_checker — Verify blast radius is contained.

Stage 5 of the edit pipeline: after delta rules are compiled, verify that
the proposed change does not unexpectedly affect more of the codebase than
the impact surface initially scoped.
"""

from __future__ import annotations

from forge.llm import LLMBackend
from forge.product_compiler.models import DeltaRuleSet, ImpactSurface


class BlastCheckerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, delta_rules: DeltaRuleSet, impact_surface: ImpactSurface) -> bool:
        """
        Check whether the delta rules stay within the predicted blast radius.

        Args:
            delta_rules: The computed delta (preserve / change / new / remove).
            impact_surface: The originally declared impact surface.

        Returns:
            True if blast radius is contained (change is safe to proceed).
            Raises NotImplementedError if the blast radius exceeds the impact surface.
        """
        raise NotImplementedError("BlastCheckerAgent.run() not yet implemented")
