"""
forge.graph — Directed graph execution engine.

Core abstractions:
  ForgeGraph    — graph structure (nodes + edges + failure lanes)
  GraphRunner   — traversal engine, dispatches to runners

All node logic lives in forge.runners.* and forge.agents.
"""

from __future__ import annotations
import json
import uuid
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from forge.db import ForgeDB
    from forge.skills import SkillRegistry
    from forge.mcp import MCPConfig
    from forge.agents import SubagentManager

from forge.db import ForgeDB, Task
from forge.llm import LLMBackend

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
    output: Optional[dict] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GraphEdge:
    id: str
    from_node: str
    to_node: str
    edge_type: str = "normal"


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
        """Build the graph: orchestrator → spec_gen → executor → review_gate."""
        g = self

        g.add_node("orchestrator", "Orchestrator")
        g.add_node("spec_gen", "Spec Generator")
        g.add_node("executor", "Executor")
        g.add_node("review_gate", "Review Gate")

        # Normal flow
        g.add_edge("orchestrator", "spec_gen", "normal")
        g.add_edge("spec_gen", "executor", "normal")
        g.add_edge("executor", "review_gate", "normal")

        # Review outcomes
        g.add_edge("review_gate", "executor", "review_fail")
        g.add_edge("review_gate", "orchestrator", "review_pass")

        # Failure/escalation lanes
        g.add_edge("orchestrator", "orchestrator", "escalate")
        g.add_edge("spec_gen", "orchestrator", "failed")
        g.add_edge("executor", "orchestrator", "failed")
        g.add_edge("review_gate", "orchestrator", "escalate")

        # Ensure at least a stub spec version exists
        if not self.db.latest_spec_version():
            self.db.save_spec_version(spec_version, "", "# Initial stub spec", "")

        # Persist graph structure to DB
        for node in g.nodes.values():
            self.db.create_task(
                task_id=node.id, spec_version=spec_version, label=node.label
            )
        for edge in g.edges:
            self.db.add_task_edge(
                edge_id=edge.id,
                from_task=edge.from_node,
                to_task=edge.to_node,
                edge_type=edge.edge_type,
            )

        return g

    def restore_from_db(self) -> None:
        """Load graph nodes and edges from DB for session resumption."""
        tasks, db_edges = self.db.get_task_graph()
        for t in tasks:
            node = self.add_node(t.id, t.label)
            node.status = (
                NodeStatus(t.status)
                if t.status in ("pending", "running", "done", "blocked", "failed")
                else NodeStatus.PENDING
            )
            node.failure_reason = t.failure_reason
        for e in db_edges:
            edge = GraphEdge(
                id=e["id"],
                from_node=e["from_task"],
                to_node=e["to_task"],
                edge_type=e["edge_type"],
            )
            self.edges.append(edge)

        # Clean up any subagent runs orphaned from an interrupted session
        self._cleanup_incomplete_subagent_runs()

    def _cleanup_incomplete_subagent_runs(self) -> None:
        """Mark any 'running' subagent runs as failed since they were interrupted."""
        incomplete = self.db.get_incomplete_subagent_runs()
        for run in incomplete:
            log.warning(
                "forge.orphaned_subagent_run",
                run_id=run["id"],
                task_id=run["task_id"],
                agent_type=run["name"],
            )
            self.db.finish_subagent_run(
                run_id=run["id"],
                status="failed",
                failure_reason="Session was interrupted; subagent run was orphaned.",
            )

    def update_node_status(
        self, node_id: str, status: NodeStatus, failure_reason: Optional[str] = None
    ) -> None:
        """Update node status and persist to DB."""
        node = self.nodes.get(node_id)
        if node:
            node.status = status
            node.failure_reason = failure_reason
        self.db.set_task_status(node_id, status.value, failure_reason)

    def store_output(self, node_id: str, output: dict) -> None:
        """Store node output."""
        node = self.nodes.get(node_id)
        if node:
            node.output = output


# ── Graph runner ───────────────────────────────────────────────────────────────

class GraphRunner:
    """
    Executes a ForgeGraph from an entry node, following edges.

    Handles:
      - Node dispatch to runner functions (from forge.runners)
      - Skill injection via SkillRegistry
      - MCP server connection and tool availability
      - Subagent spawning via SubagentManager
      - Failure lane handling (blocked/failed)
      - Episodic state persistence for forge continue
    """

    def __init__(
        self,
        g: ForgeGraph,
        db: ForgeDB,
        workdir: Optional[Path] = None,
        skill_registry: Optional[SkillRegistry] = None,
        mcp_config: Optional[MCPConfig] = None,
        agents: Optional["SubagentManager"] = None,
    ):
        self.g = g
        self.db = db
        self.stm: dict[str, Any] = {}
        self.workdir = workdir or (
            Path.home() / ".forge" / "workspace" / g.project_id
        )
        self.skill_registry = skill_registry
        self.mcp_config = mcp_config
        self._mcp_clients: dict[str, Any] = {}
        self.agents = agents

    # ── MCP ───────────────────────────────────────────────────────────────────

    def _ensure_mcp_connected(self) -> None:
        """Connect all configured MCP servers."""
        if not self.mcp_config:
            return
        for server in self.mcp_config.list_all():
            try:
                client = self.mcp_config.create_client(server.name)
                if client:
                    self._mcp_clients[server.name] = client
            except Exception as e:
                log.warning("mcp.connect_failed", server=server.name, error=str(e))

    # ── Main run loop ──────────────────────────────────────────────────────────

    def run(self, entry_node: str = "orchestrator", **context) -> dict:
        """Run from entry node, following edges, until terminal or blocked."""
        self._ensure_mcp_connected()

        from forge.runners import orchestrator as orch_runner
        from forge.runners import spec_gen as spec_runner
        from forge.runners import executor as exec_runner
        from forge.runners import review_gate as review_runner

        current = entry_node
        visited: set[str] = set()
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

            # Dispatch to runner
            output: dict = {}
            if current == "orchestrator":
                output = orch_runner.run(
                    self.g, self.db, current, self.stm,
                    prompt=context.get("prompt", ""),
                    continue_session=context.get("continue_session", False),
                    agents=self.agents,
                )
            elif current == "spec_gen":
                output = spec_runner.run(
                    self.g, self.db, current, self.stm,
                    prompt=context.get("prompt", ""),
                    skill_registry=self.skill_registry,
                )
                if "spec_md" in output:
                    context["spec_md"] = output["spec_md"]
                if "spec_version" in output:
                    context["spec_version"] = output["spec_version"]
            elif current == "executor":
                output = exec_runner.run(
                    self.g, self.db, current, self.stm,
                    spec_md=context.get("spec_md", ""),
                    workdir=self.workdir,
                    skill_registry=self.skill_registry,
                    mcp_config=self.mcp_config,
                    agents=self.agents,
                )
                context["executor_output"] = output
            elif current == "review_gate":
                output = review_runner.run(
                    self.g, self.db, current, self.stm,
                    executor_output=context.get("executor_output", {}),
                )
            else:
                log.warning("graph.unknown_node", node=current)
                break

            # Check failure lanes
            if node.status == NodeStatus.BLOCKED:
                log.warning(
                    "graph.node_blocked",
                    node=current,
                    reason=node.failure_reason,
                )
                self._handle_blocked(current)
                break

            if node.status == NodeStatus.FAILED:
                log.error(
                    "graph.node_failed",
                    node=current,
                    reason=node.failure_reason,
                )
                self._handle_failure(current)
                break

            # Route to next node
            edges = self.g.get_outgoing_edges(current)
            if not edges:
                log.info("graph.terminal", node=current)
                break

            next_node = self._resolve_next_node(current, edges, node.output)
            if not next_node:
                break

            if next_node in visited:
                log.info("graph.already_visited", node=current, next=next_node)
                break

            current = next_node

            # Carry executor output forward
            if (
                self.g.get_node("executor")
                and self.g.get_node("executor").output
            ):
                context["executor_output"] = self.g.get_node("executor").output

        self._save_episodic_state(visited, current)
        self._mcp_cleanup()

        return {
            "visited": list(visited),
            "final_node": current,
            "terminated": node.status if node else None,
        }

    def _resolve_next_node(
        self, current: str, edges: list[GraphEdge], output: Optional[dict]
    ) -> Optional[str]:
        """Resolve the next node to visit based on edge types and output."""
        pass_edge = next((e for e in edges if e.edge_type == "review_pass"), None)
        fail_edge = next((e for e in edges if e.edge_type == "review_fail"), None)

        if output and "decision" in output:
            if output["decision"] == "pass" and pass_edge:
                return pass_edge.to_node
            if output["decision"] == "fail" and fail_edge:
                return fail_edge.to_node

        # Default: follow first normal edge
        normal_edge = next((e for e in edges if e.edge_type == "normal"), None)
        return normal_edge.to_node if normal_edge else None

    # ── Failure handling ───────────────────────────────────────────────────────

    def _handle_blocked(self, node_id: str) -> None:
        self.db.write_memory(
            tier="mid",
            agent="orchestrator",
            key=f"blocked.{node_id}",
            value=json.dumps({
                "node": node_id,
                "reason": self.g.get_node(node_id).failure_reason,
            }),
        )

    def _handle_failure(self, node_id: str) -> None:
        self.db.write_memory(
            tier="mid",
            agent="orchestrator",
            key=f"failed.{node_id}",
            value=json.dumps({
                "node": node_id,
                "reason": self.g.get_node(node_id).failure_reason,
            }),
        )

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_episodic_state(self, visited: set, current: str) -> None:
        self.db.write_memory(
            tier="episodic",
            agent="orchestrator",
            key="last_session",
            value=json.dumps({
                "visited": list(visited),
                "last_node": current,
                "workdir": str(self.workdir),
                "ts": datetime.now(timezone.utc).isoformat(),
            }),
        )

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def _mcp_cleanup(self) -> None:
        if self.mcp_config:
            self.mcp_config.disconnect_all()


# ── Runner re-exports (must live after ForgeGraph/NodeStatus to avoid circular import)
from forge.runners.orchestrator import run as run_orchestrator
from forge.runners.spec_gen import run as run_spec_gen
from forge.runners.executor import run as run_executor
from forge.runners.review_gate import run as run_review_gate
from forge.runners.common import (
    inject_skills_into_context,
    write_files,
    run_tests,
    parse_pytest_output,
    generate_from_spec,
)

# review_spec_compliance lives in common.py but is looked up via forge.graph
# so test patches (forge.graph._review_spec_compliance) take effect at call time.
# Expose it as _review_spec_compliance in forge.graph's namespace:
from forge.runners.common import review_spec_compliance as _review_spec_compliance
