"""
forge.tui.components.streaming_output — Real-time token streaming display.

Shows live tokens as they arrive from the LLM. Handles:
  - Incremental token append (per-character for smooth display)
  - Tool call badges (show when a tool is executing)
  - Tool result display (collapsible, monospace)
  - Thinking indicator (animated "..." for reasoning content)
  - Error display (red text)
  - Completion state (cursor stops blinking)

States:
  - idle       → empty, waiting for input
  - streaming  → tokens appearing in real time
  - tool_call  → shows tool name + spinner
  - tool_result → shows result block
  - error      → red error message
  - done       → complete, cursor stopped
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets._text_area import TextArea
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.events import Mount
from textual.containers import Container, ScrollableContainer
from textual.widgets import Static, Label
from textual.css.query import NoMatches

from forge.tui.state import StreamEvent


class StreamingOutput(Container):
    """
    Real-time streaming output widget.

    Messages posted:
      - StreamingOutput.ContentAppended  → content changed (for auto-scroll)
      - StreamingOutput.StreamingFinished → stream complete
      - StreamingOutput.ToolStarted      → tool call started
      - StreamingOutput.ToolFinished     → tool result ready
    """

    class ContentAppended(Message):
        pass

    class StreamingFinished(Message):
        pass

    class ToolStarted(Message):
        def __init__(self, tool_name: str, tool_id: str) -> None:
            self.tool_name = tool_name
            self.tool_id = tool_id
            super().__init__()

    class ToolFinished(Message):
        def __init__(self, tool_name: str, tool_id: str, content: str, is_error: bool) -> None:
            self.tool_name = tool_name
            self.tool_id = tool_id
            self.content = content
            self.is_error = is_error
            super().__init__()

    CSS = """
    StreamingOutput {
        height: auto;
        max-height: 40%;
        overflow: hidden;
        border: solid $primary 20%;
        margin: 1 2;
        padding: 1 2;
        background: $surface;
    }

    #so-content {
        width: 100%;
        height: auto;
        color: $text;
        content: "";
    }

    #so-tool-area {
        margin-top: 1;
        padding: 1 2;
        border: solid $primary 40%;
        background: $surface-darken-1;
    }

    .tool-badge {
        color: $warning;
        text-style: bold;
    }

    .tool-running {
        color: $primary;
    }

    .tool-result {
        color: $text-muted;
        padding: 0 2;
        max-height: 10;
        overflow-y: auto;
    }

    .tool-error {
        color: $error;
    }

    #so-thinking {
        color: $text-muted;
        text-style: italic;
    }

    .cursor-blink {
        text-style: blink;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state = "idle"  # idle | streaming | tool_call | tool_result | error | done
        self._buffer = ""
        self._tool_calls: list[dict] = []
        self._current_tool: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="so-content")
        yield Static("", id="so-tool-area", display=False)

    # ── Public API ──────────────────────────────────────────────────────────────

    def start_streaming(self) -> None:
        """Reset and begin a fresh stream."""
        self._state = "streaming"
        self._buffer = ""
        self._tool_calls = []
        self._current_tool = None
        self.query_one("#so-content", Static).update("")
        tool_area = self.query_one("#so-tool-area", Static)
        tool_area.display = False
        tool_area.update("")

    def append_token(self, token: str) -> None:
        """Append a single token / text chunk to the buffer."""
        if not token:
            return
        self._buffer += token
        self._state = "streaming"
        content = self.query_one("#so-content", Static)
        content.update(self._buffer)
        self.post_message(self.ContentAppended())

    def show_thinking(self) -> None:
        """Show thinking/reasoning indicator."""
        content = self.query_one("#so-content", Static)
        if self._buffer:
            self._buffer += "\n\n[thinking...]\n"
        else:
            self._buffer = "[thinking...]\n"
        content.update(self._buffer)

    def tool_started(self, tool_name: str, tool_id: str) -> None:
        """Record that a tool call has started."""
        self._state = "tool_call"
        self._current_tool = {"name": tool_name, "id": tool_id, "result": ""}
        self._tool_calls.append(self._current_tool)

        tool_area = self.query_one("#so-tool-area", Static)
        tool_area.display = True
        tool_area.update(f"[dim]⟳[/] {tool_name}...")
        self.post_message(self.ToolStarted(tool_name, tool_id))

    def tool_finished(self, tool_name: str, tool_id: str, result: str, is_error: bool) -> None:
        """Record a tool result."""
        self._state = "tool_result"
        if self._current_tool:
            self._current_tool["result"] = result
            self._current_tool["is_error"] = is_error

        tool_area = self.query_one("#so-tool-area", Static)
        err_cls = " [dim][error][/dim]" if is_error else ""
        tool_area.update(f"[dim]✓[/dim] {tool_name}{err_cls}\n{result[:200]}")

        self.post_message(self.ToolFinished(tool_name, tool_id, result, is_error))

    def set_error(self, message: str) -> None:
        """Show an error state."""
        self._state = "error"
        content = self.query_one("#so-content", Static)
        self._buffer = f"[error] {message}"
        content.update(f"[red]{message}[/red]")

    def finish_streaming(self) -> None:
        """Mark the stream as complete."""
        self._state = "done"
        self.post_message(self.StreamingFinished())

    def get_content(self) -> str:
        """Return the full buffer content."""
        return self._buffer

    def clear(self) -> None:
        """Clear the output."""
        self._state = "idle"
        self._buffer = ""
        self._tool_calls = []
        self._current_tool = None
        try:
            self.query_one("#so-content", Static).update("")
            ta = self.query_one("#so-tool-area", Static)
            ta.display = False
            ta.update("")
        except NoMatches:
            pass

    @property
    def state(self) -> str:
        return self._state
