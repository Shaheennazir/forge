"""
forge.llm — compatibility shim.

All new code should import from ``forge.llm`` (the package) directly.
This file is kept so that any existing ``from forge import llm`` or
``from forge.llm import ...`` imports continue to work without changes.
"""

# Re-export everything from the new package so callers don't need to change
from forge.llm import (
    LLMBackend,
    LLMConfig,
    LLMResponse,
    create_backend,
    load_config,
    ensure_config,
    PROVIDER_DEFAULTS,
    PROVIDER_MODELS,
    convert_openai_to_anthropic_tools,
    tool_call_to_dict,
)

__all__ = [
    "LLMBackend",
    "LLMConfig",
    "LLMResponse",
    "create_backend",
    "load_config",
    "ensure_config",
    "PROVIDER_DEFAULTS",
    "PROVIDER_MODELS",
    "convert_openai_to_anthropic_tools",
    "tool_call_to_dict",
]
