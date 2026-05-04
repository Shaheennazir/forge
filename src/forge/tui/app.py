"""
forge.tui.app — ForgeTUI: full-screen terminal UI for Forge.

Chat-first design inspired by opencode's TUI:
  - Ctrl+A : model picker (switch provider/model)
  - Ctrl+P : command palette (forge commands)
  - Ctrl+C : cancel current run
  - Escape : close dialogs / go home

Screens:
  - HomeScreen  — session browser / recent projects
  - ChatScreen  — main chat + streaming output
  - ModelPicker — DataTable of available models
  - CommandPalette — fuzzy command search
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Header, Input, Static

from forge.tui.screens.chat import ChatScreen
from forge.tui.screens.home import HomeScreen
from forge.tui.screens.models import ModelPicker
from forge.tui.screens.product_compiler import ProductCompilerScreen
from forge.tui.components.dialog import CommandPaletteDialog
from forge.tui.context import AppContext


class ForgeTUI(App):
    """
    Full-screen Forge terminal UI.

    Default screen is HomeScreen. From there the user can:
      - Start a new chat session (opens ChatScreen)
      - Pick a model (opens ModelPicker)
      - Open the command palette (Ctrl+P)
    """

    TITLE = "Forge"
    BINDINGS = [
        ("ctrl+a", "open_model_picker", "Model"),
        ("ctrl+p", "open_command_palette", "Commands"),
        ("ctrl+c", "cancel_run", "Cancel"),
        ("escape", "go_home", "Home"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    # header-bar {
        height: 3;
        dock: top;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }

    # header-bar > Static {
        margin: 0 4;
    }

    # model-badge {
        color: $text-muted;
    }

    # status-bar {
        height: 3;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
    }

    #prompt-input {
        margin: 1 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ctx = AppContext()
        self._llm = None  # set when model is selected
        self._chat_screen: ChatScreen | None = None

    # ── Boot ──────────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        # Always show header + status bar
        self.mount(Static("", id="header-bar"))
        self.mount(Static("", id="status-bar"))
        self._update_header()

    # ── Bindings ─────────────────────────────────────────────────────────────

    def action_open_model_picker(self) -> None:
        self.push_screen(ModelPicker(on_select=self._on_model_selected))

    def action_open_command_palette(self) -> None:
        self.push_screen(CommandPaletteDialog(on_select=self._on_command))

    def action_cancel_run(self) -> None:
        if self._chat_screen:
            self._chat_screen.action_cancel()

    def action_go_home(self) -> None:
        self.pop_screen(self.query("Screen").last() if self.screen != self.query("Screen").first() else None)

    # ── Model selection ───────────────────────────────────────────────────────

    def _on_model_selected(self, provider: str, model: str) -> None:
        from forge.llm import LLMConfig, create_backend
        try:
            cfg = LLMConfig(provider=provider, model=model)
            self._llm = create_backend(cfg)
            self.ctx.current_model = f"{provider}/{model}"
        except Exception as e:
            self.notify(f"Failed to load model: {e}", severity="error")
            return

        self._update_header()
        # Pop the model picker
        self.pop_screen()
        # Open a new chat
        self._open_chat()

    def _open_chat(self) -> None:
        self._chat_screen = ChatScreen(
            project_name=self.ctx.current_session or "new",
            model=self.ctx.current_model or "—",
        )
        self.push_screen(self._chat_screen)

    # ── Command palette ───────────────────────────────────────────────────────

    def _on_command(self, command: str) -> None:
        self.pop_screen()  # close palette
        if command == "new chat":
            self._open_chat()
        elif command == "forge new":
            # Open chat directly
            self._open_chat()
        elif command == "forge compile":
            self.push_screen(ProductCompilerScreen())
        elif command == "forge setup":
            self.notify("Run `forge setup` in your terminal to configure providers.")
        elif command == "exit":
            self.exit()

    # ── Header / status ──────────────────────────────────────────────────────

    def _update_header(self) -> None:
        bar = self.query_one("#header-bar", Static)
        model_info = self.ctx.current_model or "no model selected"
        bar.update(f"[b]◆ Forge[/]   model: [i]{model_info}[/]   [dim]Ctrl+A: model  Ctrl+P: commands[/]")

    def set_status(self, text: str) -> None:
        status = self.query_one("#status-bar", Static)
        status.update(text)
