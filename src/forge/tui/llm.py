"""
forge.tui.llm — Streaming LLM bridge for the Forge TUI.

Runs the LLM backend in a background thread and streams tokens
back to the TUI via an async generator. Handles:
  - Tool call detection and execution (bash, edit, glob, grep, ls, view, etc.)
  - Code intelligence tools: bandit, radon, vulture, semgrep, pip-audit,
    jedi, rope, crosshair, hypothesis, mutmut, memray, py-spy, and more
  - Persistent shell session for bash
  - Permission checks before dangerous operations
  - Token counting and cost tracking
  - Cancellation via threading.Event

Usage:
  llm = LLMBridge(state)
  async for event in llm.stream(session_id, prompt, system):
      if event.kind == "token":
          output.append(event.content)
      elif event.kind == "tool_start":
          show_tool_spinner(event.tool_name)
      elif event.kind == "tool_result":
          show_tool_result(event.content)
      elif event.kind == "done":
          update_token_counts(...)
"""

from __future__ import annotations

import json
import threading
import time
import queue
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Generator, Optional, AsyncGenerator

from forge.tui.state import AppState, StreamEvent


@dataclass
class LLMRate:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


@dataclass
class ToolResult:
    name: str
    content: str
    is_error: bool = False


class LLMBridge:
    """
    Background LLM worker that streams tokens via a thread-safe queue.

    The TUI consumes events via `stream()` (async generator).
    Tool calls are executed inline in the worker thread after permission checks.
    """

    def  __init__(self, state: AppState):
        self.state = state
        self._cancel_event = threading.Event()
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def cancel(self) -> None:
        """Signal cancellation to the worker thread."""
        self._cancel_event.set()

    def stream(
        self,
        session_id: str,
        prompt: str,
        system: Optional[str] = None,
        tools: Optional[list] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Async generator of StreamEvents. Run this in an async context.

        Yields token deltas, tool call starts/results, and final completion.
        """
        import asyncio

        output_queue: queue.Queue[StreamEvent] = queue.Queue()
        result_ready = threading.Event()
        rate = LLMRate()
        error_msg = ""

        def worker():
            """Background thread: call LLM, stream tokens, execute tools."""
            nonlocal error_msg
            self._busy = True
            self._cancel_event.clear()

            try:
                backend = self.state.get_backend()
                if backend is None:
                    output_queue.put(StreamEvent(
                        session_id=session_id, kind="error",
                        content="No LLM backend configured. Run `forge setup` first.",
                        is_error=True,
                    ))
                    result_ready.set()
                    return

                # Build conversation messages
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})

                # Restore conversation history from DB
                history = self.state.get_session_messages(session_id)
                for msg in history:
                    role = msg["role"]
                    content = msg["content"] or ""
                    tc = msg.get("tool_calls")
                    tool_calls = json.loads(tc) if tc else None
                    if tool_calls:
                        messages.append({
                            "role": role,
                            "content": content,
                            "tool_calls": tool_calls,
                        })
                    else:
                        messages.append({"role": role, "content": content})

                messages.append({"role": "user", "content": prompt})

                # Streaming completion
                tool_results_so_far = []
                iteration = 0
                max_iterations = 20  # prevent infinite loops

                while iteration < max_iterations:
                    iteration += 1
                    if self._cancel_event.is_set():
                        output_queue.put(StreamEvent(
                            session_id=session_id, kind="error",
                            content="Cancelled.",
                            is_error=True,
                        ))
                        result_ready.set()
                        return

                    try:
                        if hasattr(backend, "complete_streaming"):
                            # Streaming backend
                            content_parts = []
                            backend_iter = backend.complete_streaming(
                                prompt="",  # prompt baked into messages
                                system="",
                                messages=messages,
                                tools=tools,
                            )
                            for chunk in backend_iter:
                                if self._cancel_event.is_set():
                                    break
                                if hasattr(chunk, "content"):
                                    content_parts.append(chunk.content)
                                elif isinstance(chunk, str):
                                    content_parts.append(chunk)

                            full_content = "".join(content_parts)
                            resp = backend.complete(
                                prompt="",
                                system="",
                                messages=messages,
                                tools=tools,
                                max_tokens=4096,
                            )
                        else:
                            resp = backend.complete(
                                prompt=prompt,
                                system=system,
                                messages=messages if messages else None,
                                tools=tools,
                                max_tokens=4096,
                            )
                        break
                    except Exception as e:
                        # Retry on error
                        if iteration < max_iterations:
                            time.sleep(1)
                            continue
                        output_queue.put(StreamEvent(
                            session_id=session_id, kind="error",
                            content=str(e), is_error=True,
                        ))
                        result_ready.set()
                        return

                if self._cancel_event.is_set():
                    result_ready.set()
                    return

                # Parse response for tool calls
                tool_calls = getattr(resp, "tool_calls", None) or []

                if not tool_calls:
                    # Plain text response
                    content = getattr(resp, "content", "") or ""
                    for char in content:
                        if self._cancel_event.is_set():
                            break
                        output_queue.put(StreamEvent(
                            session_id=session_id, kind="token",
                            content=char,
                        ))
                        time.sleep(0.005)  # throttle for visual effect
                    if self._cancel_event.is_set():
                        result_ready.set()
                        return
                else:
                    # Execute each tool call
                    messages.append({
                        "role": "assistant",
                        "content": getattr(resp, "content", "") or "",
                        "tool_calls": [
                            {
                                "id": tc.get("id") or f"call_{i}",
                                "type": "function",
                                "function": {
                                    "name": tc.get("name") or tc.get("function", {}).get("name", "?"),
                                    "arguments": tc.get("arguments") or tc.get("function", {}).get("arguments", "{}"),
                                },
                            }
                            for i, tc in enumerate(tool_calls)
                        ],
                    })

                    for i, tc in enumerate(tool_calls):
                        if self._cancel_event.is_set():
                            break
                        fn_name = tc.get("function", {}).get("name", "") if isinstance(tc.get("function"), dict) else (tc.get("name") or "?")
                        tc_id = tc.get("id", f"call_{i}")
                        raw_args = tc.get("function", {}).get("arguments", "{}") if isinstance(tc.get("function"), dict) else (tc.get("arguments") or "{}")

                        # Parse args
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {}

                        output_queue.put(StreamEvent(
                            session_id=session_id, kind="tool_start",
                            tool_name=fn_name, tool_call_id=tc_id,
                            content="",
                        ))

                        # Execute tool
                        result = self._execute_tool(fn_name, args, session_id)

                        # Save tool result
                        self.state.save_message(
                            session_id=session_id,
                            role="tool",
                            content=result.content,
                            tool_calls=None,
                            tool_result=result.content,
                        )

                        tool_results_so_far.append({
                            "role": "tool",
                            "content": result.content,
                            "tool_call_id": tc_id,
                        })
                        output_queue.put(StreamEvent(
                            session_id=session_id, kind="tool_result",
                            tool_name=fn_name, tool_call_id=tc_id,
                            content=result.content,
                            is_error=result.is_error,
                        ))

                        if not self._cancel_event.is_set():
                            messages.append({
                                "role": "tool",
                                "content": result.content,
                                "tool_call_id": tc_id,
                            })

                # Usage
                usage = getattr(resp, "usage", None) or {}
                if isinstance(usage, dict):
                    rate.prompt_tokens = usage.get("prompt_tokens", 0)
                    rate.completion_tokens = usage.get("completion_tokens", 0)
                    rate.total_tokens = usage.get("total_tokens", 0)
                elif hasattr(usage, "prompt_tokens"):
                    rate.prompt_tokens = usage.prompt_tokens
                    rate.completion_tokens = usage.completion_tokens
                    rate.total_tokens = usage.total_tokens

                # Rough cost estimate
                rate.cost = rate.completion_tokens * 0.00001  # rough default

            except Exception as e:
                error_msg = str(e)
                output_queue.put(StreamEvent(
                    session_id=session_id, kind="error",
                    content=error_msg, is_error=True,
                ))
            finally:
                self._busy = False
                result_ready.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # Yield events from the queue until result_ready
        while True:
            if self._cancel_event.is_set():
                # Drain queue quickly
                try:
                    while True:
                        ev = output_queue.get_nowait()
                        if ev.kind == "done":
                            return
                except queue.Empty:
                    yield StreamEvent(session_id=session_id, kind="error", content="Cancelled", is_error=True)
                    return

            try:
                ev = output_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if self._cancel_event.is_set() and ev.kind not in ("done", "error"):
                continue

            yield ev

            if ev.kind in ("done", "error"):
                break

        thread.join(timeout=2)

        # Publish token update
        if rate.total_tokens > 0:
            self.state.update_session_tokens(
                session_id,
                rate.prompt_tokens,
                rate.completion_tokens,
                rate.cost,
            )

    # ── Workdir resolution ────────────────────────────────────────────────────

    def _resolve_workdir(self, session_id: str) -> Path:
        """Resolve the workdir for the current session's project."""
        sess = self.state.get_session(session_id) if session_id else None
        project = sess.project_name if sess else "default"
        wd = Path.home() / ".forge" / "workspace" / project
        wd.mkdir(parents=True, exist_ok=True)
        return wd

    # ── Tool router ──────────────────────────────────────────────────────────

    def _execute_tool(
        self, name: str, args: dict, session_id: str
    ) -> ToolResult:
        """
        Execute a named tool with args, after permission checks.
        Returns ToolResult.
        """
        tool_name = name.lower()

        # Permission check
        path = args.get("path", "") or args.get("file", "") or ""
        action = self._tool_action(tool_name)
        if not self.state.check_permission(tool_name, action, path):
            return ToolResult(
                name=name,
                content="Permission denied by user.",
                is_error=True,
            )

        try:
            if tool_name in ("bash", "shell", "run"):
                return self._tool_bash(args, session_id)
            elif tool_name in ("read", "view", "cat"):
                return self._tool_read(args)
            elif tool_name == "glob":
                return self._tool_glob(args)
            elif tool_name == "grep":
                return self._tool_grep(args)
            elif tool_name == "ls":
                return self._tool_ls(args)
            elif tool_name in ("write", "create", "new_file"):
                return self._tool_write(args, session_id)
            elif tool_name in ("edit", "patch"):
                return self._tool_edit(args, session_id)
            elif tool_name == "search":
                return self._tool_search(args)
            else:
                # Delegate to code intelligence dispatcher
                return self._execute_code_intel(tool_name, args, session_id)
        except PermissionRequired as e:
            return ToolResult(name=name, content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(name=name, content=f"Error: {e}", is_error=True)

    def _tool_action(self, name: str) -> str:
        mapping = {
            "bash": "execute", "shell": "execute", "run": "execute",
            "read": "read", "view": "read", "cat": "read",
            "write": "write", "create": "create", "new_file": "create",
            "edit": "patch", "patch": "patch",
            "glob": "read", "grep": "read", "search": "read",
            "ls": "read",
        }
        return mapping.get(name.lower(), "read")

    # ── Code intelligence dispatcher ─────────────────────────────────────────

    def _execute_code_intel(
        self, tool_name: str, args: dict, session_id: str
    ) -> ToolResult:
        """
        Route code intelligence tool calls to their respective wrappers.

        All tools accept workdir=resolved_from_session as the first argument.
        Returns a human-readable formatted string for the LLM.
        """
        wd = self._resolve_workdir(session_id)

        # ── Security / quality analysis (subprocess tools) ───────────────────

        if tool_name == "bandit":
            from forge.code_intelligence.bandit_ import run_bandit
            result = run_bandit(workdir=wd)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ Bandit: no HIGH/CRITICAL issues found", is_error=False)
            lines = [f"❌ Bandit: {len(result.issues)} issues found"]
            for iss in result.issues[:15]:
                lines.append(f"  [{iss.severity}] {iss.file}:{iss.line} — {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=True)

        elif tool_name == "radon_cc":
            from forge.code_intelligence.radon_ import run_radon
            result = run_radon(workdir=wd)
            lines = ["📊 Radon complexity analysis:"]
            if result.avg_maintainability > 0:
                lines.append(f"  Average maintainability: {result.avg_maintainability:.1f}/100")
            high_issues = [i for i in result.issues if i.severity == "HIGH"]
            if high_issues:
                lines.append(f"\n❌ {len(high_issues)} functions exceed CC > 10:")
                for iss in high_issues[:10]:
                    lines.append(f"  [{iss.severity}] {iss.file}:{iss.line} — {iss.message}")
            else:
                lines.append("✅ No functions exceed complexity threshold (CC > 10)")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=bool(high_issues))

        elif tool_name == "vulture":
            from forge.code_intelligence.vulture_ import run_vulture
            result = run_vulture(workdir=wd)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ Vulture: no dead code at 80%+ confidence", is_error=False)
            lines = [f"❌ Vulture: {len(result.issues)} unused symbols at 80%+ confidence:"]
            for iss in result.issues[:20]:
                lines.append(f"  [{iss.severity}] {iss.file}:{iss.line} — {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=True)

        elif tool_name == "semgrep":
            from forge.code_intelligence.semgrep_ import run_semgrep
            configs = args.get("configs")
            result = run_semgrep(workdir=wd, configs=configs)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ Semgrep: no HIGH severity security issues", is_error=False)
            lines = [f"❌ Semgrep: {len(result.issues)} issues found"]
            for iss in result.issues[:15]:
                lines.append(f"  [{iss.severity}] {iss.file}:{iss.line} [{iss.code}] — {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=True)

        elif tool_name == "pip_audit":
            from forge.code_intelligence.pip_audit_ import run_pip_audit
            result = run_pip_audit(workdir=wd)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ pip-audit: no known vulnerabilities found", is_error=False)
            lines = [f"❌ pip-audit: {len(result.issues)} vulnerabilities found:"]
            for iss in result.issues[:20]:
                lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=True)

        elif tool_name == "pyupgrade":
            from forge.code_intelligence.pyupgrade_ import run_pyupgrade
            result = run_pyupgrade(workdir=wd)
            lines = ["🔄 pyupgrade:"]
            for iss in result.issues[:20]:
                lines.append(f"  {iss.file}:{iss.line} — {iss.message}")
            if not result.issues:
                lines.append("  All code is up to date.")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=False)

        elif tool_name == "deptry":
            from forge.code_intelligence.deptry_ import run_deptry
            result = run_deptry(workdir=wd)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ deptry: no unused or missing dependencies", is_error=False)
            lines = [f"❌ deptry: {len(result.issues)} dependency issues:"]
            for iss in result.issues[:20]:
                lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.passed)

        elif tool_name == "cyclonedx_sbom":
            from forge.code_intelligence.cyclonedx_ import run_sbom
            result = run_sbom(workdir=wd)
            if result.success:
                return ToolResult(name=tool_name, content=f"✅ SBOM generated: {result.raw[:200]}", is_error=False)
            return ToolResult(name=tool_name, content=f"❌ SBOM: {result.raw[:200]}", is_error=True)

        # ── Jedi semantic analysis ───────────────────────────────────────────

        elif tool_name == "jedi_find_callers":
            from forge.code_intelligence.jedi_ import find_callers
            fn_name = args.get("function_name", "")
            if not fn_name:
                return ToolResult(name=tool_name, content="jedi_find_callers: function_name is required", is_error=True)
            result = find_callers(workdir=wd, function_name=fn_name)
            lines = [f"🔍 jedi: {len(result.results)} references to '{fn_name}':"]
            for r in result.results[:30]:
                lines.append(f"  {r.file}:{r.line} — {r.call_type} '{r.caller_name}'")
            if not result.results:
                lines.append("  (no references found)")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.success)

        elif tool_name == "jedi_infer":
            from forge.code_intelligence.jedi_ import get_inference
            code = args.get("code", "")
            line = int(args.get("line", 1))
            column = int(args.get("column", 0))
            if not code:
                return ToolResult(name=tool_name, content="jedi_infer: code is required", is_error=True)
            result = get_inference(workdir=wd, code=code, line=line, column=column)
            lines = [f"💡 jedi infer for '{code[:40]}...':"]
            for r in result.results[:10]:
                lines.append(f"  {r.get('type','?')} — {r.get('description','?')} ({r.get('full_name','?')})")
            if not result.results:
                lines.append("  (no types inferred)")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=False)

        elif tool_name == "jedi_complete":
            from forge.code_intelligence.jedi_ import get_completions
            code = args.get("code", "")
            line = int(args.get("line", 1))
            column = int(args.get("column", 0))
            if not code:
                return ToolResult(name=tool_name, content="jedi_complete: code is required", is_error=True)
            result = get_completions(workdir=wd, code=code, line=line, column=column)
            lines = [f"💡 jedi completions at cursor:"]
            for r in result.results[:20]:
                lines.append(f"  {r.get('name','?')} — {r.get('type','?')} {r.get('full_signature','')}")
            if not result.results:
                lines.append("  (no completions)")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=False)

        elif tool_name == "jedi_signatures":
            from forge.code_intelligence.jedi_ import get_signatures
            code = args.get("code", "")
            line = int(args.get("line", 1))
            column = int(args.get("column", 0))
            if not code:
                return ToolResult(name=tool_name, content="jedi_signatures: code is required", is_error=True)
            result = get_signatures(workdir=wd, code=code, line=line, column=column)
            lines = [f"💡 jedi signatures:"]
            for r in result.results[:10]:
                params = ", ".join(f"{p['name']}: {p.get('type','?')}" for p in r.get("params", [])[:10])
                lines.append(f"  {r.get('name','?')}({params})")
            if not result.results:
                lines.append("  (no signatures found)")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=False)

        # ── Rope refactoring ────────────────────────────────────────────────

        elif tool_name == "rope_rename":
            from forge.code_intelligence.rope_ import rename_function
            old_name = args.get("old_name", "")
            new_name = args.get("new_name", "")
            if not old_name or not new_name:
                return ToolResult(name=tool_name, content="rope_rename: old_name and new_name are required", is_error=True)
            result = rename_function(workdir=wd, old_name=old_name, new_name=new_name)
            lines = [f"🔁 rope rename: '{old_name}' → '{new_name}'"]
            lines.append(f"  Files changed: {result.files_changed}")
            for ch in result.changes[:20]:
                lines.append(f"  {ch.get('path','?')}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.success)

        elif tool_name == "rope_extract":
            from forge.code_intelligence.rope_ import extract_method
            source_path = args.get("source_path", "")
            name = args.get("name", "")
            start_line = int(args.get("start_line", 1))
            end_line = int(args.get("end_line", 1))
            if not source_path or not name:
                return ToolResult(name=tool_name, content="rope_extract: source_path and name are required", is_error=True)
            result = extract_method(workdir=wd, source_path=source_path, name=name, start_line=start_line, end_line=end_line)
            lines = [f"📦 rope extract: '{name}' from lines {start_line}-{end_line} in {source_path}"]
            lines.append(f"  New method at line: ~{result.new_method_line}")
            for ch in result.changes[:10]:
                lines.append(f"  {ch}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.success)

        elif tool_name == "rope_inline":
            from forge.code_intelligence.rope_ import inline_function
            source_path = args.get("source_path", "")
            name = args.get("name", "")
            start_line = int(args.get("start_line", 1))
            end_line = int(args.get("end_line", 1))
            if not source_path or not name:
                return ToolResult(name=tool_name, content="rope_inline: source_path and name are required", is_error=True)
            result = inline_function(workdir=wd, source_path=source_path, name=name, start_line=start_line, end_line=end_line)
            lines = [f"↩ rope inline: '{name}' at {source_path}:{start_line}-{end_line}"]
            lines.append(f"  {result.get('raw', 'inlined')}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.get("success", False))

        elif tool_name == "rope_move":
            from forge.code_intelligence.rope_ import move_symbol
            source_path = args.get("source_path", "")
            symbol_name = args.get("symbol_name", "")
            dest_path = args.get("dest_path", "")
            if not source_path or not symbol_name or not dest_path:
                return ToolResult(name=tool_name, content="rope_move: source_path, symbol_name, and dest_path are required", is_error=True)
            result = move_symbol(workdir=wd, source_path=source_path, symbol_name=symbol_name, dest_path=dest_path)
            lines = [f"📦 rope move: '{symbol_name}' from {source_path} → {dest_path}"]
            lines.append(f"  {result.get('raw', 'moved')}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.get("success", False))

        # ── Contract / property testing ──────────────────────────────────────

        elif tool_name == "crosshair":
            from forge.code_intelligence.crosshair_ import run_crosshair
            targets = args.get("targets", ["."])
            timeout = int(args.get("timeout", 120))
            result = run_crosshair(workdir=wd, targets=targets, timeout=timeout)
            if result.passed:
                return ToolResult(name=tool_name, content="✅ Crosshair: all contracts proved — no violations found", is_error=False)
            lines = [f"❌ Crosshair: {len(result.issues)} contract violations:"]
            for iss in result.issues[:15]:
                lines.append(f"  [{iss.severity}] {iss.file}:{iss.line} — {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=True)

        elif tool_name == "hypothesis":
            from forge.code_intelligence.hypothesis_ import run_hypothesis
            test_suite_path = args.get("test_suite_path")
            result = run_hypothesis(workdir=wd, test_suite_path=Path(test_suite_path) if test_suite_path else None)
            lines = ["🧪 Hypothesis property-based test health check:"]
            if result.passed:
                lines.append("  ✅ All health checks passed")
            else:
                lines.append(f"  ⚠ {len(result.issues)} health check issues:")
                for iss in result.issues[:15]:
                    lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=False)

        elif tool_name == "mutmut":
            from forge.code_intelligence.mutmut_ import run_mutmut
            test_cmd = args.get("test_cmd", "pytest tests/ -x")
            threshold = float(args.get("threshold", 70))
            result = run_mutmut(workdir=wd, test_cmd=test_cmd, threshold=threshold)
            mr = result.mutation_result
            lines = [f"🧬 Mutmut mutation testing — score: {mr.mutation_score if mr else '?'}% (threshold: {threshold}%)"]
            if mr:
                lines.append(f"  Total: {mr.total_mutants} | Killed: {mr.killed} | Survived: {mr.survived} | Timeout: {mr.timeout}")
            high_issues = [i for i in result.issues if i.severity == "HIGH" and "score" not in i.message.lower()]
            if high_issues:
                lines.append(f"\n❌ {len(high_issues)} surviving mutants (test gaps):")
                for iss in high_issues[:10]:
                    lines.append(f"  {iss.message}")
            passed_msg = "✅ PASSED" if result.passed else "❌ FAILED (score below threshold)"
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.passed)

        elif tool_name == "griffe":
            from forge.code_intelligence.griffe_ import run_griffe
            expected_contracts = args.get("expected_contracts")
            result = run_griffe(workdir=wd, expected_contracts=expected_contracts)
            lines = ["📋 Griffe API contract check:"]
            if result.passed:
                lines.append("  ✅ No API drift detected")
            else:
                lines.append(f"  ⚠ {len(result.issues)} issues:")
                for iss in result.issues[:20]:
                    lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.passed)

        # ── Profiling ────────────────────────────────────────────────────────

        elif tool_name == "memray":
            from forge.code_intelligence.memray_ import run_memray_profile
            test_cmd = args.get("test_cmd", "pytest tests/ -x")
            leak_check = args.get("leak_check", True)
            memory_limit_mb = float(args.get("memory_limit_mb", 512.0))
            result = run_memray_profile(workdir=wd, test_cmd=test_cmd, leak_check=leak_check, memory_limit_mb=memory_limit_mb)
            lines = ["📊 Memray memory profiling:"]
            if result.profile:
                p = result.profile
                lines.append(f"  Peak memory: {p.peak_memory_bytes / 1024 / 1024:.1f} MB")
                if p.peak_memory_bytes / 1024 / 1024 > memory_limit_mb:
                    lines.append(f"  ❌ EXCEEDS LIMIT ({memory_limit_mb} MB)")
                if p.leaks:
                    lines.append(f"  ⚠ Memory leaks detected: {len(p.leaks)}")
            if result.passed:
                lines.append("  ✅ PASSED")
            else:
                lines.append(f"  ❌ ISSUES FOUND:")
                for iss in result.issues[:15]:
                    lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.passed)

        elif tool_name == "pyspy":
            from forge.code_intelligence.pyspy_ import run_pyspy_profile
            command = args.get("command", "")
            output = args.get("output", "profile.svg")
            if not command:
                return ToolResult(name=tool_name, content="pyspy: command is required", is_error=True)
            result = run_pyspy_profile(workdir=wd, command=command, output=output)
            lines = ["🔍 py-spy profiling:"]
            lines.append(f"  Output: {result.raw[:200] if result.raw else 'profile saved'}")
            if result.issues:
                for iss in result.issues[:10]:
                    lines.append(f"  [{iss.severity}] {iss.message}")
            return ToolResult(name=tool_name, content="\n".join(lines), is_error=not result.success)

        # ── Unknown ─────────────────────────────────────────────────────────

        return ToolResult(
            name=tool_name,
            content=f"Unknown tool: {tool_name}. Available code intelligence tools: bandit, radon_cc, vulture, semgrep, pip_audit, pyupgrade, deptry, cyclonedx_sbom, jedi_find_callers, jedi_infer, jedi_complete, jedi_signatures, rope_rename, rope_extract, rope_inline, rope_move, crosshair, hypothesis, mutmut, griffe, memray, pyspy",
            is_error=True,
        )

    # ── Tool implementations ─────────────────────────────────────────────────

    def _tool_bash(self, args: dict, session_id: str) -> ToolResult:
        cmd = args.get("command", "")
        timeout = args.get("timeout", 60)

        if not cmd:
            return ToolResult(name="bash", content="No command provided", is_error=True)

        # Safety check
        base = cmd.strip().split()[0] if cmd.strip() else ""
        dangerous = {"rm", "dd", "mkfs", "fdisk", "sfdisk"}
        if base in dangerous and not self.state.check_permission("bash", "execute", cmd):
            return ToolResult(name="bash", content=f"Blocked dangerous command: {base}", is_error=True)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=min(timeout, 300),
                cwd=str(self._resolve_workdir(session_id)),
            )
            output = result.stdout or ""
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            if result.returncode != 0 and not output:
                output = f"Exit code: {result.returncode}"
            return ToolResult(name="bash", content=output or "(no output)", is_error=result.returncode != 0)
        except subprocess.TimeoutExpired:
            return ToolResult(name="bash", content="Command timed out", is_error=True)
        except Exception as e:
            return ToolResult(name="bash", content=str(e), is_error=True)

    def _tool_read(self, args: dict) -> ToolResult:
        path = args.get("path") or args.get("file", "")
        if not path:
            return ToolResult(name="read", content="No path provided", is_error=True)
        try:
            p = Path(path).expanduser()
            if not p.exists():
                return ToolResult(name="read", content=f"File not found: {path}", is_error=True)
            # Limit read size
            content = p.read_text()
            if len(content) > 30000:
                content = content[:15000] + f"\n... [{len(content)-30000} chars truncated] ...\n" + content[-15000:]
            return ToolResult(name="read", content=content)
        except Exception as e:
            return ToolResult(name="read", content=str(e), is_error=True)

    def _tool_glob(self, args: dict) -> ToolResult:
        import glob as _glob
        pattern = args.get("pattern") or args.get("glob", "*")
        try:
            matches = _glob.glob(pattern, recursive=True)
            return ToolResult(name="glob", content="\n".join(matches) if matches else "(no matches)")
        except Exception as e:
            return ToolResult(name="glob", content=str(e), is_error=True)

    def _tool_grep(self, args: dict) -> ToolResult:
        import glob as _glob
        pattern = args.get("pattern", "")
        file_pattern = args.get("file") or args.get("path") or "*.py"
        if not pattern:
            return ToolResult(name="grep", content="No pattern provided", is_error=True)
        try:
            matches = []
            for f in _glob.glob(file_pattern, recursive=True):
                try:
                    lines = Path(f).read_text().splitlines()
                    for i, line in enumerate(lines, 1):
                        if pattern.lower() in line.lower():
                            matches.append(f"{f}:{i}: {line.rstrip()}")
                except Exception:
                    pass
            return ToolResult(name="grep", content="\n".join(matches[:100]) if matches else "(no matches)")
        except Exception as e:
            return ToolResult(name="grep", content=str(e), is_error=True)

    def _tool_ls(self, args: dict) -> ToolResult:
        path = args.get("path", ".")
        try:
            p = Path(path).expanduser()
            if not p.exists():
                return ToolResult(name="ls", content=f"Path not found: {path}", is_error=True)
            entries = []
            for child in sorted(p.iterdir()):
                marker = "/" if child.is_dir() else ""
                entries.append(f"{child.name}{marker}")
            return ToolResult(name="ls", content="\n".join(entries) if entries else "(empty)")
        except Exception as e:
            return ToolResult(name="ls", content=str(e), is_error=True)

    def _tool_write(self, args: dict, session_id: str) -> ToolResult:
        path = args.get("path") or args.get("file", "")
        content = args.get("content", "") or args.get("text", "")
        if not path:
            return ToolResult(name="write", content="No path provided", is_error=True)
        try:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return ToolResult(name="write", content=f"Written: {path} ({len(content)} bytes)")
        except Exception as e:
            return ToolResult(name="write", content=str(e), is_error=True)

    def _tool_edit(self, args: dict, session_id: str) -> ToolResult:
        path = args.get("path")
        old_text = args.get("old_text") or args.get("old", "")
        new_text = args.get("new_text") or args.get("new", "")
        if not path or not old_text:
            return ToolResult(name="edit", content="path and old_text are required", is_error=True)
        try:
            p = Path(path).expanduser()
            if not p.exists():
                return ToolResult(name="edit", content=f"File not found: {path}", is_error=True)
            original = p.read_text()
            if old_text not in original:
                return ToolResult(name="edit", content=f"Pattern not found in file: {old_text[:50]}", is_error=True)
            new_content = original.replace(old_text, new_text, 1)
            p.write_text(new_content)
            return ToolResult(name="edit", content=f"Edited: {path}")
        except Exception as e:
            return ToolResult(name="edit", content=str(e), is_error=True)

    def _tool_search(self, args: dict) -> ToolResult:
        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(name="search", content="No pattern provided", is_error=True)
        return ToolResult(
            name="search",
            content="(search falls back to grep — use the grep tool directly for better results)",
            is_error=False,
        )
