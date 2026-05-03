"""forge.llm.backends.ollama — Ollama via OpenAI SDK (localhost:11434/v1)."""

from __future__ import annotations

from forge.llm.backends.openai_ import OpenAIBackend
from forge.llm.config import LLMConfig


class OllamaBackend(OpenAIBackend):
    """
    Ollama backend.

    Uses the OpenAI SDK pointing at ``http://localhost:11434/v1``.
    api_key is set to "ollama" (Ollama ignores auth when this is set).
    Inherits all streaming and tool-support from OpenAIBackend.
    """

    def __init__(self, config: LLMConfig):
        # Force base_url to the local Ollama endpoint
        import os
        config.base_url = config.base_url or "http://localhost:11434/v1"
        # Ollama ignores the API key, but OpenAI SDK requires one
        if not config.api_key:
            config.api_key = "ollama"
        super().__init__(config)
