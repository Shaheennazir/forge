"""
forge.product_compiler.pipeline — Pipeline orchestrator for the Product Compiler.

Drives the discrete stage loop: Interview → Rule Compiler → Test Writer → Coder → Test Runner.
Each stage is a separate agent. The orchestrator holds state and passes typed structs.

Pipeline modes:
  NEW_PROJECT — full pipeline from Intent Document
  EDIT        — scoped edit pipeline with blast-radius containment (stubbed for now)
"""

from __future__ import annotations

import json
import structlog
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from forge.llm import create_backend, load_config

if TYPE_CHECKING:
    from forge.product_compiler.messaging import MessagingLayer

from forge.product_compiler.models import (
    ChangeIntent,
    DatabaseSchema,
    DeltaRuleSet,
    FailingTestSuite,
    ImpactSurface,
    IntegrationResult,
    IntentDocument,
    PipelineMode,
    ReviewResult,
    RuleSet,
    Stage,
    UserFlowTree,
    APIContract,
)

log = structlog.get_logger(__name__)


class PipelineStatus(Enum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_DB_APPROVAL = "awaiting_db_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class PipelineState:
    """Mutable state carried through the pipeline."""

    mode: PipelineMode = PipelineMode.NEW_PROJECT
    stage: Stage = Stage.INTERVIEW
    status: PipelineStatus = PipelineStatus.RUNNING

    # Stage outputs
    intent: Optional[IntentDocument] = None
    user_flows: Optional[UserFlowTree] = None
    contracts: list[APIContract] = field(default_factory=list)
    rule_set: Optional[RuleSet] = None
    test_suite: Optional[FailingTestSuite] = None

    # Edit-mode outputs
    change_intent: Optional[ChangeIntent] = None
    impact_surface: Optional[ImpactSurface] = None
    delta_rules: Optional[DeltaRuleSet] = None

    # Stage outputs
    db_schema: Optional[DatabaseSchema] = None
    integration_result: Optional[IntegrationResult] = None
    review_result: Optional[ReviewResult] = None

    # Working memory
    interview_history: list[dict] = field(default_factory=list)
    current_question: str = ""
    last_agent_output: str = ""

    # Config
    auto_approve: bool = False
    workdir: Path = Path(".")
    model_routing: dict[str, str] = field(default_factory=dict)  # agent_name → provider/model
    test_autopilot: list[str] = field(default_factory=list)  # pre-seeded answers for interview
    use_sandbox: bool = False  # E2B sandbox for code execution
    messaging_layer: Optional["MessagingLayer"] = field(default=None)  # NATS / stdout pub-sub

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "stage": self.stage.value,
            "status": self.status.value,
            "intent": self.intent.to_dict() if self.intent else None,
            "db_schema": self.db_schema.to_dict() if self.db_schema else None,
            "rule_set_approved": bool(self.rule_set and self.rule_set.approved_at),
            "test_suite": self.test_suite.to_dict() if self.test_suite else None,
            "integration_result": {
                "project_dir": self.integration_result.project_dir,
                "entry_point": self.integration_result.entry_point,
                "files_generated": self.integration_result.files_generated,
                "all_tests_passed": self.integration_result.all_tests_passed,
            } if self.integration_result else None,
        }


class ProductCompilerPipeline:
    """
    Orchestrates the Product Compiler pipeline.

    Usage:
        pipeline = ProductCompilerPipeline(auto_approve=False, workdir=Path("."))
        for event in pipeline.run("build a blog"):
            print(event)  # stage changes, questions, results

    Or single-shot (for CLI):
        result = ProductCompilerPipeline(auto_approve=False).run_single("build a blog")
    """

    def __init__(
        self,
        auto_approve: bool = False,
        workdir: Path | str = Path("."),
        llm_config=None,
        model_routing: dict[str, str] | None = None,
        test_autopilot: list[str] | None = None,
        use_sandbox: bool = False,
        messaging_layer: "MessagingLayer | None" = None,
    ):
        self.auto_approve = auto_approve
        self.use_sandbox = use_sandbox
        self.workdir = Path(workdir)
        self.llm_config = llm_config
        self._state = PipelineState(
            auto_approve=auto_approve,
            use_sandbox=use_sandbox,
            workdir=self.workdir,
            model_routing=model_routing or {},
            test_autopilot=test_autopilot or [],
            messaging_layer=messaging_layer,
        )
        self._autopilot_index = 0  # index into test_autopilot answers

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, initial_prompt: str):
        """
        Generator yielding pipeline events. Use this for streaming/TUI.
        Yields dicts with 'type' and payload.
        """
        # ── Mode dispatch ──────────────────────────────────────────────────────
        if self._state.mode == PipelineMode.EDIT:
            yield from self._run_edit_pipeline(initial_prompt)
            return

        llm = self._get_llm(agent="default")

        # ── Stage 1: Interview ───────────────────────────────────────────────
        yield from self._emit("stage", {"stage": "interview", "description": "Intent Extraction Interview"})
        yield from self._run_interview(llm, initial_prompt)

        if self._state.status == PipelineStatus.FAILED:
            return

        # ── Stage 2-4: Flow + Contract + Rules ─────────────────────────────
        yield from self._emit("stage", {"stage": "user_flow", "description": "User Flow Pipeline"})
        self._state.user_flows = self._build_user_flows(llm, self._state.intent)
        yield from self._emit("output", {"stage": "user_flow", "summary": f"{len(self._state.user_flows.flows)} flows derived"})

        yield from self._emit("stage", {"stage": "software_flow", "description": "Contract Layer"})
        self._state.contracts = self._build_contracts(llm, self._state.user_flows)
        yield from self._emit("output", {"stage": "software_flow", "summary": f"{len(self._state.contracts)} contracts defined"})

        yield from self._emit("stage", {"stage": "rule_compilation", "description": "Formal Rule Compilation"})
        self._state.rule_set = self._compile_rules(llm, self._state.contracts, self._state.user_flows)

        # ── Human Gate 1: Rule Set Approval ──────────────────────────────────
        yield from self._gate_rule_approval()

        if self._state.status == PipelineStatus.REJECTED:
            log.info("pipeline.rejected_at_gate1")
            return
        if self._state.status == PipelineStatus.FAILED:
            return

        # ── Stage 5: Database Design ─────────────────────────────────────────
        yield from self._emit("stage", {"stage": "database_design", "description": "Database Design"})
        self._state.db_schema = yield from self._run_schema_designer(llm, self._state.intent)

        # ── Human Gate 2: DB Schema Approval ────────────────────────────────
        yield from self._gate_db_approval()

        if self._state.status == PipelineStatus.REJECTED:
            return
        if self._state.status == PipelineStatus.FAILED:
            return

        # ── Stage 6: Test Generation ─────────────────────────────────────────
        yield from self._emit("stage", {"stage": "test_generation", "description": "Test Generation"})
        test_writer_llm = self._get_llm(agent="test_writer")
        self._state.test_suite = self._generate_tests(test_writer_llm, self._state.rule_set)
        yield from self._emit("output", {
            "stage": "test_generation",
            "summary": f"{len(self._state.test_suite.test_cases)} test cases generated",
        })

        # ── Stage 7: Implementation ───────────────────────────────────────────────
        yield from self._emit("stage", {"stage": "implementation", "description": "Implementation"})
        sandbox = self._create_sandbox()
        if sandbox:
            sandbox.__enter__()
        coder_llm = self._get_llm(agent="coder")
        coder_result = yield from self._run_coder(coder_llm, self._state.test_suite, self._state.rule_set, sandbox=sandbox)
        yield from self._emit("output", {
            "stage": "implementation",
            "files": coder_result.get("files_written", []),
            "sandbox_used": sandbox.enabled if sandbox else False,
        })

        # ── Stage 8: Integration ─────────────────────────────────────────────
        yield from self._emit("stage", {"stage": "integration", "description": "Project Integration"})
        self._state.integration_result = self._run_integrator(
            self._state.rule_set,
            self._state.test_suite,
            coder_result,
            self._state.db_schema,
            sandbox=sandbox,
        )
        if sandbox:
            sandbox.__exit__(None, None, None)
        yield from self._emit("output", {
            "stage": "integration",
            "files": self._state.integration_result.files_generated,
            "all_passed": self._state.integration_result.all_tests_passed,
        })

        # ── Stage 9: Review ──────────────────────────────────────────────────
        yield from self._emit("stage", {"stage": "review", "description": "Rule Compliance Review"})
        reviewer_llm = self._get_llm(agent="reviewer")
        self._state.review_result = self._run_reviewer(reviewer_llm, self._state.rule_set, self.workdir)
        yield from self._emit("output", {
            "stage": "review",
            "passed": self._state.review_result.passed,
            "violations": len(self._state.review_result.rules_violated),
            "issues": self._state.review_result.issues,
        })

        self._state.status = PipelineStatus.COMPLETE
        yield from self._emit("complete", {"state": self._state.to_dict()})

    def run_single(self, initial_prompt: str) -> dict:
        """Blocking single-shot run. Accumulates events and returns final state."""
        events = list(self.run(initial_prompt))
        return {
            "events": events,
            "state": self._state.to_dict(),
            "status": self._state.status.value,
        }

    # ── Edit Pipeline (Layer 3) ───────────────────────────────────────────────

    def _run_edit_pipeline(self, initial_prompt: str):
        """
        Edit pipeline: scoped change with blast-radius containment.

        Stages:
          1. ImpactAnalystAgent   → ImpactSurface
          2. RuleExtractorAgent   → list[Rule]
          3. DeltaCompilerAgent   → DeltaRuleSet
          4. BlastCheckerAgent    → bool (contained?)
          5. TestDeltaWriterAgent → FailingTestSuite
          6. CoderAgent           → green tests
        """
        raise NotImplementedError("Edit pipeline (Layer 3) not yet implemented")

    # ── Stage Implementations ─────────────────────────────────────────────────

    def _run_interview(self, llm, initial_prompt: str):
        """Discrete turn loop: user answers → Interviewer responds → repeat."""
        from forge.product_compiler.agents.interviewer import InterviewerAgent

        self._state.intent = IntentDocument()
        interviewer = InterviewerAgent(llm)
        history = []

        # Seed with the initial product idea
        history.append({"role": "user", "content": initial_prompt})
        self._autopilot_index = 0

        while True:
            yield from self._emit("thinking", {"agent": "interviewer", "stage": "interview"})
            output = interviewer.run(history)

            if output.done:
                self._state.intent = output.intent
                self._state.intent.interview_log = history
                yield from self._emit("output", {
                    "stage": "interview",
                    "intent_id": self._state.intent.id,
                    "entities": len(self._state.intent.entities),
                    "actions": len(self._state.intent.actions),
                })
                break

            # More questions needed — yield the question dict and pause
            yield from self._emit("question", {"question": output.question, "history_len": len(history)})

            # Autopilot mode: use pre-seeded answers; otherwise read from stdin
            if self._state.test_autopilot and self._autopilot_index < len(self._state.test_autopilot):
                answer = self._state.test_autopilot[self._autopilot_index]
                self._autopilot_index += 1
                yield from self._emit("output", {"stage": "autopilot", "answer": answer})
            else:
                answer = self._read_line(f"\n❓ {output.question}\n> ")

            if not answer:
                answer = "(no answer provided)"

            history.append({"role": "user", "content": answer})
            history.append({"role": "assistant", "content": output.question})

        self._state.stage = Stage.USER_FLOW

    def feed_answer(self, answer: str):
        """Feed an interview answer (for TUI / interactive use)."""
        self._state.interview_history.append({"role": "user", "content": answer})

    def _read_line(self, prompt: str) -> str:
        """Read a line from stdin. Override in tests."""
        return input(prompt).strip()

    def _build_user_flows(self, llm, intent: IntentDocument) -> UserFlowTree:
        """Stage 2: Derive user flow tree from Intent Document."""
        from forge.product_compiler.agents.flow_designer import FlowDesignerAgent

        designer = FlowDesignerAgent(llm)
        return designer.run(intent)

    def _build_contracts(self, llm, flows: UserFlowTree) -> list[APIContract]:
        """Stage 3: Map every user action to an API contract."""
        from forge.product_compiler.agents.contract_writer import ContractWriterAgent

        writer = ContractWriterAgent(llm)
        return writer.run(flows)

    def _compile_rules(self, llm, contracts: list[APIContract], flows: UserFlowTree) -> RuleSet:
        """Stage 4: Reduce contracts + flows to IF/THEN rules."""
        from forge.product_compiler.agents.rule_compiler import RuleCompilerAgent

        compiler = RuleCompilerAgent(llm)
        return compiler.run(contracts, flows)

    def _run_schema_designer(self, llm, intent: IntentDocument) -> DatabaseSchema:
        """Stage 5: Design database schema from Intent Document."""
        from forge.product_compiler.agents.schema_designer import SchemaDesignerAgent

        designer = SchemaDesignerAgent(llm)
        schema = designer.run(intent)
        yield from self._emit("output", {
            "stage": "database_design",
            "tables": len(schema.tables),
            "sql_preview": schema.raw_sql[:500] if schema.raw_sql else "(no SQL generated)",
        })
        return schema

    def _run_coder(self, llm, test_suite: FailingTestSuite, rule_set: RuleSet, sandbox=None):
        """Stage 7: Make tests green. Yields progress events."""
        from forge.product_compiler.agents.coder import CoderAgent

        coder = CoderAgent(llm, workdir=self.workdir, sandbox=sandbox)
        result = yield from coder.run(test_suite, rule_set)
        return result

    def _run_integrator(
        self,
        rule_set: RuleSet,
        test_suite: FailingTestSuite,
        coder_result: dict,
        schema: DatabaseSchema | None,
        sandbox=None,
    ) -> IntegrationResult:
        """Stage 8: Assemble generated code into a runnable project."""
        from forge.product_compiler.agents.integrator import IntegratorAgent

        integrator = IntegratorAgent(workdir=self.workdir, sandbox=sandbox)
        app_file = coder_result.get("files_written", ["app.py"])[-1]
        return integrator.run(rule_set, test_suite, app_file, schema)

    def _run_reviewer(self, llm, rule_set: RuleSet, workdir: Path) -> ReviewResult:
        """Stage 9: Verify implementation against rules."""
        from forge.product_compiler.agents.reviewer import ReviewerAgent

        reviewer = ReviewerAgent(llm)
        return reviewer.run(rule_set, workdir)

    def _generate_tests(self, llm, rule_set: RuleSet) -> FailingTestSuite:
        """Stage 6: Generate failing tests from rule set."""
        from forge.product_compiler.agents.test_writer import TestWriterAgent

        writer = TestWriterAgent(llm)
        return writer.run(rule_set)

    # ── Gate Logic ─────────────────────────────────────────────────────────────

    def _gate_rule_approval(self):
        """Human Gate 1: print rule set, wait for y/n or auto-approve."""
        self._state.status = PipelineStatus.AWAITING_APPROVAL

        rule_text = self._format_rule_set(self._state.rule_set)
        yield from self._emit("gate", {
            "gate": 1,
            "name": "Rule Compilation Approval",
            "rule_count": len(self._state.rule_set.rules),
            "rules": rule_text,
        })

        if self.auto_approve:
            self._state.rule_set.approved_at = self._iso_now()
            self._state.rule_set.approved_by = "auto"
            self._state.status = PipelineStatus.APPROVED
            yield from self._emit("approved", {"gate": 1, "mode": "auto", "rule_count": len(self._state.rule_set.rules)})
        else:
            yield from self._emit("awaiting_input", {"prompt": "Approve rule set? [y/n]"})

        self._state.stage = Stage.RULE_COMPILATION

    def _gate_db_approval(self):
        """Human Gate 2: print DB schema, wait for y/n or auto-approve."""
        self._state.status = PipelineStatus.AWAITING_DB_APPROVAL

        schema = self._state.db_schema
        table_count = len(schema.tables) if schema else 0

        yield from self._emit("gate", {
            "gate": 2,
            "name": "Database Schema Approval",
            "table_count": table_count,
            "sql": (schema.raw_sql[:1000] if schema and schema.raw_sql else "(no SQL)") + "...",
        })

        if self.auto_approve:
            if schema:
                schema.approved_at = self._iso_now()
                schema.approved_by = "auto"
            self._state.status = PipelineStatus.APPROVED
            yield from self._emit("approved", {"gate": 2, "mode": "auto", "table_count": table_count})
        else:
            yield from self._emit("awaiting_input", {"prompt": "Approve DB schema? [y/n]"})

        self._state.stage = Stage.DATABASE_DESIGN

    def _format_rule_set(self, rule_set: RuleSet) -> list[str]:
        """Format rules for display."""
        lines = []
        for r in rule_set.rules:
            line = f"IF {r.condition} THEN {r.action}"
            if r.else_action:
                line += f" ELSE {r.else_action}"
            lines.append(line)
        return lines

    def approve(self):
        """Programmatic approval (for --auto-approve or TUI button)."""
        if self._state.status == PipelineStatus.AWAITING_APPROVAL:
            self._state.rule_set.approved_at = self._iso_now()
            self._state.rule_set.approved_by = "user"
            self._state.status = PipelineStatus.APPROVED
        elif self._state.status == PipelineStatus.AWAITING_DB_APPROVAL:
            if self._state.db_schema:
                self._state.db_schema.approved_at = self._iso_now()
                self._state.db_schema.approved_by = "user"
            self._state.status = PipelineStatus.APPROVED

    def reject(self, reason: str = ""):
        """Programmatic rejection (for TUI)."""
        if self._state.status in (PipelineStatus.AWAITING_APPROVAL, PipelineStatus.AWAITING_DB_APPROVAL):
            self._state.status = PipelineStatus.REJECTED
            yield from self._emit("rejected", {"reason": reason})

    def _create_sandbox(self) -> "E2BSandbox | None":
        """Create an E2B sandbox if use_sandbox is enabled."""
        if not self.use_sandbox:
            return None
        from forge.product_compiler.sandbox import E2BSandbox
        return E2BSandbox(enabled=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_llm(self, agent: str = "default"):
        """
        Get an LLM backend. If model_routing is configured for this agent,
        create a backend with the specified model. Otherwise use default config.
        """
        if self.llm_config:
            return create_backend(self.llm_config)

        route = self._state.model_routing.get(agent)
        if route:
            # route format: "provider/model" or just "model" (uses default provider)
            from forge.llm import LLMConfig
            if "/" in route:
                provider, model = route.split("/", 1)
                cfg = LLMConfig(provider=provider, model=model)
            else:
                cfg = LLMConfig(provider="minimax", model=route)
            return create_backend(cfg)

        return create_backend()

    def _emit(self, event_type: str, payload: dict):
        """Yield a pipeline event and publish it through the messaging layer."""
        event = {"type": event_type, "payload": payload}
        if self._state.messaging_layer is not None:
            self._state.messaging_layer.publish_from_emit(event)
        yield event

    @staticmethod
    def _iso_now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
