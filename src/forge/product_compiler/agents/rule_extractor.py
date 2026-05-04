"""
forge.product_compiler.agents.rule_extractor — Extract rules from existing code for edit mode.

Stage 3 of the edit pipeline: given the impact surface, extract the behavioral
rules that govern the code under change so they can be preserved or mutated.
"""

from __future__ import annotations

import json
import structlog

from forge.llm import LLMBackend
from forge.product_compiler.models import ImpactSurface, Rule

log = structlog.get_logger(__name__)

EXTRACTOR_SYSTEM = """You are a behavioral rule extraction agent.

Your job: given source code in scope of a change, extract the exact IF/THEN rules
that the code currently implements. These rules represent the existing behavior
that must be preserved or intentionally changed.

Rules must be:
- Traceable: each rule maps to specific source lines
- Unambiguous: IF side has exactly one interpretation
- Complete: cover all branches (if/elif/else, try/except)
- Behavioral: describe what the code DOES, not what it SHOULD do

Extract rules by reading the actual source. Do not invent rules — observe them.

Format each rule with a `source` field pointing to the relevant code location.
"""


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
        prompt = f"""Extract the behavioral rules implemented in this code.

Files in scope (blast radius):
- Primary symbol: {impact_surface.primary_changed_symbol}
- Files affected: {json.dumps(impact_surface.files_affected)}
- Callers ({impact_surface.total_callers} total): {json.dumps(impact_surface.callers[:10])}

Source code:
```python
{code}
```

Instructions:
1. Read each function/method in the source
2. For each function, extract the IF/THEN rules it implements
3. Cover ALL control flow paths (if/elif/else, try/except/finally, match/case)
4. For each rule, note which source file and line range it comes from
5. Identify rules that are CRITICAL to preserve (breaking them would change API contract)

Output ONLY valid JSON:
{{
  "rules": [
    {{
      "condition": "IF [precise condition]",
      "action": "THEN [concrete action]",
      "else_action": "ELSE [concrete action] | null",
      "source": "filename.py:start_line-end_line",
      "is_critical": true|false
    }}
  ]
}}

Rules that are critical:
- Input validation (type checking, bounds checking)
- Authentication/authorization checks
- Error handling that users depend on
- Return value guarantees (None vs raise, empty list vs exception)
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=EXTRACTOR_SYSTEM,
            max_tokens=8192,
            temperature=0.1,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        rules = [
            Rule(
                condition=r.get("condition", ""),
                action=r.get("action", ""),
                else_action=r.get("else_action") or "",
                source_contract=r.get("source", ""),
                disposition="PRESERVE",
            )
            for r in raw.get("rules", [])
        ]

        log.info(
            "rule_extractor.extracted",
            rule_count=len(rules),
            critical=sum(1 for r in rules if r.source_contract.get("is_critical")),
            primary_symbol=impact_surface.primary_changed_symbol,
        )

        return rules
