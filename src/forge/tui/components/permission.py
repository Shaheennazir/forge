"""
forge.tui.components.permission — Blocking permission dialog.

Pops up as a modal when the LLM requests a dangerous action
(read .env, run rm, write outside project, etc.).

Subscribes to "permission" events on the bus.
User can: Allow (once), Allow+Remember, Deny.

The dialog is blocking — it halts the tool execution until resolved.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Input

from forge.tui.state import AppState, PermissionRequest


class PermissionDialog(ModalScreen):
    """
    Modal dialog for permission requests.

    Shows:
      - Tool name + action
      - Path/resource
      - Agent making the request
      - [Allow Once] [Allow+Remember] [Deny]

    Escape or Ctrl+C dismisses with Deny.
    """

    BINDINGS = [
        ("escape", "deny", "Deny"),
        ("ctrl+a", "allow_once", "Allow Once"),
        ("ctrl+r", "allow_remember", "Remember"),
        ("ctrl+d", "deny", "Deny"),
    ]

    def __init__(self, request: PermissionRequest, state: AppState, **kwargs):
        super().__init__(**kwargs)
        self.request = request
        self.state = state
        self._result: tuple[bool, bool] | None = None  # (allowed, remembered)

    def compose(self) -> ComposeResult:
        key = f"{self.request.tool}:{self.request.action}:{self.request.path}"
        yield Container(
            Static(f"🔒  PERMISSION REQUIRED", id="perm-title"),
            Static(f"Agent: [bold]{self.request.agent}[/bold]", id="perm-agent"),
            Static(f"Tool:  [warning]{self.request.tool}[/warning]  {self.request.action}"),
            Static(f"Path:  {self.request.path}", id="perm-path"),
            Static(f"Key:   [dim]{key[:60]}[/dim]", id="perm-key"),
            Container(
                Button("Allow Once  (Ctrl+A)", id="btn-allow-once", variant="primary"),
                Button("Remember  (Ctrl+R)", id="btn-allow-remember", variant="success"),
                Button("Deny  (Ctrl+D / Esc)", id="btn-deny", variant="error"),
                id="perm-buttons",
            ),
            id="perm-box",
        )

    def on_mount(self) -> None:
        self.query_one("#perm-box").focus()

    def action_allow_once(self) -> None:
        self._result = (True, False)
        self.app.pop_screen()

    def action_allow_remember(self) -> None:
        self._result = (True, True)
        self.app.pop_screen()

    def action_deny(self) -> None:
        self._result = (False, False)
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-allow-once":
            self.action_allow_once()
        elif bid == "btn-allow-remember":
            self.action_allow_remember()
        elif bid == "btn-deny":
            self.action_deny()

    def get_result(self) -> tuple[bool, bool]:
        """Returns (allowed, remembered). Call after dismiss."""
        return self._result or (False, False)
