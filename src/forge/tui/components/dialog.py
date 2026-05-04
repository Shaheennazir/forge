"""
forge.tui.components.dialog — CommandPaletteDialog.

A fuzzy-search command palette that slides down from the top,
similar to VS Code's command palette (Ctrl+Shift+P).
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static, ListView, ListItem


COMMANDS = [
    ("new chat",              "Start a new chat session"),
    ("forge new",             "forge new <prompt> — start a new project"),
    ("forge continue",        "Resume the last project"),
    ("forge plan",            "forge plan <description> — plan without executing"),
    ("forge status",          "Show project status"),
    ("forge setup",           "Configure LLM provider"),
    ("forge compile",         "forge compile <prompt> — Product Compiler pipeline"),
    ("switch model",          "Pick a different model (Ctrl+A)"),
    ("exit",                  "Exit Forge TUI"),
]


class CommandPaletteDialog(Screen):
    """
    Fuzzy-search command palette.

    Usage:
        def on_select(command: str) -> None: ...

        self.push_screen(CommandPaletteDialog(on_select=on_select))
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("enter", "run_selected", "Run"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
    ]

    class CommandSelected(Message):
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    def __init__(self, on_select: callable = None, **kwargs):
        super().__init__(**kwargs)
        self._on_select = on_select
        self._filtered = list(COMMANDS)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Type a command…", id="palette-input")
        yield ListView(*[ListItem(Static(f"[b]{cmd}[/]  [dim]{desc}")) for cmd, desc in self._filtered],
                      id="palette-list")

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Fuzzy filter commands as user types."""
        query = event.value.lower().strip()
        if query:
            self._filtered = [
                (cmd, desc) for cmd, desc in COMMANDS
                if query in cmd.lower() or query in desc.lower()
            ]
        else:
            self._filtered = list(COMMANDS)

        list_view = self.query_one("#palette-list", ListView)
        list_view.clear()
        list_view.extend(
            ListItem(Static(f"[b]{cmd}[/]  [dim]{desc}"))
            for cmd, desc in self._filtered
        )
        if self._filtered:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._run_selected()

    def action_close(self) -> None:
        self.dismiss()

    def action_run_selected(self) -> None:
        self._run_selected()

    def _run_selected(self) -> None:
        list_view = self.query_one("#palette-list", ListView)
        if list_view.index is None or not self._filtered:
            return
        cmd = self._filtered[list_view.index][0]
        self.dismiss()
        if self._on_select:
            self._on_select(cmd)
