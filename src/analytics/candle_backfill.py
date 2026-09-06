"""
Candle history backfill.

Fetches historical candles through a :class:`MarketDataGateway` and returns
a clean, deduplicated, time-ordered OHLCV frame ready for the walk-forward
harness. Exchange requests are chunked so a long history never exceeds a
single request's practical response size.

This module lives below the strategy layer: it must not import strategies.
"""

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd

from ..execution import MarketDataGateway

logger = logging.getLogger(__name__)

MS_PER_DAY = 86_400_000

# ~3k candles per request keeps responses comfortably inside exchange
# limits for common intervals.
DEFAULT_MAX_CANDLES_PER_REQUEST = 3_000


def interval_to_ms(interval: str) -> int:
    """Convert a candle interval string to milliseconds."""
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "8h": 28_800_000,
        "1d": 86_400_000,
        "1w": 604_800_000,
    }
    return mapping.get(interval, 3_600_000)


@dataclass(frozen=True)
class FetchWindow:
    """One half-open [start_ms, end_ms) request window."""

    start_ms: int
    end_ms: int


def build_fetch_windows(
    end_ms: int,
    days: int,
    interval_ms: int,
    max_candles_per_request: int = DEFAULT_MAX_CANDLES_PER_REQUEST,
) -> List[FetchWindow]:
    """Split ``[end_ms - days, end_ms)`` into ordered request windows.

    Raises:
        ValueError: if days or interval are non-positive.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")

    span_ms = max(interval_ms, 1) * max_candles_per_request
    start = end_ms - days * MS_PER_DAY
    windows: List[Tuple[int, int]] = []
    cursor = start
    while cursor < end_ms:
        window_end = min(cursor + span_ms, end_ms)
        windows.append((cursor, window_end))
        cursor = window_end
    return [FetchWindow(s, e) for s, e in windows]


def candles_to_frame(candles: List[dict]) -> pd.DataFrame:
    """Normalize raw exchange candle dicts into an OHLCV frame.

    Expects Hyperliquid candle keys (t, o, h, l, c, v); returns columns
    ``timestamp, open, high, low, close, volume`` with numeric values.
    """
    df = pd.DataFrame(candles)
    if df.empty:
        return df
    df = df.rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return pd.DataFrame()
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "t" not in df.columns:
        return pd.DataFrame()
    df["timestamp"] = pd.to_numeric(df["t"], errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    df = df.sort_values("timestamp")
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


async def backfill(
    market_data: MarketDataGateway,
    coin: str,
    interval: str,
    days: int,
    now_ms: Optional[int] = None,
    max_candles_per_request: int = DEFAULT_MAX_CANDLES_PER_REQUEST,
) -> pd.DataFrame:
    """Fetch ``days`` of candles for one coin, deduplicated and sorted.

    Window overlap is tolerated: duplicated timestamps (by open time) keep
    their first occurrence. Returns an empty frame when the gateway yields
    nothing usable.
    """
    interval_ms = interval_to_ms(interval)
    end_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    windows = build_fetch_windows(end_ms, days, interval_ms, max_candles_per_request)

    frames: List[pd.DataFrame] = []
    for window in windows:
        try:
            candles = await market_data.get_candles(
                coin, interval, window.start_ms, window.end_ms
            )
        except Exception as exc:
            logger.warning(
                "Backfill fetch failed for %s window %s: %s", coin, window, exc
            )
            continue
        frame = candles_to_frame(candles)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset="timestamp", keep="first")
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    return combined
