"""
forge.llm — Unified LLM backend using openai + anthropic Python SDKs.

Supports streaming, tool calling, and structured responses across all providers.
"""

from forge.llm.backends.base import LLMBackend
from forge.llm.config import (
    LLMConfig,
    load_config,
    ensure_config,
    PROVIDER_DEFAULTS,
    PROVIDER_MODELS,
)
from forge.llm.response import LLMResponse
from forge.llm.tools import (
    convert_openai_to_anthropic_tools,
    tool_call_to_dict,
)

__all__ = [
    "LLMBackend",
    "LLMConfig",
    "LLMResponse",
    "load_config",
    "ensure_config",
    "create_backend",
    "PROVIDER_DEFAULTS",
    "PROVIDER_MODELS",
    "convert_openai_to_anthropic_tools",
    "tool_call_to_dict",
]


def create_backend(config: LLMConfig | None = None) -> LLMBackend:
    """Factory: create an LLM backend from config (or load from disk)."""
    ensure_config()
    config = config or load_config()

    provider = config.provider.lower()

    # Normalise aliases
    if provider in ("minimax",):
        provider = "mmx"

    if provider == "mmx":
        # CLI-based MiniMax — no streaming, no tools
        from forge.llm.backends.mmx import MMXBackend
        return MMXBackend(config)
    elif provider in ("openai", "deepseek", "qwen", "kimi", "glm", "groq", "minimax_openai"):
        # All OpenAI-compatible — including MiniMax HTTP API
        from forge.llm.backends.minimax_openai import MiniMaxBackend
        return MiniMaxBackend(config)
    elif provider == "anthropic":
        from forge.llm.backends.anthropic import AnthropicBackend
        return AnthropicBackend(config)
    elif provider == "ollama":
        from forge.llm.backends.ollama import OllamaBackend
        return OllamaBackend(config)
    else:
        raise ValueError(f"Unknown provider: {config.provider}")
