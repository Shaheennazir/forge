"""forge.llm.backends.base — abstract base class for all LLM backends."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generator, Optional, Any


class LLMBackend(ABC):
    """Abstract base for all LLM backends."""

    @abstractmethod
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
    ):
        """Synchronous completion. Returns LLMResponse."""
        ...

    @abstractmethod
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
        """Streaming completion. Yields content chunks as strings."""
        ...

    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> dict:
        """Completion guaranteed to be valid JSON. Returns parsed dict."""
        ...
