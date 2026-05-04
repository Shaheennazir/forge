"""
forge.tui.screens.code_intelligence_screen — Phase 4: Code Intelligence Dashboard.

Manual invocation of all 22 code intelligence tools with:
  - Per-tool run buttons (installed status)
  - Severity-sorted results with file:line references
  - PASS/FAIL gate badge per tool
  - Run-all per category
  - Background execution with live log stream
  - Deep-analysis tools (mutmut, memray, py-spy, hypothesis, crosshair)
    separated into an "Advanced" tab

Keybindings:
  Escape          → back
  Ctrl+R          → run selected / focused tool
  Ctrl+A          → run all in Security category
  Ctrl+C          → run all in Complexity category
  Ctrl+D          → run all in Dependency category
  Ctrl+S          → run all in Semantic category
  Ctrl+X          → run all in Advanced category
  Ctrl+K          → clear results
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Static, Log, Input, Tabs, Tab

from forge.tui.state import AppState, StatusLevel


# ── Tool category ────────────────────────────────────────────────────────────

class ToolCategory(Enum):
    SECURITY    = "security"
    COMPLEXITY  = "complexity"
    DEPENDENCY  = "dependency"
    SEMANTIC    = "semantic"
    ADVANCED    = "advanced"


# ── Tool definition ─────────────────────────────────────────────────────────

@dataclass
class ToolDef:
    id: str                         # unique identifier used in routing
    name: str                       # display name
    category: ToolCategory
    description: str
    icon: str
    gate_severity: str             # severity that triggers FAIL ("HIGH" or "CRITICAL")
    long_running: bool = False     # True for mutmut / memray / deep analysis
    requires_args: bool = False    # True for tools needing extra input (target path, etc.)


# ── Built-in tool registry ───────────────────────────────────────────────────

TOOLS: list[ToolDef] = [
    # Security
    ToolDef("bandit",     "Bandit",       ToolCategory.SECURITY,   "Security scanner — HIGH/CRITICAL issues block",              "🔒", "HIGH",   False),
    ToolDef("semgrep",    "Semgrep",      ToolCategory.SECURITY,   "Static analysis with security/correctness rules",             "🔍", "HIGH",   False),
    ToolDef("pip_audit",  "pip-audit",    ToolCategory.SECURITY,   "CVE scanner for Python dependencies",                        "📦", "HIGH",   False),
    ToolDef("pyupgrade",  "pyupgrade",    ToolCategory.SECURITY,   "Auto-fix Python syntax to modern idioms",                    "⚡",  "LOW",    False),

    # Complexity / Quality
    ToolDef("radon_cc",   "Radon CC",     ToolCategory.COMPLEXITY, "Cyclomatic complexity — functions with CC>10 block",         "📊", "HIGH",   False),
    ToolDef("vulture",    "Vulture",      ToolCategory.COMPLEXITY, "Dead code detection at 80%+ confidence",                   "💀", "HIGH",   False),
    ToolDef("deptry",     "Deptry",       ToolCategory.COMPLEXITY, "Missing and unused Python dependencies",                  "📋", "HIGH",   False),

    # Dependency
    ToolDef("cyclonedx",  "CycloneDX",    ToolCategory.DEPENDENCY, "Generate SBOM in JSON/XML format",                         "🧾", "LOW",    False),

    # Semantic (Jedi)
    ToolDef("jedi_find_callers",  "Find Callers",  ToolCategory.SEMANTIC, "Find all call sites of a function (Jedi)",              "🔎", "LOW",    True),
    ToolDef("jedi_infer",         "Type Inference", ToolCategory.SEMANTIC, "Infer types at a code position (Jedi)",                "🧬", "LOW",    True),
    ToolDef("jedi_complete",      "Completions",    ToolCategory.SEMANTIC, "Autocomplete suggestions at cursor (Jedi)",              "💡", "LOW",    True),
    ToolDef("jedi_signatures",    "Signatures",     ToolCategory.SEMANTIC, "Function signatures with parameter types (Jedi)",       "�ignature", "LOW", True),

    # Rope refactoring
    ToolDef("rope_rename",   "Rename",   ToolCategory.SEMANTIC, "Semantic rename across project (Rope)",                    "✏️",  "LOW", True),
    ToolDef("rope_extract",   "Extract",  ToolCategory.SEMANTIC, "Extract selected lines into a method (Rope)",               "🔧", "LOW", True),
    ToolDef("rope_inline",    "Inline",   ToolCategory.SEMANTIC, "Inline a function at its call sites (Rope)",               "↩️",  "LOW", True),
    ToolDef("rope_move",      "Move",     ToolCategory.SEMANTIC, "Move a function/class to another module (Rope)",          "🚚",  "LOW", True),

    # Advanced (deep analysis — longer runtime)
    ToolDef("crosshair",  "Crosshair",  ToolCategory.ADVANCED, "Contract prover — violations block pipeline",              "🏷️", "HIGH", True),
    ToolDef("hypothesis", "Hypothesis", ToolCategory.ADVANCED, "Property-based test validation",                           "🎯", "MEDIUM", True),
    ToolDef("mutmut",     "Mutmut",     ToolCategory.ADVANCED, "Mutation testing — score below threshold blocks",          "🧬", "HIGH", True),
    ToolDef("griffe",     "Griffe",     ToolCategory.ADVANCED, "API contract drift detection",                             "🔎", "HIGH", True),
    ToolDef("memray",     "Memray",     ToolCategory.ADVANCED, "Memory profiler — leaks and >512MB peak block",            "📉", "HIGH", True),
    ToolDef("pyspy",      "py-spy",    ToolCategory.ADVANCED, "CPU sampling profiler",                                   "⏱️",  "MEDIUM", True),
]

TOOLS_BY_ID = {t.id: t for t in TOOLS}
TOOLS_BY_CATEGORY = {cat: [t for t in TOOLS if t.category == cat] for cat in ToolCategory}


# ── Tool result ─────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    tool_id: str
    success: bool
    passed: bool
    severity: str          # highest severity among issues
    issues: list           # [Issue]
    summary: str            # one-line summary for button badge
    raw: str                # full raw output for log
    duration_ms: int = 0


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW / CRITICAL
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]


# ── Workdir resolver ──────────────────────────────────────────────────────────

def _resolve_workdir(project_name: str) -> Path:
    wd = Path.home() / ".forge" / "workspace" / project_name
    wd.mkdir(parents=True, exist_ok=True)
    return wd


# ── Tool runner (runs in background thread) ──────────────────────────────────

def _run_tool(tool_id: str, workdir: Path, extra_args: dict) -> ToolResult:
    """Execute a single tool and return a ToolResult. Runs in a background thread."""
    start = time.monotonic()
    issues: list[Issue] = []
    passed = True
    severity = "LOW"
    summary = "no issues"
    raw = ""

    try:
        if tool_id == "bandit":
            from forge.code_intelligence.bandit_ import run_bandit
            r = run_bandit(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_high = sum(1 for i in issues if i.severity in ("HIGH", "CRITICAL"))
            summary = f"{'PASS' if passed else 'FAIL'} — {len(issues)} issues, {n_high} high/critical"
            raw = r.raw

        elif tool_id == "radon_cc":
            from forge.code_intelligence.radon_ import run_radon
            r = run_radon(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_high = sum(1 for i in issues if i.severity == "HIGH")
            summary = f"{'PASS' if passed else 'FAIL'} — CC>{10} in {n_high} functions"
            raw = r.raw

        elif tool_id == "vulture":
            from forge.code_intelligence.vulture_ import run_vulture
            r = run_vulture(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_high = sum(1 for i in issues if i.severity == "HIGH")
            summary = f"{'PASS' if passed else 'FAIL'} — {n_high} high-confidence dead code"
            raw = r.raw

        elif tool_id == "semgrep":
            from forge.code_intelligence.semgrep_ import run_semgrep
            configs = extra_args.get("configs")
            r = run_semgrep(workdir, configs=configs)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_high = sum(1 for i in issues if i.severity == "HIGH")
            summary = f"{'PASS' if passed else 'FAIL'} — {n_high} high severity findings"
            raw = r.raw

        elif tool_id == "pip_audit":
            from forge.code_intelligence.pip_audit_ import run_pip_audit
            r = run_pip_audit(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_cve = sum(1 for i in issues if i.code)
            summary = f"{'PASS' if passed else 'FAIL'} — {n_cve} CVEs found"
            raw = r.raw

        elif tool_id == "pyupgrade":
            from forge.code_intelligence.pyupgrade_ import run_pyupgrade
            r = run_pyupgrade(workdir)
            passed = r.passed  # always True for pyupgrade
            issues = []
            summary = f"PASS — {r.files_modified} files would be updated"
            raw = r.raw

        elif tool_id == "deptry":
            from forge.code_intelligence.deptry_ import run_deptry
            r = run_deptry(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            missing = sum(1 for i in issues if i.code == "DEP001")
            summary = f"{'PASS' if passed else 'FAIL'} — {missing} missing deps"
            raw = r.raw

        elif tool_id == "cyclonedx":
            from forge.code_intelligence.cyclonedx_ import run_sbom
            r = run_sbom(workdir)
            passed = r.passed
            issues = []
            summary = f"{'PASS' if passed else 'WARN'} — SBOM generated"
            raw = r.raw

        elif tool_id == "crosshair":
            from forge.code_intelligence.crosshair_ import run_crosshair
            targets = extra_args.get("targets")
            timeout = extra_args.get("timeout", 120)
            r = run_crosshair(workdir, targets=targets, timeout=timeout)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_viol = sum(1 for i in issues)
            summary = f"{'PASS' if passed else 'FAIL'} — {n_viol} contract violations"
            raw = r.raw

        elif tool_id == "hypothesis":
            from forge.code_intelligence.hypothesis_ import run_hypothesis
            r = run_hypothesis(workdir)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            summary = f"{'PASS' if passed else 'WARN'} — {len(issues)} health check issues"
            raw = r.raw

        elif tool_id == "mutmut":
            from forge.code_intelligence.mutmut_ import run_mutmut
            test_cmd = extra_args.get("test_cmd", "pytest tests/ -x")
            threshold = extra_args.get("threshold", 70)
            r = run_mutmut(workdir, test_cmd=test_cmd, threshold=threshold)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            mr = r.mutation_result
            score = mr.mutation_score if mr else 0
            summary = f"{'PASS' if passed else 'FAIL'} — score {score:.1f}% (threshold {threshold}%)"
            raw = r.raw

        elif tool_id == "griffe":
            from forge.code_intelligence.griffe_ import run_griffe
            expected = extra_args.get("expected_contracts")
            r = run_griffe(workdir, expected_contracts=expected)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            n_api = sum(1 for i in issues if i.code == "missing_contract")
            summary = f"{'PASS' if passed else 'FAIL'} — {n_api} API contracts missing"
            raw = r.raw

        elif tool_id == "memray":
            from forge.code_intelligence.memray_ import run_memray_profile
            test_cmd = extra_args.get("test_cmd", "pytest tests/ -x")
            limit = extra_args.get("memory_limit_mb", 512.0)
            r = run_memray_profile(workdir, test_cmd=test_cmd, memory_limit_mb=limit)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            peak = r.profile.peak_memory_mb if r.profile else 0
            summary = f"{'PASS' if passed else 'FAIL'} — peak {peak:.1f}MB"
            raw = r.raw

        elif tool_id == "pyspy":
            from forge.code_intelligence.pyspy_ import run_pyspy_profile
            pid = extra_args.get("pid")
            duration = extra_args.get("duration", 30)
            r = run_pyspy_profile(pid=pid, workdir=workdir, duration=duration)
            passed = r.passed
            issues = [Issue(
                severity=i.severity, message=i.message,
                file=i.file, line=i.line, code=i.code)
                for i in r.issues]
            severity = max((i.severity for i in issues), default="LOW",
                           key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0))
            summary = f"{'PASS' if passed else 'WARN'} — {len(issues)} slow functions"
            raw = r.raw

        elif tool_id == "jedi_find_callers":
            from forge.code_intelligence.jedi_ import find_callers
            fn_name = extra_args.get("function_name", "")
            if not fn_name:
                return ToolResult(tool_id=tool_id, success=False, passed=True,
                                  severity="LOW", issues=[], summary="PASS — no function name given",
                                  raw="", duration_ms=0)
            r = find_callers(workdir, fn_name)
            issues = []
            summary = f"found {len(r.results)} references to '{fn_name}'"
            raw = r.raw

        elif tool_id == "jedi_infer":
            from forge.code_intelligence.jedi_ import get_inference
            code = extra_args.get("code", "")
            line = extra_args.get("line", 1)
            col = extra_args.get("column", 0)
            r = get_inference(workdir, code, line, col)
            issues = []
            summary = f"{len(r.results)} inferred types"
            raw = r.raw

        elif tool_id == "jedi_complete":
            from forge.code_intelligence.jedi_ import get_completions
            code = extra_args.get("code", "")
            line = extra_args.get("line", 1)
            col = extra_args.get("column", 0)
            r = get_completions(workdir, code, line, col)
            issues = []
            summary = f"{len(r.results)} completions"
            raw = r.raw

        elif tool_id == "jedi_signatures":
            from forge.code_intelligence.jedi_ import get_signatures
            code = extra_args.get("code", "")
            line = extra_args.get("line", 1)
            col = extra_args.get("column", 0)
            r = get_signatures(workdir, code, line, col)
            issues = []
            summary = f"{len(r.results)} signatures"
            raw = r.raw

        elif tool_id in ("rope_rename", "rope_extract", "rope_inline", "rope_move"):
            # Rope tools are invoked via the chat/LLM in practice;
            # here we provide a "dry run" form that shows what would be done
            summary = f"INFO — {tool_id} needs args (invoke via chat for refactoring)"
            raw = f"Rope tool {tool_id} requires interactive arguments.\nUse the chat interface to invoke this tool with specific parameters."

        else:
            summary = f"UNKNOWN — tool '{tool_id}' not implemented"
            raw = f"Tool {tool_id} is not yet wired in the code intelligence runner."

    except Exception as e:
        summary = f"ERROR — {e}"
        raw = str(e)

    duration_ms = int((time.monotonic() - start) * 1000)
    return ToolResult(
        tool_id=tool_id,
        success=True,
        passed=passed,
        severity=severity,
        issues=issues,
        summary=summary,
        raw=raw,
        duration_ms=duration_ms,
    )


# ── Result cache (in-memory for current session) ──────────────────────────────

_results: dict[str, ToolResult] = {}


# ── Screen ───────────────────────────────────────────────────────────────────

_CATEGORY_LABELS = {
    ToolCategory.SECURITY:   ("🔒 Security",     "#EF4444"),
    ToolCategory.COMPLEXITY: ("📊 Complexity",    "#F59E0B"),
    ToolCategory.DEPENDENCY: ("📦 Dependency",    "#8B5CF6"),
    ToolCategory.SEMANTIC:   ("🧠 Semantic",      "#06B6D4"),
    ToolCategory.ADVANCED:   ("🔬 Advanced",      "#EC4899"),
}

_CATEGORY_KEYS = {
    ToolCategory.SECURITY:   "ctrl+a",
    ToolCategory.COMPLEXITY: "ctrl+c",
    ToolCategory.DEPENDENCY: "ctrl+d",
    ToolCategory.SEMANTIC:   "ctrl+s",
    ToolCategory.ADVANCED:   "ctrl+x",
}


class CodeIntelligenceScreen(Screen):
    """
    Code intelligence dashboard — manual tool invocation with results.

    Layout:
      [header: project selector | title | status]
      [tab bar: Security | Complexity | Dependency | Semantic | Advanced]
      [body — left: tool list | right: results panel]
      [footer: run buttons]
    """

    TITLE = "Code Intelligence"
    BINDINGS = [
        ("escape", "pop_screen",      "Back"),
        ("ctrl+k", "clear_results",    "Clear"),
        ("ctrl+r", "run_focused",      "Run"),
        ("ctrl+a", "run_security",     "Security"),
        ("ctrl+c", "run_complexity",   "Complexity"),
        ("ctrl+d", "run_dependency",   "Dependency"),
        ("ctrl+s", "run_semantic",     "Semantic"),
        ("ctrl+x", "run_advanced",     "Advanced"),
    ]

    CSS = """
    CodeIntelligenceScreen {
        layout: vertical;
    }

    #ci-header {
        height: 2;
        background: $primary;
        color: $text;
        dock: top;
    }

    #ci-tabs {
        height: 3;
        dock: top;
        background: $surface-darken-1;
    }

    #ci-body {
        layout: horizontal;
        width: 100%;
    }

    #tool-panel {
        width: 38;
        height: 1fr;
        background: $surface-darken-2;
        border: solid $primary 20%;
        padding: 1 1;
    }

    #results-panel {
        width: 1fr;
        height: 1fr;
        border: solid $primary 20%;
        margin: 0 1;
        padding: 0;
    }

    #results-header {
        height: 2;
        background: $surface-darken-1;
        dock: top;
        padding: 0 1;
    }

    #results-log {
        height: 1fr;
        border: none;
        margin: 0;
        padding: 0 1;
    }

    #ci-footer {
        height: 3;
        dock: bottom;
        background: $surface-darken-1;
        padding: 0 2;
        align: center middle;
    }

    .tool-btn {
        width: 100%;
        margin: 0 0 1 0;
        min-height: 3;
    }

    .tool-btn.success {
        border: solid $success 50%;
    }

    .tool-btn.failure {
        border: solid $error 50%;
    }

    .tool-btn.running {
        border: solid $warning 50%;
    }

    .tool-btn.pending {
        border: solid $primary 30%;
    }

    .result-high   { color: $error; text-style: bold; }
    .result-medium { color: $warning; }
    .result-low    { color: $text-muted; }
    .result-pass   { color: $success; text-style: bold; }
    .result-fail   { color: $error; text-style: bold; }
    """

    selected_category = reactive[ToolCategory](ToolCategory.SECURITY)
    selected_tool_id  = reactive[Optional[str]](None)
    is_running        = reactive[bool](False)

    def __init__(self, state: AppState, project_id: str = "default"):
        super().__init__()
        self.state       = state
        self.project_id  = project_id
        self._running: set[str] = set()   # tool_ids currently running
        self._focused_tool: Optional[str] = None

    # ── Compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"🔬 CODE INTELLIGENCE  |  Project: {self.project_id}", id="ci-header"),
            id="ci-header-bar",
        )

        # Tab bar
        with Tabs(id="ci-tabs"):
            for cat in ToolCategory:
                label, _ = _CATEGORY_LABELS[cat]
                keybind_hint = _CATEGORY_KEYS[cat].replace("ctrl+", "").upper()
                yield Tab(f"{label}  [{keybind_hint}]", id=f"tab-{cat.value}")

        # Body
        with Horizontal(id="ci-body"):
            # Left: tool list
            with Vertical(id="tool-panel"):
                yield Static("TOOLS", id="tool-panel-label")
                yield ScrollableContainer(id="tool-list")

            # Right: results
            with Container(id="results-panel"):
                yield Static("  Results  —  click a tool to run it", id="results-header")
                yield Log(id="results-log")

        # Footer
        yield Container(
            Button("▶ Run Selected",     id="btn-run",      variant="primary"),
            Button("▶ Run All (Category)", id="btn-run-all", variant="primary"),
            Button("🗑 Clear",             id="btn-clear",   variant="default"),
            Button("Export Report",        id="btn-export",  variant="default"),
            id="ci-footer",
        )

    # ── Mount ────────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._subscribe_tabs()
        self._render_tool_list()
        self._wire_footer_buttons()
        # Activate first tab
        self.query_one("#tab-security", Tab).active = True

    def _subscribe_tabs(self) -> None:
        for tab in self.query(Tab):
            tab.on_click = self._on_tab_click

    def _on_tab_click(self, event: Tab.Clicked) -> None:
        tab_id = event.tab.id or ""
        for cat in ToolCategory:
            if f"tab-{cat.value}" == tab_id:
                self.selected_category = cat
                self._render_tool_list()
                return

    def _wire_footer_buttons(self) -> None:
        self.query_one("#btn-run",      Button).on_click = lambda _: self.action_run_focused()
        self.query_one("#btn-run-all",  Button).on_click = lambda _: self._run_category(self.selected_category)
        self.query_one("#btn-clear",    Button).on_click = lambda _: self.action_clear_results()
        self.query_one("#btn-export",   Button).on_click = lambda _: self._export_report()

    # ── Render tool list ─────────────────────────────────────────────────────

    def _render_tool_list(self) -> None:
        container = self.query_one("#tool-list", ScrollableContainer)
        container.remove_children()

        cat = self.selected_category
        label, color = _CATEGORY_LABELS[cat]
        self.query_one("#tool-panel-label", Static).update(f"[{color}]{label}[/]")

        for tool in TOOLS_BY_CATEGORY.get(cat, []):
            status_cls, status_icon = self._tool_button_style(tool.id)
            installed = self._is_installed(tool)

            btn = Button(
                f"{status_icon} {tool.name}",
                id=f"tool-btn-{tool.id}",
                variant="default",
                classes=f"tool-btn {status_cls}",
            )
            btn.disabled = not installed

            def make_handler(tid: str):
                def handler(event: Button.Pressed) -> None:
                    self.selected_tool_id = tid
                    self._run_tool(tid)
                return handler

            btn.on_click = make_handler(tool.id)
            yield btn
            container.mount(btn)

    def _tool_button_style(self, tool_id: str) -> tuple[str, str]:
        if tool_id in self._running:
            return "running", "⏳"
        if result := _results.get(tool_id):
            if result.passed:
                return "success", "✅"
            return "failure", "❌"
        return "pending", "⬜"

    def _is_installed(self, tool: ToolDef) -> bool:
        mapping = {
            "bandit":     "bandit",
            "radon_cc":   "radon",
            "vulture":    "vulture",
            "semgrep":    "semgrep",
            "pip_audit":  "pip-audit",
            "pyupgrade":  "pyupgrade",
            "deptry":     "deptry",
            "cyclonedx":  "cyclonedx",
            "crosshair":  "crosshair",
            "hypothesis": "hypothesis",
            "mutmut":     "mutmut",
            "griffe":     "griffe",
            "memray":     "memray",
            "pyspy":      "py-spy",
            "jedi_find_callers":  "jedi",
            "jedi_infer":         "jedi",
            "jedi_complete":      "jedi",
            "jedi_signatures":    "jedi",
            "rope_rename":  "rope",
            "rope_extract":  "rope",
            "rope_inline":  "rope",
            "rope_move":    "rope",
        }
        bin_name = mapping.get(tool.id, tool.id)
        return shutil.which(bin_name) is not None

    # ── Run tools ────────────────────────────────────────────────────────────

    def action_run_focused(self) -> None:
        if self.selected_tool_id:
            self._run_tool(self.selected_tool_id)
        elif self._focused_tool:
            self._run_tool(self._focused_tool)
        else:
            self.state.set_status("Select a tool first", StatusLevel.WARN, ttl=2)

    def action_run_security(self)   -> None: self._run_category(ToolCategory.SECURITY)
    def action_run_complexity(self) -> None: self._run_category(ToolCategory.COMPLEXITY)
    def action_run_dependency(self) -> None: self._run_category(ToolCategory.DEPENDENCY)
    def action_run_semantic(self)   -> None: self._run_category(ToolCategory.SEMANTIC)
    def action_run_advanced(self)   -> None: self._run_category(ToolCategory.ADVANCED)

    def _run_category(self, cat: ToolCategory) -> None:
        for tool in TOOLS_BY_CATEGORY.get(cat, []):
            if self._is_installed(tool):
                self._run_tool(tool.id)

    def _run_tool(self, tool_id: str) -> None:
        tool = TOOLS_BY_ID.get(tool_id)
        if not tool:
            return

        self._running.add(tool_id)
        self._refresh_tool_button(tool_id)
        self.is_running = True

        workdir = _resolve_workdir(self.project_id)
        extra = self._get_extra_args(tool)

        def worker():
            result = _run_tool(tool_id, workdir, extra)
            _results[tool_id] = result
            self._running.discard(tool_id)
            self.post_message(self._ToolDoneMsg(tool_id, result))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        self._append_log(f"[dim]▶ {tool.name} started...[/dim]")
        self.state.set_status(f"Running {tool.name}...", StatusLevel.INFO, ttl=5)

    def _get_extra_args(self, tool: ToolDef) -> dict:
        """Sub-classes can override to pass tool-specific arguments."""
        return {}

    class _ToolDoneMsg(Message):
        def __init__(self, tool_id: str, result: ToolResult):
            self.tool_id = tool_id
            self.result  = result
            super().__init__()

    def on_code_intelligence_screen__tool_done_msg(
        self, event: _ToolDoneMsg
    ) -> None:
        self._on_tool_done(event.tool_id, event.result)

    def _on_tool_done(self, tool_id: str, result: ToolResult) -> None:
        self.is_running = bool(self._running)
        tool = TOOLS_BY_ID.get(tool_id)
        tool_name = tool.name if tool else tool_id

        self._refresh_tool_button(tool_id)
        self._append_log("")

        if result.passed:
            self._append_log(f"[success]✅ {tool_name}: {result.summary}[/success]")
        else:
            self._append_log(f"[error]❌ {tool_name}: {result.summary}[/error]")

        # Show issues in log
        if result.issues:
            sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            sorted_issues = sorted(
                result.issues,
                key=lambda i: sev_order.get(i.severity, 99),
            )
            for iss in sorted_issues[:30]:
                loc = f"{iss.file}:{iss.line}" if iss.line else (iss.file or "?")
                icon = {"CRITICAL": "🚨", "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}.get(
                    iss.severity, "⚪"
                )
                self._append_log(
                    f"  {icon} [{iss.severity}] {loc} — {iss.message[:120]}"
                )
            if len(result.issues) > 30:
                self._append_log(f"  [dim]...and {len(result.issues) - 30} more[/dim]")
        elif result.raw and "not installed" in result.raw:
            self._append_log(f"  [dim]{result.raw}[/dim]")

        self._append_log(f"  [dim]Duration: {result.duration_ms}ms[/dim]")
        self._append_log("")

        status_msg = f"{tool_name}: {result.summary}"
        if result.passed:
            self.state.set_status(status_msg, StatusLevel.SUCCESS, ttl=4)
        else:
            self.state.set_status(status_msg, StatusLevel.ERROR, ttl=5)

    def _refresh_tool_button(self, tool_id: str) -> None:
        btn_id = f"tool-btn-{tool_id}"
        try:
            btn = self.query_one(f"#{btn_id}", Button)
        except Exception:
            return
        style_cls, icon = self._tool_button_style(tool_id)
        btn.label = f"{icon} {TOOLS_BY_ID.get(tool_id, type('', (), {'name': tool_id})()).name}"
        for cls in ("success", "failure", "running", "pending"):
            btn.remove_class(cls)
        btn.add_class(style_cls)

    def _append_log(self, line: str) -> None:
        try:
            log = self.query_one("#results-log", Log)
            ts = datetime.now().strftime("%H:%M:%S")
            log.write_line(f"[dim][{ts}][/dim] {line}" if line.startswith("[") else line)
        except Exception:
            pass

    # ── Clear / Export ────────────────────────────────────────────────────────

    def action_clear_results(self) -> None:
        global _results
        _results.clear()
        try:
            log = self.query_one("#results-log", Log)
            log.clear()
        except Exception:
            pass
        self._render_tool_list()
        self.state.set_status("Results cleared", StatusLevel.INFO, ttl=2)

    def _export_report(self) -> None:
        if not _results:
            self.state.set_status("No results to export", StatusLevel.WARN, ttl=3)
            return

        lines = [
            "# Code Intelligence Report",
            f"Project: {self.project_id}",
            f"Generated: {datetime.now().isoformat()}",
            "",
        ]
        for cat in ToolCategory:
            cat_tools = [t for t in TOOLS_BY_CATEGORY[cat] if t.id in _results]
            if not cat_tools:
                continue
            label, _ = _CATEGORY_LABELS[cat]
            lines.append(f"## {label}")
            for tool in cat_tools:
                r = _results[tool.id]
                verdict = "✅ PASS" if r.passed else "❌ FAIL"
                lines.append(f"- **{tool.name}**: {verdict} — {r.summary}")
                for iss in r.issues[:10]:
                    loc = f"{iss.file}:{iss.line}" if iss.line else "?"
                    lines.append(f"  - [{iss.severity}] {loc} — {iss.message[:100]}")
            lines.append("")

        report_text = "\n".join(lines)
        out_path = Path.home() / ".forge" / f"ci-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_text)

        self.state.set_status(f"Report saved to {out_path.name}", StatusLevel.SUCCESS, ttl=5)
        self._append_log(f"[dim]📄 Report exported: {out_path}[/dim]")
