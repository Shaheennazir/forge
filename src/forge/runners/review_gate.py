"""forge.runners.review_gate — Review Gate node runner."""

from __future__ import annotations
import json
import structlog
from forge.graph import ForgeGraph, NodeStatus
from forge.db import ForgeDB
from forge.runners.common import ShortTermMemory, REVIEW_SYSTEM

log = structlog.get_logger(__name__)


def _review_spec_compliance_fallback(executor_output: dict, spec_md: str) -> dict:
    """Look up _review_spec_compliance from forge.graph at call time (enables test patching)."""
    from forge.graph import _review_spec_compliance as fn
    return fn(executor_output, spec_md)


def run(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    executor_output: dict,
    **extra,
) -> dict:
    """
    Review Gate: synchronous checkpoint.

    Pass criteria:
      - No failed tests (pytest must pass)
      - All spec features appear to be implemented (LLM review)

    Fail → BLOCKED → executor retries with failure reason
    """
    log.info(
        "review_gate.run",
        files=executor_output.get("count", 0) if executor_output else 0,
    )

    spec_md = stm.get("spec_md", "")
    files = executor_output.get("files", []) if executor_output else []
    test_results = executor_output.get("test_results")

    # Priority 1: test failures → automatic fail
    if test_results and test_results.get("failed", 0) > 0:
        failures = test_results.get("failures", [])
        failure_lines = []
        for f in failures:
            failure_lines.append(
                f"- {f['name']}: {f.get('type', 'Error')} — {f.get('message', 'no message')[:100]}"
            )
        reason = (
            f"Tests failed: {test_results['failed']} of {test_results['total']}\n"
            + "\n".join(failure_lines)
        )
        decision = {
            "pass": False,
            "reason": reason,
            "test_failures": failures,
        }
    # Priority 2: LLM review of files vs spec
    elif g.llm and files:
        files_list = "\n".join(
            f"- {ff.get('path', '?')}: {len(ff.get('content', ''))} chars"
            for ff in files
            if isinstance(ff, dict)
        )
        raw = g.llm.complete(
            prompt=f"SPEC.md:\n{spec_md}\n\nGenerated files:\n{files_list}",
            system=REVIEW_SYSTEM,
            max_tokens=2048,
            temperature=0.1,
        )
        try:
            review = json.loads(raw)
            decision = {
                "pass": review.get("pass", False),
                "reason": review.get("reason", ""),
            }
        except json.JSONDecodeError:
            decision = _review_spec_compliance_fallback(executor_output, spec_md)
    else:
        decision = _review_spec_compliance_fallback(executor_output, spec_md)

    g.update_node_status(node_id, NodeStatus.DONE)

    if decision["pass"]:
        output = {"decision": "pass", "reason": decision.get("reason", "")}
    else:
        g.update_node_status(
            node_id,
            NodeStatus.BLOCKED,
            decision.get("reason", "spec deviation"),
        )
        output = {"decision": "fail", "reason": decision.get("reason", "")}

    g.store_output(node_id, output)
    return output
