"""
forge.product_compiler.agents.interviewer — Adversarial interview agent.

Asks questions until every layer of the Intent Document is complete.
Stateless between calls — orchestrator holds history and passes it each turn.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from typing import Optional

from forge.llm import LLMBackend, LLMResponse
from forge.product_compiler.models import IntentDocument

log = structlog.get_logger(__name__)

INTERVIEWER_SYSTEM = """You are an adversarial interview agent for a product compiler.

Your job: extract a complete, unambiguous Intent Document from the user.
You are ADVERSARIAL — incomplete answers are blocking errors.
Do not proceed until every question has a concrete, non-ambiguous answer.

The product being described: {product_description}

Ask questions in this order, one at a time:
1. DATABASE LAYER: What data must persist? Name each entity and its fields.
   For each entity: what fields? What types? What can be deleted?
2. BACKEND LAYER: What can users trigger? Name each action.
   For each action: what are the failure cases? What rules govern it?
3. FRONTEND LAYER: Who are the users? What do they see on each screen?
   From each screen, what can they do?
4. AUTHENTICATION: Which routes require login? What happens to guests?

Rules:
- If the user says 'it depends', identify the condition that determines it.
- If the user is vague, push for specificity with a concrete example.
- When ALL questions are answered, output the word: INTENT_COMPLETE
- Otherwise output ONLY your next question.
"""


@dataclass
class InterviewerOutput:
    done: bool
    question: str = ""
    intent: Optional[IntentDocument] = None


class InterviewerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, conversation_history: list[dict]) -> InterviewerOutput:
        """
        Run one interview turn.

        conversation_history: list of {"role": "user"|"assistant", "content": "..."}
        Returns InterviewerOutput with either a next question or the complete IntentDocument.
        """
        product_desc = self._extract_product_description(conversation_history)

        messages = [{"role": "user", "content": p} for p in conversation_history]
        # Actually pass the full history
        full_prompt = "\n".join(
            f"{'User' if m['role']=='user' else 'Assistant'}: {m['content']}"
            for m in conversation_history
        )

        log.info("interviewer.turn", history_len=len(conversation_history))

        response = self.llm.complete(
            prompt=full_prompt,
            system=INTERVIEWER_SYSTEM.format(product_description=product_desc),
            max_tokens=1024,
            temperature=0.3,
        )

        content = response.content if hasattr(response, "content") else str(response)
        content = content.strip()

        if "INTENT_COMPLETE" in content.upper():
            # Extract intent from the full conversation
            intent = self._build_intent_from_history(conversation_history)
            return InterviewerOutput(done=True, intent=intent)
        else:
            return InterviewerOutput(done=False, question=content)

    def _extract_product_description(self, history: list[dict]) -> str:
        """First user message is the product idea."""
        for m in history:
            if m["role"] == "user":
                return m["content"][:500]
        return "unknown"

    def _build_intent_from_history(self, history: list[dict]) -> IntentDocument:
        """
        Parse the conversation history to build an IntentDocument.
        Uses a second LLM call to extract structured data from the raw conversation.
        Returns a partial IntentDocument on failure rather than crashing.
        """
        extraction_prompt = f"""Parse this interview conversation and produce a complete IntentDocument.

Extract:
- entities: list of {{name, fields: {{name, type, required}}[]}}
- relationships: list of {{from_entity, to_entity, relationship_type}}
- actions: list of action names
- failure_rules: list of failure rule descriptions
- user_screens: list of route/screen names
- user_types: list of user categories

Conversation:
{chr(10).join(f"{m['role']}: {m['content']}" for m in history)}

Output ONLY valid JSON matching this schema:
{{
  "entities": [{{"name": str, "fields": [{{"name": str, "type": str, "required": bool}}]}}],
  "relationships": [{{"from_entity": str, "to_entity": str, "relationship_type": str}}],
  "actions": [str],
  "failure_rules": [str],
  "user_screens": [str],
  "screen_actions": {{str: [str]}},
  "user_types": [str],
  "unauthenticated_access": [str]
}}
"""
        import json

        try:
            response = self.llm.complete_json(
                prompt=extraction_prompt,
                system="You are a structured data extraction agent. Output ONLY valid JSON.",
                max_tokens=4096,
            )

            raw = response if isinstance(response, dict) else json.loads(response)
        except Exception as e:
            log.warning("interviewer.extraction_failed", error=str(e))
            # Return partial intent rather than crashing
            from forge.product_compiler.models import IntentDocument
            return IntentDocument(
                entities=[],
                relationships=[],
                actions=[],
                failure_rules=[],
                user_screens=[],
                screen_actions={},
                user_types=[],
                unauthenticated_access=[],
            )

        # Validate raw response has expected structure
        if not raw or not isinstance(raw, dict):
            log.warning("interviewer.extraction_empty", raw=raw)
            from forge.product_compiler.models import IntentDocument
            return IntentDocument(
                entities=[],
                relationships=[],
                actions=[],
                failure_rules=[],
                user_screens=[],
                screen_actions={},
                user_types=[],
                unauthenticated_access=[],
            )

        from forge.product_compiler.models import Entity, EntityField, IntentDocument, Relationship

        entities = []
        for e in raw.get("entities", []):
            if not isinstance(e, dict) or not e.get("name"):
                continue
            entities.append(Entity(
                name=e["name"],
                fields=[
                    EntityField(name=f["name"], type=f.get("type", "string"), required=f.get("required", True))
                    for f in e.get("fields", [])
                    if isinstance(f, dict) and f.get("name")
                ],
            ))

        relationships = []
        for r in raw.get("relationships", []):
            if not isinstance(r, dict) or not r.get("from_entity") or not r.get("to_entity"):
                continue
            relationships.append(Relationship(
                from_entity=r["from_entity"],
                to_entity=r["to_entity"],
                relationship_type=r.get("relationship_type", "one_to_many"),
            ))

        intent = IntentDocument(
            entities=entities,
            relationships=relationships,
            actions=raw.get("actions") if isinstance(raw.get("actions"), list) else [],
            failure_rules=raw.get("failure_rules") if isinstance(raw.get("failure_rules"), list) else [],
            user_screens=raw.get("user_screens") if isinstance(raw.get("user_screens"), list) else [],
            screen_actions=raw.get("screen_actions") if isinstance(raw.get("screen_actions"), dict) else {},
            user_types=raw.get("user_types") if isinstance(raw.get("user_types"), list) else [],
            unauthenticated_access=raw.get("unauthenticated_access") if isinstance(raw.get("unauthenticated_access"), list) else [],
        )
        return intent
