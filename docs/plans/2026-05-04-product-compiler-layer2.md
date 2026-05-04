# Product Compiler Layer 2 — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Complete the Product Compiler Layer 2 implementation: fix pending bugs, wire human gates in CLI, and close all Layer 2 gaps.

**Architecture:** Python package inside `forge/src/forge/product_compiler/`, sharing the existing `forge.llm` provider layer, Textual TUI scaffolding, and SQLite memory. No Go, no separate binary.

**Tech Stack:** Python 3.11+, structlog, pydantic, pytest, httpx, FastAPI (generated scaffold)

---

## Priority 1 — Bugs (Fix First)

### Task 1: Add WAL mode to `forge/db.py`

**Objective:** Prevent "database is locked" errors when multiple subagents write simultaneously.

**Files:**
- Modify: `src/forge/db.py:241` (inside `_init_schema()`)

**Step 1: Read the file to find exact location**
```python
# Read lines 235-250 of forge/db.py
```

**Step 2: Add WAL pragmas before schema statements**
```python
# In ForgeDB._init_schema(), after conn = self._get_conn():
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

**Step 3: Verify**
```bash
grep -n "journal_mode" src/forge/db.py
```

---

### Task 2: Fix `llm.py` duplicate parameter and `complete_json` stub

**Objective:** Clean up inconsistencies in the OpenAI backend.

**Files:**
- Modify: `src/forge/llm/backends/openai_.py`

**Step 1: Read the file**
Find the `complete()` method signature and identify the duplicate `system`/`SYSTEM` parameter.

**Step 2: Fix the parameter name**
Consolidate to a single `system` parameter.

**Step 3: Implement `complete_json`**
```python
def complete_json(self, prompt: str, system: str = "", **kwargs) -> dict:
    response = self.complete(prompt=prompt, system=system, **kwargs)
    content = response.content if hasattr(response, "content") else str(response)
    return json.loads(content)
```

**Step 4: Run tests**
```bash
cd ~/forge && python -m pytest tests/test_llm.py -v
```

---

### Task 3: Add `pytest-json-report` to forge dependencies

**Objective:** The coder uses `--json-report` flag but the package isn't in dependencies.

**Files:**
- Modify: `pyproject.toml` or `setup.py` or `requirements.txt` (find which one forge uses)

**Step 1: Find the dependency file**
```bash
ls ~/forge/*.toml ~/forge/requirements*.txt 2>/dev/null
```

**Step 2: Add the dependency**
```toml
# In pyproject.toml under dependencies:
"pytest-json-report>=1.6.0",
```

**Step 3: Verify**
```bash
cd ~/forge && python -c "import pytest_json_report"  # should import without error
```

---

## Priority 2 — Layer 2 Gate Wiring

### Task 4: Wire Gate 1 manual approval in CLI

**Objective:** When `--auto-approve` is NOT set, the CLI should block on `y/n` after printing the rule set.

**Files:**
- Modify: `src/forge/cli.py:439` (the `compile` command)

**Step 1: Read the compile command (lines 420-520)**
```python
# Understand the current event loop
```

**Step 2: Add `y/n` handling for gate events**
```python
# In the event loop:
elif event["type"] == "awaiting_input":
    answer = click.prompt(event["payload"]["prompt"], default="", show_default=False)
    if answer.lower() in ("y", "yes"):
        pipeline.approve()
    else:
        click.echo("Rule set rejected. Pipeline stopping.")
        break
```

**Step 3: Also handle the "gate" event type to print rule set**
```python
elif event["type"] == "gate":
    gate_data = event["payload"]
    click.echo(f"\n{'='*60}")
    click.echo(f"  GATE {gate_data['gate']}: {gate_data['name']}")
    click.echo(f"{'='*60}")
    if "rules" in gate_data:
        for rule in gate_data["rules"][:20]:
            click.echo(f"  {rule}")
        if len(gate_data["rules"]) > 20:
            click.echo(f"  ... and {len(gate_data['rules']) - 20} more rules")
    elif "sql" in gate_data:
        click.echo(gate_data["sql"])
```

**Step 4: Test**
```bash
cd ~/forge && forge compile "a blog"  # should block on gate
# Type 'y' — should proceed
```

---

### Task 5: Wire Gate 2 (DB Schema) approval in CLI

**Files:**
- Modify: `src/forge/cli.py` (same as Task 4)

Same pattern as Gate 1, but for the DB schema gate event. Gate 2 payload includes `table_count` and `sql`.

---

### Task 6: Fix Coder to detect framework from tests

**Objective:** The coder always generates FastAPI. It should detect Flask/Django/etc. from the test code.

**Files:**
- Modify: `src/forge/product_compiler/agents/coder.py:142-167` (`_generate_scaffold`)

**Step 1: Read the _generate_scaffold method**

**Step 2: Modify to detect framework**
```python
def _detect_framework(self, test_suite: FailingTestSuite) -> str:
    """Detect web framework from test code."""
    all_code = " ".join(tc.test_code for tc in test_suite.test_cases).lower()
    if "fastapi" in all_code or "client" in all_code and "httpx" in all_code:
        return "fastapi"
    elif "flask" in all_code or "client" in all_code and "flask" in all_code:
        return "flask"
    elif "django" in all_code:
        return "django"
    return "fastapi"  # default

def _generate_scaffold(self, test_suite: FailingTestSuite, rule_set: RuleSet) -> str:
    framework = self._detect_framework(test_suite)
    prompt = f"""Generate a {framework} app scaffold...
```

---

## Priority 3 — Layer 3 Scaffolding (Edit Pipeline)

### Task 7: Create edit-mode agent stubs

**Objective:** Scaffold the 5 edit-mode agents that will be implemented in Layer 3.

**Files:**
- Create: `src/forge/product_compiler/agents/impact_analyst.py`
- Create: `src/forge/product_compiler/agents/rule_extractor.py`
- Create: `src/forge/product_compiler/agents/delta_compiler.py`
- Create: `src/forge/product_compiler/agents/blast_checker.py`
- Create: `src/forge/product_compiler/agents/test_delta_writer.py`

**Step 1: Create each file with stub implementation**
```python
"""Impact Analyst — maps change intent to blast radius."""

class ImpactAnalystAgent:
    def run(self, change_intent, codebase_index=None) -> ImpactSurface:
        raise NotImplementedError("Edit pipeline (Layer 3) not yet implemented")
```

**Step 2: Update agents/__init__.py**
Add re-exports for all new agents.

**Step 3: Update pipeline.py**
Wire in the edit-mode stages — when `PipelineMode == EDIT`, run the edit pipeline instead of the new-project pipeline. For now, raise `NotImplementedError` with a helpful message pointing to Layer 3.

---

### Task 8: Create Codebase Index stub

**Objective:** Create the index data structure and update the Integrator to build it after each change.

**Files:**
- Create: `src/forge/product_compiler/codebase_index.py`

```python
"""Codebase Index — dependency graph, call graph, contract index."""

class CodebaseIndex:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.dependency_graph: dict[str, list[str]] = {}
        self.call_graph: dict[str, list[str]] = {}
        self.contract_index: dict[str, dict] = {}
        self.rule_index: dict[str, list[str]] = {}
        self.test_coverage: dict[str, list[str]] = {}

    def build(self):
        """Build full index by scanning the codebase."""
        raise NotImplementedError("Layer 3 required")

    def update_after_change(self, changed_file: str):
        """Incrementally update index after a file change."""
        raise NotImplementedError("Layer 3 required")

    def query_impact(self, file_or_function: str) -> ImpactSurface:
        """Query what would be affected by changing this."""
        raise NotImplementedError("Layer 3 required")
```

---

## Priority 4 — TUI Scaffolding

### Task 9: Scaffold TUI app.py

**Objective:** Create the Textual TUI app with event-driven pipeline display.

**Files:**
- Create: `src/forge/tui/product_compiler.py` (or extend existing TUI)

Build on the existing Forge TUI scaffolding (`forge/tui/`), adding:
- `ProductCompilerView` — main container
- `StageProgress` — shows current stage (3 of 7)
- `InterviewPanel` — shows questions/answers as they happen
- `RuleSetPanel` — shows compiled rules
- `GateScreen` — full-screen approval modal
- `TestResultPanel` — live test output

**Keep it minimal** — wire the event loop to print to the TUI, but don't build the full visual design yet.

---

## Priority 5 — SPEC.md Update

### Task 10: Update SPEC.md with final Layer 2 status

After completing all tasks above, update SPEC.md section 8 (Layer Roadmap) to reflect actual completion status.

---

## Execution Order

1. Tasks 1-3 (Bugs) — fix first, always
2. Tasks 4-6 (Gate wiring + framework detection) — makes the pipeline interactive
3. Tasks 7-8 (Edit pipeline stubs + index stub) — sets up Layer 3
4. Task 9 (TUI scaffold) — sets up Layer 4
5. Task 10 (Update SPEC.md) — document final state

---

## Verification

After each task:
- Run `python -m pytest tests/` to ensure no regressions
- For CLI changes, run `forge compile "a blog" --auto-approve` as smoke test
- For agent changes, run `forge compile "a REST API" --auto-approve` with multiple test prompts
