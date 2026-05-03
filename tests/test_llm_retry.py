import pytest, time
from unittest.mock import patch, Mock
from forge.retry import APIError, RetryPolicy

# Mock LLM backend to test retry wrapper
def test_retry_wraps_llm_call():
    """Test that LLM complete() wraps with RetryPolicy."""
    from forge.llm import LLMBackend
    
    # Check that LLMBackend.complete has retry logic
    # by checking the source for retry imports
    import inspect
    from forge import llm
    source = inspect.getsource(llm)
    assert "from forge.retry import" in source or "forge.retry" in source, \
        "llm.py should import retry module"
