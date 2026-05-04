"""
forge.code_intelligence — Code intelligence tools for agents.

Exports:
  Parser     — tree-sitter wrapper for multi-language AST parsing
  CTags      — ctags integration for symbol indexing
  Search     — semgrep integration for code analysis
  GitOps     — pygit2 wrapper for programmatic git operations
  Sandbox    — E2B sandbox for isolated code execution
  Messaging  — NATS pub/sub for agent event streaming
  Atlas      — Atlas schema management for migration workflows
"""

from forge.code_intelligence.parser import CodeParser, ParsedFile
from forge.code_intelligence.ctags import CTags, Symbol
from forge.code_intelligence.search import CodeSearch, SearchResult
from forge.code_intelligence.git_ops import GitOps, GitError
from forge.code_intelligence.sandbox import Sandbox, SandboxResult
from forge.code_intelligence.messaging import NATSMessaging, MessagingConfig
from forge.code_intelligence.atlas import Atlas, MigrationFile, MigrationStatus

# Static analysis wrappers
from forge.code_intelligence.bandit_ import run_bandit, ToolResult as BanditResult, Issue as BanditIssue
from forge.code_intelligence.radon_ import run_radon, ToolResult as RadonResult, Issue as RadonIssue
from forge.code_intelligence.vulture_ import run_vulture, ToolResult as VultureResult, Issue as VultureIssue
from forge.code_intelligence.griffe_ import run_griffe, ToolResult as GriffeResult, Issue as GriffeIssue
from forge.code_intelligence.deptry_ import run_deptry, ToolResult as DeptryResult, Issue as DeptryIssue
from forge.code_intelligence.semgrep_ import run_semgrep, ToolResult as SemgrepResult, Issue as SemgrepIssue
from forge.code_intelligence.pyupgrade_ import run_pyupgrade, ToolResult as PyupgradeResult, Issue as PyupgradeIssue
from forge.code_intelligence.hypothesis_ import run_hypothesis, ToolResult as HypothesisResult, Issue as HypothesisIssue
from forge.code_intelligence.mutmut_ import run_mutmut, ToolResult as MutmutResult, Issue as MutmutIssue, MutationResult
from forge.code_intelligence.pip_audit_ import run_pip_audit, ToolResult as PipAuditResult, Issue as PipAuditIssue, Vulnerability
from forge.code_intelligence.cyclonedx_ import run_sbom, ToolResult as SBOMResult, Issue as SBOMIssue, SBOMResult as SBOMData
from forge.code_intelligence.jedi_ import find_callers, get_inference, get_completions, get_signatures, SemanticResult
from forge.code_intelligence.rope_ import rename_function, extract_method, inline_function, move_symbol, RenameResult, ExtractResult
from forge.code_intelligence.crosshair_ import run_crosshair, check_function_contracts, ToolResult as CrosshairResult, Issue as CrosshairIssue, ContractProof
from forge.code_intelligence.otel_ import create_tracer, TraceConfig, TraceResult, instrument_module, capture_trace
from forge.code_intelligence.memray_ import run_memray_profile, run_memray_flamegraph, ToolResult as MemrayResult, Issue as MemrayIssue, MemoryProfile
from forge.code_intelligence.pyspy_ import run_pyspy_profile, profile_function, ToolResult as PySpyResult, Issue as PySpyIssue, ProfilerResult

__all__ = [
    # Core
    "CodeParser",
    "ParsedFile",
    "CTags",
    "Symbol",
    "CodeSearch",
    "SearchResult",
    "GitOps",
    "GitError",
    "Sandbox",
    "SandboxResult",
    "NATSMessaging",
    "MessagingConfig",
    "Atlas",
    "MigrationFile",
    "MigrationStatus",
    # Static analysis
    "run_bandit",
    "BanditResult",
    "BanditIssue",
    "run_radon",
    "RadonResult",
    "RadonIssue",
    "run_vulture",
    "VultureResult",
    "VultureIssue",
    "run_griffe",
    "GriffeResult",
    "GriffeIssue",
    "run_deptry",
    "DeptryResult",
    "DeptryIssue",
    "run_semgrep",
    "SemgrepResult",
    "SemgrepIssue",
    # Phase 2: property-based + mutation
    "run_hypothesis", "HypothesisResult", "HypothesisIssue",
    "run_mutmut", "MutmutResult", "MutmutIssue", "MutationResult",
    # Phase 3: supply chain
    "run_pip_audit", "PipAuditResult", "PipAuditIssue", "Vulnerability",
    "run_sbom", "SBOMResult", "SBOMIssue", "SBOMData",
    # Phase 4: semantic
    "find_callers", "get_inference", "get_completions", "get_signatures", "SemanticResult",
    "rename_function", "extract_method", "inline_function", "move_symbol", "RenameResult", "ExtractResult",
    # Phase 5: runtime
    "create_tracer", "TraceConfig", "TraceResult", "instrument_module", "capture_trace",
    # Phase 6: contracts
    "run_crosshair", "check_function_contracts", "CrosshairResult", "CrosshairIssue", "ContractProof",
    # Runtime profiling
    "run_memray_profile", "run_memray_flamegraph", "MemrayResult", "MemrayIssue", "MemoryProfile",
    "run_pyspy_profile", "profile_function", "PySpyResult", "PySpyIssue", "ProfilerResult",
]
