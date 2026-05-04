"""
forge.cli — Click-based CLI entrypoint.

Commands:
  forge new <prompt>           Start a new project from vague prompt
  forge continue [task]        Resume a project session
  forge plan <description>    Plan-only mode (no execution)
  forge agents list           List active subagents (future)
  forge memory inspect         Query project memory
  forge status [project]       Show current project status
"""

from __future__ import annotations
import json
import click
import sys
import uuid
import logging
import structlog
from pathlib import Path

from forge.db import ForgeDB
from forge.graph import ForgeGraph, GraphRunner
from forge.skills import SkillRegistry
from forge.mcp import MCPConfig

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger(__name__)

STATUS_ICONS = {
    "done": "✓",
    "running": "⟳",
    "pending": "○",
    "blocked": "⊗",
    "failed": "✗",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_project_dir(project_name: str) -> Path:
    return Path.home() / ".forge" / "projects" / project_name


# ── Commands ──────────────────────────────────────────────────────────────────

def _build_llm(config_path: Path | None = None):
    """Create LLM backend, ensuring config exists."""
    from forge.llm import load_config, create_backend
    cfg = load_config(config_path) if config_path else None
    return create_backend(cfg)


@click.group()
@click.version_option(version="0.1.0")
def main():
    """forge — Production-ready multi-agent CLI."""
    pass


@main.command()
@click.argument("prompt")
@click.option("--project", "-p", "project_name", help="Project name (auto-generated if omitted)")
@click.option("--continue/--no-continue", "continue_session", default=False,
              help="Resume existing session instead of starting fresh")
@click.option("--provider", type=click.Choice(["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"], case_sensitive=False),
              help="LLM provider (overrides config)")
@click.option("--model", "model_override", help="Model name (overrides config)")
def new(prompt: str, project_name: str | None, continue_session: bool, provider: str | None, model_override: str | None):
    """Start a new project from a vague prompt."""
    project_name = project_name or f"proj_{uuid.uuid4().hex[:8]}"
    project_id = project_name
    db = ForgeDB(project_id=project_id)
    log.info("project.init", project=project_name, id=project_id)

    db.upsert_project(name=project_name)

    latest_ver = db.latest_spec_version() or 0
    llm = _build_llm()
    if provider:
        from forge.llm import LLMConfig
        llm_cfg = LLMConfig(provider=provider, model=model_override or "")
        from forge.llm import create_backend
        llm = create_backend(llm_cfg)
    g = ForgeGraph(project_name, db, llm=llm)
    g.build_initial_graph(spec_version=latest_ver)

    skill_registry = SkillRegistry()
    mcp_config = MCPConfig()

    log.info("forge.start", project=project_name, llm=llm.config.provider if hasattr(llm, 'config') else 'unknown', skills=len(skill_registry.skills))

    workdir = get_project_dir(project_name) / "src"
    workdir.mkdir(parents=True, exist_ok=True)

    runner = GraphRunner(
        g, db,
        workdir=workdir,
        skill_registry=skill_registry,
        mcp_config=mcp_config,
    )
    result = runner.run(
        entry_node="orchestrator",
        prompt=prompt,
        continue_session=continue_session,
    )

    log.info("forge.done", project=project_name, result=result)

    click.echo(f"\n✓ Project: {project_name}")
    click.echo(f"  Path: {db.db_path}")
    click.echo(f"  Visited nodes: {' → '.join(result['visited'])}")
    click.echo(f"  Final: {result['final_node']}")

    db.close()


@main.command()
@click.argument("task", required=False)
@click.option("--project", "-p", "project_name", help="Project name to resume")
@click.option("--provider", type=click.Choice(["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"], case_sensitive=False),
              help="LLM provider (overrides config)")
@click.option("--model", "model_override", help="Model name (overrides config)")
def continue_cmd(task: str | None, project_name: str | None, provider: str | None, model_override: str | None):
    """Resume an existing project session."""
    if not project_name:
        projects_root = Path.home() / ".forge" / "projects"
        if not projects_root.exists():
            click.echo("No projects found. Run `forge new <prompt>` first.")
            sys.exit(1)
        candidates = [p for p in projects_root.iterdir() if (p / "memory.db").exists()]
        if not candidates:
            click.echo("No projects found. Run `forge new <prompt>` first.")
            sys.exit(1)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        project_name = candidates[0].name

    db = ForgeDB(project_id=project_name)
    latest_ver = db.latest_spec_version()
    if not latest_ver:
        click.echo(f"No spec found for project '{project_name}'. Run `forge new` first.")
        sys.exit(1)

    click.echo(f"Resuming project: {project_name} (spec v{latest_ver})")

    llm = _build_llm()
    if provider:
        from forge.llm import LLMConfig
        llm_cfg = LLMConfig(provider=provider, model=model_override or "")
        llm = create_backend(llm_cfg)

    # Build fresh graph and restore from DB
    g = ForgeGraph(project_name, db, llm=llm)
    g.restore_from_db()

    skill_registry = SkillRegistry()
    mcp_config = MCPConfig()
    workdir = get_project_dir(project_name) / "src"

    sv = db.get_spec_version(latest_ver)
    spec_md = sv.spec_md if sv else ""

    runner = GraphRunner(g, db, workdir=workdir, skill_registry=skill_registry, mcp_config=mcp_config)

    # Restore executor output from mid memory if it exists (for review gate to use)
    executor_files_json = db.read_memory(tier="mid", agent="executor", key="last_files_generated")
    context = {
        "prompt": task or "",
        "continue_session": True,
        "spec_md": spec_md,
        "spec_version": latest_ver,
    }
    if executor_files_json:
        try:
            context["executor_output"] = {"files": json.loads(executor_files_json)}
        except json.JSONDecodeError:
            pass

    result = runner.run(entry_node="orchestrator", **context)

    click.echo(f"\n✓ Continued: {project_name}")
    click.echo(f"  Visited: {' → '.join(result['visited'])}")
    click.echo(f"  Final: {result['final_node']}")
    db.close()


@main.command()
@click.argument("description")
@click.option("--project", "-p", "project_name", help="Project to plan within")
@click.option("--provider", type=click.Choice(["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"], case_sensitive=False),
              help="LLM provider (overrides config)")
@click.option("--model", "model_override", help="Model name (overrides config)")
def plan(description: str, project_name: str | None, provider: str | None, model_override: str | None):
    """Plan-only mode: generate spec without executing."""
    project_name = project_name or f"plan_{uuid.uuid4().hex[:8]}"
    db = ForgeDB(project_id=project_name)
    db.upsert_project(name=project_name)

    latest_ver = db.latest_spec_version() or 0

    llm = _build_llm()
    if provider:
        from forge.llm import LLMConfig
        llm_cfg = LLMConfig(provider=provider, model=model_override or "")
        from forge.llm import create_backend
        llm = create_backend(llm_cfg)

    click.echo(f"Planning in: {project_name}")
    g = ForgeGraph(project_name, db, llm=llm)
    g.build_initial_graph(spec_version=latest_ver)

    from forge.graph import NodeStatus, GraphRunner
    skill_registry = SkillRegistry()
    mcp_config = MCPConfig()
    workdir = get_project_dir(project_name) / "src"
    runner = GraphRunner(g, db, workdir=workdir, skill_registry=skill_registry, mcp_config=mcp_config)
    result = runner.run(entry_node="spec_gen", prompt=description)

    spec_node = g.get_node("spec_gen")
    if spec_node and spec_node.output:
        spec = spec_node.output.get("spec_md", "")
        click.echo("\n" + "=" * 60)
        click.echo(spec)
        click.echo("=" * 60)

    click.echo(f"\n✓ Plan complete: {project_name}")
    db.close()


@main.command()
@click.option("--provider", type=click.Choice(["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"], case_sensitive=False),
              help="Provider to configure (interactive prompt if omitted)")
@click.option("--base-url", "base_url", help="API base URL")
@click.option("--api-key", "api_key", help="API key")
@click.option("--model", "model_override", help="Model name (provider default used if omitted)")
@click.option("--check", "check_only", is_flag=True, help="Non-interactive: check current config without prompting")
def setup(provider: str | None, base_url: str | None, api_key: str | None, model_override: str | None, check_only: bool):
    """Interactive LLM provider setup: pick a provider, enter base URL + API key, save and test."""
    import shutil, subprocess, json as _json
    from forge.llm import PROVIDER_DEFAULTS, PROVIDER_MODELS, create_backend, LLMConfig, ensure_config

    NON_OPENAI = {"mmx", "minimax", "anthropic", "ollama"}

    # ── Non-interactive check ─────────────────────────────────────────────────
    if check_only:
        from forge.llm import load_config
        cfg = load_config()
        click.echo(f"Current config ({Path.home() / '.forge' / 'config.yaml'}):")
        click.echo(f"  provider: {cfg.provider}")
        click.echo(f"  model:    {cfg.model}")
        click.echo(f"  base_url: {cfg.base_url or '(none)'}")
        click.echo(f"  api_key:  {'(set)' if cfg.api_key else '(none)'}")
        # provider-specific: mmx reads auth from ~/.mmx/config.json, so api_key may be absent
        if cfg.provider and (cfg.api_key or cfg.provider == "mmx"):
            click.echo("\n🔄 Testing connection...")
            try:
                backend = create_backend(cfg)
                test = backend.complete("Say 'connected' in one word.", system="", max_tokens=10)
                test_text = test.content if hasattr(test, "content") else str(test)
                click.echo(f"  Response: {test_text.strip()}")
                click.echo("\n✅ Connected!")
            except Exception as e:
                click.echo(f"\n❌ Connection failed: {e}")
        else:
            click.echo("\n⚠ No provider or API key configured. Run 'forge setup' to configure.")
        return

    # ── Interactive setup ───────────────────────────────────────────────────

    # ── Step 1: pick provider ──────────────────────────────────────────────────
    if not provider:
        click.echo("Select a provider:\n")
        providers = ["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"]
        for i, p in enumerate(providers, 1):
            default_info = PROVIDER_DEFAULTS.get(p, {})
            doc = default_info.get("docs", "")
            model = default_info.get("model", "")
            click.echo(f"  {i}. {p:<12} (default model: {model}) {doc}")
        click.echo()
        try:
            choice = click.prompt("Enter number or name", default="minimax", type=str).strip().lower()
        except (click.Abort, EOFError):
            click.echo("Aborted.")
            return

        # Resolve choice
        if choice.isdigit() and 1 <= int(choice) <= len(providers):
            provider = providers[int(choice) - 1]
        elif choice in providers:
            provider = choice
        else:
            click.echo(f"Unknown provider: '{choice}'")
            return

    provider = provider.lower()
    if provider == "minimax":
        provider = "mmx"

    # ── Step 2: base URL ──────────────────────────────────────────────────────
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    default_base_url = defaults.get("base_url", "") or ""

    # mmx and anthropic don't use base_url in the same way
    if provider in NON_OPENAI and provider != "ollama":
        default_base_url = ""

    if not base_url:
        if default_base_url:
            prompt = f"Base URL (Enter for default: {default_base_url})"
            raw = click.prompt(prompt, default=default_base_url, type=str).strip()
            base_url = raw or default_base_url
        elif provider == "ollama":
            base_url = click.prompt("Ollama base URL", default="http://localhost:11434", type=str).strip()
        else:
            base_url = click.prompt("Base URL", type=str).strip()

    # ── Step 3: API key ───────────────────────────────────────────────────────
    if not api_key:
        if provider in NON_OPENAI and provider != "ollama":
            # mmx uses ~/.mmx/config.json
            api_key = ""
        else:
            key_prompt = f"API key for {provider}"
            try:
                api_key = click.prompt(key_prompt, hide_input=True, type=str).strip()
            except (click.Abort, EOFError):
                click.echo("Aborted.")
                return
            if not api_key:
                click.echo("API key cannot be empty.")
                return

    # ── Step 4: model picker ───────────────────────────────────────────────────
    models = PROVIDER_MODELS.get(provider, [])
    default_model = defaults.get("model", "default") if defaults else "default"
    if not model_override:
        if models:
            click.echo(f"\nSelect a model for {provider}:\n")
            for i, m in enumerate(models, 1):
                marker = " ← default" if m == default_model else ""
                click.echo(f"  {i}. {m}{marker}")
            click.echo(f"  {len(models) + 1}. Custom (type your own)")
            click.echo()
            try:
                choice = click.prompt("Enter number", default=str(models.index(default_model) + 1) if default_model in models else "1", type=str).strip()
            except (click.Abort, EOFError):
                click.echo("Aborted.")
                return
            if choice.isdigit() and 1 <= int(choice) <= len(models):
                model_override = models[int(choice) - 1]
            elif choice == str(len(models) + 1):
                model_override = click.prompt("Enter model name", type=str).strip()
            else:
                click.echo("Invalid choice.")
                return
        else:
            raw = click.prompt(f"Model (Enter for default: {default_model})", default=default_model, type=str).strip()
            model_override = raw or default_model

    # ── Step 5: save config ───────────────────────────────────────────────────
    ensure_config()
    config_path = Path.home() / ".forge" / "config.yaml"
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg["provider"] = provider
    cfg["model"] = model_override
    if base_url:
        cfg["base_url"] = base_url
    if api_key:
        cfg["api_key"] = api_key
    with open(config_path, "w") as f:
        yaml.dump(cfg, f)

    click.echo(f"\n✓ Config saved to {config_path}")
    click.echo(f"  provider: {provider}")
    click.echo(f"  model:    {model_override}")
    if base_url:
        click.echo(f"  base_url: {base_url}")
    if api_key:
        click.echo(f"  api_key:  {api_key[:8]}...")

    # ── Step 6: test connection ───────────────────────────────────────────────
    click.echo("\n🔄 Testing connection...")
    try:
        cfg = LLMConfig(provider=provider, model=model_override, api_key=api_key, base_url=base_url)
        backend = create_backend(cfg)
        if provider == "mmx":
            test = backend.complete("Say 'connected' in one word.", system="", max_tokens=10)
        elif provider == "anthropic":
            test = backend.complete("Say 'connected' in one word.", system="You are a helpful assistant.", max_tokens=10)
        elif provider == "ollama":
            test = backend.complete("Say 'connected' in one word.", system="", max_tokens=10)
        else:
            test = backend.complete("Say 'connected' in one word.", system="", max_tokens=10)
        test_text = test.content if hasattr(test, "content") else str(test)
        click.echo(f"  Response: {test_text.strip()}")
        click.echo("\n✅ Connected! Run: forge new \"your idea\"")
    except Exception as e:
        click.echo(f"\n❌ Connection failed: {e}")
        click.echo("Check your base URL and API key, then run 'forge setup' again.")


@main.command()
def tui():
    """Launch the full-screen Forge TUI (chat-first terminal interface)."""
    from forge.tui.app import ForgeTUI
    app = ForgeTUI()
    app.run()


@main.command()
@click.argument("prompt")
@click.option("--auto-approve", is_flag=True,
              help="Skip all human gates — auto-approve without prompting")
@click.option("--workdir", "-w", type=click.Path(path_type=Path), default=Path("."),
              help="Working directory for output files (default: current directory)")
@click.option("--provider", type=click.Choice(["minimax", "mmx", "openai", "deepseek", "qwen", "kimi", "glm", "anthropic", "ollama"], case_sensitive=False),
              help="LLM provider (overrides config)")
@click.option("--model", "model_override", help="Model name (overrides config)")
@click.option("--test-prompt", "test_prompts", multiple=True,
              help="Pre-seeded answers for interview questions (for non-interactive testing). "
                   "Pass multiple times for multiple answers, in order.")
@click.option("--model-routing", "model_routing", multiple=True,
              help="Per-agent model routing (agent=provider/model). "
                   "Example: --model-routing interviewer=anthropic/claude-opus-4 ")
@click.option("--sandbox/--no-sandbox", "use_sandbox", default=False,
              help="Run code execution inside an E2B sandbox for isolation. "
                   "Uses E2B_API_KEY env var if set.")
@click.option("--nats-url", "nats_url", default=None,
              help="NATS broker URL for pipeline event streaming. "
                   "Example: nats://localhost:4222 "
                   "If not provided, events are printed to stdout only.")
def compile(prompt: str, auto_approve: bool, workdir: Path, provider: str | None, model_override: str | None,
            test_prompts: tuple[str, ...], model_routing: tuple[str, ...], use_sandbox: bool,
            nats_url: str | None):
    """
    Compile user intent into production code through the Product Compiler pipeline.

    Runs the full pipeline: Interview → Rules → Tests → Code → Integration → Review.

    The interview stage asks questions interactively in the terminal.
    Use --auto-approve to skip both human gates.

    Examples:
      forge compile "build a blog with authentication"
      forge compile "a URL shortener" --auto-approve
      forge compile "a REST API" --test-prompt "posts" --test-prompt "create,read,update" --test-prompt "admin" --test-prompt "yes"
      forge compile "a blog" --model-routing "reviewer=anthropic/claude-opus-4-5" --model-routing "coder=anthropic/claude-sonnet-4"
    """
    from forge.llm import LLMConfig, create_backend
    from forge.product_compiler.pipeline import ProductCompilerPipeline
    from forge.product_compiler.messaging import MessagingLayer, MessagingBackend, NATSConfig

    # Parse model routing
    routing_dict = {}
    for route in model_routing:
        if "=" in route:
            agent, model_spec = route.split("=", 1)
            routing_dict[agent.strip()] = model_spec.strip()

    # Parse autopilot answers
    autopilot_answers = list(test_prompts)

    # Build LLM backend
    if provider or model_override:
        cfg = LLMConfig(provider=provider or "minimax", model=model_override or "")
        llm = create_backend(cfg)
    else:
        llm = create_backend()

    # Ensure workdir exists
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Build messaging layer if --nats-url provided
    messaging_layer = None
    if nats_url:
        nats_conf = NATSConfig(url=nats_url)
        messaging_layer = MessagingLayer(backend=MessagingBackend.NATS, config=nats_conf)

    pipeline = ProductCompilerPipeline(
        auto_approve=auto_approve,
        workdir=workdir,
        llm_config=cfg if (provider or model_override) else None,
        model_routing=routing_dict if routing_dict else None,
        test_autopilot=autopilot_answers if autopilot_answers else None,
        use_sandbox=use_sandbox,
        messaging_layer=messaging_layer,
    )

    click.echo(f"\n{'='*60}")
    click.echo(f"  PRODUCT COMPILER — Layer 2 (Schema Design, Integrator, Reviewer)")
    click.echo(f"{'='*60}\n")

    # Run the pipeline generator, handling interview questions interactively
    generator = pipeline.run(prompt)

    # First iteration starts the generator (next() call)
    first_event = next(generator)
    current_event = first_event
    while True:
        t = current_event.get("type")
        p = current_event.get("payload", {})

        if t == "stage":
            click.echo(f"\n▶ STAGE: {p.get('description')} ({p.get('stage')})")
            current_event = next(generator)

        elif t == "question":
            click.echo(f"\n❓ {p.get('question')}")
            answer = click.prompt("> ", type=str, default="").strip()
            if not answer:
                answer = "(no answer provided)"
            # send() resumes generator and returns the NEXT yielded event
            current_event = generator.send(answer)

        elif t == "output":
            click.echo(f"  → {p.get('summary', '')}")
            # Validate test-autopilot answer count after interview stage completes
            if p.get("stage") == "interview" and autopilot_answers:
                question_count = (len(pipeline._state.interview_history) - 1) // 2 if pipeline._state.interview_history else 0
                consumed = pipeline.autopilot_consumed
                if consumed != len(autopilot_answers):
                    click.echo(f"\n⚠ WARNING: {len(autopilot_answers)} autopilot answer(s) provided "
                               f"but {consumed} answer(s) consumed ({question_count} question(s) asked).")
                    if consumed < len(autopilot_answers):
                        click.echo(f"  Unused answers: {autopilot_answers[consumed:]}")
            current_event = next(generator)

        elif t == "gate":
            gate_num = p.get("gate")
            click.echo(f"\n{'='*60}")
            click.echo(f"  GATE {gate_num}: {p.get('name', 'APPROVAL')}")
            click.echo(f"{'='*60}\n")

            if gate_num == 1:
                rule_count = p.get("rule_count", 0)
                click.echo(f"  {rule_count} rules compiled. Review them:\n")
                for rule in p.get("rules", []):
                    click.echo(f"    {rule}")
                click.echo(f"\n  Type 'y' to approve, 'n' to reject: ", nl=False)
                if auto_approve:
                    click.echo("y (auto-approved)")
                    pipeline.approve()
                    current_event = next(generator)
                else:
                    response = click.getchar()
                    click.echo(response)
                    if response.lower() == "y":
                        pipeline.approve()
                        click.echo("  ✓ Rule set approved")
                        current_event = next(generator)
                    else:
                        pipeline.reject()
                        click.echo("  ✗ Rule set rejected — pipeline stopped")
                        sys.exit(0)

            elif gate_num == 2:
                table_count = p.get("table_count", 0)
                click.echo(f"  {table_count} tables designed.\n")
                click.echo(f"  SQL preview:\n")
                for line in p.get("sql", "").splitlines()[:20]:
                    click.echo(f"    {line}")
                if len(p.get("sql", "").splitlines()) > 20:
                    click.echo(f"    ... (truncated)")
                click.echo(f"\n  Type 'y' to approve, 'n' to reject: ", nl=False)
                if auto_approve:
                    click.echo("y (auto-approved)")
                    pipeline.approve()
                    current_event = next(generator)
                else:
                    response = click.getchar()
                    click.echo(response)
                    if response.lower() == "y":
                        pipeline.approve()
                        click.echo("  ✓ DB schema approved")
                        current_event = next(generator)
                    else:
                        pipeline.reject()
                        click.echo("  ✗ DB schema rejected — pipeline stopped")
                        sys.exit(0)

        elif t == "complete":
            click.echo(f"\n{'='*60}")
            click.echo(f"  ✓ PIPELINE COMPLETE")
            click.echo(f"{'='*60}")
            state = p.get("state", {})
            click.echo(f"\n  Stage: {state.get('stage', 'unknown')}")
            click.echo(f"  Status: {state.get('status', 'unknown')}")
            break

        elif t == "error":
            click.echo(f"\n❌ ERROR: {p.get('message', 'unknown error')}")
            sys.exit(1)

        else:
            # Unknown event type — advance
            try:
                current_event = next(generator)
            except StopIteration:
                break


@main.command()
def agents():
    """List active subagents (v0.2+)."""
    click.echo("Subagent delegation is not yet implemented (v0.2).")
    click.echo("Currently only Orchestrator + SpecGen + Executor + ReviewGate run.")


@main.command()
@click.option("--project", "-p", "project_name", required=True)
@click.option("--tier", "-t", help="Filter by tier: short, mid, episodic, long")
@click.option("--agent", "-a", help="Filter by agent")
@click.option("--limit", "-l", default=50, help="Max entries")
def memory(project_name: str, tier: str | None, agent: str | None, limit: int):
    """Inspect project memory."""
    db = ForgeDB(project_id=project_name)
    entries = db.query_memory(tier=tier, agent=agent)
    click.echo(f"Memory entries for '{project_name}':")
    for e in entries[:limit]:
        click.echo(f"  [{e.tier}] {e.agent} / {e.key}")
        click.echo(f"    {e.value[:120]}")
    db.close()


@main.command()
@click.option("--project", "-p", "project_name", help="Project name")
def status(project_name: str | None):
    """Show project status."""
    if not project_name:
        projects_root = Path.home() / ".forge" / "projects"
        if not projects_root.exists():
            click.echo("No projects found.")
            return
        candidates = [p for p in projects_root.iterdir() if (p / "memory.db").exists()]
        if not candidates:
            click.echo("No projects found.")
            return
        click.echo("Available projects:")
        for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
            click.echo(f"  {p.name}  (modified: {p.stat().st_mtime})")
        return

    db = ForgeDB(project_id=project_name)
    sv = db.get_spec_version(db.latest_spec_version() or 0)
    tasks, edges = db.get_task_graph()

    click.echo(f"Project: {project_name}")
    if sv:
        click.echo(f"Spec version: {sv.version}")
    click.echo(f"Tasks ({len(tasks)}):")
    for t in tasks:
        status_icon = STATUS_ICONS.get(t.status, t.status)
        click.echo(f"  {status_icon} {t.id} [{t.status}] {t.label}")
    click.echo(f"Edges: {len(edges)}")
    db.close()


if __name__ == "__main__":
    main()
