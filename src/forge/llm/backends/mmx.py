"""forge.llm.backends.mmx — MiniMax via CLI wrapper (legacy, limited).

This backend wraps the ``mmx`` CLI binary. It does NOT support streaming
or tool calling.

For full streaming + tool support, use ``provider="minimax_openai"`` instead
which calls MiniMax's OpenAI-compatible HTTP API directly.
"""

from __future__ import annotations
import os
import json
import subprocess
import time as time_
from pathlib import Path
from typing import Generator, Optional

from forge.llm.backends.base import LLMBackend
from forge.llm.config import LLMConfig
from forge.llm.response import LLMResponse
from forge.retry import APIError, RetryPolicy, retryable, delay


class MMXBackend(LLMBackend):
    """
    MiniMax backend via ``mmx text chat`` CLI.

    This is the legacy CLI-based backend. It does not support streaming
    or tool calling. Prefer ``MiniMaxBackend`` (provider="minimax_openai")
    for full feature support.
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self.mmx_bin = Path.home() / ".hermes" / "node" / "bin" / "mmx"

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
                "The mmx CLI backend does not support tool calling. "
                "Use provider='minimax_openai' for MiniMax's HTTP API with tool support."
            )
        policy = RetryPolicy(max_attempts=5)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._complete(prompt, system, max_tokens=max_tokens, temperature=temperature, **kwargs)
            except Exception as e:
                api_err = APIError(str(e))
                reason = retryable(api_err)
                if not reason or not policy.should_retry(attempt, api_err):
                    raise
                time_.sleep(delay(attempt, api_err) / 1000)

    def _complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs,
    ) -> LLMResponse:
        model = self.config.model or "MiniMax-M2.7"
        cmd = [
            str(self.mmx_bin), "text", "chat",
            "--model", model,
            "--message", prompt,
            "--output", "text",
        ]
        if system:
            cmd += ["--system", system]
        if max_tokens:
            cmd += ["--max-tokens", str(max_tokens)]
        if temperature and temperature != 1.0:
            cmd += ["--temperature", str(temperature)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=kwargs.get("timeout", 120),
        )
        if result.returncode != 0:
            raise RuntimeError(f"mmx failed: {result.stderr[:200]}")

        content = result.stdout.strip()
        return LLMResponse(
            content=content,
            model=model,
            provider="mmx",
            usage=None,
            finish_reason="stop",
            tool_calls=None,
            raw=result.stdout,
        )

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
        raise NotImplementedError(
            "The mmx CLI backend does not support streaming. "
            "Use provider='minimax_openai' for MiniMax's HTTP API with streaming."
        )

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> dict:
        text = self.complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ).content
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Response was not valid JSON: {e}\n\nRaw: {text[:500]}")
