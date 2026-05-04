"""
forge.tui.screens.product_compiler — Product Compiler TUI screen.

Embeds the ProductCompilerPipeline event loop into the Forge TUI.
Shows: stage progress, interview Q&A, compiled rules, test results, gate approvals.

Access via: Ctrl+P → "compile" → opens ProductCompilerScreen
Or: forge compile --tui (future flag)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Input, Static, Log


class ProductCompilerScreen(Screen):
    """
    Full-screen Product Compiler interface.

    Run a product compilation from intent → production code
    with live stage tracking, interview display, and gate approvals.
    """

    TITLE = "Product Compiler"
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("ctrl+g", "approve_gate", "Approve Gate"),
        ("ctrl+r", "reject_gate", "Reject Gate"),
    ]

    # Reactive state
    current_stage = reactive("")
    current_question = reactive("")
    rule_count = reactive(0)
    table_count = reactive(0)
    awaiting_gate = reactive(False)
    gate_type = reactive("")
    pipeline_status = reactive("idle")
    in_question = reactive(False)

    CSS = """
    Screen {
        background: $surface;
    }
    #header {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
    }
    #stage-bar {
        height: 2;
        background: $surface-darken-1;
        dock: top;
    }
    #log-area {
        height: 1fr;
        border: solid $primary 20%;
        margin: 1 2;
    }
    #gate-panel {
        height: auto;
        background: $warning 20%;
        border: solid $warning;
        padding: 1 2;
    }
    #input-area {
        height: auto;
        dock: bottom;
        background: $surface-darken-1;
        padding: 1 2;
    }
    #question-display {
        color: $text-muted;
        padding: 0 0 0 0;
    }
    #answer-row {
        height: auto;
    }
    #answer-input {
        width: 100%;
    }
    """

    def __init__(self, initial_prompt: str = "", auto_approve: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.initial_prompt = initial_prompt
        self.auto_approve = auto_approve
        self._pipeline: "ProductCompilerPipeline | None" = None
        self._generator = None
        self._current_event: dict | None = None
        self._log_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("PRODUCT COMPILER", id="header")
        yield Static("", id="stage-bar")
        yield Log("📋 Product Compiler ready.\nAwaiting prompt...\n", id="log-area")
        yield Container(
            Static("", id="gate-content"),
            id="gate-panel",
            display=False,
        )
        # input-area always present; question display + answer row inside
        yield Container(
            Static("Awaiting next question...", id="question-display"),
            Horizontal(
                Input(placeholder="Type your answer and press Enter...", id="answer-input"),
                Button("Submit", id="submit-btn", variant="primary"),
                id="answer-row",
            ),
            id="input-area",
        )

    def on_mount(self) -> None:
        """Start the pipeline once the screen is mounted."""
        # Hide the answer row initially
        self.query_one("#answer-row", Horizontal).display = False
        if self.initial_prompt:
            self._start_pipeline()

    # ── Pipeline lifecycle ─────────────────────────────────────────────────────

    def _start_pipeline(self) -> None:
        """Initialize the pipeline and start the event loop."""
        try:
            from forge.product_compiler.pipeline import ProductCompilerPipeline
            self._pipeline = ProductCompilerPipeline(
                auto_approve=self.auto_approve,
                workdir=None,  # will use cwd
            )
            self._generator = self._pipeline.run(self.initial_prompt)
            self._advance()
        except Exception as e:
            self.query_one("#log-area", Log).write_line(f"❌ Failed to start pipeline: {e}")
            self.pipeline_status = "error"

    def _advance(self, user_input: str | None = None) -> None:
        """Advance the pipeline with optional user input."""
        if self._generator is None:
            return

        try:
            if user_input is not None:
                event = self._generator.send(user_input)
            else:
                event = next(self._generator)

            self._current_event = event
            self._handle_event(event)

        except StopIteration:
            self._pipeline_complete()
        except Exception as e:
            self.query_one("#log-area", Log).write_line(f"❌ Error: {e}")
            self.pipeline_status = "error"

    # ── Event routing ──────────────────────────────────────────────────────────

    def _handle_event(self, event: dict) -> None:
        """Route pipeline events to handlers."""
        t = event.get("type")
        p = event.get("payload", {})

        log = self.query_one("#log-area", Log)

        if t == "stage":
            self.current_stage = p.get("description", p.get("stage", ""))
            self.query_one("#stage-bar", Static).update(
                f"▶ STAGE: {p.get('description', '')} ({p.get('stage', '')})"
            )
            log.write_line(f"\n📍 STAGE: {p.get('description')} [{p.get('stage')}]\n")

        elif t == "thinking":
            log.write_line("🤔 Thinking...")

        elif t == "question":
            self._show_question(p.get("question", ""))

        elif t == "output":
            log.write_line(f"  → {p.get('summary', '')}")
            # If this is an autopilot answer echo, mark it in the log
            if event.get("stage") == "autopilot":
                answer = event.get("payload", {}).get("answer", "")
                log.write_line(f"  [autopilot answered: {answer[:60]}...]")

        elif t == "gate":
            self._show_gate(p, log)

        elif t == "complete":
            log.write_line(f"\n✅ PIPELINE COMPLETE — {p.get('state', {}).get('stage', '')}")
            self.pipeline_status = "complete"
            # Hide the input area when done
            self.query_one("#input-area", Container).display = False
            self.in_question = False

    def _show_question(self, question: str) -> None:
        """Display a question and enable the answer input."""
        self.in_question = True
        self.pipeline_status = "awaiting_question"

        # Update the question display
        self.query_one("#question-display", Static).update(f"❓ {question}")

        # Show the answer row
        answer_row = self.query_one("#answer-row", Horizontal)
        answer_row.display = True

        # Clear and focus the input
        inp = self.query_one("#answer-input", Input)
        inp.value = ""
        inp.focus()

    def _show_gate(self, p: dict, log: Log) -> None:
        """Display a gate for approval."""
        gate_num = p.get("gate")
        self.gate_type = p.get("name", "")
        self.awaiting_gate = True
        self.pipeline_status = "awaiting_gate"
        self.rule_count = p.get("rule_count", 0)
        self.table_count = p.get("table_count", 0)

        # Hide the answer row during gate — gate is Ctrl+G/Ctrl+R only
        self.query_one("#answer-row", Horizontal).display = False
        self.in_question = False

        gate_panel = self.query_one("#gate-panel", Container)
        gate_panel.display = True

        if gate_num == 1:
            rules = p.get("rules", [])
            rule_text = "\n".join(f"  • {r}" for r in rules[:30])
            if len(rules) > 30:
                rule_text += f"\n  ... and {len(rules) - 30} more rules"
            gate_panel.query_one("#gate-content", Static).update(
                f"\n🚧 GATE {gate_num}: {p.get('name')}\n\n{rule_text}\n\n"
                f"Ctrl+G: Approve  |  Ctrl+R: Reject"
            )
            log.write_line(f"\n🚧 GATE {gate_num}: {p.get('name')} — {len(rules)} rules")
            log.write_line("Press Ctrl+G to approve, Ctrl+R to reject")

        elif gate_num == 2:
            sql_preview = p.get("sql", "")[:500]
            gate_panel.query_one("#gate-content", Static).update(
                f"\n🚧 GATE {gate_num}: {p.get('name')}\n\n{sql_preview}\n\n"
                f"Ctrl+G: Approve  |  Ctrl+R: Reject"
            )
            log.write_line(f"\n🚧 GATE {gate_num}: {p.get('name')} — {p.get('table_count', 0)} tables")

    def _pipeline_complete(self) -> None:
        """Handle pipeline completion."""
        log = self.query_one("#log-area", Log)
        log.write_line("\n" + "=" * 50)
        log.write_line("✅ PIPELINE COMPLETE")
        log.write_line("=" * 50)
        self.awaiting_gate = False
        self.in_question = False
        self.pipeline_status = "complete"
        self.query_one("#input-area", Container).display = False

    # ── Actions ────────────────────────────────────────────────────────────────

    def action_approve_gate(self) -> None:
        """Approve the current gate."""
        if not self.awaiting_gate or self._pipeline is None:
            return
        self._pipeline.approve()
        self.awaiting_gate = False
        self.query_one("#gate-panel", Container).display = False
        self.query_one("#log-area", Log).write_line("✅ Gate approved")
        self._advance()

    def action_reject_gate(self) -> None:
        """Reject and stop the pipeline."""
        if not self.awaiting_gate or self._pipeline is None:
            return
        self.query_one("#log-area", Log).write_line("❌ Gate rejected — pipeline stopped")
        self.awaiting_gate = False
        self.pipeline_status = "rejected"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in the answer input."""
        if not self.in_question:
            return
        answer = event.value.strip()
        if not answer:
            return
        # Clear input immediately so the user sees it was accepted
        event.input.value = ""
        # Hide the answer row while waiting for next event
        self.query_one("#answer-row", Horizontal).display = False
        self.in_question = False
        self._advance(answer)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the Submit button press."""
        if event.button.id == "submit-btn" and self.in_question:
            inp = self.query_one("#answer-input", Input)
            answer = inp.value.strip()
            if not answer:
                return
            inp.value = ""
            self.query_one("#answer-row", Horizontal).display = False
            self.in_question = False
            self._advance(answer)


def open_product_compiler(prompt: str = "", auto_approve: bool = False) -> "ProductCompilerScreen":
    """Factory to create a ProductCompilerScreen with initial state."""
    return ProductCompilerScreen(initial_prompt=prompt, auto_approve=auto_approve)
