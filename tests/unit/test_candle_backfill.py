"""
Unit tests for candle backfill and the trend walk-forward engine adapter.
"""

import pandas as pd
import pytest

from src.analytics import (
    backfill,
    build_fetch_windows,
    candles_to_frame,
    interval_to_ms,
)
from src.core.config import Side
from src.strategy.trend_engine import TrendFollowingEngine
from src.strategy.trend_following import TrendFollowingConfig


# ---------------------------------------------------------------------------
# Window math
# ---------------------------------------------------------------------------


class TestBuildFetchWindows:
    def test_short_span_is_single_window(self):
        windows = build_fetch_windows(end_ms=10_000_000, days=2, interval_ms=3_600_000)
        assert len(windows) == 1
        assert windows[0].end_ms == 10_000_000
        assert windows[0].end_ms - windows[0].start_ms == 2 * 86_400_000

    def test_long_span_chunks_without_gaps_or_overlap(self):
        end = 1_000_000_000_000
        windows = build_fetch_windows(end, days=90, interval_ms=3_600_000)

        assert windows[0].start_ms == end - 90 * 86_400_000
        assert windows[-1].end_ms == end
        for prev, nxt in zip(windows, windows[1:]):
            assert prev.end_ms == nxt.start_ms
        total = sum(w.end_ms - w.start_ms for w in windows)
        assert total == 90 * 86_400_000

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            build_fetch_windows(0, days=0, interval_ms=3_600_000)
        with pytest.raises(ValueError):
            build_fetch_windows(0, days=5, interval_ms=0)

    def test_interval_to_ms(self):
        assert interval_to_ms("15m") == 900_000
        assert interval_to_ms("1h") == 3_600_000
        assert interval_to_ms("unknown") == 3_600_000  # 1h default


# ---------------------------------------------------------------------------
# Frame normalization
# ---------------------------------------------------------------------------


class TestCandlesToFrame:
    def test_renames_and_sorts_exchange_keys(self):
        frame = candles_to_frame(
            [
                {"t": 2, "o": "101", "h": "102", "l": "100", "c": "101.5", "v": "10"},
                {"t": 1, "o": "100", "h": "101", "l": "99", "c": "100.5", "v": "9"},
            ]
        )
        assert list(frame.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        assert frame["timestamp"].tolist() == [1, 2]
        assert frame["close"].tolist() == [100.5, 101.5]

    def test_missing_required_column_yields_empty(self):
        assert candles_to_frame([{"t": 1, "o": "1", "c": "1"}]).empty

    def test_empty_input_yields_empty(self):
        assert candles_to_frame([]).empty


# ---------------------------------------------------------------------------
# Backfill orchestration
# ---------------------------------------------------------------------------


class ScriptedGateway:
    """Returns scripted candles per requested window."""

    def __init__(self, candles_by_call):
        self.candles_by_call = list(candles_by_call)
        self.calls = []

    async def get_candles(self, coin, interval, start_ms, end_ms):
        self.calls.append((coin, interval, start_ms, end_ms))
        if not self.candles_by_call:
            return []
        return self.candles_by_call.pop(0)

    async def get_meta_and_asset_ctxs(self):  # unused by backfill
        raise NotImplementedError


class TestBackfill:
    @pytest.mark.asyncio
    async def test_deduplicates_overlapping_windows_and_sorts(self):
        # Two windows each returning candle t=1,2 then t=2,3 (overlap at 2)
        gateway = ScriptedGateway(
            [
                [
                    {"t": 1, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1"},
                    {"t": 2, "o": "2", "h": "2", "l": "2", "c": "2", "v": "1"},
                ],
                [
                    {"t": 2, "o": "2", "h": "2", "l": "2", "c": "2", "v": "1"},
                    {"t": 3, "o": "3", "h": "3", "l": "3", "c": "3", "v": "1"},
                ],
            ]
        )
        frame = await backfill(
            gateway, "HYPE", "1h", days=1, now_ms=10_000_000, max_candles_per_request=1
        )

        assert frame["timestamp"].tolist() == [1, 2, 3]
        assert len(gateway.calls) == 24

    @pytest.mark.asyncio
    async def test_window_fetch_failure_degrades_to_rest(self):
        class FailingGateway(ScriptedGateway):
            async def get_candles(self, coin, interval, start_ms, end_ms):
                if not self.calls:
                    self.calls.append((coin, interval, start_ms, end_ms))
                    raise RuntimeError("rpc down")
                return [{"t": 5, "o": "5", "h": "5", "l": "5", "c": "5", "v": "1"}]

        frame = await backfill(
            FailingGateway([]),
            "HYPE",
            "1h",
            days=1,
            now_ms=10_000_000,
            max_candles_per_request=1,
        )
        assert frame["timestamp"].tolist() == [5]

    @pytest.mark.asyncio
    async def test_no_data_yields_empty_frame(self):
        frame = await backfill(
            ScriptedGateway([[], []]), "HYPE", "1h", days=1, now_ms=1
        )
        assert frame.empty
        assert list(frame.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]


# ---------------------------------------------------------------------------
# Trend engine adapter
# ---------------------------------------------------------------------------


def uptrend_frame(n=90):
    closes = [
        100.0 * (1.003**i) * (1 + 0.006 * (1 if i % 2 == 0 else -1) * 0.5)
        for i in range(n)
    ]
    closes[-3] = closes[-4] * 0.996
    closes[-2] = closes[-3] * 0.9948
    closes[-1] = closes[-2] * 0.9968
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        rows.append(
            {
                "timestamp": i,
                "open": o,
                "high": max(o, c) * 1.002,
                "low": min(o, c) * 0.998,
                "close": c,
                "volume": 200.0 if i == n - 1 else 100.0,
            }
        )
    return pd.DataFrame(rows)


class TestTrendFollowingEngine:
    def _engine(self, **config_overrides) -> TrendFollowingEngine:
        from src.strategy.trend_following import TrendFollowingConfig

        return TrendFollowingEngine(
            TrendFollowingConfig(PAPER_TRADING=True, **config_overrides)
        )

    def test_insufficient_history_yields_no_signal(self):
        engine = self._engine()
        frame = uptrend_frame(30)
        for row in frame.to_dict("records"):
            engine.update_candle(row)
        assert engine.generate_signal(10_000.0) is None

    def test_uptrend_pullback_emits_long_with_tp_sl(self):
        engine = self._engine()
        for row in uptrend_frame(90).to_dict("records"):
            engine.update_candle(row)

        signal = engine.generate_signal(10_000.0)
        assert signal is not None
        assert signal["action"] == Side.LONG
        assert signal["sl_price"] < signal["entry_price"] < signal["tp_price"]
        expected_qty = round(10_000.0 * 0.10 / signal["entry_price"], 6)
        assert signal["quantity"] == expected_qty

    def test_duplicate_timestamps_do_not_accumulate(self):
        engine = self._engine()
        rows = uptrend_frame(70).to_dict("records")
        for row in rows:
            engine.update_candle(row)
        size_after_first = len(engine._frame)
        for row in rows[-10:]:
            engine.update_candle(row)  # re-feeds must not grow the frame
        assert len(engine._frame) == size_after_first

    def test_frame_is_trimmed_to_max_rows(self):
        engine = self._engine()
        for i in range(500):
            engine.update_candle(
                {
                    "timestamp": i,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            )
        assert len(engine._frame) == 200
        assert engine._frame["timestamp"].iloc[0] == 300


class TestEngineWindowIndexHealth:
    """Regression: after the 200-row trim, index labels must stay 0-based so
    indicator internals cannot misalign; ADX at the newest candle must be a
    real number, not nan."""

    def test_adx_at_newest_candle_is_real_after_trim(self):

        from src.strategy.trend_following import TrendFollowingStrategy

        engine = TrendFollowingEngine(TrendFollowingConfig(PAPER_TRADING=True))
        price = 100.0
        for i in range(400):
            step = 1.0 if i % 2 == 0 else -0.6
            price = max(price + step, 50.0)
            engine.update_candle(
                {
                    "timestamp": i,
                    "open": price - step,
                    "high": max(price, price - step) + 0.2,
                    "low": min(price, price - step) - 0.2,
                    "close": price,
                    "volume": 10.0,
                }
            )

        assert len(engine._frame) == 200
        assert engine._frame.index[0] == 0  # labels stay 0-based after trim

        adx = TrendFollowingStrategy._calculate_adx(engine._frame, 14)
        assert not pd.isna(adx.iloc[-1])
