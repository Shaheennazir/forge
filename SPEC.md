# Forge v0.4 — Spec

> Directed-graph multi-agent coding CLI. Vague prompt → spec → production code.

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

## File Layout

```
forge/
├── __init__.py
├── agents.py          # SubagentManager: spawn/get_incomplete_runs/finish_subagent_run
├── cli.py             # forge new / continue / plan / status / memory
├── db.py              # ForgeDB: SQLite 4-tier memory + spec versioning
├── graph.py           # ForgeGraph + GraphRunner + NodeStatus + restore_from_db
├── llm.py             # LLMBackend factory (mmx/openai/anthropic/ollama/…)
├── mcp.py             # MCPClient: stdio JSON-RPC 2.0, is_alive() heartbeat guard
├── skills.py          # SkillRegistry: scan ~/.forge/skills/ + ~/.hermes/skills/
└── runners/
    ├── __init__.py
    ├── common.py      # shared types, system prompts, write_files/run_tests/parse_pytest_output
    ├── orchestrator.py
    ├── spec_gen.py
    ├── executor.py
    └── review_gate.py
```

## Memory Model (4-tier, per-project SQLite)

| Tier | Scope | Duration |
|------|-------|----------|
| `short` | Current task | Session (in-memory) |
| `mid` | Current project | Days |
| `episodic` | Across sessions | Weeks |
| `long` | Cross-project | Months |

Schema: `~/.forge/projects/<id>/memory.db`

## Spec Versioning

- Every spec write is a **new version row** (never overwrite)
- `changelog` column tracks delta per version
- `forge continue` restores last episodic state and resumes

## SubagentManager (`forge.agents`)

- `spawn(task, agent_type, task_id, context)` → `SubagentResult`
- `get_incomplete_runs()` → incomplete `SubagentResult[]`
- `finish_subagent_run(run_id, status, …)` called on completion
- Subagent delegation triggered when LLM in orchestrator emits `action: "delegate"`

## MCP Heartbeat Guard (`forge.mcp`)

- `MCPClient.is_alive()` → `bool` (checks `proc.poll() is None`)
- `call_tool()` calls `is_alive()` **before** every request
- Raises `RuntimeError("MCP server X is not alive …")` if dead
- `MCPConfig.create_client(name)` caches and reuses connected clients

## Executor TDD Cycle

1. **Phase 1**: Ask LLM for test files (TDD-first). Write to disk.
2. **Phase 2**: Ask LLM for implementation files. Write to disk.
3. **Run pytest** via subprocess → structured `PytestResult`
4. Structured failures include: `name`, `type` (AssertionError/ImportError/…), `message`, `file`, `line`

## Review Gate

- Failed tests → automatic **BLOCKED** (no LLM needed)
- LLM review: files vs spec
- `review_gate → executor` on fail (retry lane)
- `review_gate → orchestrator` on pass (done lane)

## CLI Commands

```bash
forge new "build a Stripe-powered SaaS billing app"
forge continue "add usage-based billing"
forge plan "refactor auth layer"
forge status --project myapp
forge memory inspect --project myapp --tier episodic
```

## Config

`~/.forge/config.yaml`:
```yaml
provider: mmx
model: default
api_key: ...
base_url: ...
```

## Providers

MiniMax, OpenAI, DeepSeek, Qwen, Kimi, GLM, Anthropic, Ollama. Set via env vars or `~/.forge/config.yaml`.

## MCP Servers

`~/.forge/mcp_servers.yaml`:
```yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

## Failure Lanes

- `BLOCKED` → executor retries with reason
- `FAILED` → orchestrator escalates or blocks
- Orphaned subagent runs (`status='running'`) are cleaned up on `forge continue` → marked failed

## v0.4 Changes

- `graph.py` refactored: engine only (~420 lines), runners moved to `runners/`
- `agents.py` new: `SubagentManager` (was in graph.py)
- `runners/common.py`: shared helpers including `parse_pytest_output` (structured failures)
- `runners/executor.py`: Phase 1 tests written before Phase 2 impl (correct TDD order)
- `runners/review_gate.py`: deferred `_review_spec_compliance` lookup (enables test patching)
- MCP `is_alive()` + heartbeat guard in `call_tool()`
- `ForgeDB.get_incomplete_subagent_runs()` + cleanup in `restore_from_db()`
- `GraphRunner` accepts `agents: SubagentManager`, passes to orchestrator + executor
- Orchestrator handles `delegate` action by calling `agents.spawn()`
