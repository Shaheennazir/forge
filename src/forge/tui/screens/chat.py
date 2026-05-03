"""
forge.tui.screens.chat — ChatScreen.

The primary interactive screen: a chat-first interface where the user
types a prompt and Forge streams back the response inline.

Key features:
- Textual Input widget for the prompt (Submit on Enter)
- StreamingOutput widget for the AI response (appends tokens in real time)
- Escape cancels the current run
- Model and session info in the header
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Input, Static, Log

from forge.tui.components.output import StreamingOutput


class ChatScreen(Screen):
    """
    Primary chat interface.

    Layout (top to bottom):
      - Header bar: project name, model, status
      - Log: scrollable history of past user/assistant messages
      - StreamingOutput: live token stream for current response
      - Input: prompt entry (disabled while streaming)
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    class UserSubmitted(Message):
        """Posted when the user submits a prompt."""

    def __init__(self, project_name: str = "default", model: str = "MiniMax-M2.7"):
        super().__init__()
        self.project_name = project_name
        self.model = model
        self._busy = False

    # ── Compose ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(f"◆ {self.project_name}", id="header-project")
        yield Static(f"Model: {self.model}", id="header-model")
        yield Log(id="chat-log")
        yield StreamingOutput(id="streaming-output")
        yield Input(placeholder="Ask Forge…  (Ctrl+A: model,  Ctrl+P: commands)",
                    id="prompt-input", validate_on=["submitted"])

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    # ── Input handling ────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._busy or not event.value.strip():
            return
        self._busy = True
        prompt = event.value.strip()
        event.input.value = ""  # clear input
        self._append_user_message(prompt)
        self.post_message(self.UserSubmitted(prompt))
        self.query_one("#prompt-input", Input).disabled = True

    def on_streaming_output_content_appended(
        self, event: StreamingOutput.ContentAppended
    ) -> None:
        """Auto-scroll to bottom as tokens arrive."""
        log = self.query_one("#chat-log", Log)
        output = self.query_one("#streaming-output", StreamingOutput)
        # Keep log scrolled to bottom
        log.scroll_end(animate=False)

    def on_streaming_output_streaming_finished(
        self, event: StreamingOutput.StreamingFinished
    ) -> None:
        self._busy = False
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def action_cancel(self) -> None:
        """Cancel the current streaming run (placeholder for interrupt)."""
        if self._busy:
            output = self.query_one("#streaming-output", StreamingOutput)
            output.finish_streaming()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _append_user_message(self, text: str) -> None:
        log = self.query_one("#chat-log", Log)
        log.write_line(f"\n[bold cyan]>[/] {text}")

    def start_streaming(self) -> None:
        output = self.query_one("#streaming-output", StreamingOutput)
        output.start_streaming()

    def append_token(self, token: str) -> None:
        output = self.query_one("#streaming-output", StreamingOutput)
        output.append(token)

    def finish_streaming(self) -> None:
        output = self.query_one("#streaming-output", StreamingOutput)
        output.finish_streaming()
        log = self.query_one("#chat-log", Log)
        content = output._buffer
        log.write_line(f"\n[bold green]◆[/] {content}\n")
        output.set_content("")
