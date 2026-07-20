"""Tests."""

from {{ package_name }}.core import process

def test_process():
    """Test process function."""
    assert process("hello") == "HELLO"
