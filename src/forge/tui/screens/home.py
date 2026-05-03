"""
forge.tui.screens.home — HomeScreen.

Lists recent Forge project sessions and provides quick actions:
  - Continue a recent project
  - Start a new chat (opens ChatScreen)
  - Forge keyboard shortcuts legend
"""

from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, ListView, ListItem


class HomeScreen(Screen):
    """
    Entry screen showing recent sessions and shortcuts.

    From here:
      - Enter on a session → resumes that project
      - 'n' or button → new chat
      - Ctrl+A → model picker
      - Ctrl+P → command palette
    """

    CSS = """
    HomeScreen {
        layout: vertical;
        align: center middle;
    }

    # home-container {
        width: 70;
        max-width: 80;
        height: auto;
    }

    # home-title {
        text-align: center;
        margin-bottom: 2;
    }

    # session-list {
        height: 12;
        margin: 1 0;
    }

    # shortcuts {
        text-style: italic;
        color: $text-muted;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[b]◆ Forge[/b]  —  Multi-agent coding CLI", id="home-title")
        yield Static("Recent sessions:", id="section-label")
        yield ListView(id="session-list")
        yield Static(
            "[dim]Ctrl+A: model  •  Ctrl+P: commands  •  Enter: open session  •  N: new chat[/dim]",
            id="shortcuts",
        )

    def on_mount(self) -> None:
        self._load_recent_sessions()

    def _load_recent_sessions(self) -> None:
        list_view = self.query_one("#session-list", ListView)
        projects_root = Path.home() / ".forge" / "projects"

        if not projects_root.exists():
            list_view.append(ListItem(Static("[dim]No projects yet — run 'forge new <prompt>'[/dim]")))
            return

        candidates = [
            p for p in projects_root.iterdir()
            if (p / "memory.db").exists()
        ]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not candidates:
            list_view.append(ListItem(Static("[dim]No projects yet — run 'forge new <prompt>'[/dim]")))
            return

        for p in candidates[:10]:
            ts = p.stat().st_mtime
            from datetime import datetime
            dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            list_view.append(ListItem(
                Static(f"[b]{p.name}[/]  [dim]{dt}[/dim]")
            ))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open the selected session's chat screen."""
        list_view = self.query_one("#session-list", ListView)
        if list_view.index is None:
            return
        # Get project name from the ListView items
        item = list_view.children[list_view.index]
        static = item.query_one(Static)
        text = static.renderable
        # Extract project name (first word before '  ')
        project_name = text.split("  ")[0].strip()
        # TODO: wire to ChatScreen with session restore
        self.app.notify(f"Opening {project_name}…")
