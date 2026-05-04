"""
forge.tui.screens.home — Project hub and session browser.

Landing screen showing:
  - Recent projects (scanned from ~/.forge/projects/)
  - Recent sessions across all projects
  - Quick actions: new session, open product compiler, model picker
  - Forge version / status

Keybindings:
  Enter       → open selected session in chat
  Ctrl+N      → new session
  Ctrl+P      → command palette
  Ctrl+C      → product compiler
  R           → refresh project list
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static, Button, ListView, Log
from textual.css.query import NoMatches

from forge.tui.state import AppState, Session
from forge.tui.screens.chat import ChatScreen


class HomeScreen(Screen):
    """
    Forge landing screen — project browser + session hub.

    Layout:
      [header: FORGE title + version]
      [left: project list]  [right: session list]
      [bottom: quick actions]
    """

    TITLE = "Forge Home"
    BINDINGS = [
        ("ctrl+n", "new_session", "New Chat"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+c", "product_compiler", "Product Compiler"),
        ("ctrl+a", "model_picker", "Model"),
        ("r", "refresh", "Refresh"),
        ("enter", "open_selected", "Open"),
    ]

    CSS = """
    HomeScreen {
        layout: horizontal;
    }

    #home-left {
        width: 40%;
        border-right: solid $primary 20%;
        background: $surface-darken-2;
    }

    #home-right {
        width: 60%;
    }

    #home-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #home-section-projects {
        height: 2;
        background: $surface-darken-1;
        color: $text-muted;
        text-style: bold;
        padding: 0 2;
    }

    #home-section-sessions {
        height: 2;
        background: $surface-darken-1;
        color: $text-muted;
        text-style: bold;
        padding: 0 2;
    }

    #project-list {
        height: 1fr;
    }

    #session-list {
        height: 1fr;
    }

    #home-footer {
        height: auto;
        dock: bottom;
        background: $surface-darken-1;
        padding: 1 2;
    }

    .section-title {
        text-style: bold;
        color: $text-muted;
    }

    .project-item {
        padding: 0 1;
    }

    .project-item:hover {
        background: $primary 20%;
    }

    .session-item {
        padding: 0 1;
    }

    .session-item:hover {
        background: $primary 20%;
    }

    .session-active {
        background: $primary 30%;
    }
    """

    class OpenSession(Message):
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            super().__init__()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = AppState.get()
        self._projects: list = []
        self._sessions: list[Session] = []
        self._focused = "sessions"  # "projects" | "sessions"

    def compose(self) -> ComposeResult:
        yield Container(
            Vertical(
                Static("  💠  FORGE  —  AI Coding System", id="home-header"),
                Static("PROJECTS", id="home-section-projects"),
                ListView(id="project-list"),
                Static("RECENT SESSIONS", id="home-section-sessions"),
                ListView(id="session-list"),
                id="home-left",
            ),
            Vertical(
                Static("FORGE v0.6", id="home-version"),
                Static(
                    "Ctrl+N: New Chat  |  "
                    "Ctrl+P: Commands  |  "
                    "Ctrl+C: Product Compiler  |  "
                    "Ctrl+A: Model  |  "
                    "R: Refresh",
                    id="home-help",
                ),
                id="home-right",
            ),
            id="home-body",
        )

    def on_mount(self) -> None:
        self.state.bus.subscribe("session", self._on_session_event)
        self._refresh()

    def _on_session_event(self, event: tuple) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self._projects = self.state.list_projects()
        self._sessions = self.state.list_sessions()

        # Populate project list
        pl = self.query_one("#project-list", ListView)
        pl.clear()
        for name, path, mtime in self._projects:
            from datetime import datetime
            age = datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M")
            pl.append(f"{name}  [dim]{age}[/dim]", key=name)

        # Populate session list
        sl = self.query_one("#session-list", ListView)
        sl.clear()
        for s in self._sessions[:20]:
            cost = f"${s.cost:.4f}" if s.cost else "$0"
            label = f"{s.title or 'New Chat'}  [{s.model}]  {s.total_tokens:,}t  {cost}"
            sl.append(label, key=s.id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.control.id == "project-list":
            self._open_project(event.item_key)
        elif event.control.id == "session-list":
            self._open_session(event.item_key)

    def _open_project(self, project_name: str) -> None:
        """Open a project: create a new session and switch to chat."""
        session = self.state.create_session(
            project_name=project_name,
            model=self.state.current_model,
            provider=self.state.current_provider,
        )
        self._navigate_to_chat(session.id)

    def _open_session(self, session_id: str) -> None:
        """Open an existing session and restore its history."""
        self.state.set_current_session(session_id)
        self._navigate_to_chat(session_id, restore=True)

    def _navigate_to_chat(self, session_id: str, restore: bool = True) -> None:
        self.app.push_screen(
            ChatScreen(session_id=session_id, restore=restore),
            "chat",
        )

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_new_session(self) -> None:
        session = self.state.create_session(
            project_name="default",
            model=self.state.current_model,
            provider=self.state.current_provider,
        )
        self._navigate_to_chat(session.id)

    def action_command_palette(self) -> None:
        self.app.push_screen("command_palette")

    def action_product_compiler(self) -> None:
        from forge.tui.screens.product_compiler import ProductCompilerScreen
        self.app.push_screen(ProductCompilerScreen(), "product_compiler")

    def action_model_picker(self) -> None:
        self.app.push_screen("model_picker")

    def action_refresh(self) -> None:
        self._refresh()
        self.state.set_status("Refreshed", StatusLevel.INFO, ttl=2)

    def action_open_selected(self) -> None:
        try:
            sl = self.query_one("#session-list", ListView)
            selected = sl.index
            if 0 <= selected < len(self._sessions):
                self._open_session(self._sessions[selected].id)
        except Exception:
            pass
