"""forge.runners.executor — Executor node runner."""

from __future__ import annotations
import json
import structlog
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from forge.file_service import FileService
from forge.graph import ForgeGraph, NodeStatus
from forge.permissions import (
    check_permission,
    PermissionRequired,
    PermissionResult,
)
from forge.retry import RetryPolicy, with_retry
from forge.runners.common import (
    EXECUTOR_SYSTEM,
    write_files,
    run_tests,
    generate_from_spec,
    inject_skills_into_context as _inject_skills,
)

if TYPE_CHECKING:
    from forge.skills import SkillRegistry
    from forge.mcp import MCPConfig
    from forge.db import ForgeDB
    from forge.agents import SubagentManager
    from forge.lsp import LSPService

log = structlog.get_logger(__name__)


def run(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    spec_md: str,
    workdir: Path | None = None,
    skill_registry: "SkillRegistry | None" = None,
    mcp_config: "MCPConfig | None" = None,
    agents: "SubagentManager | None" = None,
    lsp: "LSPService | None" = None,
    **extra,
) -> dict:
    """
    Executor: reads SPEC.md, generates code/tests (TDD-first), writes files to disk.

    TDD CYCLE:
      1. Ask LLM to generate tests first
      2. Ask LLM to generate implementation
      3. Run tests via subprocess
      4. Report structured pass/fail to Review Gate
    """
    log.info("executor.run", spec_md=spec_md[:80])

    project_workdir = workdir or (
        Path.home() / ".forge" / "workspace" / g.project_id
    )
    project_workdir.mkdir(parents=True, exist_ok=True)

    # Collect relevant skills
    matched_skills = []
    if skill_registry:
        matched_skills, _ = _inject_skills("code", skill_registry, g.llm)
        matched_skills.extend(skill_registry.match("tdd"))

    system = EXECUTOR_SYSTEM
    if matched_skills:
        skill_notes = "\n".join(
            f"- [{s.name}] {s.description}" for s in matched_skills
        )
        system = EXECUTOR_SYSTEM + f"\n\nRelevant skills loaded:\n{skill_notes}"

    # Build MCP tool context
    mcp_tool_context = _build_mcp_context(mcp_config)

    # Build LSP context — code intelligence (symbols, definitions, references)
    lsp_context = _build_lsp_context(lsp, project_workdir) if lsp else ""

    files: list[dict] = []

    if g.llm:
        _retry_policy = RetryPolicy(max_attempts=5)

        # Phase 1: TDD — ask for tests first
        tdd_prompt = f"""TDD Phase 1: Write tests FIRST.

SPEC.md:
{spec_md}

{mcp_tool_context}
{lsp_context}

Generate test files as JSON: [{{"path": "tests/test_...", "action": "create", "content": "..."}}]
Write tests for every function/class described in the spec.
"""
        raw_tests = with_retry(
            _retry_policy,
            g.llm.complete,
            prompt=tdd_prompt,
            system=system,
            max_tokens=8192,
            temperature=0.2,
        )
        # with_retry returns LLMResponse when SDK succeeds
        raw_tests = raw_tests.content if hasattr(raw_tests, "content") else raw_tests
        try:
            test_files = json.loads(raw_tests)
            if isinstance(test_files, dict):
                test_files = [test_files]
        except json.JSONDecodeError:
            test_files = []
        files.extend(f for f in test_files if isinstance(f, dict))

        # Phase 2: Implementation
        test_file_list = "\n".join(
            ff.get("path", "?")
            for ff in test_files
            if isinstance(ff, dict) and "path" in ff
        )
        impl_prompt = f"""TDD Phase 2: Write implementation.

SPEC.md:
{spec_md}

Tests written ({len(test_files)} files):
{test_file_list}

{mcp_tool_context}
{lsp_context}

IMPORTANT — PATCH WHEN POSSIBLE:
- For existing files, prefer: {{"path": "...", "action": "patch", "patch": "..."}}
  over full-file replacement. The patch format uses "*** Begin Patch" / "*** End Patch"
  with "*** Update File: <path>" headers and unified diff hunks.
- For new files (no existing content): use {{"path": "...", "action": "create", "content": "..."}}
- Only use "action": "write"/"update" for full-file replacements when patch is impractical.

Generate implementation files as JSON: [{{"path": "...", "action": "create"|"patch", "content|patch": "..."}}]

# Inject git-aware file context so the LLM knows what changed
"""
        # Inject git-aware file context
        try:
            svc = FileService(project_workdir)
            status = svc.status()
            if status:
                changed = [f.path for f in status]
                impl_prompt += f"\n\nChanged files since HEAD: {', '.join(changed)}"

            # For the key files the spec mentions, include their current content
            # so the LLM can generate surgical patches instead of full replacements
            existing_content_context = ""
            for change in status[:5]:  # top 5 changed files
                if change.path.endswith(('.py', '.ts', '.js', '.md')):
                    fi = svc.read(change.path)
                    if fi.content and len(fi.content) < 2000:
                        existing_content_context += f"\n\n--- {change.path} ---\n{fi.content}"

            if existing_content_context:
                impl_prompt += f"\n\nCurrent content of changed files:{existing_content_context}"
        except Exception as e:
            log.warning("executor.fileservice_unavailable", error=str(e))

        raw_impl = with_retry(
            _retry_policy,
            g.llm.complete,
            prompt=impl_prompt,
            system=system,
            max_tokens=8192,
            temperature=0.2,
        )
        raw_impl = raw_impl.content if hasattr(raw_impl, "content") else raw_impl
        try:
            impl_files = json.loads(raw_impl)
            if isinstance(impl_files, dict):
                impl_files = [impl_files]
        except json.JSONDecodeError:
            impl_files = []
        files.extend(f for f in impl_files if isinstance(f, dict))

        # Phase 3: Run tests
        test_results = _run_tests_with_write(project_workdir, files)
    else:
        files = generate_from_spec(spec_md)
        test_results = None

    # Write all files to disk
    # Permission check before writing files
    for entry in files:
        path = entry.get("path", "")
        action = entry.get("action", "")

        if action in ("create", "write", "update"):
            result = check_permission("build", "write", path)
            if result == PermissionResult.DENY:
                raise PermissionError(f"Permission denied to write: {path}")
            if result == PermissionResult.ASK:
                raise PermissionRequired(
                    agent="build",
                    action="write",
                    path=path,
                    rule=".env files require user confirmation",
                )

        if action == "delete":
            result = check_permission("build", "delete", path)
            if result == PermissionResult.DENY:
                raise PermissionError(f"Permission denied to delete: {path}")

    written = write_files(files, project_workdir)
    log.info(
        "executor.files_written",
        count=len(written),
        workdir=str(project_workdir),
    )

    # Persist executor state to mid memory
    db.write_memory(
        tier="mid",
        agent="executor",
        key="last_files_generated",
        value=json.dumps(files),
    )
    db.write_memory(
        tier="mid",
        agent="executor",
        key="executor_done_at",
        value=datetime.now(timezone.utc).isoformat(),
    )
    db.write_memory(
        tier="mid", agent="executor", key="workdir", value=str(project_workdir)
    )

    g.update_node_status(node_id, NodeStatus.DONE)
    output = {
        "files": files,
        "count": len(files),
        "workdir": str(project_workdir),
        "written": written,
        "test_results": test_results,
    }
    g.store_output(node_id, output)
    return output


def _build_lsp_context(lsp: "LSPService", project_workdir: Path) -> str:
    """Build LSP code-intelligence context for the executor prompt."""
    if not lsp:
        return ""
    try:
        return lsp.build_context(max_files=20)
    except Exception as e:
        log.warning("executor.lsp_context_failed", error=str(e))
        return ""


def _build_mcp_context(mcp_config) -> str:
    if not mcp_config:
        return ""

    available_tools: list[str] = []
    for server_name, client in vars(mcp_config).get("_clients", {}).items():
        for tool in client.list_tools():
            available_tools.append(
                f"- {server_name}/{tool.name}: {tool.description}"
            )

    if not available_tools:
        return ""
    return "\n\nAvailable MCP tools:\n" + "\n".join(available_tools)


def _run_tests_with_write(workdir: Path, files: list[dict]) -> dict | None:
    """Write test files first, then run pytest. Returns structured results."""
    # Write only test files first
    test_files = [
        f for f in files if isinstance(f, dict) and "/test_" in f.get("path", "")
    ]
    write_files(test_files, workdir)

    # Run tests
    return run_tests(workdir)
