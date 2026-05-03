"""forge.tui.components.status — StatusBar widget."""

from textual.widgets import Static


class StatusBar(Static):
    """
    One-line status bar docked to the bottom of the screen.

    Usage:
        status = StatusBar()
        status.set("Model: gpt-4o  •  streaming…")
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
        text-style: italic;
    }
    """

    def set(self, text: str) -> None:
        self.update(text)
