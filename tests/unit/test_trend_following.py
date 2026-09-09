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
    cfg = TrendFollowingConfig(
        **{
            "PAPER_TRADING": True,
            # In-memory sentinel keeps paper-state persistence off unless a
            # test explicitly opts in with its own tmp_path database — tests
            # must never read or write the live bot's state file.
            "DATABASE_PATH": ":memory:",
            **config_overrides,
        }
    )
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


# ---------------------------------------------------------------------------
# Unmeasurable indicators must fail closed
# ---------------------------------------------------------------------------


class TestUnmeasurableIndicators:
    """Regression: `nan < threshold` is False in float comparison, so a nan
    indicator value silently passed every guard. On real data the rolling
    ADX degenerates to nan often enough that the regime gate was largely
    vacuous (an ADX_THRESHOLD=100 control still produced 20 trades)."""

    GOOD_FRAME = dict(drift=0.003, amp=0.006, freq=1.7, pull=0.004)

    def test_control_frame_signals_normally(self):
        strategy = make_strategy()
        df = build_frame(**self.GOOD_FRAME)
        assert strategy._analyze_trend("TEST", df) is not None

    def _patch(self, strategy, method, series_mutator):
        real = getattr(TrendFollowingStrategy, method)

        def patched(*args, **kwargs):
            series = real(*args, **kwargs)
            if series is not None:
                series.iloc[-1] = series_mutator
            return series

        setattr(strategy, method, patched)

    def test_nan_adx_blocks_signal(self):
        strategy = make_strategy()
        df = build_frame(**self.GOOD_FRAME)
        self._patch(strategy, "_calculate_adx", float("nan"))
        assert strategy._analyze_trend("TEST", df) is None

    def test_nan_atr_blocks_signal(self):
        strategy = make_strategy()
        df = build_frame(**self.GOOD_FRAME)
        self._patch(strategy, "_calculate_atr", float("nan"))
        assert strategy._analyze_trend("TEST", df) is None

    def test_nan_rsi_blocks_signal(self):
        strategy = make_strategy()
        df = build_frame(**self.GOOD_FRAME)
        self._patch(strategy, "_calculate_rsi", float("nan"))
        assert strategy._analyze_trend("TEST", df) is None

    def test_calculate_adx_is_index_aligned(self):
        """Regression: _calculate_adx built its DM series with a fresh
        RangeIndex, so any frame whose labels were not 0-based (e.g. a
        trimmed rolling window) produced nan at the newest candle via
        union-join division."""
        df = build_frame(**self.GOOD_FRAME).drop(columns=["t"])
        adx_zero_based = TrendFollowingStrategy._calculate_adx(df, 14)

        shifted = df.copy()
        shifted.index = shifted.index + 7  # non-zero-based labels
        adx_shifted = TrendFollowingStrategy._calculate_adx(shifted, 14)

        assert not adx_zero_based.iloc[-1] != adx_zero_based.iloc[-1]  # not nan
        assert adx_shifted.iloc[-1] == pytest.approx(adx_zero_based.iloc[-1])


# ---------------------------------------------------------------------------
# Live order direction (regression: string sides compared False against
# Side and would have sold every live LONG entry)
# ---------------------------------------------------------------------------


class TestLiveOrderDirection:
    def _live_strategy(self):
        from src.core.config import Side as CoreSide

        strategy = make_strategy(
            PAPER_TRADING=False,
            PRIVATE_KEY="0x" + "1" * 64,
            ADDRESS="0x" + "1" * 40,
        )
        strategy.api.place_order = AsyncMock(return_value={"status": "ok"})
        strategy.api.get_balance = AsyncMock(return_value={"account_value": 10_000})
        return strategy, CoreSide

    @pytest.mark.asyncio
    async def test_open_long_places_buy(self):
        strategy, CoreSide = self._live_strategy()

        await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )

        kwargs = strategy.api.place_order.call_args.kwargs
        assert kwargs["side"] == CoreSide.LONG

    @pytest.mark.asyncio
    async def test_close_long_places_sell(self):
        strategy, CoreSide = self._live_strategy()

        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, price=100.0, atr=1.0
        )
        strategy.api.place_order.reset_mock()
        closed = await strategy.close_position(
            pos_id, "take_profit", current_price=105.0
        )

        assert closed is True
        kwargs = strategy.api.place_order.call_args.kwargs
        assert kwargs["side"] == CoreSide.SHORT
        assert kwargs["reduce_only"] is True


# ---------------------------------------------------------------------------
# Paper-state restart continuity
# ---------------------------------------------------------------------------


class TestPaperStateContinuity:
    """Capital alone cannot survive a restart: open positions would be
    orphaned and closed-trade history (the scorecard's evidence) lost."""

    @staticmethod
    def _mock_api():
        api = Mock()
        api.get_mids = AsyncMock(return_value={})
        return api

    @pytest.mark.asyncio
    async def test_open_position_survives_restart(self, tmp_path):
        cfg = TrendFollowingConfig(
            PAPER_TRADING=True, DATABASE_PATH=str(tmp_path / "trend_test.db")
        )
        db = Mock()
        db.log_event = Mock()
        strategy = TrendFollowingStrategy(cfg, self._mock_api(), db)
        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, 100.0, 2.0
        )
        assert pos_id is not None
        capital_after_open = strategy._paper_capital

        restarted = TrendFollowingStrategy(cfg, self._mock_api(), db)

        assert restarted._paper_capital == pytest.approx(capital_after_open)
        restored = restarted._positions.get(pos_id)
        assert restored is not None
        assert restored.status == TrendPositionStatus.OPEN
        assert restored.coin == "TEST"
        assert restored.entry_price == 100.0
        assert restored.stop_loss == strategy._positions[pos_id].stop_loss
        assert restored.take_profit == strategy._positions[pos_id].take_profit
        assert restored.trailing_stop == strategy._positions[pos_id].trailing_stop

    @pytest.mark.asyncio
    async def test_closed_trade_restores_capital_scorecard_and_cooldown(
        self, tmp_path
    ):
        from src.storage.database import DatabaseManager

        db_path = tmp_path / "trend_test.db"
        cfg = TrendFollowingConfig(PAPER_TRADING=True, DATABASE_PATH=str(db_path))
        strategy = TrendFollowingStrategy(
            cfg, self._mock_api(), DatabaseManager(str(db_path))
        )
        pos_id = await strategy.open_position(
            "TEST", TrendPositionSide.LONG, 100.0, 2.0
        )
        assert await strategy.close_position(pos_id, "stop_loss", 90.0) is True
        original_summary = strategy.get_status()["summary"]
        assert original_summary["closed_count"] == 1

        restarted = TrendFollowingStrategy(
            cfg, self._mock_api(), DatabaseManager(str(db_path))
        )

        assert restarted._paper_capital == pytest.approx(strategy._paper_capital)
        # Scorecard replays DB trade rows — the retire verdict survives.
        assert restarted.scorecard.trade_count == 1
        summary = restarted.get_status()["summary"]
        assert summary["closed_count"] == 1
        assert summary["total_pnl"] == pytest.approx(original_summary["total_pnl"])
        # Restored closes re-arm the entry cooldown for that coin.
        assert "TEST" in restarted._coin_cooldowns

    def test_persistence_disabled_writes_no_state_file(self, tmp_path):
        cfg = TrendFollowingConfig(
            PAPER_TRADING=True,
            PERSIST_PAPER_CAPITAL=False,
            DATABASE_PATH=str(tmp_path / "trend_test.db"),
        )
        TrendFollowingStrategy(cfg, self._mock_api(), Mock())
        assert not (tmp_path / "trend_test.db.paper_state.json").exists()
