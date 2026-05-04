"""
forge.product_compiler.agents.test_delta_writer — Generate failing tests for delta rules.

Stage 6 of the edit pipeline: given the delta rule set, produce a FailingTestSuite
that captures the new expected behavior so implementation can be driven to green.
"""

from __future__ import annotations

from forge.llm import LLMBackend
from forge.product_compiler.models import DeltaRuleSet, FailingTestSuite


class TestDeltaWriterAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, delta_rules: DeltaRuleSet) -> FailingTestSuite:
        """
        Generate failing tests that encode the desired new behavior.

        Args:
            delta_rules: The delta describing what changed.

        Returns:
            FailingTestSuite with test cases for every new or changed rule.
        """
        raise NotImplementedError("TestDeltaWriterAgent.run() not yet implemented")
