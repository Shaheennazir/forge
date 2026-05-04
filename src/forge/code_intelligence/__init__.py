"""
forge.code_intelligence — Code intelligence tools for agents.

Exports:
  Parser     — tree-sitter wrapper for multi-language AST parsing
  CTags      — ctags integration for symbol indexing
  Search     — semgrep integration for code analysis
  GitOps     — pygit2 wrapper for programmatic git operations
  Sandbox    — E2B sandbox for isolated code execution
  Messaging  — NATS pub/sub for agent event streaming
"""

from forge.code_intelligence.parser import CodeParser, ParsedFile
from forge.code_intelligence.ctags import CTags, Symbol
from forge.code_intelligence.search import CodeSearch, SearchResult
from forge.code_intelligence.git_ops import GitOps, GitError
from forge.code_intelligence.sandbox import Sandbox, SandboxResult
from forge.code_intelligence.messaging import NATSMessaging, MessagingConfig

__all__ = [
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
]
