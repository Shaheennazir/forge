import pytest, time
from forge.retry import (
    RetryPolicy, APIError, retryable, delay,
    RETRY_INITIAL_DELAY, RETRY_BACKOFF_FACTOR, RETRY_MAX_DELAY, RETRY_MAX_DELAY_NO_HEADERS
)


def test_delay_exponential_backoff():
    assert delay(1) == RETRY_INITIAL_DELAY
    assert delay(2) == RETRY_INITIAL_DELAY * RETRY_BACKOFF_FACTOR
    assert delay(3) == RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** 2)


def test_delay_exact_values():
    """Verify exact delay values."""
    assert delay(1) == 2000
    assert delay(3) == 8000


def test_delay_respects_retry_after_ms():
    err = APIError("rate limited", response_headers={"retry-after-ms": "500"})
    assert delay(1, err) == 500


def test_delay_respects_retry_after_seconds():
    err = APIError("rate limited", response_headers={"retry-after": "2"})
    assert delay(1, err) == 2000


def test_delay_respects_retry_after_http_date():
    future = time.time() + 5
    http_date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(future))
    err = APIError("rate limited", response_headers={"retry-after": http_date})
    d = delay(1, err)
    assert 4000 < d < 6000


def test_delay_caps_at_max():
    assert delay(100) == RETRY_MAX_DELAY


def test_retryable_5xx_always_retryable():
    err = APIError("Internal Server Error", status_code=500)
    assert retryable(err) is not None


def test_retryable_429_rate_limit():
    err = APIError("rate limit exceeded", status_code=429)
    assert retryable(err) is not None


def test_retryable_503_overloaded():
    err = APIError("Service Unavailable — Provider Overloaded", status_code=503)
    assert retryable(err) is not None


def test_retryable_context_overflow_not_retryable():
    err = APIError("context_overflow — session too long")
    assert retryable(err) is None


def test_retryable_plain_text_rate_limit():
    err = APIError("Rate limit increased too quickly. Please slow down.")
    assert retryable(err) is not None


def test_retryable_nonretryable_flag():
    err = APIError("some error", status_code=400, is_retryable=False)
    assert retryable(err) is None


def test_retryable_429_with_explicit_false():
    err = APIError("rate limit", status_code=429, is_retryable=False)
    assert retryable(err) is None


def test_retry_policy_should_retry():
    policy = RetryPolicy(max_attempts=3)
    err = APIError("rate limit", status_code=429)
    assert policy.should_retry(1, err) is True
    assert policy.should_retry(2, err) is True
    assert policy.should_retry(3, err) is False


def test_retry_policy_should_not_retry_nonretryable():
    policy = RetryPolicy(max_attempts=3)
    err = APIError("bad request", status_code=400)
    assert policy.should_retry(1, err) is False


def test_api_error_construction():
    err = APIError("test message", status_code=500, is_retryable=True, response_headers={"retry-after": "1"})
    assert err.data.message == "test message"
    assert err.data.statusCode == 500
    assert err.data.isRetryable is True
    assert err.data.responseHeaders == {"retry-after": "1"}