"""forge.llm.tools — tool/function calling schema helpers and format conversion."""

from __future__ import annotations
from typing import Any


# ---------------------------------------------------------------------------
# OpenAI → Anthropic tool format conversion
# ---------------------------------------------------------------------------

def convert_openai_to_anthropic_tools(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Convert OpenAI-style tools schema to Anthropic's tools format.

    OpenAI format:
        {
          "type": "function",
          "function": {
            "name": "foo",
            "description": "Does foo",
            "parameters": {"type": "object", "properties": {...}, "required": [...]}
          }
        }

    Anthropic format:
        {
          "name": "foo",
          "description": "Does foo",
          "input_schema": {"type": "object", "properties": {...}, "required": [...]}
        }
    """
    result = []
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            result.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object"}),
            })
        else:
            # Already in Anthropic format or unknown — pass through
            result.append(tool)
    return result


def tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
    """
    Normalise a tool call object from any SDK into a plain dict.

    Works with:
        - OpenAI SDK: tool_call.id, tool_call.function.name, tool_call.function.arguments
        - Anthropic SDK: tool_call.name, tool_call.input
    """
    result: dict[str, Any] = {}

    # OpenAI SDK shape
    if hasattr(tool_call, "id"):
        result["id"] = tool_call.id
    if hasattr(tool_call, "function"):
        result["name"] = tool_call.function.name
        args = tool_call.function.arguments
        if isinstance(args, str):
            result["arguments"] = args
        else:
            result["arguments"] = args
    # Anthropic SDK shape
    elif hasattr(tool_call, "name"):
        result["name"] = tool_call.name
        result["arguments"] = (
            tool_call.input
            if hasattr(tool_call, "input") else
            (tool_call.input if isinstance(tool_call.input, str) else str(tool_call.input))
        )

    return result
