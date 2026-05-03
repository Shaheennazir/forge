import pytest
from forge.compactor import Compactor, AnchorConfig

def test_preserve_newer_turns():
    """Newer conversation turns (above anchor) are kept verbatim."""
    compactor = Compactor(max_tokens=500)

    older = [{"role": "user", "content": "Fix the bug in parser.py " * 50}]
    newer = [{"role": "user", "content": "Now add tests for it"}]
    anchor = AnchorConfig(keep_newer_turns=1)

    result = compactor.compact(older + newer, anchor=anchor)
    # The newer turn should be preserved verbatim
    assert len(result["kept"]) == 1
    assert "add tests" in str(result["kept"][0])
    # Older content should be summarized (not discarded)
    assert result["summary"] != ""


def test_summarize_old_content():
    """Content older than anchor gets summarized, not discarded."""
    compactor = Compactor(max_tokens=200)
    old_turns = [
        {"role": "user", "content": "Build a REST API with Flask and SQLAlchemy. Include auth, rate limiting, and OpenAPI docs."},
        {"role": "assistant", "content": "Created SPEC.md, implemented auth, rate limiting, OpenAPI. Dockerfile added."},
    ]
    anchor = AnchorConfig(keep_newer_turns=0)
    result = compactor.compact(old_turns, anchor=anchor)
    # Summary should exist and be shorter than original
    original_len = sum(len(t["content"]) for t in old_turns)
    assert len(result["summary"]) < original_len
    assert result["summary"] != ""


def test_empty_compact():
    """Compacting an empty or small session returns empty summary."""
    compactor = Compactor(max_tokens=500)
    result = compactor.compact([], anchor=AnchorConfig(keep_newer_turns=0))
    assert result["summary"] == ""
    assert result["kept"] == []


def test_all_turns_kept_when_under_limit():
    """When total turns <= keep_newer_turns, nothing is summarized."""
    compactor = Compactor(max_tokens=500)
    turns = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    anchor = AnchorConfig(keep_newer_turns=5)
    result = compactor.compact(turns, anchor=anchor)
    assert len(result["kept"]) == 2
    assert result["summary"] == ""


def test_anchor_config_defaults():
    """AnchorConfig has sensible defaults."""
    cfg = AnchorConfig()
    assert cfg.keep_newer_turns == 3
    assert cfg.summary_style == "pr_description"


def test_pr_description_style():
    """PR description style extracts key facts."""
    compactor = Compactor(max_tokens=500)
    turns = [
        {"role": "user", "content": "Build src/api.py with Flask REST endpoint"},
        {"role": "assistant", "content": "Created src/api.py with Flask app"},
    ]
    anchor = AnchorConfig(keep_newer_turns=0)
    result = compactor.compact(turns, anchor=anchor)
    # Should mention files
    assert "src/api.py" in result["summary"] or "api.py" in result["summary"].lower()