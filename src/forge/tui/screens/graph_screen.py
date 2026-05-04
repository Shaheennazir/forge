"""
forge.tui.screens.graph_screen — Graph execution dashboard.

Visualizes the ForgeGraph DAG (orchestrator → spec_gen → executor → review_gate),
streams node lifecycle events, shows spec versions, and surfaces retry counts.

Keybindings:
  Enter        → start / resume graph execution
  Ctrl+R       → reset graph to initial state
  Ctrl+V       → view current spec (in a side panel)
  Ctrl+E       → view executor output from last run
  Escape       → back
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static, Button, Log, Input

from forge.tui.state import AppState, StatusLevel
from forge.graph import ForgeGraph, GraphRunner, NodeStatus


# ── Event bus for graph node lifecycle ────────────────────────────────────────

class GraphEventType(Enum):
    NODE_STARTED = "node_started"
    NODE_UPDATED  = "node_updated"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED   = "node_failed"
    NODE_BLOCKED  = "node_blocked"
    RUN_STARTED   = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED    = "run_failed"


@dataclass
class GraphEvent:
    event_type: GraphEventType
    node_id: Optional[str]
    status: Optional[NodeStatus]
    output: Optional[dict]
    failure_reason: Optional[str]
    timestamp: datetime


from dataclasses import dataclass


class GraphEventBus:
    """Lightweight pub/sub for graph lifecycle events."""

    def __init__(self):
        self._subscribers: list[callable] = []

    def subscribe(self, callback: callable) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: callable) -> None:
        self._subscribers.remove(callback)

    def publish(self, event: GraphEvent) -> None:
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass


# Singleton
_graph_event_bus = GraphEventBus()


# ── Widget helpers ────────────────────────────────────────────────────────────

_NODE_LABELS = {
    "orchestrator": "🤖 Orchestrator",
    "spec_gen":     "📐 Spec Generator",
    "executor":     "⚡ Executor",
    "review_gate":  "🚧 Review Gate",
}

_NODE_DESCS = {
    "orchestrator": "Understands intent, decomposes into tasks, coordinates subagents",
    "spec_gen":     "Writes SPEC.md from the orchestrator's task list",
    "executor":     "Implements code from the spec using subagents and tools",
    "review_gate":  "Reviews executor output against spec; approves or rejects",
}

_STATUS_CHARS = {
    NodeStatus.PENDING: ("⏳", "#6C7086"),
    NodeStatus.RUNNING: ("🔄", "#F59E0B"),
    NodeStatus.DONE:     ("✅", "#22C55E"),
    NodeStatus.BLOCKED:  ("🚫", "#EF4444"),
    NodeStatus.FAILED:   ("❌", "#EF4444"),
}


def _node_card(node_id: str, status: NodeStatus, failure_reason: Optional[str]) -> str:
    icon, color = _STATUS_CHARS.get(status, ("❓", "#6C7086"))
    label = _NODE_LABELS.get(node_id, node_id)
    desc = _NODE_DESCS.get(node_id, "")
    lines = [
        f"[{color}]{icon}[/] [bold]{label}[/]",
        f"  [{color}]{status.value.upper()}[/]",
    ]
    if failure_reason:
        lines.append(f"  ⚠ {failure_reason[:60]}")
    lines.append(f"  {desc}")
    return "\n".join(lines)


# ── GraphScreen ───────────────────────────────────────────────────────────────

class GraphScreen(Screen):
    """
    Graph execution dashboard — DAG visualization + streaming node events.

    Layout:
      [header: project | spec version | run controls]
      [DAG row: 4 node cards horizontally with edge arrows]
      [detail panel: selected node output / spec content]
      [event log: chronological node events]
      [bottom: action buttons]
    """

    TITLE = "Forge Graph"
    BINDINGS = [
        ("escape",    "pop_screen",      "Back"),
        ("enter",     "start_run",       "Start"),
        ("ctrl+r",    "reset_run",       "Reset"),
        ("ctrl+v",    "view_spec",       "Spec"),
        ("ctrl+e",    "view_executor",   "Executor"),
        ("ctrl+p",    "command_palette", "Commands"),
    ]

    CSS = """
    GraphScreen {
        layout: vertical;
    }

    #graph-header {
        height: 2;
        background: $primary;
        color: $text;
        dock: top;
    }

    #dag-row {
        height: 10;
        layout: horizontal;
        padding: 1 2;
        background: $surface-darken-1;
    }

    .node-card {
        width: 1fr;
        border: solid $primary 30%;
        padding: 1 1;
        margin: 0 1;
        background: $surface-darken-2;
    }

    .node-card.active {
        border: solid $accent 80%;
        background: $surface;
    }

    .node-card.done {
        border: solid $success 60%;
    }

    .node-card.failed, .node-card.blocked {
        border: solid $error 60%;
    }

    #detail-panel {
        height: 1fr;
        border: solid $primary 20%;
        margin: 1 2;
        padding: 1;
    }

    #event-log {
        height: 12;
        border: solid $primary 20%;
        margin: 0 2 1 2;
        padding: 0;
    }

    #graph-footer {
        height: 3;
        dock: bottom;
        layout: horizontal;
        background: $surface-darken-1;
        padding: 1 2;
        align: center middle;
    }

    #spec-badge {
        width: 16;
    }
    """

    # Reactive state
    selected_node: reactive[Optional[str]] = reactive(None)
    is_running:    reactive[bool]          = reactive(False)

    def __init__(self, state: AppState, project_id: str, session_id: Optional[str] = None):
        super().__init__()
        self.state       = state
        self.project_id  = project_id
        self.session_id  = session_id or str(uuid.uuid4().hex[:8])
        self._graph: Optional[ForgeGraph] = None
        self._runner: Optional[GraphRunner] = None
        self._node_widgets: dict[str, Static] = {}
        self._detail_log: Optional[Log] = None
        self._event_log:  Optional[Log] = None
        self._spec_version: int = 1
        self._run_count: int = 0
        self._retry_count: int = 0
        self._latest_run_result: Optional[dict] = None

    # ── Compose ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Header
        yield Container(
            Static(f"⚙ {self.project_id}", id="project-label"),
            Static("spec: v—", id="spec-badge"),
            Static("", id="run-status"),
            id="graph-header",
        )

        # DAG row — 4 node cards
        with Container(id="dag-row"):
            for node_id in ("orchestrator", "spec_gen", "executor", "review_gate"):
                card = Vertical(
                    Static(_node_card(node_id, NodeStatus.PENDING, None), id=f"card-{node_id}"),
                    id=f"node-{node_id}",
                )
                card.border_title = _NODE_LABELS.get(node_id, node_id)
                yield card

        # Detail panel (spec or executor output)
        yield Container(
            Static("Select a node or start a run to see details", id="detail-text"),
            id="detail-panel",
        )

        # Event log
        yield Log("📋 Graph event log ( Ctrl+V: spec  |  Ctrl+E: executor output )", id="event-log")

        # Footer with action buttons
        yield Container(
            Button("▶ Start / Resume", id="btn-run", variant="primary"),
            Button("🔄 Reset", id="btn-reset", variant="warning"),
            Button("📄 View Spec", id="btn-spec", variant="default"),
            Static("", id="footer-hint"),
            id="graph-footer",
        )

    # ── On mount — init graph ─────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._detail_log = self.query_one("#detail-panel", Container)
        self._event_log  = self.query_one("#event-log", Log)
        self._init_graph()
        self._subscribe_events()
        self._update_header()
        self._log_event("GraphScreen mounted — press Enter to start the DAG")

    def _init_graph(self) -> None:
        """Build or restore the graph for this project."""
        from forge.db import ForgeDB

        db = ForgeDB.for_project(self.project_id)
        self._graph = ForgeGraph(self.project_id, db)

        # Try to restore from DB; if empty, build fresh
        tasks, _ = db.get_task_graph()
        if tasks:
            self._graph.restore_from_db()
            self._log_event(f"Graph restored from DB ({len(tasks)} nodes)")
        else:
            self._graph.build_initial_graph(spec_version=self._spec_version)
            self._log_event("Fresh graph created — 4 nodes initialized")

        self._spec_version = db.latest_spec_version() or 1

        # Wire buttons
        self.query_one("#btn-run", Button).subscribe(self._on_run_pressed)
        self.query_one("#btn-reset", Button).subscribe(self._on_reset_pressed)
        self.query_one("#btn-spec", Button).subscribe(self._on_spec_pressed)

        self._refresh_node_cards()

    def _subscribe_events(self) -> None:
        _graph_event_bus.subscribe(self._on_graph_event)

    def on_unmount(self) -> None:
        _graph_event_bus.unsubscribe(self._on_graph_event)

    # ── Event handler ─────────────────────────────────────────────────────────

    def _on_graph_event(self, event: GraphEvent) -> None:
        """Called on the Textual message thread via post_message."""
        # Schedule UI update on main thread
        self.post_message(self._GraphEventMsg(event))

    class _GraphEventMsg(Message):
        def __init__(self, event: GraphEvent):
            self.event = event
            super().__init__()

    def on_graph_screen__graph_event_msg(self, event: GraphScreen._GraphEventMsg) -> None:
        ev = event.event
        node_id = ev.node_id

        if ev.event_type == GraphEventType.RUN_STARTED:
            self.is_running = True
            self._set_run_status("🚀 Running…", "warning")

        elif ev.event_type == GraphEventType.NODE_STARTED:
            if node_id:
                self._update_node_card(node_id, NodeStatus.RUNNING, None)
                self._log_event(f"  🔄 [{node_id}] started")

        elif ev.event_type == GraphEventType.NODE_COMPLETED:
            if node_id:
                self._update_node_card(node_id, NodeStatus.DONE, None)
                output = ev.output or {}
                summary = self._summarize_output(node_id, output)
                self._log_event(f"  ✅ [{node_id}] done — {summary}")
                self._show_node_detail(node_id, output)

        elif ev.event_type == GraphEventType.NODE_FAILED:
            if node_id:
                self._update_node_card(node_id, NodeStatus.FAILED, ev.failure_reason)
                self._retry_count += 1
                self._log_event(f"  ❌ [{node_id}] FAILED — {ev.failure_reason}")

        elif ev.event_type == GraphEventType.NODE_BLOCKED:
            if node_id:
                self._update_node_card(node_id, NodeStatus.BLOCKED, ev.failure_reason)
                self._log_event(f"  🚫 [{node_id}] BLOCKED — {ev.failure_reason}")

        elif ev.event_type == GraphEventType.RUN_COMPLETED:
            self.is_running = False
            result = self._latest_run_result or {}
            visited = result.get("visited", [])
            self._run_count += 1
            self._set_run_status(
                f"✅ Completed — visited: {' → '.join(visited)}",
                "success",
            )
            self._log_event(f"🏁 Run #{self._run_count} complete — {' → '.join(visited)}")

        elif ev.event_type == GraphEventType.RUN_FAILED:
            self.is_running = False
            self._set_run_status(f"❌ Run failed: {ev.output}", "error")
            self._log_event(f"❌ Run failed: {ev.output}")

    # ── Actions ────────────────────────────────────────────────────────────────

    def action_start_run(self) -> None:
        if self.is_running:
            self.state.set_status("Already running", StatusLevel.WARN, ttl=2)
            return
        self._start_graph_run()

    def action_reset_run(self) -> None:
        if self.is_running:
            self.state.set_status("Cannot reset while running", StatusLevel.WARN, ttl=2)
            return
        self._reset_graph()

    def action_view_spec(self) -> None:
        self._show_spec_content()

    def action_view_executor(self) -> None:
        if self._latest_run_result and "executor_output" in self._latest_run_result:
            out = self._latest_run_result["executor_output"]
            self._show_output_in_detail("⚡ Executor Output", out)
        else:
            self.state.set_status("No executor output yet — run the graph first", StatusLevel.INFO, ttl=3)

    def _on_run_pressed(self) -> None:
        self.action_start_run()

    def _on_reset_pressed(self) -> None:
        self.action_reset_run()

    def _on_spec_pressed(self) -> None:
        self.action_view_spec()

    # ── Graph execution ──────────────────────────────────────────────────────

    def _start_graph_run(self) -> None:
        """Run the graph in a background thread, publishing events via EventBus."""
        if not self._graph:
            self.state.set_status("Graph not initialized", StatusLevel.ERROR, ttl=3)
            return

        self.is_running = True
        self._set_run_status("🚀 Starting…", "warning")
        self._log_event("▶ Starting graph execution")

        def worker():
            from forge.db import ForgeDB
            from forge.agents import SubagentManager
            from forge.skills import SkillRegistry

            db = ForgeDB.for_project(self.project_id)
            skill_registry = SkillRegistry() if self.state else None
            agents = SubagentManager(self.state) if self.state else None

            runner = GraphRunner(
                g=self._graph,
                db=db,
                workdir=Path.home() / ".forge" / "workspace" / self.project_id,
                skill_registry=skill_registry,
                agents=agents,
            )
            self._runner = runner

            # Publish node-started events by patching node status updates
            _orig_update = self._graph.update_node_status
            def patched_update(node_id, status, failure_reason=None):
                _graph_event_bus.publish(GraphEvent(
                    event_type=GraphEventType.NODE_STARTED
                    if status == NodeStatus.RUNNING else
                    GraphEventType.NODE_COMPLETED
                    if status == NodeStatus.DONE else
                    GraphEventType.NODE_FAILED
                    if status == NodeStatus.FAILED else
                    GraphEventType.NODE_BLOCKED
                    if status == NodeStatus.BLOCKED else
                    GraphEventType.NODE_UPDATED,
                    node_id=node_id,
                    status=status,
                    output=self._graph.get_node(node_id).output if self._graph.get_node(node_id) else None,
                    failure_reason=failure_reason,
                    timestamp=datetime.now(timezone.utc),
                ))
                _orig_update(node_id, status, failure_reason)

            self._graph.update_node_status = patched_update

            try:
                _graph_event_bus.publish(GraphEvent(
                    event_type=GraphEventType.RUN_STARTED,
                    node_id=None, status=None, output=None, failure_reason=None,
                    timestamp=datetime.now(timezone.utc),
                ))

                result = runner.run(entry_node="orchestrator")
                self._latest_run_result = result

                if result.get("status") == "permission_required":
                    _graph_event_bus.publish(GraphEvent(
                        event_type=GraphEventType.NODE_BLOCKED,
                        node_id=result.get("node"),
                        status=NodeStatus.BLOCKED,
                        output=result,
                        failure_reason=f"{result.get('action')} on {result.get('path')}",
                        timestamp=datetime.now(timezone.utc),
                    ))

                _graph_event_bus.publish(GraphEvent(
                    event_type=(
                        GraphEventType.RUN_COMPLETED
                        if result.get("terminated", None) in (NodeStatus.DONE, None)
                        else GraphEventType.RUN_FAILED
                    ),
                    node_id=None, status=None,
                    output=result.get("visited", []),
                    failure_reason=None,
                    timestamp=datetime.now(timezone.utc),
                ))

            except Exception as e:
                _graph_event_bus.publish(GraphEvent(
                    event_type=GraphEventType.RUN_FAILED,
                    node_id=None, status=None, output=str(e),
                    failure_reason=None,
                    timestamp=datetime.now(timezone.utc),
                ))
            finally:
                self._graph.update_node_status = _orig_update

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _reset_graph(self) -> None:
        """Reset all nodes to PENDING and clear outputs."""
        if not self._graph:
            return
        for node_id in ("orchestrator", "spec_gen", "executor", "review_gate"):
            self._graph.update_node_status(node_id, NodeStatus.PENDING)
        self._refresh_node_cards()
        self._latest_run_result = None
        self._retry_count = 0
        self._run_count = 0
        self._set_run_status("🔄 Graph reset", "info")
        self._log_event("🔄 Graph reset — all nodes PENDING")
        self.query_one("#detail-panel", Container).query_one(
            "#detail-text", Static
        ).update("Graph reset — press Enter to start")

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _refresh_node_cards(self) -> None:
        if not self._graph:
            return
        for node_id in ("orchestrator", "spec_gen", "executor", "review_gate"):
            node = self._graph.get_node(node_id)
            if node:
                self._update_node_card(node_id, node.status, node.failure_reason)

    def _update_node_card(self, node_id: str, status: NodeStatus, failure_reason: Optional[str]) -> None:
        node_el = self.query_one(f"#node-{node_id}", Vertical)
        card_el = self.query_one(f"#card-{node_id}", Static)
        card_el.update(_node_card(node_id, status, failure_reason))
        # Update border class
        for cls in ("active", "done", "failed", "blocked"):
            node_el.remove_class(cls)
        if status == NodeStatus.RUNNING:
            node_el.add_class("active")
        elif status == NodeStatus.DONE:
            node_el.add_class("done")
        elif status in (NodeStatus.FAILED, NodeStatus.BLOCKED):
            node_el.add_class("failed")

    def _show_node_detail(self, node_id: str, output: Optional[dict]) -> None:
        if not output:
            return
        panel = self.query_one("#detail-panel", Container)
        text = self.query_one("#detail-text", Static)
        title, content = self._format_output(node_id, output)
        text.update(f"[bold]{title}[/]\n{content[:2000]}")

    def _show_output_in_detail(self, title: str, output: Optional[dict]) -> None:
        if not output:
            output = {}
        panel = self.query_one("#detail-panel", Container)
        text = self.query_one("#detail-text", Static)
        body = json.dumps(output, indent=2, default=str)[:3000]
        text.update(f"[bold]{title}[/]\n{body}")

    def _show_spec_content(self) -> None:
        from forge.db import ForgeDB
        db = ForgeDB.for_project(self.project_id)
        spec = db.get_spec(self._spec_version)
        if spec:
            content = spec.get("md", spec.get("content", "(empty spec)"))[:3000]
        else:
            content = "(no spec found)"
        text = self.query_one("#detail-text", Static)
        text.update(f"[bold]📄 SPEC.md — v{self._spec_version}[/]\n\n{content}")
        self._log_event(f"📄 Viewing spec v{self._spec_version}")

    def _format_output(self, node_id: str, output: dict) -> tuple[str, str]:
        if node_id == "orchestrator":
            tasks = output.get("tasks", [])
            return ("🤖 Orchestrator output", f"Tasks planned: {len(tasks)}\n" + "\n".join(f"  • {t}" for t in tasks[:10]))
        elif node_id == "spec_gen":
            return ("📐 Spec Generator output", output.get("spec_md", str(output))[:1000])
        elif node_id == "executor":
            files = output.get("files_written", [])
            return ("⚡ Executor output", f"Files written: {len(files)}\n" + "\n".join(f"  • {f}" for f in files[:20]))
        elif node_id == "review_gate":
            verdict = output.get("decision", "?")
            return (f"🚧 Review Gate — {verdict.upper()}", output.get("summary", str(output))[:1000])
        return (node_id, str(output)[:500])

    def _summarize_output(self, node_id: str, output: Optional[dict]) -> str:
        if not output:
            return "no output"
        if node_id == "orchestrator":
            tasks = output.get("tasks", [])
            return f"{len(tasks)} tasks"
        if node_id == "spec_gen":
            return f"spec v{output.get('spec_version', '?')}"
        if node_id == "executor":
            files = output.get("files_written", [])
            return f"{len(files)} files written"
        if node_id == "review_gate":
            return output.get("decision", "?")
        return "done"

    def _set_run_status(self, msg: str, level: str) -> None:
        el = self.query_one("#run-status", Static)
        color = {"success": "#22C55E", "warning": "#F59E0B", "error": "#EF4444", "info": "#6C7086"}.get(level, "#CDD6F4")
        el.update(f"[{color}]{msg}[/]")

    def _update_header(self) -> None:
        spec_el = self.query_one("#spec-badge", Static)
        spec_el.update(f"spec: v{self._spec_version}")

    def _log_event(self, msg: str) -> None:
        if self._event_log:
            ts = datetime.now().strftime("%H:%M:%S")
            self._event_log.write_line(f"[{ts}] {msg}")
