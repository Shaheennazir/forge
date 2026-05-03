"""forge.llm.backends.anthropic — Anthropic Claude backend via anthropic SDK."""

from __future__ import annotations
import json
import os
from typing import Any, Generator, Optional

import anthropic

from forge.llm.backends.base import LLMBackend
from forge.llm.config import LLMConfig
from forge.llm.response import LLMResponse
from forge.llm.tools import convert_openai_to_anthropic_tools, tool_call_to_dict


class AnthropicBackend(LLMBackend):
    """
    Anthropic SDK backend for Claude models.

    Supports streaming, tool calling, and vision.
    Tool schemas are accepted in OpenAI format and automatically converted.
    """

    def __init__(self, config: LLMConfig):
        self.config = config

    def _client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key=self.config.api_key or _env("ANTHROPIC_API_KEY"),
        )

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

        anthropic_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            anthropic_kwargs["system"] = system
        if tools:
            anthropic_kwargs["tools"] = convert_openai_to_anthropic_tools(tools)
        if tool_choice:
            # "auto" → Anthropic supports "auto" directly
            if tool_choice == "auto":
                anthropic_kwargs["tool_choice"] = {"type": "auto"}
            elif isinstance(tool_choice, dict):
                anthropic_kwargs["tool_choice"] = tool_choice
            # else: let SDK handle other values
        anthropic_kwargs.update(kwargs)

        resp = client.messages.create(**anthropic_kwargs)
        return _parse_anthropic_response(resp, self.config.model)

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

        anthropic_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if system:
            anthropic_kwargs["system"] = system
        if tools:
            anthropic_kwargs["tools"] = convert_openai_to_anthropic_tools(tools)
        anthropic_kwargs.update(kwargs)

        with client.messages.stream(**anthropic_kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta" and hasattr(event, "delta"):
                    delta = event.delta
                    if hasattr(delta, "text") and delta.text:
                        yield delta.text

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> dict:
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str | None:
    return os.environ.get(key)


def _parse_anthropic_response(resp: Any, model: str) -> LLMResponse:
    # resp is anthropic.types.Message
    content_blocks = resp.content
    text_parts = []
    tool_calls: list[dict] | None = None

    for block in content_blocks:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            if tool_calls is None:
                tool_calls = []
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": (
                        block.input
                        if isinstance(block.input, str)
                        else json.dumps(block.input)
                    ),
                },
            })

    content = "\n".join(text_parts)

    # Usage
    usage: dict[str, int] | None = None
    if hasattr(resp, "usage") and resp.usage:
        usage = {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        }

    # Finish reason
    stop_reason = getattr(resp, "stop_reason", None)
    finish_reason: str | None = None
    if stop_reason in ("end_turn", "stop_sequence"):
        finish_reason = "stop"
    elif stop_reason == "max_tokens":
        finish_reason = "length"
    elif stop_reason in ("tool_use",):
        finish_reason = "tool_calls"

    return LLMResponse(
        content=content,
        model=model,
        provider="anthropic",
        usage=usage,
        finish_reason=finish_reason,
        tool_calls=tool_calls,
        raw=resp,
    )
