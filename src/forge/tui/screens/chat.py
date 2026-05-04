"""
forge.tui.screens.chat — Fully wired chat screen.

Receives user input, streams LLM responses, executes tools,
shows permission dialogs, restores session history, tracks tokens.

Keybindings:
  Ctrl+A  → model picker
  Ctrl+P  → command palette
  Ctrl+L  → toggle session log
  Ctrl+K  → clear output
  Ctrl+C  → cancel current LLM call  (when streaming)
  Escape  → close sub-screens

Architecture:
  User submits → state.save_message(role=user) →
  LLMBridge.stream() → StreamEvents drive UI updates →
  state.save_message(role=assistant) on completion →
  state.update_session_tokens()
"""

from __future__ import annotations

import asyncio
import inspect
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Static, Log

from forge.tui.state import AppState, StreamEvent, StatusLevel
from forge.tui.llm import LLMBridge
from forge.tui.components.streaming_output import StreamingOutput
from forge.tui.components.permission import PermissionDialog


class ChatScreen(Screen):
    """
    Main chat interface — fully wired to AppState + LLMBridge.

    Layout:
      [header: model | session title | token count]
      [message log — scrollable, past messages + streaming output]
      [input bar: text input + submit]
    """

    TITLE = "Forge Chat"
    BINDINGS = [
        ("escape", "pop_screen", "Back"),
        ("ctrl+a", "model_picker", "Model"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+k", "clear_output", "Clear"),
        ("ctrl+c", "cancel_stream", "Cancel"),
    ]

    CSS = """
    ChatScreen {
        layout: vertical;
    }

    #chat-header {
        height: 2;
        background: $primary;
        color: $text;
        dock: top;
    }

    #chat-log {
        height: 1fr;
        border: solid $primary 20%;
        margin: 1 2;
        padding: 0;
    }

    #chat-input-area {
        height: auto;
        dock: bottom;
        background: $surface-darken-1;
        padding: 1 2;
    }

    #chat-input {
        width: 100%;
    }

    .msg-user {
        color: $accent;
        text-style: bold;
    }

    .msg-assistant {
        color: $text;
    }

    .msg-system {
        color: $text-muted;
        text-style: italic;
    }

    .msg-tool {
        color: $warning;
    }

    .streaming-indicator {
        color: $primary;
        text-style: italic;
    }
    """

    # Reactive state
    is_streaming = reactive(False)
    current_model = reactive("")
    session_id = reactive("")

    def __init__(self, session_id: str = "", restore: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.state = AppState.get()
        self.llm = LLMBridge(self.state)
        self._restore = restore
        self._pending_permission: dict | None = None

        # Use existing or create session
        if session_id:
            self.state.set_current_session(session_id)
        elif not self.state.get_current_session():
            self.state.create_session(
                project_name="default",
                model=self.state.current_model,
                provider=self.state.current_provider,
            )

        sess = self.state.get_current_session()
        self._session_id = sess.id if sess else ""
        self._input_buffer = ""

        # Subscribe to bus
        self.state.bus.subscribe("permission", self._on_permission_request)
        self.state.bus.subscribe("permission_resolved", self._on_permission_resolved)
        self.state.bus.subscribe("stream", self._on_stream_event)

    def compose(self) -> ComposeResult:
        sess = self.state.get_current_session()
        header_sub = f"{sess.model if sess else 'no model'}  |  {sess.title if sess else 'New Chat'}"
        yield Static(f"  💠 FORGE CHAT  |  {header_sub}", id="chat-header")
        yield Container(Log(id="chat-log"), StreamingOutput(id="chat-stream"))
        yield Horizontal(
            Input(placeholder="Send a message... (Ctrl+A: model, Ctrl+P: commands, /: tools)", id="chat-input"),
            Static("", id="chat-streaming-indicator"),
            id="chat-input-area",
        )

    def on_mount(self) -> None:
        self.query_one("#chat-input", Input).focus()
        if self._restore:
            self._restore_history()

    # ── History restore ─────────────────────────────────────────────────────

    def _restore_history(self) -> None:
        """Load past messages from session DB into the log."""
        if not self._session_id:
            return
        log = self.query_one("#chat-log", Log)
        messages = self.state.get_session_messages(self._session_id)
        for msg in messages:
            role = msg["role"]
            content = msg["content"] or ""
            cls = f"msg-{role}"
            if role == "tool":
                tc = msg.get("tool_calls")
                if tc:
                    try:
                        import json
                        calls = json.loads(tc)
                        for c in calls:
                            fn = c.get("function", {})
                            log.write_line(f"\n[dim]⟳ tool:[/dim] {fn.get('name', '?')}\n", expand=False)
                    except Exception:
                        pass
            if content:
                log.write_line(f"\n[{role}] {content}\n", expand=False)

    # ── Message handling ──────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self._submit(text)

    def _submit(self, text: str) -> None:
        """Send user message to LLM."""
        if not self._session_id:
            return

        # Save user message
        self.state.save_message(self._session_id, "user", text)
        self.state.increment_session_messages(self._session_id)

        # Echo to log
        log = self.query_one("#chat-log", Log)
        log.write_line(f"\n[user] {text}\n", expand=False)

        # Start streaming
        self._start_stream(text)

    def _start_stream(self, text: str) -> None:
        """Begin async LLM stream."""
        self.is_streaming = True
        self.query_one("#chat-streaming-indicator", Static).update(
            "  [streaming...]"
        )

        # Build system prompt
        system = self._build_system_prompt()

        # Get tools schema
        tools = self._get_tools_schema()

        # Run async generator in background
        asyncio.create_task(self._run_stream(text, system, tools))

    async def _run_stream(self, text: str, system: str, tools: list) -> None:
        """Consume the async stream generator."""
        try:
            stream = self.llm.stream(
                session_id=self._session_id,
                prompt=text,
                system=system,
                tools=tools,
            )

            streaming_output = self.query_one("#chat-stream", StreamingOutput)
            log = self.query_one("#chat-log", Log)
            full_response = []

            async for ev in stream:
                if ev.kind == "token":
                    full_response.append(ev.content)
                    streaming_output.append_token(ev.content)
                elif ev.kind == "thinking":
                    streaming_output.show_thinking()
                elif ev.kind == "tool_start":
                    streaming_output.tool_started(ev.tool_name, ev.tool_call_id)
                    log.write_line(f"\n[dim]⟳ {ev.tool_name}...[/dim]\n", expand=False)
                elif ev.kind == "tool_result":
                    streaming_output.tool_finished(
                        ev.tool_name, ev.tool_call_id,
                        ev.content, ev.is_error,
                    )
                    log.write_line(
                        f"\n[dim]✓ {ev.tool_name}[/dim]\n{ev.content[:300]}\n",
                        expand=False,
                    )
                elif ev.kind == "error":
                    streaming_output.set_error(ev.content)
                    log.write_line(f"\n[red]ERROR: {ev.content}[/red]\n", expand=False)
                elif ev.kind == "done":
                    streaming_output.finish_streaming()

            # Save assistant response
            content = "".join(full_response)
            if content:
                self.state.save_message(
                    self._session_id, "assistant", content,
                    model=self.state.current_model,
                )
                self.state.increment_session_messages(self._session_id)

            # Update session title from first exchange
            sess = self.state.get_current_session()
            if sess and sess.title == "New Chat" and sess.messages == 2:
                title = content[:50].replace("\n", " ").strip() or "Chat"
                self.state.update_session_title(self._session_id, title[:60])

        except Exception as e:
            self.state.set_status(f"Stream error: {e}", StatusLevel.ERROR)
        finally:
            self.is_streaming = False
            self.query_one("#chat-streaming-indicator", Static).update("")
            self.query_one("#chat-stream", StreamingOutput).finish_streaming()

    def _build_system_prompt(self) -> str:
        sess = self.state.get_current_session()
        project = sess.project_name if sess else "default"

        # Try to load project spec from memory DB
        spec_md = ""
        try:
            from forge.db import ForgeDB
            db = ForgeDB(project_id=project)
            ver = db.latest_spec_version()
            if ver:
                sv = db.get_spec_version(ver)
                if sv:
                    spec_md = sv.spec_md
        except Exception:
            pass

        return f"""You are Forge, a production-grade AI coding assistant running inside a directed-graph coding system (forge).

Project: {project}
Model: {self.state.current_model}

## Forge capabilities
You have access to tools: bash, read, write, edit, glob, grep, ls, search.
Use them to accomplish the user's request. Always prefer file operations over
describing what you would do.

## Session rules
- Execute code and return results, don't just describe
- Ask clarifying questions when intent is ambiguous
- Report errors clearly with error type and message
- Do not read or write outside the project directory unless explicitly needed

## Project Spec
{spec_md if spec_md else '(no spec loaded — ask the user if needed)'}

Current directory context: {project}
""".strip()

    def _get_tools_schema(self) -> list[dict]:
        """Return OpenAI-format tool schema for LLM function calling."""
        return [
            # ── Shell / filesystem ────────────────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Execute a shell command in the session's project directory. Returns stdout + stderr.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command to execute"},
                            "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)", "default": 60},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read the contents of a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write",
                    "description": "Create or overwrite a file with content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file"},
                            "content": {"type": "string", "description": "File content to write"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit",
                    "description": "Replace a specific string in a file (targeted find-and-replace edit).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file"},
                            "old_text": {"type": "string", "description": "Exact substring to find and replace"},
                            "new_text": {"type": "string", "description": "Replacement string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "glob",
                    "description": "List files matching a glob pattern.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": "Search for a string inside files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regex or substring to search for"},
                            "file": {"type": "string", "description": "File pattern, e.g. *.py", "default": "*.py"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ls",
                    "description": "List files in a directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Directory path", "default": "."},
                        },
                    },
                },
            },
            # ── Security & quality gates ──────────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "bandit",
                    "description": "Run Bandit security scanner on the project. Detects common security issues in Python code. Gate: any HIGH/CRITICAL finding blocks.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "radon_cc",
                    "description": "Run Radon cyclomatic complexity analysis. Gate: functions with CC > 10 are flagged HIGH.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "vulture",
                    "description": "Run Vulture dead code detection. Flags unused code at 80%+ confidence. Gate: HIGH confidence dead code blocks.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "semgrep",
                    "description": "Run Semgrep static analysis with security and correctness rules. Gate: HIGH severity security rules block.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "configs": {"type": "array", "items": {"type": "string"}, "description": "Specific Semgrep config names or paths (default: auto)", "default": []},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pip_audit",
                    "description": "Audit installed Python dependencies for known vulnerabilities via pip-audit. Gate: any CVE blocks.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pyupgrade",
                    "description": "Check if Python code uses modern idioms and is up to date with the target Python version.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "deptry",
                    "description": "Detect unused or missing Python dependencies via deptry.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cyclonedx_sbom",
                    "description": "Generate a CycloneDX SBOM (Software Bill of Materials) for the project dependencies.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            # ── Jedi semantic analysis ─────────────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "jedi_find_callers",
                    "description": "Find all references to a function or method in the codebase using Jedi semantic analysis.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "function_name": {"type": "string", "description": "Name of the function to find callers of"},
                        },
                        "required": ["function_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "jedi_infer",
                    "description": "Infer the type of a Python expression at a given cursor position using Jedi.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Code snippet context (full line or more)"},
                            "line": {"type": "integer", "description": "1-indexed line number of cursor", "default": 1},
                            "column": {"type": "integer", "description": "0-indexed column of cursor", "default": 0},
                        },
                        "required": ["code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "jedi_complete",
                    "description": "Get code completion suggestions at a cursor position using Jedi.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Code snippet with cursor position"},
                            "line": {"type": "integer", "description": "1-indexed line number", "default": 1},
                            "column": {"type": "integer", "description": "0-indexed column of cursor", "default": 0},
                        },
                        "required": ["code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "jedi_signatures",
                    "description": "Get function/method signatures at a cursor position using Jedi.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Code snippet with cursor on function call"},
                            "line": {"type": "integer", "description": "1-indexed line number", "default": 1},
                            "column": {"type": "integer", "description": "0-indexed column of cursor", "default": 0},
                        },
                        "required": ["code"],
                    },
                },
            },
            # ── Rope refactoring ──────────────────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "rope_rename",
                    "description": "Rename a function, class, or variable across the entire project using Rope.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "old_name": {"type": "string", "description": "Current fully-qualified name (e.g. module.function or Class.method)"},
                            "new_name": {"type": "string", "description": "New name for the symbol"},
                        },
                        "required": ["old_name", "new_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rope_extract",
                    "description": "Extract a block of code into a new method using Rope refactoring.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_path": {"type": "string", "description": "Path to the source file"},
                            "name": {"type": "string", "description": "Name for the extracted method"},
                            "start_line": {"type": "integer", "description": "Start line of code block (1-indexed)"},
                            "end_line": {"type": "integer", "description": "End line of code block (1-indexed)"},
                        },
                        "required": ["source_path", "name", "start_line", "end_line"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rope_inline",
                    "description": "Inline a function or method at its call sites using Rope.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_path": {"type": "string", "description": "Path to the source file"},
                            "name": {"type": "string", "description": "Name of the function to inline"},
                            "start_line": {"type": "integer", "description": "Start line of function definition"},
                            "end_line": {"type": "integer", "description": "End line of function definition"},
                        },
                        "required": ["source_path", "name", "start_line", "end_line"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "rope_move",
                    "description": "Move a symbol (function/class) to a different module using Rope.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_path": {"type": "string", "description": "Current path to the source file"},
                            "symbol_name": {"type": "string", "description": "Name of the function or class to move"},
                            "dest_path": {"type": "string", "description": "Destination module path"},
                        },
                        "required": ["source_path", "symbol_name", "dest_path"],
                    },
                },
            },
            # ── Contracts & property testing ──────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "crosshair",
                    "description": "Prove or disprove contracts (preconditions/postconditions) on Python functions using Crosshair. Any violation found blocks the pipeline.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "targets": {"type": "array", "items": {"type": "string"}, "description": "Specific files or functions to check (default: entire project)", "default": ["."]},
                            "timeout": {"type": "integer", "description": "Timeout in seconds per function", "default": 120},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "hypothesis",
                    "description": "Run Hypothesis property-based test health checks to validate test quality.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "test_suite_path": {"type": "string", "description": "Path to test directory (default: project tests/)"},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "mutmut",
                    "description": "Run mutation testing via mutmut. Measures test suite effectiveness. Gate: mutation score must meet threshold (default 70%).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "test_cmd": {"type": "string", "description": "Command to run tests", "default": "pytest tests/ -x"},
                            "threshold": {"type": "number", "description": "Minimum mutation score (0-100)", "default": 70},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "griffe",
                    "description": "Extract and validate public API signatures using Griffe. Detects API contract drift from expected contracts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expected_contracts": {"type": "array", "description": "List of expected API contract names or objects", "default": []},
                        },
                        "required": [],
                    },
                },
            },
            # ── Profiling ─────────────────────────────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "memray",
                    "description": "Profile memory usage of the test suite using Memray. Detects memory leaks and excessive allocations. Gate: peak memory > 512 MB or leaks found.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "test_cmd": {"type": "string", "description": "Test command to profile", "default": "pytest tests/ -x"},
                            "leak_check": {"type": "boolean", "description": "Enable memory leak detection", "default": True},
                            "memory_limit_mb": {"type": "number", "description": "Maximum allowed peak memory in MB", "default": 512.0},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pyspy",
                    "description": "Profile CPU usage of a running Python process or command using py-spy. Generates a flame graph.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Python command to profile (e.g. 'python -m pytest tests/')"},
                            "output": {"type": "string", "description": "Output path for the flame graph SVG", "default": "profile.svg"},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_model_picker(self) -> None:
        self.app.push_screen("model_picker")

    def action_command_palette(self) -> None:
        self.app.push_screen("command_palette")

    def action_clear_output(self) -> None:
        self.query_one("#chat-log", Log).clear()
        self.query_one("#chat-stream", StreamingOutput).clear()

    def action_cancel_stream(self) -> None:
        if self.is_streaming:
            self.llm.cancel()
            self.is_streaming = False
            self.query_one("#chat-streaming-indicator", Static).update("")
            self.state.set_status("Stream cancelled", StatusLevel.WARN, ttl=3)

    # ── Permission events ─────────────────────────────────────────────────────

    def _on_permission_request(self, req: "PermissionRequest") -> None:
        """Show the permission dialog modally."""
        dialog = PermissionDialog(req, self.state)
        self.app.push_screen(dialog, "permission_overlay")

    def _on_permission_resolved(self, event: tuple) -> None:
        key, allowed, remembered = event
        if allowed:
            self.state.grant_permission(key, persistent=remembered)
        else:
            self.state.deny_permission(key)
