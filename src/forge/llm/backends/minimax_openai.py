"""forge.llm.backends.mmx — MiniMax via OpenAI-compatible HTTP API (not the CLI).

This backend uses MiniMax's native OpenAI-compatible endpoint, enabling full
streaming and tool calling support.

For config, set provider="minimax_openai" in ~/.forge/config.yaml, or set
FORGE_LLM_PROVIDER=minimax_openai with MINIMAX_API_KEY in your environment.

If MiniMax's API does not support a given feature (e.g. tool calling), the
backend raises NotImplementedError with a clear message.
"""

from __future__ import annotations
import os
from typing import Any, Generator, Optional

from forge.llm.backends.base import LLMBackend
from forge.llm.config import LLMConfig
from forge.llm.response import LLMResponse


# Base URL for MiniMax's OpenAI-compatible API.
_MINIMAX_BASE_URL = "https://api.minimax.chat/v1"


class MiniMaxBackend(LLMBackend):
    """
    MiniMax via OpenAI-compatible API.

    Supports streaming. Raises NotImplementedError for tool calling
    (MiniMax chat completions API does not currently support function calling).
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    def _client(self) -> "openai.OpenAI":  # deferred to avoid top-level import
        import openai
        return openai.OpenAI(
            api_key=self.config.api_key or os.environ.get("MINIMAX_API_KEY"),
            base_url=_MINIMAX_BASE_URL,
        )

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tools: Optional[list] = None,
        **kwargs,
    ) -> LLMResponse:
        if tools:
            raise NotImplementedError(
                "MiniMax OpenAI-compatible API does not support tool calling. "
                "Use provider='mmx' (CLI) for tool calling or switch to a provider "
                "with tool support (OpenAI, Anthropic, DeepSeek, etc.)."
            )
        from forge.llm.backends.openai_ import _build_messages, _parse_openai_response
        client = self._client()
        messages = _build_messages(prompt, system)
        import openai
        resp = client.chat.completions.create(
            model=self.config.model or "MiniMax-Text-01",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        return _parse_openai_response(resp, self.config.model, "minimax_openai")

    def complete_streaming(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tools: Optional[list] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        if tools:
            raise NotImplementedError(
                "MiniMax OpenAI-compatible API does not support tool calling."
            )
        from forge.llm.backends.openai_ import _build_messages
        client = self._client()
        messages = _build_messages(prompt, system)
        stream = client.chat.completions.create(
            model=self.config.model or "MiniMax-Text-01",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            **kwargs,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> dict:
        import json
        system_json = (system or "") + (
            "\n\nYou must respond with valid JSON only, no markdown or explanation."
        )
        text = self.complete(
            prompt,
            system=system_json,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ).content
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Response was not valid JSON: {e}\n\nRaw: {text[:500]}")
