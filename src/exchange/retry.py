"""
Retry utility with exponential backoff for exchange API calls.

Provides a standalone async retry helper that wraps any callable with
configurable exponential backoff, jitter, and retryable-error filtering.
"""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Tuple, Type

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Retryable error types
# ------------------------------------------------------------------ #
# Network / transport layer
RETRYABLE_ERRORS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)

# HTTP status codes that indicate transient failures
RETRYABLE_HTTP_CODES = (429, 500, 502, 503, 504)


class RetryableError(Exception):
    """Wrapper to explicitly mark an error as retryable."""

    def __init__(self, original: Exception):
        self.original = original
        super().__init__(str(original))


class NonRetryableError(Exception):
    """Wrapper to explicitly mark an error as non-retryable."""

    def __init__(self, original: Exception):
        self.original = original
        super().__init__(str(original))


def is_retryable(exc: Exception) -> bool:
    """Determine if an exception is transient and worth retrying.

    Checks against:
    1. Explicit RetryableError wrappers
    2. Built-in network/timeout error types
    3. HTTP errors containing retryable status codes in their message
    """
    # Explicitly marked
    if isinstance(exc, RetryableError):
        return True
    if isinstance(exc, NonRetryableError):
        return False

    # Built-in transient errors
    if isinstance(exc, RETRYABLE_ERRORS):
        return True

    # Check for HTTP-style errors with status codes in the message
    msg = str(exc).lower()
    for code in RETRYABLE_HTTP_CODES:
        if str(code) in msg:
            return True

    # Common transient error substrings
    transient_hints = (
        "timed out",
        "connection refused",
        "connection reset",
        "temporary failure",
        "service unavailable",
        "gateway",
        "too many requests",
        "rate limit",
    )
    if any(hint in msg for hint in transient_hints):
        return True

    return False


def calculate_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """Calculate the delay for a given attempt using exponential backoff.

    Formula: min(base_delay * 2^attempt, max_delay) + random jitter

    Args:
        attempt: Zero-indexed attempt number (0 = first retry).
        base_delay: Base delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: Whether to add random jitter (0 to base_delay*0.5).

    Returns:
        Delay in seconds.
    """
    delay = min(base_delay * (2**attempt), max_delay)
    if jitter:
        delay += random.uniform(0, base_delay * 0.5)
    return delay


async def retry_with_backoff(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    **kwargs: Any,
) -> Any:
    """Execute an async function with exponential backoff retry logic.

    Only retries on transient errors (network, timeout, specific HTTP errors).
    Non-retryable errors (validation, insufficient balance, etc.) propagate
    immediately.

    Args:
        fn: Async callable to execute.
        *args: Positional arguments passed to *fn*.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Base delay in seconds for exponential backoff.
        max_delay: Maximum delay cap in seconds.
        jitter: Whether to add random jitter to delays.
        **kwargs: Keyword arguments passed to *fn*.

    Returns:
        The return value of *fn* on success.

    Raises:
        The last encountered exception if all retries are exhausted.
    """
    last_exc: Exception = RuntimeError("unreachable")

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc

            function_name = getattr(fn, "__name__", None) or type(fn).__name__
            if not is_retryable(exc):
                logger.debug(f"Non-retryable error in {function_name}: {exc}")
                raise

            if attempt >= max_retries:
                logger.warning(
                    f"Retry exhausted for {function_name} after "
                    f"{max_retries} retries: {exc}"
                )
                raise

            delay = calculate_delay(
                attempt, base_delay=base_delay, max_delay=max_delay, jitter=jitter
            )
            logger.warning(
                f"Retry {attempt + 1}/{max_retries} for {function_name} "
                f"after {delay:.2f}s — error: {exc}"
            )
            await asyncio.sleep(delay)

    # Should never reach here, but satisfy type checkers
    raise last_exc
