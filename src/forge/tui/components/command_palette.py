"""
forge.tui.components.command_palette — Executable command palette.

Modal overlay that fuzzy-matches commands and executes them with arguments.

Commands:
  /new          — new chat session
  /model        — open model picker
  /project <n>  — switch to project N
  /compile      — open product compiler
  /clear        — clear current output
  /cancel       — cancel current LLM call
  /theme <name> — switch theme
  /session      — list sessions
  /exit         — quit forge
  /help         — show help
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static, Label


class CommandPalette(ModalScreen):
    """
    Executable command palette (Ctrl+P).

    Type a command, press Enter to execute.
    Arrow keys navigate completions.
    Escape closes without executing.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Close"),
        ("ctrl+p", "app.pop_screen", "Close"),
    ]

    COMMANDS = [
        ("new",          "new",           "Start a new chat session"),
        ("model",        "model",          "Open model picker"),
        ("compile",      "compile",        "Open product compiler"),
        ("clear",        "clear",          "Clear current output"),
        ("cancel",       "cancel",         "Cancel running LLM call"),
        ("sessions",     "sessions",       "Browse session history"),
        ("projects",     "projects",       "Switch project"),
        ("exit",         "exit",           "Exit Forge"),
        ("help",         "help",           "Show keyboard shortcuts"),
    ]

    def __init__(self, on_execute: callable = None, **kwargs):
        super().__init__(**kwargs)
        self.on_execute = on_execute  # callback(cmd_name, args_str)
        self._selected = 0

    def compose(self) -> ComposeResult:
        yield Container(
            Static("COMMAND PALETTE", id="cp-title"),
            Input(placeholder="Type a command... (/help for list)", id="cp-input"),
            Vertical(id="cp-results"),
            id="cp-box",
        )

    def on_mount(self) -> None:
        self.query_one("#cp-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_completions(event.value)

    def _update_completions(self, query: str) -> None:
        results = self.query_one("#cp-results", Vertical)
        results.remove_children()

        if not query:
            query = "/"

        query = query.lstrip("/")
        matched = [
            (name, desc)
            for name, cmd, desc in self.COMMANDS
            if query.lower() in name.lower() or query.lower() in cmd.lower()
        ]

        for i, (name, desc) in enumerate(matched[:8]):
            marker = "▶ " if i == self._selected else "  "
            results.mount(
                Static(f"{marker}[bold]{name}[/bold]  [dim]{desc}[/dim]", id=f"cp-opt-{i}")
            )

        self._selected = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            self.app.pop_screen()
            return

        cmd_name = value.lstrip("/").split()[0].lower()
        args = " ".join(value.lstrip("/").split()[1:])

        for name, cmd, _ in self.COMMANDS:
            if cmd == cmd_name:
                self.app.pop_screen()
                if self.on_execute:
                    self.on_execute(cmd, args)
                return

        # Unknown command
        self.app.pop_screen()
