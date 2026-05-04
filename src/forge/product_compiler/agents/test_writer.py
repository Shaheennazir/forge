"""
forge.product_compiler.agents.test_writer — Generates failing tests from rule set.

Tests are written from the Rule Set, not from code.
The codebase does not exist yet — tests define what it must do.
Every rule gets at least one test. Every test is red initially.
"""

from __future__ import annotations

import json
import structlog
from forge.llm import LLMBackend
from forge.product_compiler.models import RuleSet, TestCase, FailingTestSuite

log = structlog.get_logger(__name__)

TEST_WRITER_SYSTEM = """You are a test generation agent.

Your job: produce failing tests that define correct behavior for a codebase that doesn't exist yet.
Tests are derived from rules, not from code. The rules are the specification.
Write pytest-format Python tests. Every test must be runnable and fail immediately.
"""


class TestWriterAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, rule_set: RuleSet) -> FailingTestSuite:
        """
        Generate a failing test suite from the rule set.
        Every rule gets at least one test.
        """
        prompt = f"""Generate failing pytest tests from this rule set.

Rules:
{json.dumps(rule_set.to_dict(), indent=2)}

Requirements:
- One test per rule minimum
- Test name format: test_rule_[rule_id]_[short_description]
- Use pytest. Use descriptive assertions.
- Tests should call the API endpoints described in the rules
- Mock external services (payments, email, etc.)
- Do NOT import any application code — the app doesn't exist yet
- Tests must be syntactically valid Python and fail immediately when run

Output ONLY valid JSON:
{{
  "test_cases": [
    {{
      "rule_id": str,
      "description": str,
      "test_code": str  # full pytest test code as a string
    }}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=TEST_WRITER_SYSTEM,
            max_tokens=8192,
            temperature=0.2,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        test_cases = [
            TestCase(
                rule_id=t.get("rule_id", ""),
                description=t.get("description", ""),
                test_code=t.get("test_code", ""),
                language="python",
                status="failing",
            )
            for t in raw.get("test_cases", [])
        ]

        log.info("test_writer.generated", count=len(test_cases))

        return FailingTestSuite(
            test_cases=test_cases,
            language="python",
            framework="pytest",
        )
