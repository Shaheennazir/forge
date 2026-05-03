# forge — Production-Ready Multi-Agent CLI

> Vague prompt → spec → production code. Directed graph orchestration with 4-tier memory and full streaming TUI.

## Architecture

```
User Prompt
    ↓
Orchestrator       ← decides: new or continue, handles failures/escalation
    ↓
Spec Generator     ← asks clarifying Qs, writes SPEC.md (git-tracked, append-only changelog)
    ↓
Executor           ← TDD: tests first, then implementation, against SPEC.md
    ↓
Review Gate        ← synchronous checkpoint: pass → done, fail → retry executor
    ↓
Orchestrator       ← done or escalate to user
```

## Memory Model (4-tier, per-project SQLite)

| Tier | Scope | Duration | Storage |
|------|-------|----------|---------|
| `short` | Current task | Session | In-memory dict |
| `mid` | Current project | Days | `~/.forge/projects/<id>/memory.db` |
| `episodic` | Across sessions | Weeks | Same DB |
| `long` | Cross-project | Months | `~/.forge/memory/` |

## Spec Versioning

- Every spec write is a **new version row** (never overwrite)
- `changelog` column tracks delta per version
- SPEC.md is **git-tracked** inside the project directory
- `forge continue` loads latest spec and resumes from last orchestrator state

## Failure Lanes (explicit)

Every node can emit:
- `done` → follow normal edge
- `blocked` → review_gate emits `review_fail` → executor retries with reason
- `failed` → orchestrator receives, decides: `retry` / `escalate` / `block`

## TUI

Launch with `forge tui` — full-screen Textual interface:

```
┌─────────────────────────────────────────────┐
│  Home                                       │
│                                             │
│  Recent Sessions                            │
│  > forge new "build a Stripe billing app"   │
│  > forge new "CLI tool for todos"           │
│                                             │
│  [Ctrl+A] Model   [Ctrl+P] Commands         │
└─────────────────────────────────────────────┘
```

- `Ctrl+A` — Model picker (20+ models across 9 providers)
- `Ctrl+P` — Command palette (fuzzy search: new, continue, plan, agents, tui, setup, ls, models)
- `Ctrl+C` — Cancel running task
- `Escape` — Return home

Chat screen streams tokens inline with rendered markdown:

```
┌─────────────────────────────────────────────┐
│  Chat                                       │
│                                             │
│  You: build a Stripe-powered billing app    │
│                                             │
│  Forge: Analyzing...                        │
│  Forge: Creating SPEC.md...                 │
│  Forge: Running tests...                     │
│                                             │
│  [Escape] Home                              │
└─────────────────────────────────────────────┘
```

## CLI Commands

```bash
forge new "build a Stripe-powered SaaS billing app"   # vague → spec → build
forge continue "add usage-based billing"              # resume from episodic state
forge plan "refactor auth layer"                      # spec only, no execution
forge status --project myapp                           # task graph + spec version
forge memory inspect --project myapp --tier episodic   # query memory
forge tui                                               # launch full-screen TUI
forge setup                                             # diagnose / configure providers
```

## LLM Providers

Forge uses the official `openai` and `anthropic` Python SDKs directly:

| Provider | SDK | Streaming | Tools |
|----------|-----|-----------|-------|
| OpenAI (`openai`) | `openai` | ✓ | ✓ |
| Anthropic (`anthropic`) | `anthropic` | ✓ | ✓ |
| MiniMax — OpenAI compat (`minimax_openai`) | `openai` + HTTP API | ✓ | ✓ |
| DeepSeek (`deepseek`) | `openai` + `base_url` | ✓ | ✓ |
| Qwen (`qwen`) | `openai` + `base_url` | ✓ | ✓ |
| Kimi (`kimi`) | `openai` + `base_url` | ✓ | ✓ |
| GLM (`glm`) | `openai` + `base_url` | ✓ | ✓ |
| Groq (`groq`) | `openai` + `base_url` | ✓ | ✓ |
| Ollama (`ollama`) | `openai` + localhost | ✓ | ✓ |
| MiniMax — CLI (`mmx`) | CLI wrapper | ✗ | ✗ |

**Note:** The `mmx` CLI backend is kept for legacy compatibility but does not support streaming or tool calling. Use `minimax_openai` (OpenAI-compatible HTTP API) for full MiniMax support.

## Config

`~/.forge/config.yaml`:
```yaml
provider: minimax_openai
model: default
api_key: ...
base_url: ...      # optional, for proxy/custom endpoints
```

**Provider env vars** (auto-detected by `forge setup`):
- MiniMax: `MINIMAX_API_KEY` or `mmx auth login --api-key <key>` (→ `~/.mmx/config.json`)
- OpenAI: `OPENAI_API_KEY`
- DeepSeek: `DEEPSEEK_API_KEY`
- Qwen: `QWEN_API_KEY`
- Kimi: `KIMI_API_KEY`
- GLM: `GLM_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`
- Ollama: `OLLAMA_BASE_URL` (default: `http://localhost:11434`)

**Runtime override:**
```bash
forge new "my idea" --provider deepseek --model deepseek-chat
forge plan "auth"   --provider kimi   --model moonshot-v1-8k
```

**First-time setup:**
```bash
forge setup          # diagnose all providers
forge setup --check  # non-interactive: show config + test
```

## Skills & MCP Support

- Skills: `~/.forge/skills/` + `~/.hermes/skills/` (shared)
- MCP servers: `~/.forge/mcp_servers.yaml`
- Both are **discovered and loaded on demand**, not at startup

## Install

```bash
cd ~/forge
pip install -e .
forge --version
```

## Run Tests

```bash
cd ~/forge
pytest tests/ -v
```

---

## Project Status

- **v0.2**: Real LLM integration across 4 providers (mmx/OpenAI/Anthropic/Ollama).
- **v0.2**: `forge setup` — diagnose provider setup issues.
- **v0.3 ✅**: Subagent delegation, MCP session management, skill auto-loading, TDD-first executor, multi-session state restore.
- **v0.4 ✅**: graph.py → engine-only + runners/ split; `forge.agents` (SubagentManager); MCP `is_alive()` heartbeat guard; structured pytest failure parsing; subagent run orphan cleanup on `forge continue`.
- **v0.5 ✅**: Surgical patch editing; git-aware FileService; LSP integration (pyright/tsserver); exponential backoff with Retry-After support; anchored context compaction; per-agent permission rulesets.
- **v0.6 ✅**: SDK migration — `openai` + `anthropic` Python SDKs for all providers, `LLMResponse` dataclass with streaming + tool calling, `minimax_openai` provider with full streaming + tools. Full Textual TUI with chat-first interface, model picker, command palette.

---

## v0.6 Features

### SDK-Based LLM Architecture

`forge/llm/` package replaces the legacy single-file backend:

```
forge/llm/
  __init__.py           — create_backend() factory, backward-compat re-exports
  config.py             — LLMConfig, load_config(), PROVIDER_DEFAULTS, PROVIDER_MODELS
  response.py           — LLMResponse dataclass (content, usage, finish_reason, tool_calls, raw)
  tools.py              — OpenAI ↔ Anthropic tool schema conversion
  backends/
    base.py             — LLMBackend ABC
    openai_.py          — OpenAI + all OpenAI-compatible providers
    anthropic.py        — Claude via anthropic SDK
    minimax_openai.py   — MiniMax OpenAI-compatible HTTP API
    ollama.py           — OpenAI SDK → localhost:11434/v1
    mmx.py              — CLI wrapper (legacy, no streaming/tools)
```

Every `complete()` call returns `LLMResponse` (not raw `str`). Callers extract `.content`.

### Streaming

All modern backends (`openai_`, `anthropic`, `minimax_openai`, `ollama`) support `complete_streaming()` — a synchronous generator yielding content chunks for TUI inline display:

```python
for chunk in backend.complete_streaming(prompt):
    output.append(chunk)  # token-by-token TUI update
```

### Tool Calling

OpenAI-style tool schemas throughout. `AnthropicBackend` converts schemas automatically via `convert_openai_to_anthropic_tools()`. Tool calls are attached to `LLMResponse.tool_calls`.

### Full Textual TUI

Built with Textual — full-screen app with:

- **Home screen** — recent session history, keybindings shown at bottom
- **Chat screen** — streaming token output, rendered markdown/ANSI, scroll-safe Log history
- **Model picker** — DataTable of 20+ models grouped by provider (Ctrl+A)
- **Command palette** — fuzzy search over all CLI commands (Ctrl+P)
- **Status bar** — shows current provider/model state

---

## v0.5 Features (still present)

### Surgical Patch Editing
Forge edits existing files using surgical patches, not full-file replacements. The executor generates `action: "patch"` manifest entries with hunk-based diffs:

```json
{
  "path": "src/foo.py",
  "action": "patch",
  "patch": "*** Begin Patch\n*** Update File: src/foo.py\n@@ context\n-old line\n+new line\n*** End Patch"
}
```

The patch engine uses 4-pass sequence matching (exact → rstrip → trim → normalized unicode) for reliable hunk application.

### Git-Aware FileService
Before generating code, the executor inspects the working tree via `FileService`:
- `read(path)` — file content + git diff against HEAD
- `status()` — modified, untracked, deleted files
- `search(query)` — fuzzy filename search
- `list(dir)` — directory listing

### LSP Integration
Hover, go-to-definition, and find-references for Python and TypeScript via pyright and tsserver. Automatically detects which servers are available and spawns them on demand.

### Retry with Exponential Backoff
All LLM `complete()` calls are wrapped with `RetryPolicy`:
- 5 attempts max, exponential backoff (2s → 4s → 8s …)
- `Retry-After` header support (ms, seconds, HTTP-date formats)
- Context overflow errors are never retried
- `_normalise_error()` handles `openai.APIError`, `anthropic.RateLimitError`, `httpx.HTTPStatusError` uniformly

### Context Compaction
Long sessions survive by compacting older turns: the 3 most recent turns are kept verbatim, everything older is summarized into a dense paragraph stored in episodic memory. Uses `tiktoken` (cl100k_base) for accurate token counting with fallback to `len(text.split()) * 1.3`.

### Permission System
Per-agent permission rulesets:
- `build` — full access, `.env` files → ASK confirmation
- `plan` — read-only, plan files in `.opencode/plans/` allowed
- `review` — read + patch, no create/delete
- `doom_loop` and out-of-scope external directories → always ASK

## Quick Start

```bash
forge setup                     # Interactive: pick provider, enter base URL + API key, test
forge setup --check             # Non-interactive: show current config and test connection
forge tui                       # Launch full-screen TUI
forge new "build a Stripe-powered SaaS billing app"
forge plan "add user authentication"
forge continue "add usage-based billing"
```
