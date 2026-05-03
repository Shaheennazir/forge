"""Anchored context summarization for forge sessions.

Strategy:
- Keep the N most recent turns verbatim (anchor zone)
- Summarize everything below the anchor into a dense paragraph
- Preserve exact file paths, function names, technical terms in summary
- Output: {"kept": [...turns above anchor], "summary": "..."}
"""

from __future__ import annotations

import re
import structlog
from dataclasses import dataclass

log = structlog.get_logger(__name__)

# Lazy tiktoken loader — only imported when compaction actually fires
_tiktoken = None


def _get_tiktoken():
    """Lazily import and cache the tiktoken encoder."""
    global _tiktoken
    if _tiktoken is None:
        try:
            import tiktoken
            _tiktoken = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            log.warning("compactor.tiktoken_unavailable", error=str(e))
            _tiktoken = None
    return _tiktoken


def count_tokens(text: str) -> int:
    """
    Count tokens in ``text`` using tiktoken (cl100k_base).
    Falls back to a conservative ``len(text) // 4`` heuristic if tiktoken
    is unavailable.
    """
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback: conservative — use 4 chars per token
    return len(text) // 4


@dataclass
class AnchorConfig:
    """Configuration for anchored summarization."""
    keep_newer_turns: int = 3  # How many newest turns to preserve verbatim
    summary_style: str = "pr_description"  # "pr_description" | "detailed"


class Compactor:
    """Anchored context summarizer — newer turns verbatim, older summarized."""

    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens

    def token_count(self, text: str) -> int:
        """Count tokens for a string using tiktoken or the heuristic fallback."""
        return count_tokens(text)

    def compact(self, turns: list[dict], anchor: AnchorConfig = None) -> dict:
        """
        Compact a list of conversation turns.

        Returns:
            {
                "kept": [...turns in the anchor zone, verbatim],
                "summary": "Summarized version of older turns"
            }
        """
        if anchor is None:
            anchor = AnchorConfig()
        if not turns:
            return {"kept": [], "summary": ""}

        k = anchor.keep_newer_turns
        if k == 0:
            history = turns
            anchor_zone = []
        else:
            history = turns[:-k] if k < len(turns) else turns[:0]
            anchor_zone = turns[-k:] if k > 0 else []

        if not history:
            return {"kept": anchor_zone, "summary": ""}

        summary = self._summarize(history, anchor.summary_style)
        return {"kept": anchor_zone, "summary": summary}

    def _summarize(self, turns: list[dict], style: str) -> str:
        """Summarize older turns into a dense paragraph."""
        if not turns:
            return ""

        facts = self._extract_facts(turns)

        if style == "pr_description":
            return self._pr_description(facts, turns)
        return self._detailed_summary(facts, turns)

    def _extract_facts(self, turns: list[dict]) -> dict:
        """Extract structured facts from turns: files touched, decisions, errors."""
        facts = {
            "files": set(),
            "decisions": [],
            "errors": [],
            "features": [],
        }
        for turn in turns:
            content = turn.get("content", "")
            # Extract file paths (simple heuristic)
            files = re.findall(r'[\w./\\]+\.py|\.ts|\.js|\.md|\.yaml|\.json|\.toml', content)
            facts["files"].update(files)
            # Extract decisions (lines that made a choice)
            if turn.get("role") == "assistant":
                decisions = re.findall(r'(?:Created|Added|Implemented|Fixed|Updated|Modified)\s+[^\n]+', content)
                facts["decisions"].extend(decisions[:3])
        return facts

    def _pr_description(self, facts: dict, turns: list[dict]) -> str:
        """Format summary as PR description: 2-3 sentences, first person."""
        files = list(facts["files"])
        if not files:
            files_str = "the codebase"
        elif len(files) <= 3:
            files_str = ", ".join(f"`{f}`" for f in files)
        else:
            files_str = f"{', '.join('`'+f+'`' for f in files[:3])} and {len(files)-3} more files"

        decisions = " ".join(facts["decisions"]) if facts["decisions"] else None
        summary = f"Worked on {files_str}."
        if decisions:
            summary += f" {decisions}."
        # Cap at reasonable length
        if len(summary) > 500:
            summary = summary[:497] + "..."
        return summary

    def _detailed_summary(self, facts: dict, turns: list[dict]) -> str:
        """More detailed paragraph summarization."""
        lines = ["Previous session context:"]
        files = list(facts["files"])
        if files:
            lines.append(f"- Files involved: {', '.join(files[:10])}")
        for decision in facts["decisions"]:
            lines.append(f"- Decision: {decision}")
        return "\n".join(lines) if len(lines) > 1 else ""