"""
forge.tui.app — Forge Textual TUI application.

Full production-grade TUI wired to:
  - AppState (session DB, pub/sub, permissions, status)
  - LLMBridge (async streaming, tool execution)
  - HomeScreen, ChatScreen, ModelPicker, ProductCompilerScreen
  - CommandPalette (executable command overlay)
  - PermissionDialog (blocking permission modal)
  - StatusBar (TTL-based status)

Keybindings (global):
  Ctrl+A  → model picker
  Ctrl+P  → command palette
  Ctrl+H  → home / back
  Ctrl+Q  → quit
  Escape  → pop screen

Themes (switch via command palette /theme <name>): dark | light | monokai
"""

from __future__ import annotations

from textual.app import App as TuiApp
from textual.binding import Binding
from textual.widgets import Static

from forge.tui.state import AppState, StatusLevel
from forge.tui.components.status_bar import StatusBar
from forge.tui.components.command_palette import CommandPalette
from forge.tui.screens.home import HomeScreen
from forge.tui.screens.chat import ChatScreen
from forge.tui.screens.models import ModelPicker
from forge.tui.screens.product_compiler import ProductCompilerScreen
from forge.tui.screens.graph_screen import GraphScreen
from forge.tui.screens.code_intelligence_screen import CodeIntelligenceScreen


# ── Forge CSS Themes ────────────────────────────────────────────────────────────

_FORGE_DARK_CSS = """
/* Forge Dark — rich purple accent */
$primary: #7C3AED;
$accent: #A78BFA;
$surface: #1E1E2E;
$surface-darken-1: #2A2A3E;
$surface-darken-2: #16161E;
$text: #CDD6F4;
$text-muted: #6C7086;
$warning: #F59E0B;
$error: #EF4444;
$success: #22C55E;
"""

_FORGE_LIGHT_CSS = """
/* Forge Light */
$primary: #7C3AED;
$accent: #7C3AED;
$surface: #FAFAFA;
$surface-darken-1: #F0F0F0;
$surface-darken-2: #E5E5E5;
$text: #1A1A2E;
$text-muted: #6B7280;
$warning: #D97706;
$error: #DC2626;
$success: #16A34A;
"""

_FORGE_MONOKAI_CSS = """
/* Monokai-inspired */
$primary: #A6E22E;
$accent: #AE81FF;
$surface: #272822;
$surface-darken-1: #1E1F1C;
$surface-darken-2: #111111;
$text: #F8F8F2;
$text-muted: #75715E;
$warning: #E6DB74;
$error: #F92672;
$success: #A6E22E;
"""


class ForgeApp(TuiApp):
    """
    Main Forge TUI application.

    Singleton entry point. All screens are lazily registered here.
    AppState singleton is shared across all screens.
    """

    TITLE = "Forge"
    SUB_TITLE = "AI Coding System"

    BINDINGS = [
        ("ctrl+h", "go_home",         "Home"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+a", "model_picker",    "Model"),
        ("ctrl+q", "quit",            "Quit"),
        ("escape", "pop_screen",       "Back"),
    ]

    # Default CSS
    CSS = _FORGE_DARK_CSS

    _THEMES = {
        "dark":    _FORGE_DARK_CSS,
        "light":   _FORGE_LIGHT_CSS,
        "monokai": _FORGE_MONOKAI_CSS,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = AppState.get()
        self._theme_name = "dark"

        # Screen constructors (lazy — instantiated on first use)
        self._screen_registry = {
            "home":              HomeScreen,
            "chat":              ChatScreen,
            "model_picker":      ModelPicker,
            "product_compiler":  ProductCompilerScreen,
            "graph":             GraphScreen,
            "ci":                CodeIntelligenceScreen,
        }

        self.state.set_status("Forge ready", StatusLevel.SUCCESS, ttl=3)

    def compose(self) -> ComposeResult:
        yield StatusBar(self.state, id="global-status")

    # ── Screen stack ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.push_screen("home")

    def push_screen(self, screen: "str | Screen", name: str | None = None) -> None:
        """
        Support both string-named lazy screens and direct Screen instances.
        String names are resolved from _screen_registry.
        """
        if isinstance(screen, str):
            screen_name = name or screen
            lazy = self._screen_registry.get(screen)
            if lazy is not None:
                screen = lazy()
            else:
                return  # Unknown screen — silently skip
        super().push_screen(screen, name)

    # ── Global actions ───────────────────────────────────────────────────────

    def action_go_home(self) -> None:
        self.push_screen("home")

    def action_model_picker(self) -> None:
        self.push_screen("model_picker")

    def action_command_palette(self) -> None:
        self.push_screen(
            CommandPalette(on_execute=self._execute_command),
            "command_palette",
        )

    def action_quit(self) -> None:
        self.exit(result="quit")

    # ── Command palette dispatch ─────────────────────────────────────────────

    def _execute_command(self, cmd: str, args: str) -> None:
        """Route a executed command from the palette to an action."""
        dispatch = {
            "new":       lambda: self.push_screen(ChatScreen()),
            "model":     lambda: self.push_screen("model_picker"),
            "compile":   lambda: self.push_screen(
                ProductCompilerScreen(), "product_compiler",
            ),
            "clear":     lambda: self.state.set_status(
                "Output cleared", StatusLevel.INFO, ttl=2,
            ),
            "cancel":    lambda: self.state.set_status(
                "No active stream", StatusLevel.INFO, ttl=2,
            ),
            "sessions":  lambda: self.push_screen("home"),
            "projects":  lambda: self.push_screen("home"),
            "graph":     lambda: self.push_screen("graph"),
            "ci":        lambda: self.push_screen("ci"),
            "intelligence": lambda: self.push_screen("ci"),
            "code":      lambda: self.push_screen("ci"),
            "exit":      lambda: self.exit(result="quit"),
            "help":      lambda: self.state.set_status(
                "Ctrl+A: model  |  Ctrl+P: commands  |  "
                "Ctrl+H: home  |  Ctrl+Q: quit",
                StatusLevel.INFO, ttl=6,
            ),
            "theme":     lambda a: self._set_theme(a),
        }
        action = dispatch.get(cmd)
        if action:
            action()
        elif cmd:
            self.state.set_status(
                f"Unknown command: /{cmd}", StatusLevel.WARN, ttl=3,
            )

    def _set_theme(self, name: str) -> None:
        """Switch the active CSS theme at runtime."""
        if name not in self._THEMES:
            self.state.set_status(
                f"Themes: {'  '.join(self._THEMES.keys())}", StatusLevel.INFO, ttl=4,
            )
            return
        self.CSS = self._THEMES[name]
        self.refresh_css()
        self.state.set_status(f"Theme: {name}", StatusLevel.INFO, ttl=3)


def run_tui():
    """Entry point: forge tui"""
    app = ForgeApp()
    app.run()


if __name__ == "__main__":
    run_tui()
