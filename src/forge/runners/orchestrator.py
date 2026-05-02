"""forge.runners.orchestrator — Orchestrator node runner."""

from __future__ import annotations
import json
import structlog
from typing import TYPE_CHECKING
from forge.graph import ForgeGraph, NodeStatus
from forge.db import ForgeDB
from forge.runners.common import ShortTermMemory, ORCHESTRATOR_SYSTEM

if TYPE_CHECKING:
    from forge.agents import SubagentManager

log = structlog.get_logger(__name__)


def run(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
    continue_session: bool = False,
    agents: "SubagentManager | None" = None,
    **extra,
) -> dict:
    """
    Orchestrator: decides next action based on prompt + session state.

    Actions:
      - generate_spec: User has a vague idea, produce SPEC.md first
      - executor: User has a spec, generate code files
      - done: All work is complete
      - delegate: Offload a specific subtask to a subagent
    """
    log.info("orchestrator.run", prompt=prompt[:80], is_continue=continue_session)

    if continue_session:
        last_state_json = db.read_memory(
            tier="episodic", agent="orchestrator", key="last_session"
        )
        if last_state_json:
            last_state = json.loads(last_state_json)
            log.info(
                "orchestrator.resume", from_state=last_state.get("last_node")
            )
            if last_state.get("last_node"):
                stm["resume_from"] = last_state["last_node"]

    g.update_node_status(node_id, NodeStatus.DONE)

    action = "generate_spec"
    if g.llm:
        raw = g.llm.complete(
            prompt=f"User request: {prompt}\n\nWhat should happen next?",
            system=ORCHESTRATOR_SYSTEM,
            max_tokens=512,
            temperature=0.2,
        )
        try:
            decision = json.loads(raw)
            action = decision.get("action", "generate_spec")
            subagent_type = decision.get("subagent_type")
        except json.JSONDecodeError:
            has_spec = bool(db.latest_spec_version())
            action = "generate_spec" if not has_spec else "executor"
            subagent_type = None
    else:
        subagent_type = None

    # Handle delegation if LLM decided to spawn a subagent
    if action == "delegate" and agents and subagent_type:
        log.info("orchestrator.delegating", agent_type=subagent_type)
        result = agents.spawn(
            task=prompt,
            agent_type=subagent_type,
            task_id=node_id,
            context={
                "project_name": g.project_id,
                "spec_md": stm.get("spec_md", ""),
                "spec_version": db.latest_spec_version() or 1,
                "existing_files": stm.get("existing_files", []),
            },
        )
        stm["subagent_result"] = result
        action = "delegate"

    stm["orchestrator_action"] = action
    output = {"prompt": prompt, "continue": continue_session, "action": action}
    g.store_output(node_id, output)
    return output
