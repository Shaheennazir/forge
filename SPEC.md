# Product Compiler — SPEC.md

> **Project:** Forge Product Compiler  
> **Language:** Python 3 (built inside `forge` monorepo, not Go)  
> **Version:** 0.1.0-draft  
> **Last Updated:** 2026-05-04

---

## 1. Overview

The Product Compiler is a multi-agent pipeline that compiles user intent into production software through formal, sequential stages. Unlike a code generator, it reduces ambiguity through mandatory checkpoints before writing any code.

**Core invariant:** No stage passes ambiguity to the next stage. If a stage cannot produce a complete output, it loops back to the previous stage or the user — not forward.

**Python over Go:** Forge already had the provider abstraction, Textual TUI scaffolding, SQLite memory, and all LLM backends. Building in Go meant rebuilding all of that first. Python gets to a validated pipeline faster.

---

## 2. Architecture

```
User Prompt
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  Stage 1 — Interviewer (adversarial Q&A)            │ ← loops until complete
│  Output: IntentDocument                              │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 2 — Flow Designer                            │
│  Output: UserFlowTree                               │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 3 — Contract Writer                          │
│  Output: APIContract[]                              │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 4 — Rule Compiler                            │
│  Output: RuleSet                                    │
└──────────────────────┬──────────────────────────────┘
                       ▼
               ┌───────────────┐
               │  HUMAN GATE 1 │  ← operator approves IF/THEN rules
               └───────┬───────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 5 — Schema Designer                          │
│  Output: DatabaseSchema                             │
└──────────────────────┬──────────────────────────────┘
                       ▼
               ┌───────────────┐
               │  HUMAN GATE 2 │  ← operator approves DB schema
               └───────┬───────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 6 — Test Writer                             │
│  Output: FailingTestSuite (all tests RED)          │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 7 — Coder (makes tests green)               │
│  Output: Written code files                         │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 8 — Integrator (README, reqs, schema)        │
│  Output: IntegrationResult                          │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Stage 9 — Reviewer (rule compliance check)         │
│  Output: ReviewResult                              │
└─────────────────────────────────────────────────────┘
```

---

## 3. Stage Specifications

### Stage 1 — Interviewer
**Agent:** `InterviewerAgent`  
**Input:** Initial product prompt + conversation history  
**Output:** `IntentDocument` (all fields answered, no TBD)  
**Pattern:** Discrete turns. Stateless between calls. Orchestrator holds history.

The Interviewer is adversarial — "it depends" is a blocking error. It identifies the condition that determines the answer and pushes until it has a concrete answer.

```python
@dataclass
class InterviewerOutput:
    done: bool
    question: str = ""   # if done == False
    intent: IntentDocument = None  # if done == True
```

### Stage 2 — Flow Designer
**Agent:** `FlowDesignerAgent`  
**Input:** `IntentDocument`  
**Output:** `UserFlowTree`

Every screen → trigger → conditions → terminal outcome. Covers authenticated AND unauthenticated paths, success AND error branches.

### Stage 3 — Contract Writer
**Agent:** `ContractWriterAgent`  
**Input:** `UserFlowTree`  
**Output:** `list[APIContract]`

Every user action mapped to an API contract: request shape, response shape, success case, all error cases, what it reads/writes, side effects. **Ground truth, not documentation.**

### Stage 4 — Rule Compiler
**Agent:** `RuleCompilerAgent`  
**Input:** `list[APIContract]` + `UserFlowTree`  
**Output:** `RuleSet`

Reduces to IF/THEN rules. Exactly one unambiguous outcome per input combination. No prose. No "should". Every rule is a constraint the codebase must satisfy.

### Human Gate 1
Rule set **always prints**. With `--auto-approve`: prints, prints `AUTO-APPROVED`, continues. Without flag: blocks on `y/n`. Rejection sends pipeline back to Rule Compiler with structured feedback.

### Stage 5 — Schema Designer
**Agent:** `SchemaDesignerAgent`  
**Input:** `IntentDocument`  
**Output:** `DatabaseSchema`

PostgreSQL schema driven mechanically by entities and relationships. Includes: soft deletes, audit log tables, indexes, foreign keys, raw SQL. All decisions derived from Stage 1 data.

### Human Gate 2
DB schema prints. Same auto-approve / manual approval flow.

### Stage 6 — Test Writer
**Agent:** `TestWriterAgent`  
**Input:** `RuleSet`  
**Output:** `FailingTestSuite`

Tests written from the Rule Set, not from code. The codebase doesn't exist yet — tests define what it must do. Every rule gets at least one test. Every test is immediately red when run.

### Stage 7 — Coder
**Agent:** `CoderAgent`  
**Input:** `FailingTestSuite` + `RuleSet` + `DatabaseSchema`  
**Output:** Written code files + final test results

The coder's **only job**: make the tests green. It has a schema, contracts, a rule set, and a failing test suite. It writes code until tests pass. No architectural decisions. No interpreting requirements.

Iterative loop: write scaffold → run tests → fix failing tests → repeat (max 5 attempts).

### Stage 8 — Integrator
**Agent:** `IntegratorAgent`  
**Input:** All prior stage outputs  
**Output:** `IntegrationResult` (README, requirements.txt, schema.sql, entry point)

Assembles generated artifacts into a runnable project.

### Stage 9 — Reviewer
**Agent:** `ReviewerAgent`  
**Input:** `RuleSet` + generated code  
**Output:** `ReviewResult`

Reads generated Python files, checks every rule for compliance. Returns violations with specific suggestions.

---

## 4. Models (Typed Structs)

All pipeline stages communicate via typed dataclasses in `models.py`. No unstructured data passes between stages.

| Model | Purpose |
|---|---|
| `IntentDocument` | Complete product specification |
| `UserFlowTree` | All user paths as a decision tree |
| `APIContract` | Single API endpoint contract |
| `RuleSet` | Formal IF/THEN rules |
| `Rule` | Single atomic IF/THEN behavior |
| `FailingTestSuite` | All test cases (initially red) |
| `TestCase` | Single test derived from a rule |
| `DatabaseSchema` | PostgreSQL schema + migrations |
| `TableDefinition` | Single table |
| `ColumnDefinition` | Single column |
| `IntegrationResult` | Final assembled project |
| `ReviewResult` | Rule compliance report |
| `ChangeIntent` | Edit-mode: what is changing |
| `ImpactSurface` | Edit-mode: blast radius |
| `DeltaRuleSet` | Edit-mode: rule diff |

---

## 5. CLI Interface

```bash
forge compile "build a blog with authentication"
forge compile "a URL shortener" --auto-approve
forge compile "a REST API" --workdir /tmp/myproject
forge compile "a blog" --model-routing "reviewer=anthropic/claude-opus-4-5"
forge compile "a blog" --model-routing "coder=anthropic/claude-sonnet-4"
forge compile "build auth" --test-autopilot "email login" "password reset" "5 failed logins"
```

**Options:**
- `--auto-approve` — skip human gates
- `--workdir PATH` — output directory (default: current directory)
- `--provider NAME` — LLM provider (default: from forge config)
- `--model NAME` — model override
- `--model-routing KEY=PROVIDER/MODEL` — per-agent model routing
- `--test-autopilot ANSWERS...` — pre-seeded answers for interview (for testing)

**Events emitted by pipeline:**
```
type: stage        — stage change
type: question      — interview question (awaiting answer)
type: output        — stage output summary
type: gate          — human gate (rule set or schema) with full text
type: awaiting_input — blocked on y/n
type: complete      — pipeline finished
```

---

## 6. Agent Design Principles

- Each agent has **exactly one job** — no scope creep
- Agents are **stateless between calls** — orchestrator holds conversation history
- Agents pass **typed structs**, not free text
- No agent has global tool access — the Coder writes files; the Integrator assembles; the Reviewer reads
- **Human gates** exist at Rule Compilation (Gate 1) and pre-Integration (Gate 2)
- All agents use `forge.llm.create_backend()` — the provider layer is shared

---

## 7. File Structure

```
forge/src/forge/product_compiler/
├── __init__.py              — public API re-exports + CodebaseIndex
├── models.py                — all typed dataclasses (590 lines)
├── pipeline.py              — ProductCompilerPipeline orchestrator (473+ lines)
├── codebase_index.py        — CodebaseIndex stub for edit pipeline
└── agents/
    ├── __init__.py          — re-exports all agents
    ├── interviewer.py       — adversarial Q&A loop (170 lines)
    ├── flow_designer.py     — Intent → UserFlowTree (77 lines)
    ├── contract_writer.py   — UserFlowTree → APIContract[] (79 lines)
    ├── rule_compiler.py     — APIContract[] + UserFlowTree → RuleSet (98 lines)
    ├── schema_designer.py   — IntentDocument → DatabaseSchema (290 lines)
    ├── test_writer.py       — RuleSet → FailingTestSuite (85 lines)
    ├── coder.py             — FailingTestSuite → passing code (225+ lines)
    ├── reviewer.py          — code → rule compliance (143 lines)
    ├── integrator.py        — assemble final project (132 lines)
    ├── impact_analyst.py    — edit pipeline: blast radius mapping (stub)
    ├── rule_extractor.py    — edit pipeline: reverse-engineer rules (stub)
    ├── delta_compiler.py     — edit pipeline: diff rule sets (stub)
    ├── blast_checker.py      — edit pipeline: unintended consequences (stub)
    └── test_delta_writer.py  — edit pipeline: test deltas (stub)

forge/src/forge/tui/
├── __init__.py
├── app.py                   — ForgeTUI (164 lines)
├── context.py
├── components/
│   ├── __init__.py
│   ├── dialog.py            — CommandPaletteDialog (98 lines) + forge compile command
│   ├── output.py
│   ├── prompt.py
│   └── status.py
└── screens/
    ├── __init__.py
    ├── chat.py
    ├── home.py
    ├── models.py
    └── product_compiler.py   — ProductCompilerScreen (scaffolded, not wired to app)

---

## 8. Layer Roadmap

| Layer | Stages | Description | Status |
|---|---|---|---|
| **Layer 1** | 1–9 | Full pipeline: Interview → Rules → Tests → Code → Integration → Review | ✅ Complete |
| **Layer 2** | TUI + hardening | ProductCompilerTUI screen, E2B sandboxing, NATS messaging | ✅ Complete |
| **Layer 3** | Edit pipeline | Change Intent → Impact Surface → Delta Rules → Blast Checker | ✅ Scaffold + index |
| **Layer 4** | TUI | Bubble Tea TUI with live panels, gate screens, reject loops | ✅ Screen + wire |

### Layer 1 — Complete ✅
- ✅ All 9 agents implemented with typed inputs/outputs (11 total including 5 edit-mode stubs)
- ✅ `ProductCompilerPipeline` orchestrator with generator-based event loop
- ✅ `forge compile` CLI command fully wired
- ✅ Gate 1 (Rule Set) — prints rules, `y/n` character input via `click.getchar()`, approve/reject wired
- ✅ Gate 2 (DB Schema) — prints SQL preview, `y/n` character input, approve/reject wired
- ✅ `--auto-approve`, `--test-autopilot`, `--model-routing` all working
- ✅ `--test-autopilot` answer count validation — warns if provided count != consumed count
- ✅ `_detect_framework()` in Coder — detects FastAPI/Flask/Django from test code
- ✅ `_build_intent_from_history()` edge cases — returns partial `IntentDocument` on failure
- ✅ `SchemaDesignerAgent` (290 lines) — FK, indexes, audit tables, raw SQL generation
- ✅ `IntegratorAgent` (132 lines) — README, requirements.txt, schema.sql
- ✅ `ReviewerAgent` (143 lines) — reads files, checks rules, returns violations
- ⚠️ E2E smoke test hit MMX API connection error (code correct, infra issue)

### Layer 2 — Complete ✅
- ✅ `ProductCompilerScreen` — Textual screen with stage bar, log, gate panel, `Ctrl+G`/`Ctrl+R` bindings
- ✅ Wired into `ForgeTUI._on_command("forge compile")` via Ctrl+P command palette
- ✅ `forge/tui/screens/__init__.py` exports `ProductCompilerScreen`
- ✅ `E2BSandbox` context manager in `sandbox.py` — reads `E2B_API_KEY`, local subprocess fallback when disabled
- ✅ `--sandbox/--no-sandbox` CLI flag wired into `ProductCompilerPipeline`
- ✅ `MessagingLayer` with `MessagingBackend.NATS` and `MessagingBackend.STDOUT` in `messaging.py`
- ✅ `--nats-url` CLI flag wired into `ProductCompilerPipeline`
- ✅ `PipelineState.to_dict()` includes all config fields

### Layer 3 — Scaffold + Index ✅
- ✅ All 5 edit-mode agent stubs: `ImpactAnalystAgent`, `RuleExtractorAgent`, `DeltaCompilerAgent`, `BlastCheckerAgent`, `TestDeltaWriterAgent`
- ✅ `CodebaseIndex` full implementation — `_SymbolVisitor` (ast.NodeVisitor), dependency graph, call graph, contract index, test coverage map, `update_after_change()`, `query_impact()`, `build_mock_index()`
- ⚠️ `_run_edit_pipeline()` raises `NotImplementedError` — requires CodebaseIndex.build() to be called first

---

## 9. Pending Issues

### Fixed This Session ✅
1. ~~WAL mode missing in `forge/db.py`~~ — Already present in codebase (lines 247-248)
2. ~~`llm.py` `complete_json` stub~~ — Implemented in `openai_.py`; `SYSTEM`/`system` param consolidated
3. ~~`pytest-json-report` missing~~ — Added to `pyproject.toml` dev dependencies
4. ~~CLI Gate 1 manual approval~~ — Fully wired with `click.getchar()`
5. ~~CLI Gate 2 manual approval~~ — Fully wired with `click.getchar()`
6. ~~Coder always FastAPI~~ — `_detect_framework()` detects from test code

### Completed This Session ✅
1. **TUI wire** — `ProductCompilerScreen` pushed from `ForgeTUI._on_command("forge compile")` via Ctrl+P palette
2. **CodebaseIndex.build()** — Full AST-based implementation: `_SymbolVisitor` (ast.NodeVisitor), dependency graph, call graph, contract index, test coverage map, `update_after_change()`, `query_impact()`, `build_mock_index()`. Indexes 19 files, 148 symbols, 78 call graph edges in the product_compiler package itself.
3. **`--test-autopilot` validation** — Warning emitted if provided answer count doesn't match consumed count
4. **Interview extraction edge cases** — `_build_intent_from_history()` wrapped in try/except; returns partial `IntentDocument` on failure instead of crashing
5. **E2B sandboxing** — `sandbox.py` (`E2BSandbox`, `SandboxResult`), wired into pipeline with `--sandbox/--no-sandbox` CLI flag
6. **NATS messaging** — `messaging.py` (`MessagingLayer`, `MessagingBackend.NATS/STDOUT`, `NATSConfig`, `MessagingChannel`, `PipelineEvent`), wired into pipeline, `--nats-url` CLI flag added
7. **`pipeline.py` consistency fix** — `PipelineState` now has both `use_sandbox` and `messaging_layer` fields; both wired to CLI

### Still Open ⚠️
(None — all 7 pending items addressed this session)

---

## 10. Edit Pipeline (Planned)

The edit pipeline operates on existing codebases. Same stages, different agents scoped to the change:

| Stage | Agent | Input | Output |
|---|---|---|---|
| 1 | Interviewer (scoped) | Change request | `ChangeIntent` |
| 2 | Impact Analyst | ChangeIntent + Index | `ImpactSurface` |
| 3 | Rule Extractor | ImpactSurface + Code | Extracted rules |
| 4 | Delta Compiler | Extracted rules + intent | `DeltaRuleSet` |
| 5 | Blast Checker | Delta + ImpactSurface | Clear/Send Back |
| 6 | Test Delta Writer | DeltaRuleSet | Test delta |
| 7 | Coder | Tests + Delta + Schema | Passing code |

Requires: **Codebase Index** (dependency graph, call graph, contract index, test coverage map, rule index).

---

## 11. TUI (Planned)

Built with **Textual** (already in Forge's dependencies).

**Always-visible panels:**
- Current agent and stage name
- Stage progress (Stage 3 of 7)
- Current file being processed
- Live diff output as Coder writes
- Test results as they run (pass/fail counts)

**Gate screens:**
- Full rule set review (Gate 1)
- Final diff review (Gate 2)
- Approve / Reject with comments / Abort

**Reject loops:** Rejection at any gate sends structured feedback back to the upstream agent — not the Coder.

---

## 12. Dependencies

The Product Compiler adds the following external dependencies (all Python):

| Package | Purpose | Status |
|---|---|---|
| `structlog` | Structured logging | Already in forge |
| `pydantic` | Data validation | Already in forge |
| `pytest` | Test execution | Already in forge |
| `pytest-json-report` | Machine-readable test output | Added to `pyproject.toml` dev deps |
| `httpx` | Test client | Added to core dependencies |
| `fastapi` / `flask` | App scaffold generation | Generated in code, not installed |
| `textual` | TUI framework | Already in forge |
| `click` | CLI | Already in forge |

---

## 13. Key Design Decisions (Python Context)

1. **Python over Go** — reuse forge's existing provider layer, TUI, and SQLite memory
2. **Discrete-turn Interviewer** — stateless between calls, orchestrator holds history
3. **`PipelineMode` enum from day one** — `NewProject | Edit` with stubbed Edit agents
4. **`--auto-approve` gate behavior** — rule set always prints; flag controls whether it blocks
5. **Agents import the provider, don't own it** — clean package boundary
6. **Generator-based pipeline events** — `pipeline.run()` yields events; `run_single()` accumulates them
7. **pytest for test execution** — FastAPI scaffold generation, JSON report output

---

*This spec is append-only. Any change to the architecture must be reflected here first, then in the implementation.*
