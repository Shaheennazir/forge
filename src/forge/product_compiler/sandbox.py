"""
forge.product_compiler.sandbox — E2B sandbox integration.

Now uses the real e2b-code-interpreter SDK.
Import delegation: this module re-exports from forge.code_intelligence.sandbox.
"""

from __future__ import annotations

# Delegate to the canonical implementation in code_intelligence
from forge.code_intelligence.sandbox import Sandbox, SandboxResult
from forge.code_intelligence.sandbox import _load_e2b_api_key

__all__ = ["Sandbox", "SandboxResult"]
