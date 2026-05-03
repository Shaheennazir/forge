from __future__ import annotations
import time
import structlog
from dataclasses import dataclass
from typing import Optional

log = structlog.get_logger(__name__)

RETRY_INITIAL_DELAY = 2000   # ms
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY = 2_147_483_647  # max 32-bit signed int
RETRY_MAX_DELAY_NO_HEADERS = 30_000  # ms


class APIError(Exception):
    """Minimal API error struct matching what LLM backends raise."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        is_retryable: Optional[bool] = None,
        response_headers: Optional[dict] = None,
    ):
        super().__init__(message)
        self.data = _ErrorData(message, status_code, is_retryable, response_headers or {})

    @staticmethod
    def is_instance(e) -> bool:
        return isinstance(e, APIError)


class _ErrorData:
    def __init__(self, message, status_code, is_retryable, response_headers):
        self.message = message
        self.statusCode = status_code
        self.isRetryable = is_retryable
        self.responseHeaders = response_headers


def cap(ms: float) -> float:
    return min(ms, RETRY_MAX_DELAY)


def delay(attempt: int, error: Optional[APIError] = None) -> float:
    """Compute delay in ms for a given attempt. Respects Retry-After headers."""
    if error and error.data.responseHeaders:
        headers = error.data.responseHeaders

        # retry-after-ms takes priority
        ms = headers.get("retry-after-ms")
        if ms:
            try:
                return cap(float(ms))
            except ValueError:
                pass

        # retry-after as seconds
        ra = headers.get("retry-after")
        if ra:
            try:
                return cap(float(ra) * 1000)
            except ValueError:
                pass
            # HTTP date format: "Tue, 01 Jan 2030 00:00:00 GMT"
            try:
                parsed = time.strptime(ra, "%a, %d %b %Y %H:%M:%S %Z")
                delta_ms = (time.mktime(parsed) - time.time()) * 1000
                if delta_ms > 0:
                    return cap(delta_ms)
            except ValueError:
                pass

        # Fallback to exponential
        return cap(RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)))

    return cap(min(
        RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)),
        RETRY_MAX_DELAY
    ))


def retryable(error: APIError) -> Optional[str]:
    """Classify an error. Return None if not retryable, or a reason string."""
    if not isinstance(error, APIError):
        return None
    d = error.data

    # Context overflow — never retry
    msg_lower = d.message.lower()
    if "context_overflow" in msg_lower or "context overflow" in msg_lower:
        return None

    # Explicit is_retryable = False
    if d.isRetryable is False:
        return None

    # 5xx always retry
    if d.statusCode and d.statusCode >= 500:
        return d.message or "Server error"

    # Overloaded pattern
    if "overloaded" in msg_lower:
        return d.message

    # Rate limit patterns in plain text
    if any(p in msg_lower for p in ("rate increased too quickly", "rate limit", "too many requests")):
        return d.message

    return None


@dataclass
class RetryPolicy:
    """Configurable retry policy for LLM calls."""
    max_attempts: int = 5
    initial_delay_ms: float = RETRY_INITIAL_DELAY
    backoff_factor: float = RETRY_BACKOFF_FACTOR
    max_delay_ms: float = RETRY_MAX_DELAY

    def compute_delay(self, attempt: int, error: Optional[APIError] = None) -> float:
        return delay(attempt, error)

    def is_retryable(self, error: APIError) -> bool:
        return retryable(error) is not None

    def should_retry(self, attempt: int, error: APIError) -> bool:
        if attempt >= self.max_attempts:
            return False
        return self.is_retryable(error)


def with_retry(policy: RetryPolicy, fn, *args, **kwargs):
    """
    Execute ``fn(*args, **kwargs)`` with exponential-backoff retry.

    Retries on retryable API errors (5xx, rate limits, overload).
    Does NOT retry on context overflow or non-API errors.
    Handles openai and anthropic SDK errors by normalising them to APIError.

    Raises:
        The last exception if all attempts are exhausted or the error is not retryable.

    Returns:
        The return value of ``fn``.
    """
    last_error: Optional[APIError] = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            api_err = _normalise_error(e)
            if api_err is None:
                raise  # non-API error — propagate immediately
            if not policy.should_retry(attempt, api_err):
                raise
            wait_ms = policy.compute_delay(attempt, api_err)
            log.warning(
                "retry.attempt_failed",
                attempt=attempt,
                max=policy.max_attempts,
                delay_ms=wait_ms,
                error=api_err.data.message,
                error_type=type(e).__name__,
            )
            time.sleep(wait_ms / 1000.0)
            last_error = api_err
    # Should not reach here, but guard anyway
    if last_error:
        raise last_error
    raise RuntimeError("retry.with_retry: unexpected exit")


def _normalise_error(e: Exception) -> Optional[APIError]:
    """Coerce openai / anthropic SDK errors (and APIError) into APIError."""
    if APIError.is_instance(e):
        return e

    # openai Python SDK
    if _is_openai_error(e):
        import requests
        msg = str(e)
        status = getattr(e, "status_code", None) or (
            e.response.status_code if isinstance(e, requests.HTTPError) and e.response else None
        )
        headers = (
            dict(e.response.headers)
            if isinstance(e, requests.HTTPError) and e.response else {}
        )
        return APIError(
            message=msg,
            status_code=status,
            is_retryable=None,
            response_headers=headers,
        )

    # anthropic Python SDK
    if _is_anthropic_error(e):
        msg = str(e)
        status = getattr(e, "status_code", None)
        try:
            body = e.body if hasattr(e, "body") else None
            if body and isinstance(body, dict):
                msg = body.get("error", {}).get("message", msg)
        except Exception:
            pass
        return APIError(message=msg, status_code=status, is_retryable=None, response_headers={})

    return None


def _is_openai_error(e: Exception) -> bool:
    """True if this is an openai Python SDK error."""
    mod = getattr(type(e), "__module__", "")
    return (
        "openai" in mod
        or type(e).__name__ in (
            "APIError", "APIStatusError", "AuthenticationError",
            "RateLimitError", "Timeout", "BadRequestError",
            "NotFoundError", "ConflictError", "UnprocessableEntityError",
        )
    ) and hasattr(e, "status_code")


def _is_anthropic_error(e: Exception) -> bool:
    """True if this is an anthropic Python SDK error."""
    name = type(e).__name__
    mod = getattr(type(e), "__module__", "")
    return (
        "anthropic" in mod
        or name == "APIException"
        or (name.endswith("Error") and "anthropic" in mod)
    )
