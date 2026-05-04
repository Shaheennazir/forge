# @tearbin/forge — AI Coding Agent Framework

> Production-ready multi-agent CLI with unified memory, TUI, and code intelligence tools. Powered by Large Language Models, built for developers.

```bash
npx @tearbin/forge
```

---

## Install

### Via npm (recommended)

```bash
  # 1. Install the Python CLI first
  pip install forge-tui

# 2. Install the npm wrapper (optional — gives you the `forge` command)
npm install -g @tearbin/forge

# 3. Verify
forge --version
forge setup  # interactive: pick provider, enter API key, test
```

### Via npx (no install)

```bash
# npx downloads the npm wrapper which looks for forge-cli on PATH
npx @tearbin/forge
```

---

## Requirements

- **Node.js** ≥ 18
- **Python** ≥ 3.11
- **forge-cli** installed via `pip install forge-cli`

---

## Two Modes

| Command | What it does |
|---|---|
| `forge new "build a Stripe billing app"` | Directed graph: orchestrator → spec → executor → review gate |
| `forge compile "build a URL shortener"` | Pipeline: interview → rules → failing tests → code → review → integration |
| `forge compile --edit "rename get_user"` | Edit pipeline: blast radius → extract rules → delta → test delta |

Both modes share the same LLM backend, TUI, skills system, and memory model.

---

## TUI Screens

- **Home** — session browser, model info, project status
- **Chat** — 29 code intelligence tools (bandit, ruff, pyupgrade, semgrep, hypothesis, mutmut, griffe, radon, vulture, and more)
- **Graph** — DAG visualizer, event stream, start/reset/spec/executor controls

---

## License

MIT — Shaheen Nazir
