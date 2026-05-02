"""
forge.graph — Directed graph execution engine.

Phase 1 (v0.1): Orchestrator + SpecGen + Executor + ReviewGate + Subagents.
Each phase is a graph node. Transitions are explicit edges.
Failure lanes are explicit: any node can emit blocked|failed → orchestrator decides.
"""

from __future__ import annotations
import uuid
import json
import os
import shutil
import subprocess
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from forge.db import ForgeDB

from forge.db import ForgeDB, Task
from forge.llm import LLMBackend, create_backend
from forge.skills import SkillRegistry, Skill
from forge.mcp import MCPConfig, MCPClient, MCPTool

log = structlog.get_logger(__name__)


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


# ── Graph nodes ─────────────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id: str
    label: str
    status: NodeStatus = NodeStatus.PENDING
    failure_reason: Optional[str] = None
    output: Optional[dict] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GraphEdge:
    id: str
    from_node: str
    to_node: str
    edge_type: str = "normal"


class ForgeGraph:
    """
    Directed graph with explicit failure lanes.
    Nodes: orchestrator, spec_gen, executor, review_gate
    Edges: define all possible transitions.
    """

    def __init__(self, project_id: str, db: ForgeDB, llm: Optional[LLMBackend] = None):
        self.project_id = project_id
        self.db = db
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.llm = llm

    def add_node(self, node_id: str, label: str) -> GraphNode:
        node = GraphNode(id=node_id, label=label)
        self.nodes[node_id] = node
        return node

    def add_edge(self, from_id: str, to_id: str, edge_type: str = "normal") -> GraphEdge:
        edge = GraphEdge(
            id=f"edge_{uuid.uuid4().hex[:8]}",
            from_node=from_id, to_node=to_id, edge_type=edge_type,
        )
        self.edges.append(edge)
        return edge

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_outgoing_edges(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.from_node == node_id]

    def get_incoming_edges(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.to_node == node_id]

    def successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self.get_outgoing_edges(node_id)]

    def build_initial_graph(self, spec_version: int) -> "ForgeGraph":
        """Build the v0.1 graph: orchestrator → spec_gen → executor → review_gate."""
        g = self

        g.add_node("orchestrator", "Orchestrator")
        g.add_node("spec_gen", "Spec Generator")
        g.add_node("executor", "Executor")
        g.add_node("review_gate", "Review Gate")

        g.add_edge("orchestrator", "spec_gen", "normal")
        g.add_edge("spec_gen", "executor", "normal")
        g.add_edge("executor", "review_gate", "normal")

        g.add_edge("review_gate", "executor", "review_fail")
        g.add_edge("review_gate", "orchestrator", "review_pass")

        g.add_edge("orchestrator", "orchestrator", "escalate")
        g.add_edge("spec_gen", "orchestrator", "failed")
        g.add_edge("executor", "orchestrator", "failed")
        g.add_edge("review_gate", "orchestrator", "escalate")

        if not self.db.latest_spec_version():
            self.db.save_spec_version(spec_version, "", "# Initial stub spec", "")

        for node in g.nodes.values():
            self.db.create_task(
                task_id=node.id, spec_version=spec_version, label=node.label
            )
        for edge in g.edges:
            self.db.add_task_edge(
                edge_id=edge.id, from_task=edge.from_node, to_task=edge.to_node, edge_type=edge.edge_type
            )

        return g

    def restore_from_db(self):
        """Load nodes and edges from DB (for continue_cmd)."""
        tasks, db_edges = self.db.get_task_graph()
        for t in tasks:
            node = self.add_node(t.id, t.label)
            node.status = NodeStatus(t.status) if t.status in ("pending","running","done","blocked","failed") else NodeStatus.PENDING
            node.failure_reason = t.failure_reason
        for e in db_edges:
            edge = GraphEdge(id=e["id"], from_node=e["from_task"], to_node=e["to_task"], edge_type=e["edge_type"])
            self.edges.append(edge)

    def update_node_status(self, node_id: str, status: NodeStatus, failure_reason: Optional[str] = None):
        node = self.nodes.get(node_id)
        if node:
            node.status = status
            node.failure_reason = failure_reason
        self.db.set_task_status(node_id, status.value, failure_reason)

    def store_output(self, node_id: str, output: dict):
        node = self.nodes.get(node_id)
        if node:
            node.output = output


# ── Subagent system ────────────────────────────────────────────────────────────

@dataclass
class SubagentResult:
    run_id: str
    agent_type: str
    status: str
    output: Optional[dict] = None
    failure_reason: Optional[str] = None


class SubagentManager:
    """
    Manages hierarchical subagent delegation.

    Subagents are lightweight workers that receive:
    - Their task description
    - Relevant spec context
    - Relevant skill content
    - Access to MCP tools

    Results are stored in DB with failure tracking.
    """

    def __init__(
        self,
        db: ForgeDB,
        llm: LLMBackend,
        workdir: Path,
        skills: list[Skill],
        mcp_config: MCPConfig,
        parent_task_id: str,
    ):
        self.db = db
        self.llm = llm
        self.workdir = workdir
        self.skills = skills
        self.mcp_config = mcp_config
        self.parent_task_id = parent_task_id
        self._active_runs: dict[str, MCPClient] = {}

    def run(self, agent_type: str, task_description: str, context: dict) -> SubagentResult:
        """
        Run a subagent synchronously and return the result.
        agent_type maps to a skill or a built-in prompt template.
        """
        run_id = f"sub_{uuid.uuid4().hex[:8]}"

        skill = self._find_skill(agent_type)
        self.db.create_subagent_run(run_id, self.parent_task_id, agent_type)

        system_prompt = self._build_system_prompt(agent_type, task_description, context, skill)
        user_prompt = self._build_user_prompt(task_description, context)

        try:
            if self.llm:
                raw = self.llm.complete(
                    prompt=user_prompt,
                    system=system_prompt,
                    max_tokens=8192,
                    temperature=0.3,
                )
                result_data = self._parse_output(raw, agent_type)
                status = "done"
                failure_reason = None
            else:
                result_data = {"action": "stub", "raw": f"[stub] {agent_type}: {task_description}"}
                status = "done"
                failure_reason = None

            output_summary = json.dumps(result_data)[:500]
            self.db.finish_subagent_run(run_id, status, failure_reason, output_summary)

            return SubagentResult(
                run_id=run_id,
                agent_type=agent_type,
                status=status,
                output=result_data,
                failure_reason=failure_reason,
            )

        except Exception as e:
            log.error("subagent.error", agent_type=agent_type, error=str(e))
            self.db.finish_subagent_run(run_id, "failed", str(e), None)
            return SubagentResult(
                run_id=run_id,
                agent_type=agent_type,
                status="failed",
                output=None,
                failure_reason=str(e),
            )

    def _find_skill(self, agent_type: str) -> Optional[Skill]:
        """Find a matching skill by name or category."""
        for s in self.skills:
            if s.name == agent_type:
                return s
            if s.category and agent_type.startswith(s.category):
                return s
        return None

    def _build_system_prompt(self, agent_type: str, task: str, context: dict, skill: Optional[Skill]) -> str:
        """Build the system prompt for a subagent."""
        skill_content = f"\n\n## Skill: {skill.name}\n{skill.description}\n" if skill else ""

        skill_md = ""
        if skill:
            try:
                from forge.skills import load_skill_content
                skill_md = load_skill_content(skill)
            except Exception:
                pass

        spec_md = context.get("spec_md", "")
        project_files = context.get("existing_files", [])

        return f"""You are a {agent_type} subagent in a directed-graph coding system (forge).
Your role: {task}

You must work within the project directory: {self.workdir}

## Project Spec
{self_md}

## Existing Files
{chr(10).join(f'- {f}' for f in project_files) if project_files else '(none)'}

## Your Skill{skill_content}
{skill_md}

## Instructions
1. Read the spec carefully
2. Write tests FIRST (TDD) before implementation
3. Write only files described in the spec
4. Stay within the project directory
5. When done, output your result as JSON: {{"action": "done", "files_created": ["..."], "summary": "..."}}
"""

    def _build_user_prompt(self, task: str, context: dict) -> str:
        """Build the user prompt for a subagent."""
        return f"""Task: {task}

Project: {context.get('project_name', 'unknown')}
Spec version: {context.get('spec_version', '?')}

Execute your assigned task. Write all code to: {self.workdir}

When complete, output JSON: {{"action": "done", "files_created": ["..."], "summary": "..."}}
"""

    def _parse_output(self, raw: str, agent_type: str) -> dict:
        """Parse LLM output into structured result."""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"action": "done", "raw": raw[:500], "agent_type": agent_type}


# ── Skill injection helper ────────────────────────────────────────────────────

def inject_skills_into_context(
    task_type: str,
    skill_registry: SkillRegistry,
    llm: Optional[LLMBackend],
) -> tuple[list[Skill], list[str]]:
    """
    Match and load relevant skills for a task type.
    Returns (matched_skills, skill_summaries_for_prompt).
    """
    matched = skill_registry.match(task_type)
    summaries = []
    for s in matched:
        summaries.append(f"[skill:{s.name}] {s.description}")
    return matched, summaries


# ── System prompts ─────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM = """You are the Orchestrator in a directed-graph agentic coding system (forge).
You receive a user prompt and must decide the next action.

Available actions:
- generate_spec: User has a vague idea, produce a concrete SPEC.md first
- executor: User has a spec, generate code files
- done: All work is complete
- delegate: Offload a specific subtask to a subagent

Respond ONLY with a JSON object: {"action": "generate_spec"|"executor"|"done"|"delegate", "reason": "...", "subagent_type": "..."}
"""


SPEC_GEN_SYSTEM = """You are the Spec Generator in a directed-graph agentic coding system (forge).
Given a user prompt, produce a complete, detailed SPEC.md.
Include: project name, overview, features list, file tree, acceptance criteria, tech stack.
Use markdown. Output ONLY the SPEC.md content.

IMPORTANT: Ask clarifying questions FIRST if the request is vague.
Ask at most 3 focused questions. Wait for answers before generating the spec.
"""


EXECUTOR_SYSTEM = """You are the Executor in a directed-graph agentic coding system (forge).
You have a SPEC.md. Generate all the code files described.

IMPORTANT — TDD-FIRST WORKFLOW:
1. Write tests BEFORE implementation code
2. Then write implementation to make tests pass
3. Verify all tests pass

For each file output a JSON entry: {"path": "...", "action": "create"|"update"|"delete", "content": "..."}.
Only include files that need to be created/updated.
"""


REVIEW_SYSTEM = """You are the Review Gate in a directed-graph agentic coding system (forge).
Review the generated files against the spec. Check:
1. All spec features are implemented
2. Code is syntactically correct
3. Files match the described file tree
4. Tests exist and cover the implementation

Respond ONLY with JSON: {"pass": true|false, "reason": "...", "issues": ["..."]}
"""


# ── Phase runners ─────────────────────────────────────────────────────────────

ShortTermMemory = dict[str, Any]


def run_orchestrator(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
    continue_session: bool = False,
    **extra,
) -> dict:
    """Orchestrator: decides next action based on prompt + session state."""
    log.info("orchestrator.run", prompt=prompt[:80], is_continue=continue_session)

    if continue_session:
        last_state_json = db.read_memory(tier="episodic", agent="orchestrator", key="last_session")
        if last_state_json:
            last_state = json.loads(last_state_json)
            log.info("orchestrator.resume", from_state=last_state.get("last_node"))
            # Restore context from last session
            if last_state.get("last_node"):
                stm["resume_from"] = last_state["last_node"]

    g.update_node_status(node_id, NodeStatus.DONE)

    if g.llm:
        raw = g.llm.complete(
            prompt=f"User request: {prompt}\n\nWhat should happen next?",
            system=ORCHESTRATOR_SYSTEM,
            max_tokens=512,
            temperature=0.2,
        )
        try:
            decision = json.loads(raw)
            action = decision.get("action", "generate_spec")
        except json.JSONDecodeError:
            has_spec = bool(db.latest_spec_version())
            action = "generate_spec" if not has_spec else "executor"
        stm["orchestrator_action"] = action
        g.store_output(node_id, {"prompt": prompt, "continue": continue_session, "action": action})
        return {"prompt": prompt, "continue": continue_session, "action": action}

    g.store_output(node_id, {"prompt": prompt, "continue": continue_session})
    return {"prompt": prompt, "continue": continue_session}


def run_spec_gen(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
    skill_registry: Optional[SkillRegistry] = None,
    **extra,
) -> dict:
    """Spec Generator: produces SPEC.md with optional skill context."""
    log.info("spec_gen.run", prompt=prompt[:100])

    latest_ver = db.latest_spec_version()
    existing_spec_md = None
    existing_version = None
    if latest_ver:
        sv = db.get_spec_version(latest_ver)
        if sv:
            existing_spec_md = sv.spec_md
            existing_version = latest_ver

    spec_md = None
    version = (existing_version or 0) + 1

    if g.llm:
        # Inject relevant skills for spec generation
        system = SPEC_GEN_SYSTEM
        skill_summaries = []
        if skill_registry:
            matched, skill_summaries = inject_skills_into_context("planning", skill_registry, g.llm)
            if skill_summaries:
                system = SPEC_GEN_SYSTEM + "\n\nRelevant skills:\n" + "\n".join(skill_summaries)

        llm_prompt = prompt
        if existing_spec_md:
            llm_prompt = (
                f"Update this existing SPEC.md based on the new request.\n\n"
                f"Current spec:\n{existing_spec_md}\n\n"
                f"New request: {prompt}"
            )

        spec_md = g.llm.complete(
            prompt=llm_prompt,
            system=system,
            max_tokens=4096,
            temperature=0.3,
        )
        changelog = _build_changelog(prompt, spec_md, existing_spec_md)
    else:
        spec_md = _generate_spec_md(prompt, existing_spec_md)
        changelog = _build_changelog(prompt, spec_md, existing_spec_md)

    sv = db.save_spec_version(version, prompt, spec_md, changelog)
    db.write_memory(tier="mid", agent="spec_gen", key="current_spec_version", value=str(version))
    db.write_memory(tier="mid", agent="spec_gen", key="current_spec_md", value=spec_md)

    g.update_node_status(node_id, NodeStatus.DONE)
    output = {"spec_version": version, "spec_md": spec_md}
    g.store_output(node_id, output)
    return output


def run_executor(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    spec_md: str,
    workdir: Optional[Path] = None,
    skill_registry: Optional[SkillRegistry] = None,
    mcp_config: Optional[MCPConfig] = None,
    **extra,
) -> dict:
    """
    Executor: reads SPEC.md, generates code/tests (TDD-first), writes files to disk.

    TDD CYCLE:
    1. Ask LLM to generate tests first
    2. Ask LLM to generate implementation
    3. Run the tests via subprocess
    4. Report pass/fail to Review Gate
    """
    log.info("executor.run", spec_md=spec_md[:80])

    project_workdir = workdir or (Path.home() / ".forge" / "workspace" / g.project_id)
    project_workdir.mkdir(parents=True, exist_ok=True)

    matched_skills = []
    if skill_registry:
        matched_skills, _ = inject_skills_into_context("code", skill_registry, g.llm)
    matched_skills.extend(skill_registry.match("tdd") if skill_registry else [])

    system = EXECUTOR_SYSTEM
    if matched_skills:
        skill_notes = "\n".join(f"- [{s.name}] {s.description}" for s in matched_skills)
        system = EXECUTOR_SYSTEM + f"\n\nRelevant skills loaded:\n{skill_notes}"

    # MCP tools
    mcp_tool_context = ""
    if mcp_config:
        available_tools = []
        for server_name, client in vars(mcp_config).get("_clients", {}).items():
            for tool in client.list_tools():
                available_tools.append(f"- {server_name}/{tool.name}: {tool.description}")
        if available_tools:
            mcp_tool_context = "\n\nAvailable MCP tools:\n" + "\n".join(available_tools)

    files = []

    if g.llm:
        # Phase 1: TDD — ask for tests first
        tdd_prompt = f"""TDD Phase 1: Write tests FIRST.

SPEC.md:
{spec_md}

{mcp_tool_context}

Generate test files as JSON: [{{"path": "tests/test_...", "action": "create", "content": "..."}}]
Write tests for every function/class described in the spec.
"""
        raw_tests = g.llm.complete(
            prompt=tdd_prompt,
            system=system,
            max_tokens=8192,
            temperature=0.2,
        )
        try:
            test_files = json.loads(raw_tests)
            if isinstance(test_files, dict):
                test_files = [test_files]
        except json.JSONDecodeError:
            test_files = []
        files.extend([f for f in test_files if isinstance(f, dict)])

        # Phase 2: Implementation
        test_file_list = "\n".join(ff.get("path", "?") for ff in test_files if isinstance(ff, dict) and "path" in ff)
        impl_prompt = f"""TDD Phase 2: Write implementation.

SPEC.md:
{spec_md}

Tests written ({len(test_files)} files):
{test_file_list}

{mcp_tool_context}

Generate implementation files as JSON: [{{"path": "...", "action": "create", "content": "..."}}]
"""
        raw_impl = g.llm.complete(
            prompt=impl_prompt,
            system=system,
            max_tokens=8192,
            temperature=0.2,
        )
        try:
            impl_files = json.loads(raw_impl)
            if isinstance(impl_files, dict):
                impl_files = [impl_files]
        except json.JSONDecodeError:
            impl_files = []
        files.extend([f for f in impl_files if isinstance(f, dict)])

        # Phase 3: Run tests if pytest is available
        test_results = _run_tests(project_workdir, files)

    else:
        files = _generate_from_spec(spec_md)
        test_results = None

    # Write all files to disk
    written = _write_files(files, project_workdir)
    log.info("executor.files_written", count=len(written), workdir=str(project_workdir))

    db.write_memory(tier="mid", agent="executor", key="last_files_generated", value=json.dumps(files))
    db.write_memory(tier="mid", agent="executor", key="executor_done_at", value=datetime.now(timezone.utc).isoformat())
    db.write_memory(tier="mid", agent="executor", key="workdir", value=str(project_workdir))

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


def run_review_gate(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    executor_output: dict,
    **extra,
) -> dict:
    """Review Gate: synchronous checkpoint. Returns pass or fail decision."""
    log.info("review_gate.run", files=executor_output.get("count", 0) if executor_output else 0)

    spec_md = stm.get("spec_md", "")
    files = executor_output.get("files", []) if executor_output else []
    test_results = executor_output.get("test_results")

    if test_results and test_results.get("failed", 0) > 0:
        decision = {
            "pass": False,
            "reason": f"Tests failed: {test_results['failed']} of {test_results['total']}",
            "test_failures": test_results.get("failures", []),
        }
    elif g.llm and files:
        files_list = "\n".join(f"- {ff.get('path', '?')}: {len(ff.get('content', ''))} chars" for ff in files if isinstance(ff, dict))
        raw = g.llm.complete(
            prompt=f"SPEC.md:\n{spec_md}\n\nGenerated files:\n{files_list}",
            system=REVIEW_SYSTEM,
            max_tokens=2048,
            temperature=0.1,
        )
        try:
            review = json.loads(raw)
            decision = {"pass": review.get("pass", False), "reason": review.get("reason", "")}
        except json.JSONDecodeError:
            decision = _review_spec_compliance(executor_output, spec_md)
    else:
        decision = _review_spec_compliance(executor_output, spec_md)

    g.update_node_status(node_id, NodeStatus.DONE)

    if decision["pass"]:
        output = {"decision": "pass", "reason": decision.get("reason", "")}
    else:
        g.update_node_status(node_id, NodeStatus.BLOCKED, decision.get("reason", "spec deviation"))
        output = {"decision": "fail", "reason": decision.get("reason", "")}

    g.store_output(node_id, output)
    return output


# ── LLM stub helpers ───────────────────────────────────────────────────────────

def _generate_spec_md(prompt: str, existing: Optional[str]) -> str:
    return f"""# SPEC.md — Generated

## Project
{prompt}

## Overview
Generated by forge v0.1.

## Features
- TBD based on prompt: {prompt}

## Files to Generate
- `src/` — source code
- `tests/` — test files
- `README.md`

## Acceptance Criteria
1. Code compiles/runs without errors
2. Tests pass
3. Matches spec above

## Tech Stack
- Language: Python 3.11+
- Testing: pytest
"""


def _build_changelog(prompt: str, new_spec: str, existing: Optional[str]) -> str:
    if not existing:
        return f"- v1: Initial spec generated from prompt: {prompt[:60]}"
    return f"- v1: Initial spec\n- v2: Updated from prompt: {prompt[:60]}\n"


def _generate_from_spec(spec_md: str) -> list[dict]:
    return [
        {"path": "README.md", "action": "create", "content": "# Project\n\n" + spec_md[:200]},
        {"path": "src/__init__.py", "action": "create", "content": ""},
        {"path": "tests/test_main.py", "action": "create", "content": "def test_placeholder(): assert True"},
    ]


def _review_spec_compliance(executor_output: dict, spec_md: str) -> dict:
    """Stub: review gate. Replace with real LLM validation call."""
    return {"pass": True, "reason": "v0.1 stub: auto-pass"}


def _write_files(files: list[dict], workdir: Path) -> list[str]:
    """Write files to disk. Returns list of written paths."""
    written = []
    for f in files:
        if not isinstance(f, dict):
            continue
        rel_path = f.get("path", "")
        if not rel_path:
            continue
        action = f.get("action", "create")
        content = f.get("content", "")

        target = workdir / rel_path
        if action == "delete":
            if target.exists():
                target.unlink()
                written.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(str(target))
    return written


def _run_tests(workdir: Path, files: list[dict]) -> Optional[dict]:
    """Run pytest on the project directory. Returns test results summary."""
    # Identify test files
    test_files = [f for f in files if isinstance(f, dict) and "/test_" in f.get("path", "")]
    if not test_files:
        return None

    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-q"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr

        # Parse pytest output
        failed = 0
        total = 0
        failures = []
        for line in output.splitlines():
            if " failed" in line:
                import re
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed = int(m.group(1))
            if " passed" in line:
                import re
                m = re.search(r"(\d+) passed", line)
                if m:
                    total = int(m.group(1)) + failed

        if failed == 0 and total == 0:
            import re
            m = re.search(r"(\d+) passed", output)
            total = int(m.group(1)) if m else 0

        return {"total": total, "failed": failed, "output": output[:1000], "failures": failures}

    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return {"total": 0, "failed": -1, "output": "timeout", "failures": []}
    except Exception as e:
        return {"total": 0, "failed": -1, "output": str(e), "failures": []}


# ── Graph runner ───────────────────────────────────────────────────────────────

class GraphRunner:
    """
    Executes a ForgeGraph from entry point, following edges.
    Handles retry/escalate/failure decisions at orchestrator level.
    Supports skill injection and MCP tools.
    """

    def __init__(
        self,
        g: ForgeGraph,
        db: ForgeDB,
        workdir: Optional[Path] = None,
        skill_registry: Optional[SkillRegistry] = None,
        mcp_config: Optional[MCPConfig] = None,
    ):
        self.g = g
        self.db = db
        self.stm: ShortTermMemory = {}
        self.workdir = workdir or (Path.home() / ".forge" / "workspace" / g.project_id)
        self.skill_registry = skill_registry
        self.mcp_config = mcp_config
        self._mcp_clients: dict[str, MCPClient] = {}

    def _ensure_mcp_connected(self):
        """Connect all configured MCP servers."""
        if not self.mcp_config:
            return
        for server in self.mcp_config.list_all():
            try:
                client = self.mcp_config.create_client(server.name)
                if client:
                    self._mcp_clients[server.name] = client
            except Exception as e:
                log.warning("mcp.connect_failed", server=server.name, error=str(e))

    def run(self, entry_node: str = "orchestrator", **context):
        """Run from entry node, following edges, until terminal or blocked."""
        self._ensure_mcp_connected()

        current = entry_node
        visited: set[str] = set()
        max_hops = 20

        while current and len(visited) < max_hops:
            if current in visited:
                log.warning("graph.cycle_detected", node=current, path=list(visited))
                break
            visited.add(current)

            node = self.g.get_node(current)
            if not node:
                log.error("graph.node_not_found", node=current)
                break

            log.info("graph.executing_node", node=current, label=node.label)

            # Dispatch
            if current == "orchestrator":
                output = run_orchestrator(self.g, self.db, current, self.stm, **context)
            elif current == "spec_gen":
                output = run_spec_gen(
                    self.g, self.db, current, self.stm,
                    prompt=context.get("prompt", ""),
                    skill_registry=self.skill_registry,
                )
                if "spec_md" in output:
                    context["spec_md"] = output["spec_md"]
                if "spec_version" in output:
                    context["spec_version"] = output["spec_version"]
            elif current == "executor":
                output = run_executor(
                    self.g, self.db, current, self.stm,
                    spec_md=context.get("spec_md", ""),
                    workdir=self.workdir,
                    skill_registry=self.skill_registry,
                    mcp_config=self.mcp_config,
                )
                context["executor_output"] = output
            elif current == "review_gate":
                output = run_review_gate(
                    self.g, self.db, current, self.stm,
                    executor_output=context.get("executor_output", {}),
                )
            else:
                log.warning("graph.unknown_node", node=current)
                break

            if node.status == NodeStatus.BLOCKED:
                log.warning("graph.node_blocked", node=current, reason=node.failure_reason)
                self._handle_blocked(current)
                break

            if node.status == NodeStatus.FAILED:
                log.error("graph.node_failed", node=current, reason=node.failure_reason)
                self._handle_failure(current)
                break

            # Route to next
            edges = self.g.get_outgoing_edges(current)
            if not edges:
                log.info("graph.terminal", node=current)
                break

            pass_edge = next((e for e in edges if e.edge_type == "review_pass"), None)
            fail_edge = next((e for e in edges if e.edge_type == "review_fail"), None)

            if node.output and "decision" in node.output:
                if node.output["decision"] == "pass" and pass_edge:
                    next_node = pass_edge.to_node
                    if next_node in visited:
                        log.info("graph.review_pass_terminal", node=current, next=next_node)
                        break
                    current = next_node
                    context["review_output"] = node.output
                elif node.output["decision"] == "fail" and fail_edge:
                    next_node = fail_edge.to_node
                    if next_node in visited:
                        log.warning("graph.review_fail_cycle", node=current, next=next_node)
                        break
                    current = next_node
                    context["review_output"] = node.output
                else:
                    current = edges[0].to_node
            else:
                current = edges[0].to_node

            if self.g.get_node("executor") and self.g.get_node("executor").output:
                context["executor_output"] = self.g.get_node("executor").output

        self._save_episodic_state(visited, current)
        self._mcp_cleanup()
        return {"visited": list(visited), "final_node": current, "terminated": node.status if node else None}

    def _handle_blocked(self, node_id: str):
        self.db.write_memory(
            tier="mid", agent="orchestrator",
            key=f"blocked.{node_id}",
            value=json.dumps({"node": node_id, "reason": self.g.get_node(node_id).failure_reason})
        )

    def _handle_failure(self, node_id: str):
        self.db.write_memory(
            tier="mid", agent="orchestrator",
            key=f"failed.{node_id}",
            value=json.dumps({"node": node_id, "reason": self.g.get_node(node_id).failure_reason})
        )

    def _save_episodic_state(self, visited: set, current: str):
        self.db.write_memory(
            tier="episodic", agent="orchestrator",
            key="last_session",
            value=json.dumps({
                "visited": list(visited),
                "last_node": current,
                "workdir": str(self.workdir),
                "ts": datetime.now(timezone.utc).isoformat()
            })
        )

    def _mcp_cleanup(self):
        if self.mcp_config:
            self.mcp_config.disconnect_all()
        self._mcp_clients.clear()
