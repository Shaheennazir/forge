"""
forge.tui.components.output — StreamingOutput widget.

A read-only text widget that appends streamed tokens inline,
simulating the character-by-character output of a real AI terminal.
"""

from textual.widgets import Static
from textual.message import Message
from textual.reactive import reactive


class StreamingOutput(Static):
    """
    A text display that appends streamed tokens without overwriting.

    Usage:
        output = StreamingOutput()
        output.start_streaming()   # clears and begins
        for token in tokens:
            output.append(token)
        output.finish_streaming()

    CSS: set ``overflow-y: auto`` in parent to make it scrollable.
    """

    DEFAULT_CSS = """
    StreamingOutput {
        height: auto;
        width: 100%;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    """

    streaming = reactive(False)

    class ContentAppended(Message):
        """Posted when new content is appended while streaming."""

    class StreamingFinished(Message):
        """Posted when streaming completes (all tokens received)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._buffer = ""

    def start_streaming(self) -> None:
        """Clear buffer and enter streaming mode."""
        self._buffer = ""
        self.streaming = True
        self.update("")

    def append(self, text: str) -> None:
        """Append a token to the output. Works during streaming only."""
        if not self.streaming:
            return
        self._buffer += text
        self.update(self._buffer)
        self.post_message(self.ContentAppended())

    def finish_streaming(self) -> None:
        """Exit streaming mode."""
        self.streaming = False
        self.post_message(self.StreamingFinished())

    def set_content(self, text: str) -> None:
        """Set full content at once (for non-streaming content)."""
        self._buffer = text
        self.streaming = False
        self.update(text)
