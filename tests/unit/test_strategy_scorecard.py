"""
Unit tests for the strategy scorecard and walk-forward validator.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.analytics import RetirePolicy, StrategyScorecard, WalkForwardValidator
from src.core.config import Side, Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(pnl, fees=0.0, hours_ago=0, side=Side.LONG) -> Trade:
    exit_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return Trade(
        side=side,
        entry_price=100.0,
        exit_price=101.0 if pnl > 0 else 99.0,
        quantity=1.0,
        entry_time=exit_time - timedelta(hours=1),
        exit_time=exit_time,
        pnl=pnl,
        fees=fees,
    )


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


class TestScorecardMetrics:
    def test_empty_scorecard(self):
        card = StrategyScorecard("t")
        snap = card.snapshot()
        assert snap.total_trades == 0
        assert snap.expectancy == 0.0
        assert snap.profit_factor == float("inf")
        assert card.should_retire() == (False, "insufficient data (no trades)")

    def test_expectancy_and_win_rate(self):
        card = StrategyScorecard("t")
        for pnl in (50.0, -20.0, 30.0, -10.0):
            card.record(make_trade(pnl))

        snap = card.snapshot()
        assert snap.total_trades == 4
        assert snap.wins == 2
        assert snap.losses == 2
        assert snap.win_rate == 0.5
        assert snap.expectancy == pytest.approx(12.5)
        assert snap.total_net_pnl == pytest.approx(50.0)
        assert snap.profit_factor == pytest.approx(80.0 / 30.0)

    def test_profit_factor_infinite_without_losses(self):
        card = StrategyScorecard("t")
        card.record(make_trade(10.0))
        assert card.snapshot().profit_factor == float("inf")

    def test_max_drawdown_tracks_equity_trough(self):
        card = StrategyScorecard("t", initial_capital=1000.0)
        # equity: 1100 → 1000 (dd 100 from peak 1100) → 1050
        card.record(make_trade(100.0))
        card.record(make_trade(-100.0))
        card.record(make_trade(50.0))

        assert card.snapshot().max_drawdown_pct == pytest.approx(100.0 / 1100.0)

    def test_weekly_summary_buckets_by_iso_week(self):
        card = StrategyScorecard("t")
        this_week = datetime.now(timezone.utc)
        last_week = this_week - timedelta(weeks=1)
        card.record(make_trade(10.0, hours_ago=0))
        trade = make_trade(-4.0)
        trade.exit_time = last_week
        trade.entry_time = last_week - timedelta(hours=1)
        card.record(trade)

        weekly = card.weekly_summary()
        assert len(weekly) == 2
        assert weekly[-1].net_pnl == pytest.approx(10.0)
        assert weekly[0].net_pnl == pytest.approx(-4.0)


class TestRetirePolicy:
    def _card_with_history(self, recent_pnls, **policy_kwargs) -> StrategyScorecard:
        card = StrategyScorecard(
            "t",
            policy=RetirePolicy(min_trades=5, window_days=28, **policy_kwargs),
        )
        # 10 old winning trades build history but fall outside the window
        for i in range(10):
            trade = make_trade(20.0, hours_ago=24 * 60 + i)
            card.record(trade)
        for i, pnl in enumerate(recent_pnls):
            card.record(make_trade(pnl, hours_ago=i))
        return card

    def test_insufficient_recent_trades_does_not_retire(self):
        card = self._card_with_history([10.0, 10.0])
        retire, reason = card.should_retire()
        assert retire is False
        assert "insufficient data" in reason

    def test_losing_recent_window_retires(self):
        card = self._card_with_history([-5.0] * 8)
        retire, reason = card.should_retire()
        assert retire is True
        assert "expectancy" in reason or "profit factor" in reason

    def test_winning_recent_window_survives(self):
        card = self._card_with_history([5.0, -2.0, 6.0, -1.0, 4.0, 3.0, 2.0])
        retire, reason = card.should_retire()
        assert retire is False
        assert reason == "policy satisfied"

    def test_drawdown_breach_retires(self):
        # Equity: 10000 → trough 9940 (dd 0.6%) while expectancy stays
        # positive and PF > 1, so drawdown is the breached rule.
        card = self._card_with_history(
            [-20.0, -20.0, -20.0, 13.0, 13.0, 13.0, 13.0, 13.0],
            max_drawdown_pct=0.005,
        )
        retire, reason = card.should_retire()
        assert retire is True
        assert "drawdown" in reason

    def test_old_losses_do_not_trigger_retirement(self):
        """Old losing history outside the window cannot mask recent wins."""
        card = StrategyScorecard(
            "t",
            policy=RetirePolicy(min_trades=3, window_days=28),
        )
        for i in range(10):
            card.record(make_trade(-50.0, hours_ago=24 * 90 + i))
        card.record(make_trade(10.0, hours_ago=1))
        card.record(make_trade(12.0, hours_ago=2))
        card.record(make_trade(11.0, hours_ago=3))

        retire, reason = card.should_retire()
        assert retire is False
        assert reason == "policy satisfied"


# ---------------------------------------------------------------------------
# Walk-forward validator
# ---------------------------------------------------------------------------


class StubEngine:
    """Deterministic engine: LONG on the first call, then silent."""

    def __init__(self, signals):
        self.signals = list(signals)
        self.candles_seen = 0

    def update_candle(self, candle):
        self.candles_seen += 1

    def generate_signal(self, capital):
        if self.signals:
            return self.signals.pop(0)
        return None


def long_signal(tp, sl, qty=1.0):
    from src.core.config import Side

    return {
        "action": Side.LONG,
        "confidence": 90,
        "entry_price": 0.0,  # filled at candle close by the validator
        "tp_price": tp,
        "sl_price": sl,
        "quantity": qty,
        "atr": 1.0,
    }


def flat_candles(n, price=100.0):
    return pd.DataFrame(
        [
            {
                "timestamp": i,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 10.0,
            }
            for i in range(n)
        ]
    )


class TestWalkForwardValidator:
    def test_invalid_window_sizes_rejected(self):
        with pytest.raises(ValueError):
            WalkForwardValidator(lambda: StubEngine([]), train_size=0)

    def test_no_folds_when_data_too_short(self):
        validator = WalkForwardValidator(lambda: StubEngine([]), 100, 50)
        result = validator.run(flat_candles(80))
        assert result.folds == []
        assert result.total_trades == 0

    def test_take_profit_fill_net_of_fees(self):
        # Entry at close of the signal candle (100.0); next candle rallies
        # to the tp at 102.0.
        candles = flat_candles(6)
        candles.loc[5, "high"] = 102.0

        validator = WalkForwardValidator(
            lambda: StubEngine([long_signal(tp=102.0, sl=98.0)]),
            train_size=4,
            test_size=2,
            fee_pct=0.001,
        )
        result = validator.run(candles)

        assert result.total_trades == 1
        fold = result.folds[0]
        assert fold.trades == 1
        assert fold.trades_list[0].exit_reason == "take_profit"
        assert fold.trades_list[0].exit_price == 102.0
        # gross 2.0, fees (100+102)*1*0.001 = 0.202
        assert fold.net_pnl == pytest.approx(2.0 - 0.202)

    def test_stop_loss_fills_first_when_both_touched(self):
        candles = flat_candles(6)
        candles.loc[5, "high"] = 102.0
        candles.loc[5, "low"] = 97.0

        validator = WalkForwardValidator(
            lambda: StubEngine([long_signal(tp=102.0, sl=98.0)]),
            train_size=4,
            test_size=2,
            fee_pct=0.0,
        )
        result = validator.run(candles)
        assert result.folds[0].trades_list[0].exit_reason == "stop_loss"
        assert result.folds[0].trades_list[0].exit_price == 98.0

    def test_open_position_force_closed_at_fold_end(self):
        validator = WalkForwardValidator(
            lambda: StubEngine([long_signal(tp=200.0, sl=50.0)]),
            train_size=4,
            test_size=2,
            fee_pct=0.001,
        )
        result = validator.run(flat_candles(6))
        trade = result.folds[0].trades_list[0]
        assert trade.exit_reason == "fold_end"
        assert trade.exit_price == 100.0
        # Fees charged on both legs even for the flat force-close
        assert trade.fees == pytest.approx(200.0 * 0.001)

    def test_multiple_folds_chain_forward(self):
        validator = WalkForwardValidator(
            lambda: StubEngine([]), train_size=4, test_size=3
        )
        result = validator.run(flat_candles(11))
        assert [f.fold for f in result.folds] == [0, 1, 2]
        assert [f.test_candles for f in result.folds] == [3, 3, 1]

    def test_aggregate_profit_factor(self):
        # Fold trade 1 wins +2, fold trade 2 loses -1 → aggregate PF = 2.0
        def factory():
            return StubEngine([long_signal(tp=102.0, sl=98.0)])

        up = flat_candles(6)
        up.loc[5, "high"] = 102.0
        down = flat_candles(6)
        down.loc[5, "low"] = 98.0

        validator = WalkForwardValidator(
            factory, train_size=4, test_size=2, fee_pct=0.0
        )
        result = validator.run(up)
        result2 = validator.run(down)
        combined = result.folds + result2.folds

        wins = sum(t.pnl for f in combined for t in f.trades_list if t.pnl > 0)
        losses = abs(sum(t.pnl for f in combined for t in f.trades_list if t.pnl < 0))
        assert wins == pytest.approx(2.0)
        assert losses == pytest.approx(2.0)
