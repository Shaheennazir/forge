"""forge.llm.backends.openai_ — OpenAI + all OpenAI-compatible providers via openai SDK."""

from __future__ import annotations
import json
import os
from typing import Any, Generator, Optional

import openai

from forge.llm.backends.base import LLMBackend
from forge.llm.config import LLMConfig
from forge.llm.response import LLMResponse


class OpenAIBackend(LLMBackend):
    """
    OpenAI SDK backend.

    Handles: OpenAI, DeepSeek, Qwen, Kimi, GLM, Groq, MiniMax (OpenAI compat),
    and any other provider that exposes an OpenAI-compatible chat completions API.

    Pass ``base_url`` in ``LLMConfig`` to target a custom endpoint.
    Streaming and tool calling are fully supported.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    def _client(self) -> openai.OpenAI:
        kwargs: dict[str, Any] = {
            "api_key": self.config.api_key or _env("OPENAI_API_KEY"),
        }
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return openai.OpenAI(**kwargs)

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tools: Optional[list] = None,
        tool_choice: Optional[Any] = None,
        **kwargs,
    ) -> LLMResponse:
        client = self._client()
        messages = _build_messages(prompt, system)
        openai_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            openai_kwargs["tools"] = tools
        if tool_choice:
            openai_kwargs["tool_choice"] = tool_choice
        openai_kwargs.update(kwargs)

        resp = client.chat.completions.create(**openai_kwargs)
        return _parse_openai_response(resp, self.config.model, self.config.provider)

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
        client = self._client()
        messages = _build_messages(prompt, system)
        openai_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            openai_kwargs["tools"] = tools
        openai_kwargs.update(kwargs)

        stream = client.chat.completions.create(**openai_kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def complete_json(
        self,
        prompt: str,
        system: str = "",
        **kwargs,
    ) -> dict:
        response = self.complete(prompt=prompt, system=system, **kwargs)
        content = response.content if hasattr(response, "content") else str(response)
        return json.loads(content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str | None:
    return os.environ.get(key)


def _build_messages(prompt: str, system: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _parse_openai_response(resp: Any, model: str, provider: str) -> LLMResponse:
    choice = resp.choices[0]
    message = choice.message

    # Tool calls
    tool_calls = None
    if hasattr(message, "tool_calls") and message.tool_calls:
        tool_calls = []
        for tc in message.tool_calls:
            fn = tc.function
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": fn.name,
                    "arguments": (
                        fn.arguments
                        if isinstance(fn.arguments, str)
                        else fn.arguments
                    ),
                },
            })

    # Finish reason
    finish = choice.finish_reason
    if finish == "tool_calls":
        finish_reason = "tool_calls"
    elif finish == "length":
        finish_reason = "length"
    else:
        finish_reason = "stop"

    # Usage
    usage = None
    if resp.usage:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }

    return LLMResponse(
        content=message.content or "",
        model=model,
        provider=provider,
        usage=usage,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        raw=resp,
    )
