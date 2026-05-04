"""
forge.product_compiler.models — Typed data models for the Product Compiler pipeline.

All pipeline stages communicate via these structs. No unstructured data passes
between stages. Every field is typed, every optional field is explicit.

Stage outputs are deterministic and serializable to JSON for caching/audit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class PipelineMode(Enum):
    """Which pipeline variant is running."""

    NEW_PROJECT = "new_project"
    EDIT = "edit"


class Stage(Enum):
    """Current active stage in the pipeline."""

    INTERVIEW = "interview"
    USER_FLOW = "user_flow"
    SOFTWARE_FLOW = "software_flow"
    RULE_COMPILATION = "rule_compilation"
    DATABASE_DESIGN = "database_design"
    TEST_GENERATION = "test_generation"
    IMPLEMENTATION = "implementation"

    # Edit-mode only stages
    IMPACT_ANALYSIS = "impact_analysis"
    RULE_EXTRACTION = "rule_extraction"
    DELTA_RULES = "delta_rules"
    BLAST_CHECK = "blast_check"

    def __str__(self) -> str:
        return self.value


# ─────────────────────────────────────────────────────────────────────────────
# Intent Document
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EntityField:
    """A single field on an entity."""

    name: str
    type: str  # string, int, float, bool, datetime, enum:[values]
    required: bool = True
    description: str = ""
    deleted_at_supported: bool = False  # soft-delete strategy for this field


@dataclass
class Entity:
    """A persistable data entity."""

    name: str
    fields: list[EntityField] = field(default_factory=list)
    description: str = ""
    deletable: bool = True  # can rows be deleted, or only soft-deleted?
    audit_log: bool = False  # track change history for this entity?


@dataclass
class Relationship:
    """Relationship between two entities."""

    from_entity: str
    to_entity: str
    relationship_type: str  # "one_to_one", "one_to_many", "many_to_many"
    foreign_key_field: Optional[str] = None  # which field holds the FK
    on_delete: str = "cascade"  # cascade, set_null, restrict


@dataclass
class IntentDocument:
    """
    Output of Stage 1 — Intent Extraction Interview.

    Every field is answered. No TBD, no open questions, no 'maybe'.
    This is the complete and only input to Stage 2.

    Lifecycle: created by Interviewer agent, immutable thereafter.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Database layer
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    # Backend layer
    actions: list[str] = field(default_factory=list)  # e.g. ["login", "create_post"]
    failure_rules: list[str] = field(
        default_factory=list
    )  # e.g. ["5 failed logins → lock account"]

    # Frontend layer
    user_screens: list[str] = field(
        default_factory=list
    )  # e.g. ["/login", "/dashboard", "/settings"]
    screen_actions: dict[str, list[str]] = field(
        default_factory=dict
    )  # screen → list of actions available

    # User metadata
    user_types: list[str] = field(
        default_factory=list
    )  # e.g. ["admin", "regular_user", "guest"]
    unauthenticated_access: list[str] = field(
        default_factory=list
    )  # routes accessible without login

    # Raw interview Q&A kept for audit trail
    interview_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "entities": [
                {
                    "name": e.name,
                    "fields": [
                        {
                            "name": f.name,
                            "type": f.type,
                            "required": f.required,
                            "description": f.description,
                            "deleted_at_supported": f.deleted_at_supported,
                        }
                        for f in e.fields
                    ],
                    "description": e.description,
                    "deletable": e.deletable,
                    "audit_log": e.audit_log,
                }
                for e in self.entities
            ],
            "relationships": [
                {
                    "from_entity": r.from_entity,
                    "to_entity": r.to_entity,
                    "relationship_type": r.relationship_type,
                    "foreign_key_field": r.foreign_key_field,
                    "on_delete": r.on_delete,
                }
                for r in self.relationships
            ],
            "actions": self.actions,
            "failure_rules": self.failure_rules,
            "user_screens": self.user_screens,
            "screen_actions": self.screen_actions,
            "user_types": self.user_types,
            "unauthenticated_access": self.unauthenticated_access,
            "interview_log": self.interview_log,
        }


# ─────────────────────────────────────────────────────────────────────────────
# User Flow
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class UserFlow:
    """
    A single path through the product: trigger → conditions → state change.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    screen: str = ""  # which screen this flow originates from
    trigger: str = ""  # what the user does or what the system does
    conditions: list[str] = field(
        default_factory=list
    )  # e.g. ["user_authenticated", "form_valid"]
    outcome: str = ""  # terminal outcome description
    outcome_type: str = "success"  # success | error | redirect
    redirect_to: Optional[str] = None  # if outcome_type == redirect


@dataclass
class UserFlowTree:
    """
    Output of Stage 2 — complete decision tree of every user path.

    Every node is a state. Every edge is a trigger.
    Every branch is a condition. Every leaf is terminal.
    """

    flows: list[UserFlow] = field(default_factory=list)
    entry_screen: str = ""  # the landing screen (usually / or /login)
    terminal_screens: list[str] = field(
        default_factory=list
    )  # screens with no further user action

    def to_dict(self) -> dict:
        return {
            "flows": [
                {
                    "id": f.id,
                    "screen": f.screen,
                    "trigger": f.trigger,
                    "conditions": f.conditions,
                    "outcome": f.outcome,
                    "outcome_type": f.outcome_type,
                    "redirect_to": f.redirect_to,
                }
                for f in self.flows
            ],
            "entry_screen": self.entry_screen,
            "terminal_screens": self.terminal_screens,
        }


# ─────────────────────────────────────────────────────────────────────────────
# API Contracts
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RequestShape:
    method: str = "GET"
    path: str = ""
    body_fields: list[str] = field(default_factory=list)  # expected JSON fields
    query_params: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)


@dataclass
class ResponseShape:
    status_code: int = 200
    body_fields: list[str] = field(default_factory=list)
    error_codes: list[int] = field(default_factory=list)  # e.g. [400, 401, 403, 404]


@dataclass
class APIContract:
    """
    Output of Stage 3 — the Contract Layer.

    Every action in the flow tree mapped to its API call.
    Not documentation — ground truth the implementation must satisfy.
    """

    action: str  # maps back to a UserFlow.trigger
    request: RequestShape = field(default_factory=RequestShape)
    response: ResponseShape = field(default_factory=ResponseShape)
    read_entities: list[str] = field(default_factory=list)  # what this reads
    write_entities: list[str] = field(default_factory=list)  # what this writes
    side_effects: list[str] = field(default_factory=list)  # other actions triggered
    error_cases: list[str] = field(
        default_factory=list
    )  # all ways this can fail

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "request": {
                "method": self.request.method,
                "path": self.request.path,
                "body_fields": self.request.body_fields,
                "query_params": self.request.query_params,
                "headers": self.request.headers,
            },
            "response": {
                "status_code": self.response.status_code,
                "body_fields": self.response.body_fields,
                "error_codes": self.response.error_codes,
            },
            "read_entities": self.read_entities,
            "write_entities": self.write_entities,
            "side_effects": self.side_effects,
            "error_cases": self.error_cases,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Rule Set
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Rule:
    """
    A single IF/THEN rule — the atomic unit of system behavior.

    Every behavior in the system is a rule. No prose, no 'should',
    no 'might'. Every rule is a constraint the codebase must satisfy.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    condition: str = ""  # the IF side
    action: str = ""  # the THEN side
    else_action: str = ""  # optional ELSE branch
    source_contract: str | dict = ""  # str = APIContract name (compiler) | dict = source location (extractor)
    disposition: str = "NEW"  # NEW | PRESERVE | CHANGE | REMOVE (edit mode only)


@dataclass
class RuleSet:
    """
    Output of Stage 4 — Formal Rule Compilation.

    Complete formal specification. Every behavior is a rule.
    Immutable once approved at the human gate.
    """

    rules: list[Rule] = field(default_factory=list)
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "rules": [
                {
                    "id": r.id,
                    "condition": r.condition,
                    "action": r.action,
                    "else_action": r.else_action,
                    "source_contract": r.source_contract,
                    "disposition": r.disposition,
                }
                for r in self.rules
            ],
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TestCase:
    """
    A single test case derived from a rule.

    The codebase does not exist yet — tests define what it must do.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    rule_id: str = ""  # which Rule this tests
    description: str = ""  # human-readable test name
    test_code: str = ""  # the actual test code (pytest format)
    language: str = "python"  # python | go | typescript
    status: str = "failing"  # failing | passing (always failing initially)


@dataclass
class FailingTestSuite:
    """
    Output of Stage 6 — Test Generation.

    Every rule has at least one test. Every test is red.
    The implementation stage ends when every test is green.
    """

    test_cases: list[TestCase] = field(default_factory=list)
    language: str = "python"  # python | go | typescript
    framework: str = "pytest"  # pytest | go_test | vitest

    def to_dict(self) -> dict:
        return {
            "test_cases": [
                {
                    "id": t.id,
                    "rule_id": t.rule_id,
                    "description": t.description,
                    "test_code": t.test_code,
                    "language": t.language,
                    "status": t.status,
                }
                for t in self.test_cases
            ],
            "language": self.language,
            "framework": self.framework,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Database Schema
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ColumnDefinition:
    """A single column in a database table."""
    name: str
    type: str  # postgres type: text, integer, boolean, timestamp, uuid, jsonb, etc.
    nullable: bool = False
    default: str = ""
    primary_key: bool = False
    unique: bool = False
    index: bool = False  # whether to create a B-tree index on this column
    description: str = ""


@dataclass
class IndexDefinition:
    """A database index."""
    name: str
    table: str
    columns: list[str]
    unique: bool = False
    method: str = "btree"  # btree, hash, gin, gist


@dataclass
class FKDefinition:
    """A foreign key constraint."""
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    on_delete: str = "cascade"  # cascade, set_null, restrict, no_action


@dataclass
class TableDefinition:
    """A database table."""
    name: str
    columns: list[ColumnDefinition] = field(default_factory=list)
    primary_key: str = "id"
    soft_delete_column: str | None = None  # e.g. "deleted_at" if entity.deleted_at_supported
    audit_log_table: str | None = None  # e.g. "user_audit_log" if entity.audit_log
    description: str = ""


@dataclass
class DatabaseSchema:
    """
    Output of Stage 5 — Database Design.
    Complete schema ready to apply as migrations.

    Atlas integration: set migration_dir to enable versioned migrations.
    Atlas will manage the migrations/ directory and track applied versions.
    """
    tables: list[TableDefinition] = field(default_factory=list)
    indexes: list[IndexDefinition] = field(default_factory=list)
    foreign_keys: list[FKDefinition] = field(default_factory=list)
    raw_sql: str = ""  # full generated SQL for reference
    approved_at: str | None = None
    approved_by: str | None = None

    # Atlas migration fields (set by pipeline when Atlas is available)
    migration_dir: str | None = None   # e.g. "migrations/" — if set, Atlas manages versioning
    migration_status: str | None = None  # "current" if schema is applied
    last_migration_applied: str | None = None  # version string of last applied migration

    def to_dict(self) -> dict:
        return {
            "tables": [
                {
                    "name": t.name,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type,
                            "nullable": c.nullable,
                            "default": c.default,
                            "primary_key": c.primary_key,
                            "unique": c.unique,
                            "index": c.index,
                            "description": c.description,
                        }
                        for c in t.columns
                    ],
                    "primary_key": t.primary_key,
                    "soft_delete_column": t.soft_delete_column,
                    "audit_log_table": t.audit_log_table,
                    "description": t.description,
                }
                for t in self.tables
            ],
            "indexes": [
                {
                    "name": i.name,
                    "table": i.table,
                    "columns": i.columns,
                    "unique": i.unique,
                    "method": i.method,
                }
                for i in self.indexes
            ],
            "foreign_keys": [
                {
                    "from_table": fk.from_table,
                    "from_column": fk.from_column,
                    "to_table": fk.to_table,
                    "to_column": fk.to_column,
                    "on_delete": fk.on_delete,
                }
                for fk in self.foreign_keys
            ],
            "raw_sql": self.raw_sql,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "migration_dir": self.migration_dir,
            "migration_status": self.migration_status,
            "last_migration_applied": self.last_migration_applied,
        }


@dataclass
class IntegrationResult:
    """
    Output of Stage 7 (Integration) — assembled project.
    """
    project_dir: str = ""
    entry_point: str = ""  # e.g. "main.py" or "cmd/server/main.go"
    readme_path: str = ""
    requirements_path: str = ""
    files_generated: list[str] = field(default_factory=list)
    all_tests_passed: bool = False
    test_summary: str = ""


@dataclass
class RuleEvidence:
    """
    Per-rule evidence from static analysis tools.

    Tool output is collected first, then passed to the LLM as context
    so the LLM makes decisions backed by measurements, not just vibes.
    """
    rule_id: str
    satisfied: bool = False

    # Tool outputs that informed this decision
    bandit_issues: list[str] = field(default_factory=list)   # Bandit security findings
    radon_complexity: list[str] = field(default_factory=list)  # Radon CC > 10 warnings
    pyright_errors: list[str] = field(default_factory=list)    # type errors
    imports_found: list[str] = field(default_factory=list)      # imports present/missing
    symbols_found: list[str] = field(default_factory=list)      # functions/classes present
    test_coverage: list[str] = field(default_factory=list)      # related test names
    llm_assessment: str = ""                                    # LLM's own assessment


@dataclass
class StaticAnalysisResult:
    """
    Aggregated results from all static analysis tools.
    Produced by running 6 tools in parallel before the LLM synthesis step.
    """
    # Tool results (serialised as plain dicts for persistence)
    bandit_issues: list[str] = field(default_factory=list)
    radon_warnings: list[str] = field(default_factory=list)
    vulture_issues: list[str] = field(default_factory=list)
    griffe_drift: list[str] = field(default_factory=list)
    deptry_issues: list[str] = field(default_factory=list)
    semgrep_issues: list[str] = field(default_factory=list)

    # Tool availability
    tool_errors: list[str] = field(default_factory=list)   # unavailable tools
    tool_versions: dict[str, str] = field(default_factory=dict)  # "bandit": "1.9.4"

    # Per-tool success/failure
    bandit_success: bool = True
    radon_success: bool = True
    vulture_success: bool = True
    griffe_success: bool = True
    deptry_success: bool = True
    semgrep_success: bool = True

    def blocking_failures(self) -> list[str]:
        """
        Return list of human-readable blocking failures.
        An empty list means all gates passed — LLM synthesis proceeds.
        """
        failures = []

        # Bandit: HIGH or CRITICAL
        high_bandit = [i for i in self.bandit_issues if any(
            lvl in i.upper() for lvl in ("HIGH", "CRITICAL"))]
        if high_bandit:
            failures.append(f"Bandit: {len(high_bandit)} HIGH/CRITICAL severity issues")

        # Radon: any complexity warning (CC > threshold)
        if self.radon_warnings:
            failures.append(f"Radon: {len(self.radon_warnings)} functions exceed complexity threshold")

        # Vulture: any HIGH confidence dead code
        high_vulture = [i for i in self.vulture_issues if "HIGH" in i.upper()]
        if high_vulture:
            failures.append(f"Vulture: dead code at ≥80% confidence detected")

        # Griffe: any drift
        if self.griffe_drift:
            failures.append(f"Griffe: contract drift detected ({len(self.griffe_drift)} issues)")

        # Deptry: missing deps (DEP001)
        missing_deptry = [i for i in self.deptry_issues if "DEP001" in i]
        if missing_deptry:
            failures.append(f"Deptry: {len(missing_deptry)} missing dependencies")

        # Semgrep: HIGH from security rules
        high_semgrep = [i for i in self.semgrep_issues if "HIGH" in i.upper()]
        if high_semgrep:
            failures.append(f"Semgrep: {len(high_semgrep)} HIGH severity security violations")

        return failures


@dataclass
class ReviewResult:
    """
    Output of Stage 9 (Reviewer) — rule compliance check backed by static analysis.

    Hard gate: static analysis runs first. If any tool fails a hard gate,
    the pipeline blocks immediately — LLM synthesis only runs when all gates pass.
    """
    passed: bool = False
    rules_checked: int = 0
    rules_violated: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    # Static analysis results (all gates must pass before LLM runs)
    static_analysis: StaticAnalysisResult = field(default_factory=StaticAnalysisResult)
    blocking_failures: list[str] = field(default_factory=list)  # hard gate failures
    llm_verdict: str = ""                                      # "" if hard gate blocked

    # Per-rule evidence from tools (keyed by rule_id)
    rule_evidences: dict[str, RuleEvidence] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Mutation Testing & Property-Based Testing
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MutationTestResult:
    """
    Output of Stage 6b — Mutation testing after test generation.

    Produced by running mutmut on the generated code + tests.
    Hard gate: mutation score must exceed threshold (default 70%).
    """
    success: bool = False
    mutation_score: float = 0.0          # 0-100
    total_mutants: int = 0
    killed: int = 0
    survived: int = 0
    incompetent: int = 0
    threshold: float = 70.0
    passed: bool = False                 # score >= threshold
    surviving_mutants: list[str] = field(default_factory=list)  # ["file:line:fn:mutation"]
    blocking_failures: list[str] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class PropertyTestResult:
    """
    Output of Stage 6c — Property-based test validation.

    Produced by running hypothesis health checks on generated tests.
    Advisory: reports health check failures but doesn't block.
    """
    success: bool = False
    test_files_checked: int = 0
    hypothesis_tests_found: int = 0
    health_check_failures: list[str] = field(default_factory=list)
    passed: bool = True
    suggestions: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Supply Chain — CVE Scanning & SBOM
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VulnerabilityInfo:
    """A single CVE vulnerability."""
    cve_id: str
    package: str
    version: str
    severity: str        # HIGH / MEDIUM / LOW / CRITICAL
    description: str
    fix_versions: list[str] = field(default_factory=list)


@dataclass
class SupplyChainResult:
    """
    Output of Stage 7b — Supply chain hardening.

    Produced by running pip-audit (CVE scan) and cyclonedx-py (SBOM).
    pip-audit HIGH/CRITICAL CVEs in direct deps = hard gate.
    SBOM is always generated (advisory).
    """
    success: bool = False
    pip_audit_passed: bool = True
    vulnerabilities: list[VulnerabilityInfo] = field(default_factory=list)
    blocking_failures: list[str] = field(default_factory=list)  # HIGH/CRITICAL CVEs
    advisory_failures: list[str] = field(default_factory=list)  # MEDIUM/LOW CVEs

    # SBOM
    sbom_generated: bool = False
    sbom_path: str = ""
    sbom_component_count: int = 0
    sbom_licenses: list[str] = field(default_factory=list)

    raw_pip_audit: str = ""
    raw_sbom: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Semantic Analysis — Blast Radius & Refactoring
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BlastRadiusResult:
    """
    Output of semantic call-graph analysis (jedi).

    Produced by Impact Analyst before any edit operation.
    Shows all callers of a function — essential for safe refactoring.
    """
    function_name: str
    callers: list[dict] = field(default_factory=list)  # [{file, line, column, caller_name, call_type}]
    total_callers: int = 0
    files_affected: list[str] = field(default_factory=list)


@dataclass
class SemanticRefactorResult:
    """
    Output of rope refactoring operations.

    Produced when the Coder or Impact Analyst performs a semantic rename,
    extract, inline, or move operation.
    """
    operation: str          # "rename" / "extract" / "inline" / "move"
    success: bool = False
    files_changed: int = 0
    changes: list[dict] = field(default_factory=list)
    raw: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Runtime Intelligence — Traces & Profiling
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TraceResult:
    """
    OpenTelemetry trace for a coded module.

    Produced when a module is written — spans cover every function.
    When tests fail, traces feed into the Coder's debugging context.
    """
    service_name: str
    trace_id: str = ""
    span_count: int = 0
    error_spans: int = 0
    slow_spans: list[dict] = field(default_factory=list)   # spans exceeding threshold
    generated_at: str = ""


@dataclass
class ProfilingResult:
    """
    Output of memory (memray) and CPU (py-spy) profiling.

    Produced by Test Runner after code is generated and tests pass.
    Advisory: memory leaks and extreme CPU usage are flagged but don't block
    unless memory_limit_mb is exceeded.
    """
    success: bool = False
    peak_memory_mb: float = 0.0
    memory_limit_mb: float = 512.0
    memory_passed: bool = True

    cpu_slow_functions: list[dict] = field(default_factory=list)  # >10% time
    top_allocators: list[dict] = field(default_factory=list)     # memory

    flamegraph_path: str = ""
    profile_path: str = ""

    blocking_failures: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Edit-mode specific models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ChangeIntent:
    """
    Output of Stage 1 (Edit mode) — what is changing, what must not.
    """

    behavior_changing: str = ""
    behavior_preserved: str = ""
    users_affected: list[str] = field(default_factory=list)
    change_type: str = "modifying"  # additive | modifying | removing
    before_state: str = ""  # formal description of current behavior
    after_state: str = ""  # formal description of new behavior


@dataclass
class ImpactSurface:
    """
    Output of Stage 2 (Edit mode) — precise blast radius of the change.
    """

    # Primary changed symbol (function/class being modified)
    primary_changed_symbol: str = ""
    # Blast-radius callers (all call sites of the primary symbol)
    callers: list[dict] = field(default_factory=list)  # [{file, line, caller_name, call_type}, ...]
    # Files directly affected (contain callers or the symbol itself)
    files_affected: list[str] = field(default_factory=list)
    # Total count of callers across the codebase
    total_callers: int = 0
    # Functions at risk (all functions in modules touched by the change)
    functions_at_risk: list[str] = field(default_factory=list)
    # Contracts at risk (APIContract names that may need updating)
    contracts_at_risk: list[str] = field(default_factory=list)
    # Tests that may need updating (test functions that call the symbol)
    tests_at_risk: list[str] = field(default_factory=list)
    # Risk classification
    risk_level: str = "LOW"  # LOW | MEDIUM | HIGH


@dataclass
class DeltaRuleSet:
    """
    Output of Stage 4 (Edit mode) — diff against the extracted rule set.
    """

    preserve: list[Rule] = field(default_factory=list)  # untouched rules
    change: list[tuple[Rule, Rule]] = field(
        default_factory=list
    )  # (old_rule, new_rule) pairs
    new: list[Rule] = field(default_factory=list)
    remove: list[Rule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "preserve": [r.to_dict() for r in self.preserve],
            "change": [{"from": old.to_dict(), "to": new.to_dict()} for old, new in self.change],
            "new": [r.to_dict() for r in self.new],
            "remove": [r.to_dict() for r in self.remove],
        }
