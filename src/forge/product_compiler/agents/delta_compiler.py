"""
forge.product_compiler.agents.delta_compiler — Build a DeltaRuleSet from extracted rules.

Stage 4 of the edit pipeline: given the change intent and extracted rules,
produce a DeltaRuleSet that describes what rules are preserved, changed, added, or removed.
"""

from __future__ import annotations

import json
import structlog

from forge.llm import LLMBackend
from forge.product_compiler.models import ChangeIntent, DeltaRuleSet, Rule

log = structlog.get_logger(__name__)

DELTA_COMPILER_SYSTEM = """You are a rule diff agent.

Your job: compare extracted existing rules against the desired new behavior,
and produce a precise classification of what changed.

Four categories:
- PRESERVE: rule is correct and must not be modified
- CHANGE: rule exists but needs to be modified (show old AND new)
- NEW: rule describes behavior that doesn't exist yet
- REMOVE: rule describes behavior that is being intentionally deleted

Every extracted rule must appear in exactly one category.
Every new rule must have a corresponding change intent justification.
"""


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
        prompt = f"""Classify each existing rule against the proposed change.

CHANGE INTENT:
- What is changing: {change_intent.behavior_changing}
- What must be preserved: {change_intent.behavior_preserved}
- Before state: {change_intent.before_state}
- After state: {change_intent.after_state}
- Users affected: {json.dumps(change_intent.users_affected)}

EXTRACTED RULES (current behavior):
{json.dumps([{"id": r.id, "condition": r.condition, "action": r.action, "else_action": r.else_action, "source": r.source_contract} for r in extracted_rules], indent=2)}

Instructions:
1. For each extracted rule, decide: PRESERVE | CHANGE | REMOVE
2. PRESERVE: rule is correct, unaffected by the change, must not be modified
3. CHANGE: rule needs modification to match new behavior — provide both old and new
4. REMOVE: rule describes behavior being intentionally deleted
5. For NEW behavior (not in extracted rules), add entries with disposition=NEW

Output ONLY valid JSON:
{{
  "preserve": [
    {{"id": str, "condition": str, "action": str, "else_action": str|null, "source": str}}
  ],
  "change": [
    {{
      "old": {{"id": str, "condition": str, "action": str}},
      "new": {{"condition": str, "action": str}}
    }}
  ],
  "new": [
    {{"condition": str, "action": str, "else_action": str|null, "justification": str}}
  ],
  "remove": [
    {{"id": str, "condition": str, "action": str, "reason": str}}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=DELTA_COMPILER_SYSTEM,
            max_tokens=4096,
            temperature=0.1,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        preserve = [
            Rule(
                id=r.get("id", ""),
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                else_action=r.get("else_action") or "",
                source_contract=r.get("source", ""),
                disposition="PRESERVE",
            )
            for r in raw.get("preserve", [])
        ]

        change_pairs = []
        for entry in raw.get("change", []):
            old_r = entry.get("old", {})
            new_r = entry.get("new", {})
            old_rule = Rule(
                id=old_r.get("id", ""),
                condition=old_r.get("condition", ""),
                action=old_r.get("action", ""),
                disposition="CHANGE",
            )
            new_rule = Rule(
                condition=new_r.get("condition", ""),
                action=new_r.get("action", ""),
                else_action=new_r.get("else_action") or "",
                disposition="CHANGE",
            )
            change_pairs.append((old_rule, new_rule))

        new_rules = [
            Rule(
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                else_action=r.get("else_action") or "",
                disposition="NEW",
            )
            for r in raw.get("new", [])
        ]

        remove_rules = [
            Rule(
                id=r.get("id", ""),
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                disposition="REMOVE",
            )
            for r in raw.get("remove", [])
        ]

        delta = DeltaRuleSet(
            preserve=preserve,
            change=change_pairs,
            new=new_rules,
            remove=remove_rules,
        )

        log.info(
            "delta_compiler.compiled",
            preserve=len(delta.preserve),
            change=len(delta.change),
            new=len(delta.new),
            remove=len(delta.remove),
            behavior_changing=change_intent.behavior_changing[:80],
        )

        return delta
