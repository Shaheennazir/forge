"""
forge.tui.components.sidebar — Session list + project browser sidebar.

Shows:
  - Project selector (left side, collapsible)
  - Session list with title, model, token count, cost
  - Active session highlighted
  - Right-click context menu: rename, delete, export

Subscribes to:
  - "session" events → refresh list
  - "status" events → update token/cost display
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets._list_view import ListView
from textual.widgets import Static, Tree, Label, Button
from textual.css.query import NoMatches

from forge.tui.state import AppState, Session


class Sidebar(Container):
    """
    Left-side session/project browser.

    Binds:
      Ctrl+S  → focus session list
      Ctrl+P  → focus project list
    """

    CSS = """
    Sidebar {
        width: 32;
        max-width: 40;
        background: $surface-darken-2;
        border-right: solid $primary 30%;
        height: 100%;
    }

    #sb-header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #sb-section-label {
        height: 2;
        color: $text-muted;
        text-style: bold;
        padding: 0 1;
    }

    #sb-session-list {
        height: 1fr;
    }

    #sb-footer {
        height: auto;
        background: $surface-darken-1;
        padding: 1 1;
    }

    .session-item {
        padding: 0 1;
    }

    .session-item:hover {
        background: $primary 20%;
    }

    .session-active {
        background: $primary 30%;
        color: $accent;
    }

    .session-title {
        text-style: bold;
    }

    .session-meta {
        color: $text-muted;
        text-style: italic;
    }
    """

    class SessionChosen(Message):
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            super().__init__()

    BINDINGS = [
        ("ctrl+s", "focus_sessions", "Sessions"),
        ("ctrl+n", "new_session", "New"),
    ]

    def __init__(self, state: AppState, **kwargs):
        super().__init__(**kwargs)
        self.state = state
        self._sessions: list[Session] = []
        self._active_id: str = ""

    def compose(self) -> ComposeResult:
        yield Static("FORGE", id="sb-header")
        yield Static("SESSIONS", id="sb-section-label")
        yield ListView(id="sb-session-list")
        yield Container(id="sb-footer")

    def on_mount(self) -> None:
        self.state.bus.subscribe("session", self._on_session_event)
        self.state.bus.subscribe("model", self._on_model_event)
        self._refresh()

    def _on_session_event(self, event: tuple) -> None:
        self._refresh()

    def _on_model_event(self, event: tuple) -> None:
        self._refresh_meta()

    def _refresh(self) -> None:
        self._sessions = self.state.list_sessions()
        lv = self.query_one("#sb-session-list", ListView)
        lv.clear()
        for s in self._sessions:
            label = self._session_label(s)
            key = s.id
            lv.append(label, key=key)
        self._refresh_meta()

    def _session_label(self, s: Session) -> str:
        cost = f"${s.cost:.4f}" if s.cost else "$0.00"
        return f"{s.title or 'New Chat'}  [{s.model}]  {s.total_tokens:,}t  {cost}"

    def _refresh_meta(self) -> None:
        current = self.state.get_current_session()
        if not current:
            return
        footer = self.query_one("#sb-footer", Container)
        footer.remove_children()
        footer.mount(Static(
            f"[{current.model}]  {current.total_tokens:,}t  ${current.cost:.4f}",
            id="sb-meta",
        ))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.post_message(self.SessionChosen(event.item_key))

    def action_focus_sessions(self) -> None:
        self.query_one("#sb-session-list", ListView).focus()

    def action_new_session(self) -> None:
        session = self.state.create_session(
            project_name="default",
            model=self.state.current_model,
            provider=self.state.current_provider,
        )
        self._refresh()
        self.post_message(self.SessionChosen(session.id))
