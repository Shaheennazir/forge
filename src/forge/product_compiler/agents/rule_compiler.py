"""
forge.product_compiler.agents.rule_compiler — Reduces contracts + flows to IF/THEN rules.

This is the most reasoning-heavy agent. Reserved for the best model.
Rule format: IF [condition] THEN [action] ELSE [action]

Every rule has exactly one unambiguous outcome per input combination.
No prose. No 'should'. No 'might'. Every rule is a constraint.
"""

from __future__ import annotations

import json
import structlog
from forge.llm import LLMBackend
from forge.product_compiler.models import APIContract, Rule, RuleSet, UserFlowTree

log = structlog.get_logger(__name__)

RULE_COMPILER_SYSTEM = """You are a formal rule compilation agent.

Your job: reduce contracts and user flows to atomic IF/THEN rules.
Every rule must have exactly ONE unambiguous outcome per input combination.
No prose. No 'should'. No 'might'. No 'TBD'. No 'depends'.

Format:
IF [condition] THEN [action]
IF [condition] AND [condition] THEN [action] ELSE [action]

Good rule: IF user is not authenticated THEN redirect to /login
Good rule: IF login fails AND attempts >= 5 THEN lock account AND send unlock email
Bad rule: IF user is logged in THEN they can see the dashboard (vague: "can see" → specify what happens)

Every rule must be actionable — the THEN side must be a concrete behavior,
not a description of access or permission.
"""


class RuleCompilerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, contracts: list[APIContract], flows: UserFlowTree) -> RuleSet:
        """
        Compile contracts and flows into a formal RuleSet.
        Each rule is tagged with its source contract for traceability.
        """
        prompt = f"""Compile formal IF/THEN rules from these contracts and user flows.

Contracts:
{json.dumps([c.to_dict() for c in contracts], indent=2)}

User Flows:
{json.dumps(flows.to_dict(), indent=2)}

Rules must cover:
1. Auth rules: authentication, authorization, session management
2. Validation rules: input validation, format checking
3. Action rules: what happens when each action is triggered
4. Error rules: what happens when actions fail
5. Navigation rules: redirects, post-action behavior

Each rule must be traceable to a contract or flow.
Tag each rule with its source: e.g. "[login_contract]" or "[user_flow:dashboard]"

Output ONLY valid JSON:
{{
  "rules": [
    {{
      "condition": str,
      "action": str,
      "else_action": str|null,
      "source_contract": str
    }}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=RULE_COMPILER_SYSTEM,
            max_tokens=4096,
            temperature=0.2,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        rules = [
            Rule(
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                else_action=r.get("else_action") or "",
                source_contract=r.get("source_contract", ""),
            )
            for r in raw.get("rules", [])
        ]

        log.info("rule_compiler.compiled", rule_count=len(rules))

        return RuleSet(rules=rules)
