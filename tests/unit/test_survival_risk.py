"""
Unit tests for the survival risk managers.

These gate entries in the main trading bot (trading_bot.py), so every
block/allow branch and risk multiplier is pinned here.
"""

from datetime import datetime, timedelta

import pytest

from src.core.config import BotConfig, Position, Side, Trade
from src.core.survival_risk import (
    MaxDrawdownCircuitBreaker,
    PositionHeatManager,
    SurvivalRiskManager,
    TieredRiskManager,
    TimeBasedRiskManager,
    VolatilityRiskManager,
)

# A Wednesday 10:30 UTC: safely away from weekends and hour boundaries.
SAFE_TIME = datetime(2026, 9, 2, 10, 30)


def make_position(
    side=Side.LONG, entry=100.0, qty=10.0, sl=98.0, leverage=2, pnl=0.0
) -> Position:
    return Position(
        side=side,
        entry_price=entry,
        quantity=qty,
        tp_price=entry * 1.05,
        sl_price=sl,
        entry_time=SAFE_TIME,
        leverage=leverage,
        unrealized_pnl=pnl,
    )


def make_trade(pnl: float) -> Trade:
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=101.0,
        quantity=1.0,
        entry_time=SAFE_TIME,
        exit_time=SAFE_TIME + timedelta(hours=1),
        pnl=pnl,
    )


# ---------------------------------------------------------------------------
# PositionHeatManager
# ---------------------------------------------------------------------------


class TestPositionHeatManager:
    def test_position_within_heat_limit_is_allowed(self):
        manager = PositionHeatManager(max_heat_per_setup=0.05)
        new_pos = (
            make_position()
        )  # risk = qty * entry * stop_distance = 10*100*0.02 = 20

        can_add, reason = manager.can_add_position(
            new_pos, [], capital=10_000.0, current_price=100.0
        )

        assert can_add is True
        assert reason == ""

    def test_heat_limit_blocks_accumulation(self):
        manager = PositionHeatManager(max_heat_per_setup=0.05)
        existing = make_position()
        new_pos = make_position(
            qty=60.0
        )  # new risk alone = 120 → 1.8% with existing 20
        # Existing same-setup risk (20) + new (120) = 140/1000 = 14% > 5%
        can_add, reason = manager.can_add_position(
            new_pos, [existing], capital=1_000.0, current_price=100.0
        )
        assert can_add is False
        assert "exceeds limit" in reason

    def test_cannot_add_to_losing_same_setup(self):
        manager = PositionHeatManager()
        losing = make_position(pnl=-50.0)
        new_pos = make_position()

        can_add, reason = manager.can_add_position(
            new_pos, [losing], capital=10_000.0, current_price=100.0
        )

        assert can_add is False
        assert "losing position" in reason

    def test_on_position_closed_removes_setup_key(self):
        manager = PositionHeatManager()
        manager.position_heat["LONG_2x"] = 0.03

        manager.on_position_closed(make_position())

        assert "LONG_2x" not in manager.position_heat


# ---------------------------------------------------------------------------
# TieredRiskManager
# ---------------------------------------------------------------------------


class TestTieredRiskManager:
    def test_no_tier_when_healthy(self):
        manager = TieredRiskManager()
        manager.update(
            trade=None, daily_pnl=10.0, consecutive_losses=0, starting_capital=10_000.0
        )
        assert manager.current_tier == 0
        assert manager.get_risk_multiplier() == 1.0

    def test_tier_1_after_consecutive_losses(self):
        manager = TieredRiskManager()
        manager.update(
            trade=None, daily_pnl=-50.0, consecutive_losses=2, starting_capital=10_000.0
        )
        assert manager.current_tier == 1
        assert manager.get_risk_multiplier() == 0.5

    def test_tier_2_overrides_after_daily_loss_threshold(self):
        manager = TieredRiskManager()
        manager.update(
            trade=None,
            daily_pnl=-250.0,
            consecutive_losses=0,
            starting_capital=10_000.0,
        )
        assert manager.current_tier == 2
        assert manager.get_risk_multiplier() == 0.25

    def test_positive_daily_pnl_never_counts_as_loss(self):
        manager = TieredRiskManager(tier_2_daily_loss_pct=0.001)
        manager.update(
            trade=None, daily_pnl=500.0, consecutive_losses=0, starting_capital=10_000.0
        )
        assert manager.current_tier == 0

    def test_reset(self):
        manager = TieredRiskManager()
        manager.update(
            trade=None,
            daily_pnl=-250.0,
            consecutive_losses=3,
            starting_capital=10_000.0,
        )
        manager.reset()
        assert manager.current_tier == 0
        assert manager.get_risk_multiplier() == 1.0


# ---------------------------------------------------------------------------
# VolatilityRiskManager
# ---------------------------------------------------------------------------


class TestVolatilityRiskManager:
    def test_insufficient_history_returns_neutral(self):
        manager = VolatilityRiskManager(atr_window=14)
        for atr in [1.0] * 5:
            manager.update_atr(atr)
        assert manager.get_volatility_multiplier() == 1.0

    def test_elevated_volatility_reduces_exposure(self):
        manager = VolatilityRiskManager(atr_window=14, vol_multiplier_threshold=1.5)
        for atr in [1.0] * 14:  # baseline
            manager.update_atr(atr)
        for atr in [2.0] * 14:  # recent: 2x baseline > 1.5 threshold
            manager.update_atr(atr)
        assert manager.get_volatility_multiplier() == 0.6

    def test_normal_volatility_returns_neutral(self):
        manager = VolatilityRiskManager(atr_window=14)
        for atr in [1.0] * 28:
            manager.update_atr(atr)
        assert manager.get_volatility_multiplier() == 1.0

    def test_adjusted_leverage_is_clamped(self):
        manager = VolatilityRiskManager(atr_window=14, max_leverage=2)
        assert manager.get_adjusted_leverage(5) == 2  # capped by max_leverage

        for atr in [1.0] * 14:
            manager.update_atr(atr)
        for atr in [2.0] * 14:
            manager.update_atr(atr)
        assert manager.get_adjusted_leverage(1) == 1  # 1 * 0.6 floors at 1


# ---------------------------------------------------------------------------
# TimeBasedRiskManager
# ---------------------------------------------------------------------------


class TestTimeBasedRiskManager:
    def test_weekend_blocked(self):
        manager = TimeBasedRiskManager(avoid_weekends=True)
        saturday = datetime(2026, 9, 5, 12, 0)
        safe, reason = manager.is_safe_to_trade(saturday)
        assert safe is False
        assert "Weekend" in reason

    def test_weekday_mid_session_allowed(self):
        manager = TimeBasedRiskManager()
        safe, reason = manager.is_safe_to_trade(SAFE_TIME)
        assert safe is True
        assert reason == ""

    def test_session_boundary_hours_blocked(self):
        manager = TimeBasedRiskManager(avoid_first_last_minutes=30)
        for hour in (0, 8, 12, 16, 20):
            safe, reason = manager.is_safe_to_trade(datetime(2026, 9, 2, hour, 15))
            assert safe is False, f"hour {hour} should be blocked"
            assert "boundary" in reason

    def test_weekends_allowed_when_disabled(self):
        manager = TimeBasedRiskManager(avoid_weekends=False)
        # Sunday 10:30 — outside the session-boundary hours so the weekend
        # flag is the only rule under test
        safe, _ = manager.is_safe_to_trade(datetime(2026, 9, 6, 10, 30))
        assert safe is True


# ---------------------------------------------------------------------------
# MaxDrawdownCircuitBreaker
# ---------------------------------------------------------------------------


class TestMaxDrawdownCircuitBreaker:
    def test_no_trigger_without_drawdown(self):
        breaker = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = SAFE_TIME
        breaker.update(10_000.0, now)
        breaker.update(10_500.0, now + timedelta(hours=1))
        assert breaker.is_triggered() is False
        assert breaker.get_drawdown_pct() == 0.0

    def test_triggers_at_max_drawdown(self):
        breaker = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, cooldown_hours=24)
        now = SAFE_TIME
        breaker.update(10_000.0, now)
        breaker.update(8_900.0, now + timedelta(hours=1))  # 11% below peak
        assert breaker.is_triggered() is True
        assert breaker.triggered_at == now + timedelta(hours=1)
        assert breaker.reset_at == now + timedelta(hours=25)

    def test_cooldown_expiry_rearms_breaker(self):
        breaker = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, cooldown_hours=24)
        now = SAFE_TIME
        breaker.update(10_000.0, now)
        breaker.update(8_900.0, now + timedelta(hours=1))

        breaker.update(8_950.0, now + timedelta(hours=24, minutes=30))
        # Cooldown expires (re-arms), but drawdown is still >= 10% so the
        # breaker immediately re-triggers with a fresh cooldown window.
        assert breaker.is_triggered() is True

    def test_new_equity_high_resets_breaker(self):
        breaker = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = SAFE_TIME
        breaker.update(10_000.0, now)
        breaker.update(8_900.0, now + timedelta(hours=1))
        assert breaker.is_triggered() is True

        breaker.update(10_600.0, now + timedelta(hours=2))  # new high
        assert breaker.is_triggered() is False
        assert breaker.get_drawdown_pct() == 0.0


# ---------------------------------------------------------------------------
# SurvivalRiskManager facade
# ---------------------------------------------------------------------------


@pytest.fixture
def manager() -> SurvivalRiskManager:
    config = BotConfig(PAPER_TRADING=True, MAX_DAILY_LOSS_PCT=0.02, LEVERAGE=2)
    return SurvivalRiskManager(config)


class TestSurvivalRiskManager:
    def test_healthy_conditions_allow_entry(self, manager):
        can_open, reason = manager.can_open_position(
            make_position(),
            existing_positions=[],
            capital=10_000.0,
            current_price=100.0,
            daily_pnl=0.0,
            consecutive_losses=0,
            current_time=SAFE_TIME,
        )
        assert can_open is True
        assert reason == ""

    def test_drawdown_breaker_blocks_entry(self, manager):
        manager.drawdown_breaker.update(10_000.0, SAFE_TIME)
        manager.drawdown_breaker.update(8_900.0, SAFE_TIME + timedelta(hours=1))

        can_open, reason = manager.can_open_position(
            make_position(),
            [],
            capital=10_000.0,
            current_price=100.0,
            daily_pnl=0.0,
            consecutive_losses=0,
            current_time=SAFE_TIME + timedelta(hours=1),
        )
        assert can_open is False
        assert "circuit breaker" in reason

    def test_time_restriction_blocks_entry(self, manager):
        saturday = datetime(2026, 9, 5, 12, 0)
        can_open, reason = manager.can_open_position(
            make_position(),
            [],
            capital=10_000.0,
            current_price=100.0,
            daily_pnl=0.0,
            consecutive_losses=0,
            current_time=saturday,
        )
        assert can_open is False
        assert "Time restriction" in reason

    def test_minimum_time_between_trades(self, manager):
        manager.update_after_trade(
            make_trade(10.0), capital=10_000.0, current_time=SAFE_TIME
        )

        can_open, reason = manager.can_open_position(
            make_position(),
            [],
            capital=10_000.0,
            current_price=100.0,
            daily_pnl=0.0,
            consecutive_losses=0,
            current_time=SAFE_TIME + timedelta(minutes=5),
        )
        assert can_open is False
        assert "Too soon" in reason

    def test_after_15_minutes_entry_allowed_again(self, manager):
        manager.update_after_trade(
            make_trade(10.0), capital=10_000.0, current_time=SAFE_TIME
        )

        can_open, _ = manager.can_open_position(
            make_position(),
            [],
            capital=10_000.0,
            current_price=100.0,
            daily_pnl=0.0,
            consecutive_losses=0,
            current_time=SAFE_TIME + timedelta(minutes=16),
        )
        assert can_open is True

    def test_update_after_trade_feeds_drawdown_breaker(self, manager):
        # Peak equity is set by the first observation, so establish a peak
        # before drawing down.
        manager.update_after_trade(
            make_trade(500.0), capital=10_000.0, current_time=SAFE_TIME
        )
        assert manager.drawdown_breaker.is_triggered() is False  # peak 10,500

        # Equity 7,000 vs peak 10,500 = 33% drawdown > 10% limit
        manager.update_after_trade(
            make_trade(-3_000.0),
            capital=10_000.0,
            current_time=SAFE_TIME + timedelta(hours=1),
        )
        assert manager.drawdown_breaker.is_triggered() is True

    def test_position_size_multiplier_combines_sources(self, manager):
        # Healthy: no volatility history, tier 0
        assert manager.get_position_size_multiplier() == 1.0

        # Two consecutive losses -> tier 1 multiplier
        manager.tiered_risk.update(
            trade=None,
            daily_pnl=-100.0,
            consecutive_losses=2,
            starting_capital=10_000.0,
        )
        assert manager.get_position_size_multiplier() == 0.5

    def test_survival_summary_renders(self, manager):
        summary = manager.get_survival_summary()
        assert "SURVIVAL" in summary
        assert "10.0%" in summary  # drawdown limit rendered
