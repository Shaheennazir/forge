"""
forge.product_compiler — Intent → Rules → Tests → Code pipeline.

A multi-agent system that compiles user intent into production software
through nine formal stages. Each stage either eliminates ambiguity or
blocks further progress.

Architecture:
  interviewer       → IntentDocument   (adversarial interview, discrete turns)
  flow_designer    → UserFlowTree     (user paths and decision trees)
  contract_writer  → APIContract[]    (API ground truth per action)
  rule_compiler    → RuleSet          (IF/THEN formal rules from contracts + flows)
  schema_designer  → DatabaseSchema   (PostgreSQL schema from entities)
  test_writer      → FailingTestSuite (tests derived from rules, not code)
  coder            → PassingCode      (makes tests green, no architectural decisions)
  integrator       → IntegrationResult (README, requirements, project layout)
  reviewer         → ReviewResult     (rule compliance check)

Pipeline modes:
  NEW_PROJECT — full 9-stage pipeline from scratch
  EDIT        — surgical edit pipeline with blast-radius containment

All agents use the shared forge LLM provider layer (forge.llm).
They do not own or modify it.
"""

from forge.product_compiler.models import (
    # Enums
    PipelineMode,
    Stage,
    # Intent
    Entity,
    EntityField,
    Relationship,
    IntentDocument,
    # Flows
    UserFlow,
    UserFlowTree,
    # Contracts
    APIContract,
    RequestShape,
    ResponseShape,
    # Rules
    Rule,
    RuleSet,
    # Tests
    TestCase,
    FailingTestSuite,
    # Database schema
    ColumnDefinition,
    IndexDefinition,
    FKDefinition,
    TableDefinition,
    DatabaseSchema,
    # Integration
    IntegrationResult,
    # Review
    ReviewResult,
    # Edit-mode
    ChangeIntent,
    ImpactSurface,
    DeltaRuleSet,
)

from forge.product_compiler.pipeline import (
    ProductCompilerPipeline,
    PipelineState,
    PipelineStatus,
)

from forge.product_compiler.codebase_index import CodebaseIndex

__all__ = [
    # Enums
    "PipelineMode",
    "Stage",
    # Intent
    "Entity",
    "EntityField",
    "Relationship",
    "IntentDocument",
    # Flows
    "UserFlow",
    "UserFlowTree",
    # Contracts
    "APIContract",
    "RequestShape",
    "ResponseShape",
    # Rules
    "Rule",
    "RuleSet",
    # Tests
    "TestCase",
    "FailingTestSuite",
    # Database schema
    "ColumnDefinition",
    "IndexDefinition",
    "FKDefinition",
    "TableDefinition",
    "DatabaseSchema",
    # Integration
    "IntegrationResult",
    # Review
    "ReviewResult",
    # Edit-mode
    "ChangeIntent",
    "ImpactSurface",
    "DeltaRuleSet",
    # Codebase index
    "CodebaseIndex",
    # Pipeline
    "ProductCompilerPipeline",
    "PipelineState",
    "PipelineStatus",
]
