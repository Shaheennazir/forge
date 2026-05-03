import pytest, time
from unittest.mock import patch, Mock
from forge.retry import APIError, RetryPolicy, with_retry, _normalise_error


def test_mmx_backend_uses_retry():
    """Test that the MMX backend (CLI wrapper) uses RetryPolicy."""
    import inspect
    from forge.llm.backends import mmx as mmx_module
    source = inspect.getsource(mmx_module)
    assert "from forge.retry import" in source or "forge.retry" in source, \
        "mmx backend module should import retry module"


def test_with_retry_does_not_retry_non_api_error():
    """Non-API errors should propagate immediately without retry."""
    call_count = 0

    def fails():
        nonlocal call_count
        call_count += 1
        raise ValueError("not an API error")

    policy = RetryPolicy(max_attempts=3)
    with pytest.raises(ValueError):
        with_retry(policy, fails)

    # Should have called exactly once — no retry for non-API errors
    assert call_count == 1, f"Expected 1 attempt, got {call_count}"


def test_normalise_error_returns_none_for_non_api_errors():
    """_normalise_error returns None for plain exceptions."""
    assert _normalise_error(ValueError("bad input")) is None
    assert _normalise_error(RuntimeError("oops")) is None


def test_normalise_error_coerces_api_error():
    """_normalise_error passes through existing APIError."""
    original = APIError(message="oops", status_code=500)
    result = _normalise_error(original)
    assert result is original


def test_with_retry_retries_on_rate_limit():
    """with_retry retries when retryable APIError is raised."""
    call_count = 0

    def rate_limited():
        nonlocal call_count
        call_count += 1
        raise APIError(
            message="rate_limit_error: too many requests",
            status_code=429,
            is_retryable=True,
            response_headers={"retry-after-ms": "10"},
        )

    policy = RetryPolicy(max_attempts=3, initial_delay_ms=10)
    start = time.time()
    with pytest.raises(APIError):
        with_retry(policy, rate_limited)

    elapsed_ms = (time.time() - start) * 1000
    assert call_count == 3, f"Expected 3 attempts, got {call_count}"
    # Should have retried with delay (at least 10ms)
    assert elapsed_ms >= 8, f"Expected delay between attempts, got {elapsed_ms:.1f}ms"
