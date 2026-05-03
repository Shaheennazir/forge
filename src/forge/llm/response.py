"""forge.llm.response — structured LLM response object."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class LLMResponse:
    """
    Structured response from any LLM backend.

    Attributes
    ----------
    content : str
        The assistant's text response.
    model : str
        Model that generated the response.
    provider : str
        Provider that was used (e.g. "openai", "anthropic").
    usage : dict | None
        Token usage breakdown: {prompt_tokens, completion_tokens, total_tokens}.
    finish_reason : str | None
        Why generation stopped: "stop", "length", "tool_calls".
    tool_calls : list | None
        List of tool calls the model requested (OpenAI format).
    raw : Any | None
        Raw provider-specific response object.
    """

    content: str
    model: str
    provider: str
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[list] = None
    raw: Any = None

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return f"LLMResponse(provider={self.provider!r}, model={self.model!r}, content={preview!r}...)"
