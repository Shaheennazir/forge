"""forge.tui.components.prompt — PromptInput widget."""

from textual.widgets import Input


class PromptInput(Input):
    """
    Styled single-line input for the chat prompt.

    Exists as a dedicated widget so the app can style it
    consistently and reference it by ID across screens.
    """
    DEFAULT_CSS = """
    PromptInput {
        margin: 1 2;
        border: solid $border;
    }

    PromptInput:focus {
        border: solid $focus;
    }
    """
