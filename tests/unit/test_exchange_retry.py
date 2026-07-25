"""
Unit tests for exchange API retry logic with exponential backoff.

Tests cover:
- Retry on transient errors (succeeds on Nth try)
- No retry on non-retryable errors
- Exponential delay calculation
- Max retries exhausted
- Jitter is applied
"""

import asyncio
import logging
import random
from unittest.mock import AsyncMock, patch

import pytest

from src.exchange.retry import (
    NonRetryableError,
    RetryableError,
    calculate_delay,
    is_retryable,
    retry_with_backoff,
)


# ------------------------------------------------------------------ #
# is_retryable tests
# ------------------------------------------------------------------ #


class TestIsRetryable:
    """Tests for the is_retryable() error classifier."""

    def test_connection_error_is_retryable(self):
        assert is_retryable(ConnectionError("refused")) is True

    def test_timeout_error_is_retryable(self):
        assert is_retryable(TimeoutError("timed out")) is True

    def test_asyncio_timeout_is_retryable(self):
        assert is_retryable(asyncio.TimeoutError()) is True

    def test_os_error_is_retryable(self):
        assert is_retryable(OSError("network unreachable")) is True

    def test_broken_pipe_is_retryable(self):
        assert is_retryable(BrokenPipeError()) is True

    def test_value_error_is_not_retryable(self):
        assert is_retryable(ValueError("bad input")) is False

    def test_type_error_is_not_retryable(self):
        assert is_retryable(TypeError("wrong type")) is False

    def test_explicit_retryable_error(self):
        assert is_retryable(RetryableError(ValueError("wrap me"))) is True

    def test_explicit_non_retryable_error(self):
        assert is_retryable(NonRetryableError(ConnectionError("don't retry"))) is False

    def test_http_500_in_message(self):
        assert is_retryable(RuntimeError("HTTP 500 Internal Server Error")) is True

    def test_http_502_in_message(self):
        assert is_retryable(RuntimeError("HTTP 502 Bad Gateway")) is True

    def test_http_503_in_message(self):
        assert is_retryable(RuntimeError("HTTP 503 Service Unavailable")) is True

    def test_http_429_in_message(self):
        assert is_retryable(RuntimeError("HTTP 429 Too Many Requests")) is True

    def test_http_200_is_not_retryable(self):
        # "200" should not match any retryable codes
        assert is_retryable(RuntimeError("HTTP 200 OK")) is False

    def test_rate_limit_in_message(self):
        assert is_retryable(Exception("rate limit exceeded")) is True

    def test_gateway_in_message(self):
        assert is_retryable(Exception("bad gateway response")) is True

    def test_generic_exception_not_retryable(self):
        assert is_retryable(Exception("something went wrong")) is False

    def test_insufficient_balance_not_retryable(self):
        assert is_retryable(ValueError("Insufficient balance")) is False


# ------------------------------------------------------------------ #
# calculate_delay tests
# ------------------------------------------------------------------ #


class TestCalculateDelay:
    """Tests for the exponential backoff delay calculation."""

    def test_first_retry_delay(self):
        delay = calculate_delay(0, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 1.0  # 1.0 * 2^0 = 1.0

    def test_second_retry_delay(self):
        delay = calculate_delay(1, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 2.0  # 1.0 * 2^1 = 2.0

    def test_third_retry_delay(self):
        delay = calculate_delay(2, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 4.0  # 1.0 * 2^2 = 4.0

    def test_fourth_retry_delay(self):
        delay = calculate_delay(3, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 8.0  # 1.0 * 2^3 = 8.0

    def test_max_delay_cap(self):
        delay = calculate_delay(10, base_delay=1.0, max_delay=30.0, jitter=False)
        assert delay == 30.0  # 1.0 * 2^10 = 1024, capped at 30

    def test_custom_base_delay(self):
        delay = calculate_delay(2, base_delay=2.0, max_delay=60.0, jitter=False)
        assert delay == 8.0  # 2.0 * 2^2 = 8.0

    def test_jitter_increases_delay_range(self):
        """With jitter, delay should be >= base exponential and < base + jitter_range."""
        random.seed(42)
        delays = [
            calculate_delay(1, base_delay=1.0, max_delay=30.0, jitter=True)
            for _ in range(100)
        ]
        # Without jitter: 2.0. With jitter: [2.0, 2.5)
        assert all(2.0 <= d < 2.5 for d in delays)

    def test_jitter_produces_varying_delays(self):
        """Jitter should produce different values across calls."""
        random.seed(42)
        delays = {calculate_delay(0, jitter=True) for _ in range(50)}
        # With random jitter, we should get many distinct values
        assert len(delays) > 10

    def test_zero_base_delay(self):
        delay = calculate_delay(5, base_delay=0.0, max_delay=30.0, jitter=False)
        assert delay == 0.0


# ------------------------------------------------------------------ #
# retry_with_backoff tests
# ------------------------------------------------------------------ #


class TestRetryWithBackoff:
    """Tests for the retry_with_backoff() async wrapper."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Function succeeds immediately — no retries needed."""
        fn = AsyncMock(return_value="ok")
        result = await retry_with_backoff(fn, max_retries=3)
        assert result == "ok"
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_third_try(self):
        """Function fails twice with ConnectionError, succeeds on 3rd call."""
        fn = AsyncMock(
            side_effect=[ConnectionError("fail"), ConnectionError("fail"), "success"]
        )
        with patch(
            "src.exchange.retry.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            result = await retry_with_backoff(
                fn, max_retries=3, base_delay=0.01, jitter=False
            )

        assert result == "success"
        assert fn.call_count == 3
        # Should have slept twice (before retry 1 and retry 2)
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        """Non-retryable errors propagate immediately without retrying."""
        fn = AsyncMock(side_effect=ValueError("invalid input"))
        with pytest.raises(ValueError, match="invalid input"):
            await retry_with_backoff(fn, max_retries=3)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self):
        """All attempts fail — the last error is raised."""
        fn = AsyncMock(side_effect=ConnectionError("network down"))
        with patch("src.exchange.retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="network down"):
                await retry_with_backoff(fn, max_retries=3, base_delay=0.01)
        # 1 initial + 3 retries = 4 total calls
        assert fn.call_count == 4

    @pytest.mark.asyncio
    async def test_exponential_delay_values(self):
        """Verify delays follow exponential backoff pattern."""
        fn = AsyncMock(
            side_effect=[
                ConnectionError("fail"),
                ConnectionError("fail"),
                ConnectionError("fail"),
                "ok",
            ]
        )
        with patch(
            "src.exchange.retry.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await retry_with_backoff(
                fn, max_retries=3, base_delay=1.0, max_delay=30.0, jitter=False
            )

        # attempt 0: delay = 1.0 * 2^0 = 1.0
        # attempt 1: delay = 1.0 * 2^1 = 2.0
        # attempt 2: delay = 1.0 * 2^2 = 4.0
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_jitter_applied_to_delays(self):
        """When jitter=True, delays should vary (not all identical)."""
        fn = AsyncMock(
            side_effect=[
                ConnectionError("fail"),
                ConnectionError("fail"),
                ConnectionError("fail"),
                "ok",
            ]
        )
        with patch(
            "src.exchange.retry.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await retry_with_backoff(fn, max_retries=3, base_delay=1.0, jitter=True)

        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        # Each delay should be >= base (1.0, 2.0, 4.0) but < base + 0.5
        assert actual_delays[0] >= 1.0
        assert actual_delays[1] >= 2.0
        assert actual_delays[2] >= 4.0
        # With jitter, values should exceed the base
        # (statistically very likely with 3 retries, but check at least one)
        assert any(d > base for d, base in zip(actual_delays, [1.0, 2.0, 4.0]))

    @pytest.mark.asyncio
    async def test_timeout_error_is_retried(self):
        """TimeoutError triggers retry."""
        fn = AsyncMock(side_effect=[TimeoutError("timed out"), "ok"])
        with patch("src.exchange.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_with_backoff(fn, max_retries=2, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_asyncio_timeout_error_is_retried(self):
        """asyncio.TimeoutError triggers retry."""
        fn = AsyncMock(side_effect=[asyncio.TimeoutError(), "ok"])
        with patch("src.exchange.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_with_backoff(fn, max_retries=2, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_http_503_error_is_retried(self):
        """RuntimeError with 503 status in message triggers retry."""
        fn = AsyncMock(side_effect=[RuntimeError("HTTP 503 Service Unavailable"), "ok"])
        with patch("src.exchange.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await retry_with_backoff(fn, max_retries=2, base_delay=0.01)
        assert result == "ok"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_with_args_and_kwargs(self):
        """Arguments and keyword arguments are forwarded to the function."""

        async def my_func(a, b, key=None):
            return (a, b, key)

        result = await retry_with_backoff(my_func, 1, 2, key="test")
        assert result == (1, 2, "test")

    @pytest.mark.asyncio
    async def test_zero_max_retries_no_retry(self):
        """With max_retries=0, function runs once and errors propagate."""
        fn = AsyncMock(side_effect=ConnectionError("fail"))
        with pytest.raises(ConnectionError):
            await retry_with_backoff(fn, max_retries=0)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_non_retryable_explicit_wrapper(self):
        """NonRetryableError wrapper prevents retry even for ConnectionError."""
        fn = AsyncMock(side_effect=NonRetryableError(ConnectionError("don't retry")))
        with pytest.raises(NonRetryableError):
            await retry_with_backoff(fn, max_retries=3)
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_logs_warnings(self, caplog):
        """Each retry attempt logs a warning."""
        fn = AsyncMock(
            side_effect=[ConnectionError("fail"), ConnectionError("fail"), "ok"]
        )
        with patch("src.exchange.retry.asyncio.sleep", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING, logger="src.exchange.retry"):
                await retry_with_backoff(
                    fn, max_retries=3, base_delay=0.01, jitter=False
                )

        retry_logs = [r for r in caplog.records if "Retry" in r.message]
        assert len(retry_logs) == 2

    @pytest.mark.asyncio
    async def test_max_delay_is_respected(self):
        """Delays should never exceed max_delay (without jitter)."""
        fn = AsyncMock(side_effect=[ConnectionError("fail")] * 6 + ["ok"])
        with patch(
            "src.exchange.retry.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await retry_with_backoff(
                fn, max_retries=6, base_delay=5.0, max_delay=10.0, jitter=False
            )

        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        # attempt 0: 5*1=5, attempt 1: 5*2=10, attempt 2+: capped at 10
        for d in actual_delays:
            assert d <= 10.0
        assert actual_delays[2:] == [10.0] * 4
