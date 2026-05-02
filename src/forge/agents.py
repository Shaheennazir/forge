"""
forge.agents — Subagent management.

SubagentManager spawns hierarchical worker processes that share project memory.
Each worker receives: task description, spec context, relevant skills.
"""

from __future__ import annotations
import json
import subprocess
import uuid
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from forge.db import ForgeDB

log = structlog.get_logger(__name__)


@dataclass
class SubagentResult:
    run_id: str
    agent_type: str
    status: str  # running | done | failed | escalated
    output: Optional[dict] = None
    failure_reason: Optional[str] = None


class SubagentManager:
    """
    Manages hierarchical subagent delegation.

    Subagents are lightweight workers spawned as subprocesses that receive:
      - task description
      - project spec (SPEC.md)
      - relevant skills
      - workdir

    They return a JSON result on stdout. SubagentManager parses it and
    records the run in the subagent_runs DB table.
    """

    def __init__(self, db: ForgeDB, workdir: Path, project_id: str):
        self.db = db
        self.workdir = workdir
        self.project_id = project_id
        self._active_runs: dict[str, SubagentResult] = {}

    def spawn(
        self,
        task: str,
        agent_type: str,
        task_id: str,
        context: dict,
    ) -> SubagentResult:
        """
        Spawn a subagent worker for a specific task.

        Args:
            task: Task description for the subagent
            agent_type: Skill name or agent category (matched against skill registry)
            task_id: Graph task ID this subagent is fulfilling
            context: Dict with project_name, spec_md, spec_version, existing_files

        Returns:
            SubagentResult with run_id, status, output
        """
        run_id = f"sub_{uuid.uuid4().hex[:12]}"
        self.db.create_subagent_run(run_id, task_id, agent_type)

        self._active_runs[run_id] = SubagentResult(
            run_id=run_id,
            agent_type=agent_type,
            status="running",
        )

        system_prompt = self._build_system_prompt(agent_type, task, context)
        user_prompt = self._build_user_prompt(task, context)

        try:
            raw = self._run_agent_process(system_prompt, user_prompt)
            result = self._parse_output(raw, agent_type)
            status = "done" if result.get("action") != "error" else "failed"
            self.db.finish_subagent_run(
                run_id,
                status,
                output_summary=json.dumps(result),
            )
            sub = self._active_runs[run_id]
            sub.status = status
            sub.output = result
            return sub
        except Exception as e:
            log.error("subagent.spawn_failed", run_id=run_id, error=str(e))
            self.db.finish_subagent_run(run_id, "failed", failure_reason=str(e))
            sub = self._active_runs[run_id]
            sub.status = "failed"
            sub.failure_reason = str(e)
            return sub

    def get_incomplete_runs(self) -> list[SubagentResult]:
        """Return all runs that are still 'running' (for restore_from_db)."""
        return [r for r in self._active_runs.values() if r.status == "running"]

    # ── Private ─────────────────────────────────────────────────────────────────

    def _build_system_prompt(
        self, agent_type: str, task: str, context: dict
    ) -> str:
        """Build the system prompt with skills and project context."""
        skill_md = ""
        self_md = ""

        # Try to load skill content
        try:
            from forge.skills import SkillRegistry

            registry = SkillRegistry()
            matched = registry.match(agent_type)
            for s in matched:
                skill_path = s.path / "SKILL.md"
                if skill_path.exists():
                    skill_md = skill_path.read_text()
                    break
        except Exception:
            pass

        # Build self-description from context
        project_files = context.get("existing_files", [])
        self_md = f"""You are a {agent_type} subagent in a directed-graph coding system (forge).
Your role: {task}

Project directory: {self.workdir}

## Project Spec
{context.get('spec_md', 'No spec available')}

## Existing Files
{chr(10).join(f'- {f}' for f in project_files) if project_files else '(none)'}

## Your Skill{skill_md}

## Instructions
1. Read the spec carefully
2. Write tests FIRST (TDD) before implementation
3. Write only files described in the spec
4. Stay within the project directory
5. Output JSON result: {{"action": "done"|"error", "files_created": ["..."], "summary": "..."}}
"""
        return self_md

    def _build_user_prompt(self, task: str, context: dict) -> str:
        """Build the user prompt for a subagent."""
        return f"""Task: {task}

Project: {context.get('project_name', 'unknown')}
Spec version: {context.get('spec_version', '?')}

Execute your assigned task. Write all code to: {self.workdir}

Output JSON: {{"action": "done", "files_created": ["..."], "summary": "..."}}
"""

    def _run_agent_process(self, system_prompt: str, user_prompt: str) -> str:
        """Run an agent LLM call as a subprocess. Returns stdout."""
        # Re-use the forge LLM backend to call the LLM
        from forge.llm import create_backend, load_config

        cfg = load_config()
        llm = create_backend(cfg)

        response = llm.complete(
            prompt=user_prompt,
            system=system_prompt,
            max_tokens=4096,
            temperature=0.3,
        )
        return response

    def _parse_output(self, raw: str, agent_type: str) -> dict:
        """Parse LLM output into structured result."""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {
            "action": "done",
            "raw": raw[:500],
            "agent_type": agent_type,
        }
