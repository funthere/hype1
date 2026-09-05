"""
Unit tests for the trend following strategy.

Signal and exit logic are exercised through synthetic candle frames and a
mocked gateway port, so no network or SDK involvement is required.
"""

import time
from unittest.mock import AsyncMock, Mock

import numpy as np
import pandas as pd
import pytest

from src.strategy.trend_following import (
    TrendFollowingConfig,
    TrendFollowingStrategy,
    TrendPosition,
    TrendPositionSide,
    TrendPositionStatus,
)


# ---------------------------------------------------------------------------
# Synthetic candle builders
# ---------------------------------------------------------------------------


def build_frame(drift, amp, freq, pull, n=90, base=100.0):
    """Build an OHLCV frame: geometric drift with a sinusoidal wiggle and a
    pullback tail over the final three candles (positive `pull` pulls the
    price back towards the fast EMA in an uptrend; mirrored for downtrends).
    """
    drift_steps = np.array([(1.0 + drift) ** i for i in range(n)])
    wiggle = 1 + amp * np.sin(np.arange(n) * freq)
    closes = base * drift_steps * wiggle
    # Pullback moves price back TOWARDS the fast EMA: down in an uptrend,
    # up in a downtrend.
    pull_sign = -1.0 if drift >= 0 else 1.0
    closes[-3] = closes[-4] * (1 + pull_sign * pull)
    closes[-2] = closes[-3] * (1 + pull_sign * pull * 1.3)
    closes[-1] = closes[-2] * (1 + pull_sign * pull * 0.8)

    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        rows.append(
            {
                "t": i,
                "open": o,
                "close": c,
                "high": max(o, c) * 1.002,
                "low": min(o, c) * 0.998,
                # Last candle carries elevated volume so the volume filter
                # passes unless a test explicitly disables it.
                "volume": 200.0 if i == n - 1 else 100.0,
            }
        )
    return pd.DataFrame(rows)


def make_strategy(**config_overrides) -> TrendFollowingStrategy:
    cfg = TrendFollowingConfig(PAPER_TRADING=True, **config_overrides)
    api = Mock()
    api.get_mids = AsyncMock(return_value={})
    db = Mock()
    db.log_event = Mock()
    db.save_trade = Mock()
    db.save_daily_summary = Mock()
    return TrendFollowingStrategy(cfg, api, db)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


class TestAnalyzeTrend:
    def test_long_signal_on_pullback_in_uptrend(self):
        strategy = make_strategy()
        df = build_frame(drift=0.003, amp=0.006, freq=1.7, pull=0.004)

        signal = strategy._analyze_trend("TEST", df)

        assert signal is not None
        assert signal["side"] == TrendPositionSide.LONG
        assert 0 <= signal["confidence"] <= 100

    def test_short_signal_on_pullback_in_downtrend(self):
        strategy = make_strategy()
        df = build_frame(drift=-0.003, amp=0.006, freq=1.7, pull=0.004)

        signal = strategy._analyze_trend("TEST", df)

        assert signal is not None
        assert signal["side"] == TrendPositionSide.SHORT

    def test_signal_fires_without_fresh_crossover(self):
        """Regression: entries required an EMA cross on the very last candle,
        buying the top of the move. An established trend with a pullback must
        be enterable even when the crossover happened many candles ago."""
        strategy = make_strategy()
        df = build_frame(drift=0.003, amp=0.006, freq=1.7, pull=0.004)

        close = df["close"]
        fast = close.ewm(span=9).mean()
        assert fast.iloc[-2] > fast.iloc[-3], "crossover must not be on the last bar"

        signal = strategy._analyze_trend("TEST", df)
        assert signal is not None, "established trend + pullback must still enter"

    def test_chop_generates_no_signal(self):
        strategy = make_strategy()
        rng = np.random.default_rng(11)
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.003, 90))

        rows = []
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            rows.append(
                {
                    "t": i,
                    "open": o,
                    "close": c,
                    "high": max(o, c) * 1.002,
                    "low": min(o, c) * 0.998,
                    "volume": 100.0,
                }
            )
        signal = strategy._analyze_trend("TEST", pd.DataFrame(rows))
        assert signal is None

    def test_volume_filter_blocks_low_volume(self):
        strategy = make_strategy()
        df = build_frame(drift=0.003, amp=0.006, freq=1.7, pull=0.004)
        df.loc[df.index[-1], "volume"] = 100.0  # below 20-SMA * 1.2

        assert strategy._analyze_trend("TEST", df) is None

    def test_rsi_filter_blocks_overbought_long(self):
        """A steep low-oscillation rally is trending but overbought; the RSI
        guard must reject the long even though everything else passes."""
        strategy = make_strategy()
        df = build_frame(drift=0.004, amp=0.004, freq=1.7, pull=0.004)
        close = df["close"]
        rsi = TrendFollowingStrategy._calculate_rsi(close, 14).iloc[-1]
        assert rsi > 70  # the frame is genuinely overbought

        signal = strategy._analyze_trend("TEST", df)
        assert signal is None

    def test_missing_volume_column_is_rejected(self):
        strategy = make_strategy()
        df = build_frame(drift=0.003, amp=0.006, freq=1.7, pull=0.004)
        df = df.drop(columns=["volume"])

        assert strategy._analyze_trend("TEST", df) is None


# ---------------------------------------------------------------------------
# Position opening guards
# ---------------------------------------------------------------------------


class TestOpenPositionGuards:
    @pytest.mark.asyncio
    async def test_blacklisted_coin_rejected(self):
        strategy = make_strategy()
        assert "VVV" in strategy.config.BLACKLIST

        pos_id = await strategy.open_position(
            "VVV", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        assert pos_id is None

    @pytest.mark.asyncio
    async def test_short_only_mode_rejects_long(self):
        strategy = make_strategy(SHORT_ONLY=True)
        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        assert pos_id is None

    @pytest.mark.asyncio
    async def test_duplicate_position_rejected(self):
        strategy = make_strategy()
        first = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        second = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.5, atr=1.0
        )
        assert first is not None
        assert second is None

    @pytest.mark.asyncio
    async def test_cooldown_blocks_reentry(self):
        strategy = make_strategy()
        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        await strategy.close_position(pos_id, "take_profit", current_price=105.0)

        reentry = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        assert reentry is None

    @pytest.mark.asyncio
    async def test_max_concurrent_positions(self):
        strategy = make_strategy(MAX_CONCURRENT_POSITIONS=1, COINS=["A", "B"])
        first = await strategy.open_position(
            "A", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        second = await strategy.open_position(
            "B", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        assert first is not None
        assert second is None


# ---------------------------------------------------------------------------
# Exit ladder
# ---------------------------------------------------------------------------


def inject_position(strategy, **overrides) -> TrendPosition:
    pos = TrendPosition(
        id="testpos1",
        coin="TEST",
        side=TrendPositionSide.LONG,
        entry_price=100.0,
        quantity=1.0,
        notional=100.0,
        entry_time=time.time(),
        atr_at_entry=1.0,
        stop_loss=100.0 - 2.0,  # ATR_STOP_MULT=2.0
        take_profit=100.0 + 4.0,  # ATR_TP_MULT=4.0
    )
    for key, value in overrides.items():
        setattr(pos, key, value)
    strategy._positions[pos.id] = pos
    return pos


class TestExitLadder:
    def _strategy_with_price(self, price) -> TrendFollowingStrategy:
        strategy = make_strategy()
        strategy.api.get_mids = AsyncMock(return_value={"TEST": str(price)})
        return strategy

    @pytest.mark.asyncio
    async def test_stop_loss_closes_long(self):
        strategy = self._strategy_with_price(97.0)
        pos = inject_position(strategy)

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.CLOSED
        assert pos.close_reason.startswith("stop_loss")

    @pytest.mark.asyncio
    async def test_take_profit_closes_long(self):
        strategy = self._strategy_with_price(105.0)
        pos = inject_position(strategy)

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.CLOSED
        assert pos.close_reason.startswith("take_profit")

    @pytest.mark.asyncio
    async def test_trailing_stop_locks_profit(self):
        # Trail sits at 105 - 2.5 = 102.5; price slipping to 102 breaches it
        strategy = self._strategy_with_price(102.0)
        pos = inject_position(
            strategy,
            highest_profit_price=105.0,
            trailing_stop=102.5,
        )

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.CLOSED
        assert pos.close_reason.startswith("trailing_stop")

    @pytest.mark.asyncio
    async def test_trailing_stop_ratchets_upward_only(self):
        """A price retreat must tighten the trail, never loosen it."""
        strategy = self._strategy_with_price(103.0)
        pos = inject_position(
            strategy,
            highest_profit_price=106.0,
            trailing_stop=106.0 - 2.5,
        )

        await strategy.check_existing_positions()

        assert pos.trailing_stop == 106.0 - 2.5

    @pytest.mark.asyncio
    async def test_max_hold_time_closes_position(self):
        strategy = self._strategy_with_price(101.0)
        pos = inject_position(
            strategy,
            entry_time=time.time() - 200 * 3600,  # > MAX_HOLD_HOURS
        )

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.CLOSED
        assert pos.close_reason.startswith("max_hold")

    @pytest.mark.asyncio
    async def test_max_loss_closes_position(self):
        strategy = self._strategy_with_price(94.0)  # -6% > MAX_LOSS_PCT (5%)
        pos = inject_position(strategy, stop_loss=0.0)

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.CLOSED
        assert pos.close_reason.startswith("max_loss")

    @pytest.mark.asyncio
    async def test_healthy_position_stays_open(self):
        strategy = self._strategy_with_price(101.0)
        pos = inject_position(strategy)

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.OPEN

    @pytest.mark.asyncio
    async def test_missing_mid_leaves_position_open(self):
        strategy = make_strategy()
        strategy.api.get_mids = AsyncMock(return_value={})
        pos = inject_position(strategy)

        await strategy.check_existing_positions()

        assert pos.status == TrendPositionStatus.OPEN


# ---------------------------------------------------------------------------
# Scorecard integration
# ---------------------------------------------------------------------------


class TestScorecardIntegration:
    @pytest.mark.asyncio
    async def test_closed_trades_feed_scorecard_status(self):
        strategy = make_strategy()
        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        status = strategy.get_status()
        assert status["summary"]["scorecard"]["trades"] == 0

        await strategy.close_position(pos_id, "take_profit", current_price=106.0)

        status = strategy.get_status()
        scorecard = status["summary"]["scorecard"]
        assert scorecard["trades"] == 1
        assert scorecard["expectancy"] > 0  # closed in profit
        assert scorecard["retire_recommended"] is False  # insufficient data
        assert strategy.scorecard.trade_count == 1
