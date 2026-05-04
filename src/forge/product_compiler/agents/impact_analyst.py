"""
forge.product_compiler.agents.impact_analyst — Blast-radius analysis for edit mode.

Stage 2 of the edit pipeline: given a change intent and the current codebase,
identifies the precise surface area that the change will affect.
"""

from __future__ import annotations

from forge.llm import LLMBackend
from forge.product_compiler.codebase_index import CodebaseIndex
from forge.product_compiler.models import ChangeIntent, ImpactSurface


class ImpactAnalystAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, change_intent: ChangeIntent, codebase_index: CodebaseIndex) -> ImpactSurface:
        """
        Analyze the blast radius of the proposed change.

        Args:
            change_intent: What is changing, what must be preserved.
            codebase_index: Indexed representation of the current codebase.

        Returns:
            ImpactSurface describing files, functions, contracts, and tests at risk.
        """
        raise NotImplementedError("ImpactAnalystAgent.run() not yet implemented")
