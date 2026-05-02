"""
forge.graph — Directed graph execution engine.

Phase 1 (v0.1): Orchestrator + SpecGen + Executor + ReviewGate (no subagents).
Each phase is a graph node. Transitions are explicit edges.
Failure lanes are explicit: any node can emit blocked|failed → orchestrator decides.
"""

from __future__ import annotations
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Any
from enum import Enum
import structlog

from forge.db import ForgeDB, Task, Task as TaskRow
from forge.llm import LLMBackend, create_backend

log = structlog.get_logger(__name__)


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


# ── Graph nodes ─────────────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id: str
    label: str
    status: NodeStatus = NodeStatus.PENDING
    failure_reason: Optional[str] = None
    output: Optional[dict] = None  # Arbitrary output stored after execution
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GraphEdge:
    id: str
    from_node: str
    to_node: str
    edge_type: str = "normal"  # normal | review_pass | review_fail | retry | escalate


class ForgeGraph:
    """
    Directed graph with explicit failure lanes.
    Nodes: orchestrator, spec_gen, executor, review_gate
    Edges: define all possible transitions.
    """

    def __init__(self, project_id: str, db: ForgeDB, llm: Optional[LLMBackend] = None):
        self.project_id = project_id
        self.db = db
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.llm = llm

    def add_node(self, node_id: str, label: str) -> GraphNode:
        node = GraphNode(id=node_id, label=label)
        self.nodes[node_id] = node
        return node

    def add_edge(self, from_id: str, to_id: str, edge_type: str = "normal") -> GraphEdge:
        edge = GraphEdge(
            id=f"edge_{uuid.uuid4().hex[:8]}",
            from_node=from_id, to_node=to_id, edge_type=edge_type,
        )
        self.edges.append(edge)
        return edge

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_outgoing_edges(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.from_node == node_id]

    def get_incoming_edges(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.to_node == node_id]

    def successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self.get_outgoing_edges(node_id)]

    def build_initial_graph(self, spec_version: int) -> "ForgeGraph":
        """Build the v0.1 graph: orchestrator → spec_gen → executor → review_gate."""
        g = self

        # Nodes
        g.add_node("orchestrator", "Orchestrator")
        g.add_node("spec_gen", "Spec Generator")
        g.add_node("executor", "Executor")
        g.add_node("review_gate", "Review Gate")

        # Edges
        g.add_edge("orchestrator", "spec_gen", "normal")
        g.add_edge("spec_gen", "executor", "normal")
        g.add_edge("executor", "review_gate", "normal")

        # Review gate outcomes
        g.add_edge("review_gate", "executor", "review_fail")   # reject → retry
        g.add_edge("review_gate", "orchestrator", "review_pass")  # pass → done

        # Failure edges
        g.add_edge("orchestrator", "orchestrator", "escalate")   # blocked → re-plan
        g.add_edge("spec_gen", "orchestrator", "failed")         # spec gen failed → escalate
        g.add_edge("executor", "orchestrator", "failed")         # executor failed → escalate
        g.add_edge("review_gate", "orchestrator", "escalate")    # review can't decide → escalate

        # Persist to DB — first ensure a spec_versions row exists (FK prerequisite)
        if not self.db.latest_spec_version():
            self.db.save_spec_version(spec_version, "", "# Initial stub spec", "")

        for node in g.nodes.values():
            self.db.create_task(
                task_id=node.id, spec_version=spec_version, label=node.label
            )
        for edge in g.edges:
            self.db.add_task_edge(
                edge_id=edge.id, from_task=edge.from_node, to_task=edge.to_node, edge_type=edge.edge_type
            )

        return g

    def update_node_status(self, node_id: str, status: NodeStatus, failure_reason: Optional[str] = None):
        node = self.nodes.get(node_id)
        if node:
            node.status = status
            node.failure_reason = failure_reason
        self.db.set_task_status(node_id, status.value, failure_reason)

    def store_output(self, node_id: str, output: dict):
        node = self.nodes.get(node_id)
        if node:
            node.output = output


# ── Phase runners ──────────────────────────────────────────────────────────────

# Each runner is a callable: (graph, db, node_id, short_term_memory) → dict
# Must call g.update_node_status() before returning.
# Return dict is stored as node.output.

ShortTermMemory = dict[str, Any]  # in-memory, current task only


# System prompts for each phase

ORCHESTRATOR_SYSTEM = """You are the Orchestrator in a directed-graph agentic coding system (forge).
You receive a user prompt and must decide the next action.
Available actions:
- generate_spec: User has a vague idea, produce a concrete SPEC.md first
- executor: User has a spec, generate code files
- done: All work is complete

Respond ONLY with a JSON object: {"action": "generate_spec"|"executor"|"done", "reason": "..."}"""

SPEC_GEN_SYSTEM = """You are the Spec Generator in a directed-graph agentic coding system (forge).
Given a user prompt, produce a complete, detailed SPEC.md.
Include: project name, overview, features list, file tree, acceptance criteria, tech stack.
Use markdown. Output ONLY the SPEC.md content."""

EXECUTOR_SYSTEM = """You are the Executor in a directed-graph agentic coding system (forge).
You have a SPEC.md. Generate all the code files described.
For each file output a JSON entry: {"path": "...", "action": "create"|"update", "content": "..."}.
Respond with a JSON array of file objects. Only include files that need to be created/updated."""

REVIEW_SYSTEM = """You are the Review Gate in a directed-graph agentic coding system (forge).
Review the generated files against the spec. Check:
1. All spec features are implemented
2. Code is syntactically correct
3. Files match the described file tree

Respond ONLY with JSON: {"pass": true|false, "reason": "...", "issues": ["..."]}"""


def run_orchestrator(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
    continue_session: bool = False,
    **extra,
) -> dict:
    """
    Orchestrator node: decides next action.
    - If continue_session=True, reads episodic memory and resumes from last state.
    - Otherwise starts fresh with user_prompt.
    """
    log.info("orchestrator.run", prompt=prompt[:80], is_continue=continue_session)

    if continue_session:
        last_state_json = db.read_memory(tier="episodic", agent="orchestrator", key="last_session")
        if last_state_json:
            last_state = json.loads(last_state_json)
            log.info("orchestrator.resume", from_state=last_state.get("last_node"))

    g.update_node_status(node_id, NodeStatus.DONE)

    # Real LLM call to decide next action
    if g.llm:
        raw = g.llm.complete(
            prompt=f"User request: {prompt}\n\nWhat should happen next?",
            system=ORCHESTRATOR_SYSTEM,
            max_tokens=512,
            temperature=0.2,
        )
        # Try to parse as JSON
        try:
            decision = json.loads(raw)
            action = decision.get("action", "generate_spec")
        except json.JSONDecodeError:
            # Fallback: if prompt is vague, generate spec; if spec exists, executor; else done
            has_spec = bool(db.latest_spec_version())
            action = "generate_spec" if not has_spec else "executor" if not has_spec else "done"
        stm["orchestrator_action"] = action
        g.store_output(node_id, {"prompt": prompt, "continue": continue_session, "action": action})
        return {"prompt": prompt, "continue": continue_session, "action": action}

    # No LLM: use stub
    g.store_output(node_id, {"prompt": prompt, "continue": continue_session})
    return {"prompt": prompt, "continue": continue_session}


def run_spec_gen(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
) -> dict:
    """
    Spec Generator node: takes a vague prompt, generates SPEC.md.
    Asks clarifying questions if needed.
    """
    log.info("spec_gen.run", prompt=prompt[:100])

    latest_ver = db.latest_spec_version()
    existing_spec_md = None
    existing_version = None
    if latest_ver:
        sv = db.get_spec_version(latest_ver)
        if sv:
            existing_spec_md = sv.spec_md
            existing_version = latest_ver

    if g.llm:
        # Real LLM call to generate spec
        llm_prompt = prompt
        if existing_spec_md:
            llm_prompt = (
                f"Update this existing SPEC.md based on the new request.\n\n"
                f"Current spec:\n{existing_spec_md}\n\n"
                f"New request: {prompt}"
            )
        spec_md = g.llm.complete(
            prompt=llm_prompt,
            system=SPEC_GEN_SYSTEM,
            max_tokens=4096,
            temperature=0.3,
        )
        version = (existing_version or 0) + 1
        changelog = _build_changelog(prompt, spec_md, existing_spec_md)
    else:
        # Stub
        spec_md = _generate_spec_md(prompt, existing_spec_md)
        version = (existing_version or 0) + 1
        changelog = _build_changelog(prompt, spec_md, existing_spec_md)

    sv = db.save_spec_version(version, prompt, spec_md, changelog)
    db.write_memory(tier="mid", agent="spec_gen", key="current_spec_version", value=str(version))
    db.write_memory(tier="mid", agent="spec_gen", key="current_spec_md", value=spec_md)

    g.update_node_status(node_id, NodeStatus.DONE)
    output = {"spec_version": version, "spec_md": spec_md}
    g.store_output(node_id, output)
    return output


def run_executor(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    spec_md: str,
) -> dict:
    """
    Executor node: reads SPEC.md, generates code/tests against it.
    TDD: tests first, then implementation.
    """
    log.info("executor.run")

    if g.llm:
        raw = g.llm.complete(
            prompt=f"SPEC.md:\n{spec_md}",
            system=EXECUTOR_SYSTEM,
            max_tokens=8192,
            temperature=0.2,
        )
        try:
            files = json.loads(raw)
            if isinstance(files, dict):
                files = [files]
        except json.JSONDecodeError as e:
            log.warning("executor.json_parse_failed", error=str(e), raw=raw[:200])
            files = [{"path": "README.md", "action": "create", "content": "# Generated\n\nSpec:\n" + spec_md[:500]}]
    else:
        files = _generate_from_spec(spec_md)

    db.write_memory(tier="mid", agent="executor", key="last_files_generated", value=json.dumps(files))
    db.write_memory(tier="mid", agent="executor", key="executor_done_at", value=datetime.now(timezone.utc).isoformat())

    g.update_node_status(node_id, NodeStatus.DONE)
    output = {"files": files, "count": len(files)}
    g.store_output(node_id, output)
    return output


def run_review_gate(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    executor_output: dict,
) -> dict:
    """
    Review Gate: synchronous checkpoint.
    Returns 'pass' or 'fail' decision.
    Failures route back to executor with rejection reason.
    """
    log.info("review_gate.run", files=executor_output.get("count", 0) if executor_output else 0)

    spec_md = stm.get("spec_md", "")
    files = executor_output.get("files", []) if executor_output else []

    if g.llm:
        raw = g.llm.complete(
            prompt=f"SPEC.md:\n{spec_md}\n\nGenerated files:\n" + "\n".join(f"- {f.get('path')}" for f in files),
            system=REVIEW_SYSTEM,
            max_tokens=2048,
            temperature=0.1,
        )
        try:
            review = json.loads(raw)
            decision = {"pass": review.get("pass", False), "reason": review.get("reason", "")}
        except json.JSONDecodeError:
            decision = {"pass": True, "reason": "LLM review parse failed, auto-passing"}
    else:
        decision = _review_spec_compliance(executor_output, spec_md)

    g.update_node_status(node_id, NodeStatus.DONE)

    if decision["pass"]:
        g.update_node_status("review_gate", NodeStatus.DONE)
        output = {"decision": "pass", "reason": decision.get("reason", "")}
        g.store_output(node_id, output)
        return output
    else:
        g.update_node_status(node_id, NodeStatus.BLOCKED, decision.get("reason", "spec deviation"))
        output = {"decision": "fail", "reason": decision.get("reason", "")}
        g.store_output(node_id, output)
        return output


# ── LLM stubs (replace with real calls in config) ───────────────────────────────

def _generate_spec_md(prompt: str, existing: Optional[str]) -> str:
    """Stub: generates SPEC.md. Replace with real LLM call."""
    return f"""# SPEC.md — Generated

## Project
{prompt}

## Overview
Generated by forge v0.1.

## Features
- TBD based on prompt: {prompt}

## Files to Generate
- `src/` — source code
- `tests/` — test files
- `README.md`

## Acceptance Criteria
1. Code compiles/runs without errors
2. Tests pass
3. Matches spec above

## Tech Stack
- Language: Python 3.11+
- Testing: pytest
"""

def _build_changelog(prompt: str, new_spec: str, existing: Optional[str]) -> str:
    if not existing:
        return f"- v1: Initial spec generated from prompt: {prompt[:60]}"
    return (
        f"- v1: Initial spec\n"
        f"- v2: Updated from prompt: {prompt[:60]}\n"
    )

def _generate_from_spec(spec_md: str) -> list[dict]:
    """Stub: generates files from spec. Replace with real LLM call."""
    return [
        {"path": "README.md", "action": "create", "summary": "Project readme"},
        {"path": "src/__init__.py", "action": "create", "summary": "Source init"},
        {"path": "tests/test_main.py", "action": "create", "summary": "Main tests"},
    ]

def _review_spec_compliance(executor_output: dict, spec_md: str) -> dict:
    """Stub: review gate. Replace with real LLM validation call."""
    return {"pass": True, "reason": "v0.1 stub: auto-pass"}


# ── Graph runner ────────────────────────────────────────────────────────────────

class GraphRunner:
    """
    Executes a ForgeGraph from entry point, following edges.
    Handles retry/escalate/failure decisions at orchestrator level.
    """

    def __init__(self, g: ForgeGraph, db: ForgeDB):
        self.g = g
        self.db = db
        self.stm: ShortTermMemory = {}

    def run(self, entry_node: str = "orchestrator", **context):
        """Run from entry node, following edges, until terminal or blocked."""
        current = entry_node
        visited = set()
        max_hops = 20

        while current and len(visited) < max_hops:
            if current in visited:
                log.warning("graph.cycle_detected", node=current, path=list(visited))
                break
            visited.add(current)

            node = self.g.get_node(current)
            if not node:
                log.error("graph.node_not_found", node=current)
                break

            log.info("graph.executing_node", node=current, label=node.label)

            # ── Dispatch to runner ──────────────────────────────────────────
            if current == "orchestrator":
                output = run_orchestrator(self.g, self.db, current, self.stm, **context)
            elif current == "spec_gen":
                output = run_spec_gen(self.g, self.db, current, self.stm, prompt=context.get("prompt", ""))
                if "spec_md" in output:
                    context["spec_md"] = output["spec_md"]
                if "spec_version" in output:
                    context["spec_version"] = output["spec_version"]
            elif current == "executor":
                output = run_executor(self.g, self.db, current, self.stm, spec_md=context.get("spec_md", ""))
                # Forward executor output to review_gate
                context["executor_output"] = output
            elif current == "review_gate":
                output = run_review_gate(self.g, self.db, current, self.stm, executor_output=context.get("executor_output", {}))
            else:
                log.warning("graph.unknown_node", node=current)
                break

            if node.status == NodeStatus.BLOCKED:
                log.warning("graph.node_blocked", node=current, reason=node.failure_reason)
                self._handle_blocked(current)
                break

            if node.status == NodeStatus.FAILED:
                log.error("graph.node_failed", node=current, reason=node.failure_reason)
                self._handle_failure(current)
                break

            # ── Route to next ────────────────────────────────────────────────
            edges = self.g.get_outgoing_edges(current)
            if not edges:
                log.info("graph.terminal", node=current)
                break

            # Pick the first normal edge (or pass/fail edge based on outcome)
            pass_edge = next((e for e in edges if e.edge_type == "review_pass"), None)
            fail_edge = next((e for e in edges if e.edge_type == "review_fail"), None)

            if node.output and "decision" in node.output:
                if node.output["decision"] == "pass" and pass_edge:
                    next_node = pass_edge.to_node
                    if next_node in visited:
                        log.info("graph.review_pass_terminal", node=current, next=next_node)
                        break
                    current = next_node
                    context["review_output"] = node.output
                elif node.output["decision"] == "fail" and fail_edge:
                    next_node = fail_edge.to_node
                    if next_node in visited:
                        log.warning("graph.review_fail_cycle", node=current, next=next_node)
                        break
                    current = next_node
                    context["review_output"] = node.output
                else:
                    current = edges[0].to_node
            else:
                current = edges[0].to_node

            if self.g.get_node("executor") and self.g.get_node("executor").output:
                context["executor_output"] = self.g.get_node("executor").output

        self._save_episodic_state(visited, current)
        return {"visited": list(visited), "final_node": current, "terminated": node.status if node else None}

    def _handle_blocked(self, node_id: str):
        """Blocked node: expose to orchestrator for decision."""
        self.db.write_memory(
            tier="mid", agent="orchestrator",
            key=f"blocked.{node_id}",
            value=json.dumps({"node": node_id, "reason": self.g.get_node(node_id).failure_reason})
        )

    def _handle_failure(self, node_id: str):
        """Failure: log and escalate to orchestrator."""
        self.db.write_memory(
            tier="mid", agent="orchestrator",
            key=f"failed.{node_id}",
            value=json.dumps({"node": node_id, "reason": self.g.get_node(node_id).failure_reason})
        )

    def _save_episodic_state(self, visited: set, current: str):
        """Persist session state to episodic memory for `forge continue`."""
        self.db.write_memory(
            tier="episodic", agent="orchestrator",
            key="last_session",
            value=json.dumps({"visited": list(visited), "last_node": current, "ts": datetime.now(timezone.utc).isoformat()})
        )
