"""
forge.tui.components.status_bar — Real-time status bar with TTL auto-dismiss.

Subscribes to "status" events from the bus.
Each message has a TTL — after TTL seconds it is replaced by the next.
Persistent messages (ttl=0) stay until explicitly dismissed.
"""

from __future__ import annotations

import time
import threading
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from forge.tui.state import AppState, StatusLevel, StatusMsg


class StatusBar(Horizontal):
    """
    Bottom status bar showing context-aware messages.

    Auto-dismisses INFO/WARN messages after their TTL.
    Persistent messages (ttl=0) show until the next non-persistent message.
    ERROR messages are persistent until the next non-error.

    Layout: [level-icon] [message]               [model] [tokens] [cost]
    """

    CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $surface-darken-1;
        padding: 0 2;
        align: left middle;
    }

    #sb-left {
        width: 60%;
    }

    #sb-right {
        width: 40%;
        align: right;
    }

    .status-info {
        color: $text;
    }

    .status-warn {
        color: $warning;
        text-style: bold;
    }

    .status-error {
        color: $error;
        text-style: bold;
    }

    .status-success {
        color: $success;
    }

    #sb-model-tag {
        color: $text-muted;
        margin-left: auto;
    }

    #sb-tokens {
        color: $text-muted;
        margin-left: 2;
    }
    """

    def __init__(self, state: AppState, **kwargs):
        super().__init__(**kwargs)
        self.state = state
        self._dismiss_timer: threading.Timer | None = None
        self._current: StatusMsg | None = None

    def compose(self) -> ComposeResult:
        yield Static("ready", id="sb-status-text", classes="status-info")
        yield Static("", id="sb-model-tag")
        yield Static("", id="sb-tokens")

    def on_mount(self) -> None:
        self.state.bus.subscribe("status", self._on_status)
        self.state.bus.subscribe("model", self._on_model)
        self.state.bus.subscribe("session", self._on_session)

    def _level_class(self, level: StatusLevel) -> str:
        return f"status-{level.value}"

    def _level_icon(self, level: StatusLevel) -> str:
        icons = {
            StatusLevel.INFO: "●",
            StatusLevel.WARN: "▲",
            StatusLevel.ERROR: "✗",
            StatusLevel.SUCCESS: "✓",
        }
        return icons.get(level, "●")

    def _on_status(self, msg: StatusMsg) -> None:
        self._current = msg
        text_el = self.query_one("#sb-status-text", Static)
        cls = self._level_class(msg.level)
        text_el.update(f"{self._level_icon(msg.level)}  {msg.text}")
        text_el.set_class(cls, "status-info")
        text_el.set_class(cls, "status-warn")
        text_el.set_class(cls, "status-error")
        text_el.set_class(cls, "status-success")
        text_el.set_class(True, cls)

        # Cancel any pending dismiss
        if self._dismiss_timer:
            self._dismiss_timer.cancel()

        # Auto-dismiss after TTL (0 = persistent)
        if msg.ttl > 0:
            self._dismiss_timer = threading.Timer(msg.ttl, self._dismiss)
            self._dismiss_timer.start()

    def _dismiss(self) -> None:
        # Replace with the next message in queue
        next_msg = self.state.pop_status()
        if next_msg:
            self._on_status(next_msg)
        else:
            # Back to idle
            text_el = self.query_one("#sb-status-text", Static)
            text_el.set_class(False, "status-warn", "status-error", "status-success")
            text_el.set_class(True, "status-info")
            text_el.update("ready")

    def _on_model(self, event: tuple) -> None:
        provider, model = event
        self.query_one("#sb-model-tag", Static).update(f"{provider}/{model}")

    def _on_session(self, event: tuple) -> None:
        op, session = event
        if op in ("updated", "selected") and session:
            self.query_one("#sb-tokens", Static).update(
                f" {session.total_tokens:,}t  ${session.cost:.4f}"
            )

    def clear(self) -> None:
        """Clear the status bar."""
        if self._dismiss_timer:
            self._dismiss_timer.cancel()
        text_el = self.query_one("#sb-status-text", Static)
        text_el.set_class(False, "status-warn", "status-error", "status-success")
        text_el.set_class(True, "status-info")
        text_el.update("ready")
