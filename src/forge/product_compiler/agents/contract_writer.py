"""
forge.product_compiler.agents.contract_writer — Maps user actions to API contracts.
"""

from __future__ import annotations

import json
import structlog
from forge.llm import LLMBackend
from forge.product_compiler.models import APIContract, RequestShape, ResponseShape, UserFlowTree

log = structlog.get_logger(__name__)


class ContractWriterAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, flows: UserFlowTree) -> list[APIContract]:
        """
        Map every user action in the flow tree to an API contract.
        Ground truth — not documentation.
        """
        prompt = f"""Define the API contract for every action in this user flow tree.

Flows:
{json.dumps(flows.to_dict(), indent=2)}

For each unique action, define:
- action: the trigger name from the flow
- request: method, path, body fields, query params
- response: status codes, body fields, error codes
- read_entities: what data this reads
- write_entities: what data this writes
- side_effects: other actions triggered as a result
- error_cases: all failure scenarios

Output ONLY valid JSON array:
[
  {{
    "action": str,
    "request": {{"method": str, "path": str, "body_fields": [str], "query_params": [str], "headers": [str]}},
    "response": {{"status_code": int, "body_fields": [str], "error_codes": [int]}},
    "read_entities": [str],
    "write_entities": [str],
    "side_effects": [str],
    "error_cases": [str]
  }}
]
"""
        raw = self.llm.complete_json(prompt=prompt, max_tokens=4096, temperature=0.2)
        contracts_list = raw if isinstance(raw, list) else json.loads(raw)

        contracts = []
        for c in contracts_list:
            req_data = c.get("request", {})
            resp_data = c.get("response", {})
            contracts.append(
                APIContract(
                    action=c.get("action", ""),
                    request=RequestShape(
                        method=req_data.get("method", "GET"),
                        path=req_data.get("path", "/"),
                        body_fields=req_data.get("body_fields", []),
                        query_params=req_data.get("query_params", []),
                        headers=req_data.get("headers", []),
                    ),
                    response=ResponseShape(
                        status_code=resp_data.get("status_code", 200),
                        body_fields=resp_data.get("body_fields", []),
                        error_codes=resp_data.get("error_codes", []),
                    ),
                    read_entities=c.get("read_entities", []),
                    write_entities=c.get("write_entities", []),
                    side_effects=c.get("side_effects", []),
                    error_cases=c.get("error_cases", []),
                )
            )
        return contracts
