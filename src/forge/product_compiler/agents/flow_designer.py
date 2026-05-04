"""
forge.product_compiler.agents.flow_designer — Derives user flow tree from Intent Document.
"""

from __future__ import annotations

import json
import structlog
from forge.llm import LLMBackend
from forge.product_compiler.models import IntentDocument, UserFlow, UserFlowTree

log = structlog.get_logger(__name__)


class FlowDesignerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, intent: IntentDocument) -> UserFlowTree:
        """
        Derive a complete user flow tree from the Intent Document.
        Every screen → trigger → conditions → terminal outcome.
        """
        prompt = f"""Derive every possible user path through this product.

Intent Document:
- Screens: {intent.user_screens}
- Actions: {intent.actions}
- Screen actions: {intent.screen_actions}
- User types: {intent.user_types}
- Unauthenticated access: {intent.unauthenticated_access}

Output a complete user flow tree as JSON. Every flow must have:
- screen (origin)
- trigger (user action)
- conditions (auth state, form validity, etc.)
- outcome (terminal: success/error/redirect)
- redirect_to (if outcome is redirect)

Cover all paths: authenticated and unauthenticated, success and error branches.

Output ONLY valid JSON:
{{
  "flows": [
    {{
      "screen": str,
      "trigger": str,
      "conditions": [str],
      "outcome": str,
      "outcome_type": "success"|"error"|"redirect",
      "redirect_to": str|null
    }}
  ],
  "entry_screen": str,
  "terminal_screens": [str]
}}
"""
        raw = self.llm.complete_json(prompt=prompt, max_tokens=4096, temperature=0.2)
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        flows = [
            UserFlow(
                screen=f.get("screen", ""),
                trigger=f.get("trigger", ""),
                conditions=f.get("conditions", []),
                outcome=f.get("outcome", ""),
                outcome_type=f.get("outcome_type", "success"),
                redirect_to=f.get("redirect_to"),
            )
            for f in raw.get("flows", [])
        ]

        return UserFlowTree(
            flows=flows,
            entry_screen=raw.get("entry_screen", intent.user_screens[0] if intent.user_screens else "/"),
            terminal_screens=raw.get("terminal_screens", []),
        )
