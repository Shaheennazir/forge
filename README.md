# forge — Production-Ready Multi-Agent CLI

> Vague prompt → spec → production code. Directed graph orchestration with 4-tier memory.

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

## Skills & MCP Support

- Skills: `~/.forge/skills/` + `~/.hermes/skills/` (shared)
- MCP servers: `~/.forge/mcp_servers.yaml`
- Both are **discovered and loaded on demand**, not at startup

## CLI Commands

```bash
forge new "build a Stripe-powered SaaS billing app"   # vague → spec → build
forge continue "add usage-based billing"                # resume from episodic state
forge plan "refactor auth layer"                      # spec only, no execution
forge status --project myapp                           # task graph + spec version
forge memory inspect --project myapp --tier episodic   # query memory
```

## Config

`~/.forge/config.yaml`:
```yaml
provider: mmx      # mmx | openai | deepseek | qwen | kimi | glm | anthropic | ollama
model: default     # provider-specific default is used if omitted
api_key: ...       # or set via env var (see below)
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
forge setup --provider deepseek  # diagnose one provider
```

`~/.forge/mcp_servers.yaml`:
```yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/shaheen/projects"]
```

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

## Project Status

- **v0.2**: Real LLM integration across 4 providers (mmx/OpenAI/Anthropic/Ollama).
- **v0.2**: `forge setup` — diagnose provider setup issues.
- **v0.3 ✅**: Subagent delegation, MCP session management, skill auto-loading, TDD-first executor, multi-session state restore.
- **v0.4 ✅**: graph.py → engine-only + runners/ split; `forge.agents` (SubagentManager); MCP `is_alive()` heartbeat guard; structured pytest failure parsing; subagent run orphan cleanup on `forge continue`.
- **v0.5 ✅**: Surgical patch editing; git-aware FileService; LSP integration (pyright/tsserver); exponential backoff with Retry-After support; anchored context compaction; per-agent permission rulesets.

## v0.5 Features

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

### Context Compaction
Long sessions survive by compacting older turns: the 3 most recent turns are kept verbatim, everything older is summarized into a dense paragraph stored in episodic memory.

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
forge new "build a Stripe-powered SaaS billing app"
forge plan "add user authentication"
forge continue "add usage-based billing"
```
