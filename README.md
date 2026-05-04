# forge — AI Coding Agent Framework

> Two modes, one CLI. **Forge new** → directed graph orchestration. **Forge compile** → intent-to-production pipeline.

## Two Modes

| Command | What it does |
|---|---|
| `forge new "build a Stripe billing app"` | Directed graph: orchestrator → spec → executor → review gate → done |
| `forge compile "build a URL shortener"` | Pipeline: interview → rules → failing tests → passing code → integration |

Both modes share the same LLM backend, TUI, skills system, and memory model.

---

## forge new — Directed Graph Orchestration

Vague prompt → structured spec → production code. The original Forge workflow.

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

Describe what you want in plain English. Forge interviews you, writes failing tests, writes the code to pass them — then integrates everything into a runnable project.

```
Intent (plain English)
    ↓
Stage 1: Interviewer       adversarial Q&A — extracts entities, relationships, rules
    ↓
Stage 2: Flow Designer     → UserFlowTree (screen-by-screen)
    ↓
Stage 3: Contract Writer   → APIContract[] (request/response/error for every action)
    ↓
Stage 4: Rule Compiler     → RuleSet (IF/THEN constraints, no prose)
    ↓
🚧 Gate 1: Human Review   y/n — reject sends feedback back to Rule Compiler
    ↓
Stage 5: Schema Designer   → DatabaseSchema (tables, FKs, indexes, audit columns)
    ↓
Stage 6: Test Writer       → FailingTestSuite (one test per rule)
    ↓
Stage 7: Coder             → passing code (framework detected from tests)
    ↓
Stage 8: Integrator        → project skeleton (README, requirements.txt, schema.sql)
    ↓
🚧 Gate 2: Human Review   y/n — reject sends feedback back to Coder
    ↓
Stage 9: Reviewer          → compliance report (rules vs. actual files)
    ↓
Done: project_dir/
```

### CLI

```bash
forge compile "build a URL shortener with analytics"
forge compile "a REST API" --auto-approve
forge compile "a blog" --test-prompt "posts" --test-prompt "auth" --test-prompt "admin"
forge compile "a service" --sandbox              # E2B sandbox isolation
forge compile "a service" --nats-url nats://localhost:4222  # NATS event stream
forge compile "a blog" --workdir ./myproject
```

### Agents

| Agent | Input | Output |
|---|---|---|
| `InterviewerAgent` | Plain intent | `IntentDocument` with entities, relationships, actions |
| `FlowDesignerAgent` | `IntentDocument` | `UserFlowTree` |
| `ContractWriterAgent` | `UserFlowTree` | `list[APIContract]` |
| `RuleCompilerAgent` | `list[APIContract]` + `UserFlowTree` | `RuleSet` |
| `SchemaDesignerAgent` | `IntentDocument` | `DatabaseSchema` |
| `TestWriterAgent` | `RuleSet` | `FailingTestSuite` |
| `CoderAgent` | `FailingTestSuite` + `RuleSet` | Code files + test results |
| `ReviewerAgent` | `RuleSet` + project files | `ReviewResult` (violations) |
| `IntegratorAgent` | All of the above | Project skeleton |

### Edit Pipeline (change existing code)

```bash
forge compile --edit "add OAuth to the API" --workdir ./myproject
```

Same pipeline but scoped to the change surface. Requires a `CodebaseIndex` (AST-based dependency + call graph + contract index) to determine blast radius.

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
- **Ctrl+P** — Command palette (fuzzy search: new, compile, continue, plan, setup…)
- **Ctrl+C** — Cancel running task
- **Escape** — Return home

Chat streams tokens inline with rendered markdown. Product Compiler mode shows a live stage bar, interview Q&A log, and gate approval panel (Ctrl+G / Ctrl+R).

---

## LLM Providers

Forge uses official Python SDKs where available, OpenAI-compatible HTTP for the rest.

| Provider | SDK | Streaming | Tools |
|---|---|---|---|
| OpenAI | `openai` | ✓ | ✓ |
| Anthropic | `anthropic` | ✓ | ✓ |
| MiniMax (OpenAI compat) | `openai` + HTTP | ✓ | ✓ |
| DeepSeek | `openai` + `base_url` | ✓ | ✓ |
| Qwen | `openai` + `base_url` | ✓ | ✓ |
| Kimi | `openai` + `base_url` | ✓ | ✓ |
| GLM | `openai` + `base_url` | ✓ | ✓ |
| Groq | `openai` + `base_url` | ✓ | ✓ |
| Ollama | `openai` + localhost | ✓ | ✓ |
| MiniMax CLI | CLI wrapper | ✗ | ✗ |

Config `~/.forge/config.yaml`:

```yaml
provider: minimax_openai
model: default
api_key: ...
base_url: ...      # optional, for proxy/custom endpoints
```

Runtime override:
```bash
forge new "my idea" --provider deepseek --model deepseek-chat
forge compile "API" --model-routing "coder=anthropic/claude-sonnet-4"
```

---

## Skills & MCP

Skills are reusable prompts stored in `~/.forge/skills/` and `~/.hermes/skills/` (shared). MCP servers are configured in `~/.forge/mcp_servers.yaml`. Both are discovered and loaded on demand, not at startup.

```bash
forge --list-skills     # show available skills
forge skills install <name>  # install from registry
```

---

## Install

```bash
cd ~/forge
pip install -e .
forge --version
forge setup             # interactive: pick provider, enter API key, test
forge setup --check     # non-interactive: show config + test connection
```

## Run Tests

```bash
cd ~/forge
pytest tests/ -v
```

---

## Project Status

| Version | Highlights |
|---|---|
| **v0.7 ✅** | Product Compiler: 9-agent pipeline, 2 human gates, E2B sandbox, NATS messaging, full TUI wire, CodebaseIndex (AST-based), Edit pipeline stubs |
| **v0.6 ✅** | SDK migration: `openai` + `anthropic` Python SDKs for all providers, `LLMResponse` dataclass, `complete_streaming()`, Textual TUI with chat, model picker, command palette |
| **v0.5 ✅** | Surgical patch editing, git-aware FileService, LSP integration (pyright/tsserver), exponential backoff + Retry-After, context compaction, per-agent permissions |
| **v0.4 ✅** | Subagent delegation, MCP session management, structured pytest failure parsing, orphan cleanup |
| **v0.3 ✅** | Multi-session state restore, TDD-first executor, skill auto-loading |
