# forge — AI Coding Agent Framework

> Two modes, one CLI. **Forge new** → directed graph orchestration. **Forge compile** → intent-to-production pipeline with formal verification, mutation testing, and runtime profiling.

---

## Install

```bash
cd ~/forge
pip install -e .
forge --version
forge setup  # interactive: pick provider, enter API key, test
forge setup --check  # non-interactive: show config + test connection
```

---

## Two Modes

| Command | What it does |
|---|---|
| `forge new "build a Stripe billing app"` | Directed graph: orchestrator → spec → executor → review gate |
| `forge compile "build a URL shortener"` | Pipeline: interview → rules → failing tests → code → review → integration |
| `forge compile --edit "rename get_user"` | Edit pipeline: blast radius → extract rules → delta → test delta |

Both modes share the same LLM backend, TUI, skills system, and memory model.

---

## forge new — Directed Graph Orchestration

```
User Prompt
    ↓
Orchestrator       ← decides: new or continue, handles failures/escalation
    ↓
Spec Generator     ← asks clarifying Qs, writes SPEC.md (git-tracked, append-only)
    ↓
Executor           ← TDD: tests first, then implementation, against SPEC.md
    ↓
Review Gate        ← checkpoint: pass → done, fail → retry executor
    ↓
Orchestrator       ← done or escalate to user
```

### Memory Model (4-tier, per-project SQLite)

| Tier | Scope | Duration | Storage |
|---|---|---|---|
| `short` | Current task | Session | In-memory dict |
| `mid` | Current project | Days | `~/.forge/projects/<id>/memory.db` |
| `episodic` | Across sessions | Weeks | Same DB |
| `long` | Cross-project | Months | `~/.forge/memory/` |

Every spec write is a **new version row** (never overwrite). `forge continue` loads the latest and resumes from orchestrator state.

### Failure Lanes (explicit)

Every node can emit:
- `done` → follow normal edge
- `blocked` → review_gate emits `review_fail` → executor retries with reason
- `failed` → orchestrator decides: `retry` / `escalate` / `block`

---

## forge compile — Intent to Production Pipeline

Describe what you want in plain English. Forge interviews you, writes failing tests from formal rules, writes the code to pass them, runs 10 parallel static analysis gates, and integrates everything into a runnable project.

```
Intent (plain English)
    ↓
Stage 1: Interviewer         adversarial Q&A — extracts entities, relationships, rules
    ↓
Stage 2: Flow Designer        → UserFlowTree (screen-by-screen flows)
    ↓
Stage 3: Contract Writer      → APIContract[] (request/response/error for every action)
    ↓
Stage 4: Rule Compiler        → RuleSet (IF/THEN constraints, no prose)
    ↓
Gate 1: Human Review          y/n — reject sends feedback back to Rule Compiler
    ↓
Stage 5: Schema Designer      → DatabaseSchema (PostgreSQL — tables, FKs, indexes, audit columns)
    ↓
Stage 6: Test Writer          → FailingTestSuite (one test per rule) + Hypothesis strategies
    ↓
Stage 7: Mutation Testing     ← mutmut — hard gate if score < 70%
    ↓
Stage 8: Coder                → passing code (pyupgrade + pyright + crosshair in TDD loop)
    ↓
Stage 9: Integrator           → requirements.txt + supply chain scan + SBOM
    ↓
Stage 10: Reviewer            → 10 parallel gates: bandit, semgrep, radon, vulture, griffe,
    │                            deptry, pyright, mutmut, pip-audit, crosshair
    ↓
Gate 2: Human Review          y/n — reject sends feedback back to Coder
    ↓
Stage 11: Profiling           ← memray (memory) + pyspy (CPU) — advisory feedback
    ↓
Done: project_dir/
```

### The 10 Hard Gates

Every gate blocks the pipeline. No LLM synthesis happens until all gates pass.

| # | Gate | Tool | Threshold |
|---|---|---|---|
| 1 | Security vulnerabilities | `bandit` | HIGH/CRITICAL → BLOCK |
| 2 | Hardcoded secrets | `semgrep` (p/secrets) | HIGH → BLOCK |
| 3 | Complexity | `radon` | CC > 10 → BLOCK |
| 4 | Dead code | `vulture` | ≥80% confidence → BLOCK |
| 5 | Contract drift | `griffe` | Any drift → BLOCK |
| 6 | Missing dependencies | `deptry` (DEP001) | Missing dep → BLOCK |
| 7 | Type errors | `pyright` | Any error → BLOCK |
| 8 | Test quality | `mutmut` | Score < 70% → BLOCK |
| 9 | Supply chain CVEs | `pip-audit` | HIGH/CRITICAL → BLOCK |
| 10 | Contract violations | `crosshair` | Any violation → BLOCK |

### CLI

```bash
forge compile "build a URL shortener with analytics"
forge compile "a REST API" --auto-approve
forge compile "a blog" --test-prompt "posts" --test-prompt "auth"
forge compile "a service" --sandbox              # E2B sandbox isolation
forge compile "a service" --nats-url nats://localhost:4222
forge compile "a blog" --workdir ./myproject
```

---

## Edit Pipeline — Change Existing Code

```bash
forge compile --edit "add OAuth to the API" --workdir ./myproject
forge compile --edit "rename get_user to fetch_user" --workdir ./myproject
forge compile --edit "change beta user discount from 20% to 30%" --workdir ./myproject
```

```
Stage 1: ChangeIntent          ← what is changing, what must not
    ↓
Stage 2: ImpactAnalyst        ← jedi blast-radius: all callers of changed function
    ↓
Stage 3: RuleExtractor        ← extract IF/THEN rules from source by observation
    ↓
Stage 4: DeltaCompiler        ← PRESERVE / CHANGE / NEW / REMOVE per rule
    ↓
Stage 5: BlastChecker         ← verify delta stays within declared blast radius
    ↓
Stage 6: TestDeltaWriter      ← failing tests for CHANGE rules, regression for PRESERVE
    ↓
    Coder (edit-mode TDD loop)
```

The BlastChecker raises `BlastCheckError` if the delta touches files outside the declared impact surface. The user must approve an expanded scope or narrow the change.

---

## Agents

| Agent | Stage | Input | Output |
|---|---|---|---|
| `InterviewerAgent` | 1 | Plain intent | `IntentDocument` |
| `FlowDesignerAgent` | 2 | `IntentDocument` | `UserFlowTree` |
| `ContractWriterAgent` | 3 | `UserFlowTree` | `list[APIContract]` |
| `RuleCompilerAgent` | 4 | `list[APIContract]` + `UserFlowTree` | `RuleSet` |
| `SchemaDesignerAgent` | 5 | `IntentDocument` | `DatabaseSchema` |
| `TestWriterAgent` | 6 | `RuleSet` | `FailingTestSuite` + Hypothesis strategies |
| `CoderAgent` | 8 | `FailingTestSuite` + `RuleSet` | Code files + passing tests |
| `IntegratorAgent` | 9 | All of the above | `requirements.txt` + SBOM + supply chain |
| `ReviewerAgent` | 10 | `RuleSet` + project files | `ReviewResult` (violations + LLM synthesis) |
| `ImpactAnalystAgent` | 2 (edit) | `ChangeIntent` + codebase | `ImpactSurface` (jedi blast-radius) |
| `RuleExtractorAgent` | 3 (edit) | `ImpactSurface` + source | `list[Rule]` (PRESERVE) |
| `DeltaCompilerAgent` | 4 (edit) | `list[Rule]` + `ChangeIntent` | `DeltaRuleSet` |
| `BlastCheckerAgent` | 5 (edit) | `DeltaRuleSet` + `ImpactSurface` | bool (raises `BlastCheckError`) |
| `TestDeltaWriterAgent` | 6 (edit) | `DeltaRuleSet` | `FailingTestSuite` |

---

## Code Intelligence (20 tools)

Formal verification, semantic analysis, runtime intelligence, mutation testing, supply chain, and quality gates — all wired as structured tool integrations.

| Tool | Role | Mode |
|---|---|---|
| `hypothesis_` | Property-based test generation | Advisory — enriches test coverage |
| `mutmut_` | Mutation testing | **Hard gate** — score ≥ 70% |
| `pip_audit_` | CVE scanning | **Hard gate** — no HIGH/CRITICAL CVEs |
| `cyclonedx_` | SBOM generation | Artifact — CycloneDX JSON per build |
| `jedi_` | Semantic callers/inference | Blast-radius analysis |
| `rope_` | Semantic refactoring | Safe renames/moves |
| `crosshair_` | Static contract proving | **Hard gate** — no violations |
| `otel_` | OpenTelemetry tracing | Traces on test failure |
| `memray_` | Memory profiling | Advisory feedback |
| `pyspy_` | CPU sampling | Advisory feedback |
| `bandit_` | Security analysis | **Hard gate** |
| `radon_` | Complexity analysis | **Hard gate** CC > 10 |
| `vulture_` | Dead code detection | **Hard gate** ≥80% confidence |
| `griffe_` | API contract drift | **Hard gate** |
| `deptry_` | Dependency analysis | Missing dep = **hard gate** |
| `semgrep_` | Secrets + security rules | **Hard gate** (p/secrets) |
| `pyupgrade_` | Python version upgrade | Auto-fix — no gate |
| `pyright_` | Type checking | **Hard gate** — advisory (not yet wired) |
| `ctags` | Symbol index | Navigation |
| `atlas` | Schema migration | Migration files |

---

## Skills

Skills are reusable knowledge stored in `~/.forge/skills/` and `~/.hermes/skills/`. They teach agents *how to use* the tools — thresholds, patterns, error interpretation. They are loaded on demand per agent role.

```bash
forge --list-skills
```

### Agent Role Skills

| Skill | Teaches |
|---|---|
| `reviewer` | 10-gate hierarchy, evidence formatting, blocking failure strings |
| `test-writer` | Property-based vs example-based thinking, mutmut loop, Hypothesis patterns |
| `coder` | TDD discipline, minimal fix, pyupgrade + crosshair in TDD loop |

### Quality Gate Skills

| Skill | Teaches |
|---|---|
| `bandit` | B301/B303/B310/B608 patterns, fix templates, rejection strings |
| `radon` | CC sources (nested conditionals, boolean expressions), refactor patterns |
| `vulture` | 80% confidence rule, speculative code detection |
| `pyright` | TypedDict/Generic/Protocol patterns, type annotation discipline |
| `mutmut` | Survivor interpretation, test-to-kill mapping, threshold philosophy |
| `crosshair` | PEP 316 contract syntax, counterexample reading |
| `hypothesis` | Strategy map, invariant testing, `@given`/`@settings` patterns |
| `pip-audit` | CVE severity mapping, auto-fix strategy |
| `griffe` | Drift detection, contract comparison logic |

### Semantic Skills

| Skill | Teaches |
|---|---|
| `jedi` | find_callers/get_inference vs grep, reference fields, limitations |
| `rope` | rename/extract/inline vs string-replace, safe refactor pattern |

### Stack Skills

| Skill | Teaches |
|---|---|
| `fastapi` | Project structure, route patterns, Pydantic v2, dependency injection |
| `postgres` | Soft deletes, audit logging, JSONB, multi-tenancy, indexing |

---

## TUI

Launch with `forge tui` — full-screen Textual interface.

```
┌─────────────────────────────────────────────┐
│  Home                                       │
│                                             │
│  Recent Sessions                            │
│  > forge new "build a Stripe billing app"   │
│  > forge compile "a URL shortener"           │
│                                             │
│  [Ctrl+A] Model   [Ctrl+P] Commands         │
└─────────────────────────────────────────────┘
```

- **Ctrl+A** — Model picker (20+ models across 10 providers)
- **Ctrl+P** — Command palette (fuzzy search)
- **Ctrl+G / Ctrl+R** — Gate approve/reject in compile mode
- **Ctrl+C** — Cancel running task
- **Escape** — Return home

---

## LLM Providers

| Provider | SDK | Streaming | Tools |
|---|---|---|---|
| OpenAI | `openai` | ✓ | ✓ |
| Anthropic | `anthropic` | ✓ | ✓ |
| MiniMax (OpenAI compat) | `openai` + HTTP | ✓ | ✓ |
| DeepSeek | `openai` + `base_url` | ✓ | ✓ |
| Qwen | `openai` + `base_url` | ✓ | ✓ |
| Groq | `openai` + `base_url` | ✓ | ✓ |
| Ollama | `openai` + localhost | ✓ | ✓ |

Config `~/.forge/config.yaml`:

```yaml
provider: minimax_openai
model: default
api_key: ...
base_url: ...   # optional, for proxy/custom endpoints
```

Runtime override:
```bash
forge new "my idea" --provider deepseek --model deepseek-chat
forge compile "API" --model-routing "coder=anthropic/claude-sonnet-4"
```

---

## Run Tests

```bash
cd ~/forge
pytest tests/ -v
```

---

## Project Status

| Version | Highlights |
|---|---|
| **v0.8 ✅** | 10 hard gates, 20 code intelligence tools, edit pipeline (6 stages), 17 skills, jedi/rope semantic layer, mutation testing, supply chain SBOM, OpenTelemetry tracing |
| **v0.7 ✅** | Product Compiler: 9-agent pipeline, 2 human gates, E2B sandbox, NATS messaging, full TUI wire |
| **v0.6 ✅** | SDK migration: `openai` + `anthropic` Python SDKs, Textual TUI, model picker, command palette |
| **v0.5 ✅** | Surgical patch editing, git-aware FileService, LSP integration, exponential backoff |
| **v0.4 ✅** | Subagent delegation, MCP session management, structured pytest failure parsing |
| **v0.3 ✅** | Multi-session state restore, TDD-first executor, skill auto-loading |
