"""
forge.product_compiler.agents.test_delta_writer — Generate failing tests for delta rules.

Stage 6 of the edit pipeline: given the delta rule set, produce a FailingTestSuite
that captures the new expected behavior so implementation can be driven to green.
"""

from __future__ import annotations

import json
import structlog

from forge.llm import LLMBackend
from forge.product_compiler.models import DeltaRuleSet, FailingTestSuite, Rule, TestCase

log = structlog.get_logger(__name__)

TEST_DELTA_WRITER_SYSTEM = """You are a test generation agent for edit-mode changes.

Your job: produce failing tests that encode the NEW expected behavior AFTER the change.
These tests must fail against the CURRENT code and pass after the change is applied.

Key distinction from green-field test generation:
- PRESERVE rules → tests that MUST STILL PASS (regression tests)
- CHANGE rules → tests that MUST FAIL now, pass after change
- NEW rules → tests that MUST FAIL now, pass after change
- REMOVE rules → tests that MUST PASS now, fail after change (confirming removal)

For CHANGE rules: the test encodes the NEW behavior, not the old behavior.
For REMOVE rules: write a test that currently passes but would fail if the old
behavior were restored (confirms the behavior was actually removed).
"""


class TestDeltaWriterAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, delta_rules: DeltaRuleSet, existing_test_code: str = "") -> FailingTestSuite:
        """
        Generate failing tests that encode the desired new behavior.

        Args:
            delta_rules: The delta describing what changed.
            existing_test_code: Optional existing test file content (for context).

        Returns:
            FailingTestSuite with test cases for every new or changed rule.
        """
        prompt = f"""Generate failing tests from this delta rule set.

DELTA RULES:

PRESERVE ({len(delta_rules.preserve)} rules) — these must still pass:
{json.dumps([{"id": r.id, "condition": r.condition, "action": r.action} for r in delta_rules.preserve], indent=2)[:2000]}

CHANGE ({len(delta_rules.change)} rules) — tests must FAIL now, pass after change:
{json.dumps([{"old": {"condition": old.condition, "action": old.action}, "new": {"condition": new.condition, "action": new.action}} for old, new in delta_rules.change], indent=2)[:2000]}

NEW ({len(delta_rules.new)} rules) — tests must FAIL now, pass after change:
{json.dumps([{"condition": r.condition, "action": r.action} for r in delta_rules.new], indent=2)[:2000]}

REMOVE ({len(delta_rules.remove)} rules) — confirm behavior was removed:
{json.dumps([{"condition": r.condition, "action": r.action, "reason": r.source_contract} for r in delta_rules.remove], indent=2)[:1000]}

Existing test code (for context on testing patterns used):
```python
{existing_test_code[:3000] if existing_test_code else "(no existing tests)"}
```

Requirements:
- CHANGE and NEW rules: write tests for the NEW behavior
  - Test names: test_delta_[rule_id]_[short_description]
  - These tests MUST FAIL against current code
  - They MUST PASS after the change is applied
- PRESERVE rules: write regression tests that MUST STILL PASS
  - Test names: test_preserve_[rule_id]_[short_description]
  - These verify existing behavior is not broken
- REMOVE rules: write confirmation tests that MUST PASS now
  - Test names: test_removed_[rule_id]_[short_description]
  - These confirm the behavior is gone (would fail if old code were restored)

Output ONLY valid JSON:
{{
  "test_cases": [
    {{
      "rule_id": str,
      "description": str,
      "test_code": str,
      "test_type": "delta|preserve|removed",
      "expected_to_fail": true|false
    }}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=TEST_DELTA_WRITER_SYSTEM,
            max_tokens=8192,
            temperature=0.2,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        test_cases = []
        for t in raw.get("test_cases", []):
            test_type = t.get("test_type", "delta")
            expected_to_fail = t.get("expected_to_fail", True)

            # Map test_type to disposition
            disposition = {
                "delta": "CHANGE",
                "preserve": "PRESERVE",
                "removed": "REMOVE",
            }.get(test_type, "CHANGE")

            test_cases.append(
                TestCase(
                    rule_id=t.get("rule_id", ""),
                    description=t.get("description", ""),
                    test_code=t.get("test_code", ""),
                    language="python",
                    status="failing" if expected_to_fail else "passing",
                )
            )

        log.info(
            "test_delta_writer.generated",
            total=len(test_cases),
            delta=sum(1 for t in test_cases if t.rule_id.startswith("delta")),
            preserve=sum(1 for t in test_cases if t.rule_id.startswith("preserve")),
            removed=sum(1 for t in test_cases if t.rule_id.startswith("removed")),
        )

        return FailingTestSuite(
            test_cases=test_cases,
            language="python",
            framework="pytest",
        )
