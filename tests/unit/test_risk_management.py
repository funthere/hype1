"""
Unit tests for Survival Risk Management — src/core/survival_risk.py

Tests PositionHeatManager, TieredRiskManager, VolatilityRiskManager,
TimeBasedRiskManager, MaxDrawdownCircuitBreaker, and SurvivalRiskManager.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from src.core.config import BotConfig, Side, Position, Trade
from src.core.survival_risk import (
    PositionHeatManager,
    TieredRiskManager,
    VolatilityRiskManager,
    TimeBasedRiskManager,
    MaxDrawdownCircuitBreaker,
    SurvivalRiskManager,
)


# ---------------------------------------------------------------------------
# Fixtures — prefixed to avoid collisions with conftest.py
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_config():
    cfg = BotConfig()
    cfg.PAPER_TRADING = True
    cfg.MAX_DAILY_LOSS_PCT = 0.15
    return cfg


@pytest.fixture
def long_pos():
    return Position(
        side=Side.LONG,
        entry_price=100.0,
        quantity=10.0,
        tp_price=105.0,
        sl_price=98.0,
        entry_time=datetime.now(),
        leverage=5,
    )


@pytest.fixture
def short_pos():
    return Position(
        side=Side.SHORT,
        entry_price=100.0,
        quantity=10.0,
        tp_price=95.0,
        sl_price=102.0,
        entry_time=datetime.now(),
        leverage=5,
    )


@pytest.fixture
def winning_trade():
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=105.0,
        quantity=10.0,
        entry_time=datetime.now(),
        exit_time=datetime.now(),
        pnl=50.0,
    )


@pytest.fixture
def losing_trade():
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=95.0,
        quantity=10.0,
        entry_time=datetime.now(),
        exit_time=datetime.now(),
        pnl=-50.0,
    )


# ---------------------------------------------------------------------------
# PositionHeatManager
# ---------------------------------------------------------------------------

class TestPositionHeatManager:
    def test_default_max_heat(self):
        mgr = PositionHeatManager()
        assert mgr.max_heat_per_setup == 0.05

    def test_custom_max_heat(self):
        mgr = PositionHeatManager(max_heat_per_setup=0.10)
        assert mgr.max_heat_per_setup == 0.10

    def test_get_setup_key(self, long_pos):
        mgr = PositionHeatManager()
        key = mgr.get_setup_key(long_pos)
        assert key == "LONG_5x"

    def test_get_setup_key_short(self, short_pos):
        mgr = PositionHeatManager()
        key = mgr.get_setup_key(short_pos)
        assert key == "SHORT_5x"

    def test_calculate_position_risk_long(self, long_pos):
        mgr = PositionHeatManager()
        # Signature: calculate_position_risk(self, position, entry_price)
        risk = mgr.calculate_position_risk(long_pos, 100.0)
        # LONG: stop_distance = (100 - 98) / 100 = 0.02
        # notional_risk = 10 * 100 * 0.02 = 20
        assert risk == pytest.approx(20.0)

    def test_calculate_position_risk_short(self, short_pos):
        mgr = PositionHeatManager()
        risk = mgr.calculate_position_risk(short_pos, 100.0)
        # SHORT: stop_distance = (102 - 100) / 100 = 0.02
        # notional_risk = 10 * 100 * 0.02 = 20
        assert risk == pytest.approx(20.0)

    def test_can_add_position_no_existing(self, long_pos):
        mgr = PositionHeatManager(max_heat_per_setup=0.10)
        can_add, reason = mgr.can_add_position(
            long_pos, existing_positions=[], capital=10000.0, current_price=100.0
        )
        assert can_add is True
        assert reason == ""

    def test_can_add_position_exceeds_heat(self, long_pos):
        mgr = PositionHeatManager(max_heat_per_setup=0.001)  # Very small limit
        can_add, reason = mgr.can_add_position(
            long_pos, existing_positions=[], capital=10000.0, current_price=100.0
        )
        assert can_add is False
        assert "heat" in reason.lower()

    def test_cannot_add_to_losing_position_same_setup(self):
        mgr = PositionHeatManager(max_heat_per_setup=1.0)  # Large limit
        losing_pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            unrealized_pnl=-50.0,  # Losing position
        )
        new_pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=5.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        can_add, reason = mgr.can_add_position(
            new_pos, existing_positions=[losing_pos], capital=100000.0, current_price=100.0
        )
        assert can_add is False
        assert "losing" in reason.lower()

    def test_can_add_different_setup_ok(self, long_pos, short_pos):
        mgr = PositionHeatManager(max_heat_per_setup=0.10)
        can_add, reason = mgr.can_add_position(
            short_pos, existing_positions=[long_pos],
            capital=10000.0, current_price=100.0
        )
        # Different setup keys (LONG_5x vs SHORT_5x), so should be OK
        assert can_add is True

    def test_on_position_closed(self, long_pos):
        mgr = PositionHeatManager()
        mgr.position_heat["LONG_5x"] = 0.05
        mgr.on_position_closed(long_pos)
        assert "LONG_5x" not in mgr.position_heat


# ---------------------------------------------------------------------------
# TieredRiskManager
# ---------------------------------------------------------------------------

class TestTieredRiskManager:
    def test_default_values(self):
        mgr = TieredRiskManager()
        assert mgr.tier_1_after_losses == 2
        assert mgr.tier_1_reduction == 0.5
        assert mgr.tier_2_daily_loss_pct == 0.02
        assert mgr.tier_2_reduction == 0.25
        assert mgr.current_tier == 0
        assert mgr.consecutive_losses == 0
        assert mgr.daily_pnl == 0.0

    def test_initial_risk_multiplier(self):
        mgr = TieredRiskManager()
        assert mgr.get_risk_multiplier() == 1.0

    def test_tier_1_after_consecutive_losses(self, losing_trade):
        mgr = TieredRiskManager(tier_1_after_losses=2)
        mgr.update(losing_trade, daily_pnl=0, consecutive_losses=2)
        assert mgr.current_tier == 1
        assert mgr.get_risk_multiplier() == 0.5

    def test_tier_2_after_daily_loss(self, losing_trade):
        mgr = TieredRiskManager(tier_2_daily_loss_pct=0.02)
        mgr.update(losing_trade, daily_pnl=-0.05, consecutive_losses=0)
        assert mgr.current_tier == 2
        assert mgr.get_risk_multiplier() == 0.25

    def test_tier_2_takes_priority(self, losing_trade):
        """Tier 2 should override tier 1"""
        mgr = TieredRiskManager(tier_1_after_losses=2, tier_2_daily_loss_pct=0.02)
        mgr.update(losing_trade, daily_pnl=-0.05, consecutive_losses=3)
        assert mgr.current_tier == 2

    def test_no_tier_when_ok(self, winning_trade):
        mgr = TieredRiskManager()
        mgr.update(winning_trade, daily_pnl=0.01, consecutive_losses=0)
        assert mgr.current_tier == 0
        assert mgr.get_risk_multiplier() == 1.0

    def test_reset(self):
        mgr = TieredRiskManager()
        mgr.current_tier = 2
        mgr.consecutive_losses = 5
        mgr.daily_pnl = -0.1
        mgr.reset()
        assert mgr.current_tier == 0
        assert mgr.consecutive_losses == 0
        assert mgr.daily_pnl == 0.0

    def test_exact_boundary_tier_1(self, losing_trade):
        """Exactly at tier_1_after_losses should trigger"""
        mgr = TieredRiskManager(tier_1_after_losses=3)
        mgr.update(losing_trade, daily_pnl=0, consecutive_losses=3)
        assert mgr.current_tier == 1

    def test_below_tier_1_threshold(self, losing_trade):
        mgr = TieredRiskManager(tier_1_after_losses=3)
        mgr.update(losing_trade, daily_pnl=0, consecutive_losses=2)
        assert mgr.current_tier == 0


# ---------------------------------------------------------------------------
# VolatilityRiskManager
# ---------------------------------------------------------------------------

class TestVolatilityRiskManager:
    def test_default_values(self):
        mgr = VolatilityRiskManager()
        assert mgr.atr_window == 14
        assert mgr.vol_multiplier_threshold == 1.5
        assert mgr.leverage_reduction_factor == 0.6
        assert mgr.base_leverage == 5
        assert mgr.atr_history == []

    def test_initial_multiplier_is_one(self):
        mgr = VolatilityRiskManager()
        assert mgr.get_volatility_multiplier() == 1.0

    def test_multiplier_one_when_insufficient_data(self):
        mgr = VolatilityRiskManager(atr_window=14)
        for i in range(10):
            mgr.update_atr(1.0)
        assert mgr.get_volatility_multiplier() == 1.0

    def test_high_volatility_reduces_leverage(self):
        mgr = VolatilityRiskManager(
            atr_window=5, vol_multiplier_threshold=1.5, leverage_reduction_factor=0.6
        )
        # Baseline: low ATR values
        for i in range(5):
            mgr.update_atr(1.0)
        # Recent: high ATR values (3x baseline)
        for i in range(5):
            mgr.update_atr(3.0)
        mult = mgr.get_volatility_multiplier()
        assert mult == 0.6

    def test_normal_volatility_no_reduction(self):
        mgr = VolatilityRiskManager(atr_window=5, vol_multiplier_threshold=1.5)
        for i in range(10):
            mgr.update_atr(1.0 + i * 0.01)  # Slowly increasing
        mult = mgr.get_volatility_multiplier()
        assert mult == 1.0

    def test_zero_baseline_atr(self):
        mgr = VolatilityRiskManager(atr_window=5)
        for i in range(5):
            mgr.update_atr(0.0)
        for i in range(5):
            mgr.update_atr(1.0)
        assert mgr.get_volatility_multiplier() == 1.0

    def test_atr_history_trimming(self):
        mgr = VolatilityRiskManager(atr_window=5)
        for i in range(100):
            mgr.update_atr(float(i))
        # Should be trimmed to atr_window * 2 = 10
        assert len(mgr.atr_history) <= 10

    def test_get_adjusted_leverage_normal(self):
        mgr = VolatilityRiskManager(atr_window=5)
        for i in range(10):
            mgr.update_atr(1.0)
        lev = mgr.get_adjusted_leverage(base_leverage=5)
        assert lev == 5

    def test_get_adjusted_leverage_high_vol(self):
        mgr = VolatilityRiskManager(
            atr_window=5, vol_multiplier_threshold=1.5, leverage_reduction_factor=0.6
        )
        for i in range(5):
            mgr.update_atr(1.0)
        for i in range(5):
            mgr.update_atr(3.0)
        lev = mgr.get_adjusted_leverage(base_leverage=5)
        assert lev == max(1, min(int(5 * 0.6), 10))  # = 3

    def test_get_adjusted_leverage_clamped_min(self):
        mgr = VolatilityRiskManager(
            atr_window=5, vol_multiplier_threshold=1.5, leverage_reduction_factor=0.1
        )
        for i in range(5):
            mgr.update_atr(1.0)
        for i in range(5):
            mgr.update_atr(3.0)
        lev = mgr.get_adjusted_leverage(base_leverage=2)
        assert lev >= 1  # Clamped to at least 1

    def test_get_adjusted_leverage_clamped_max(self):
        mgr = VolatilityRiskManager(atr_window=5)
        for i in range(10):
            mgr.update_atr(1.0)
        lev = mgr.get_adjusted_leverage(base_leverage=50)
        assert lev <= 10  # Clamped to at most 10


# ---------------------------------------------------------------------------
# TimeBasedRiskManager
# ---------------------------------------------------------------------------

class TestTimeBasedRiskManager:
    def test_default_values(self):
        mgr = TimeBasedRiskManager()
        assert mgr.avoid_first_last_minutes == 30
        assert mgr.avoid_weekends is True

    def test_weekday_safe(self):
        mgr = TimeBasedRiskManager(avoid_weekends=True)
        # Monday at 10:00 UTC (weekday=0)
        t = datetime(2025, 5, 19, 10, 0)  # Monday
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is True

    def test_saturday_not_safe(self):
        mgr = TimeBasedRiskManager(avoid_weekends=True)
        t = datetime(2025, 5, 17, 10, 0)  # Saturday
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is False
        assert "weekend" in reason.lower()

    def test_sunday_not_safe(self):
        mgr = TimeBasedRiskManager(avoid_weekends=True)
        t = datetime(2025, 5, 18, 10, 0)  # Sunday
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is False

    def test_weekend_allowed(self):
        mgr = TimeBasedRiskManager(avoid_weekends=False)
        t = datetime(2025, 5, 17, 10, 0)  # Saturday
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is True

    def test_market_hour_boundary_unsafe(self):
        mgr = TimeBasedRiskManager(avoid_first_last_minutes=30)
        # Monday at 00:15 UTC — hour=0 (session start), minute < 30
        t = datetime(2025, 5, 19, 0, 15)
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is False
        assert "boundary" in reason.lower()

    def test_safe_during_normal_hours(self):
        mgr = TimeBasedRiskManager(avoid_first_last_minutes=30)
        # Monday at 10:30 UTC — normal trading hour
        t = datetime(2025, 5, 19, 10, 30)
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is True

    def test_no_time_restriction(self):
        mgr = TimeBasedRiskManager(avoid_first_last_minutes=0, avoid_weekends=False)
        t = datetime(2025, 5, 17, 0, 0)  # Weekend + hour boundary
        is_safe, reason = mgr.is_safe_to_trade(t)
        assert is_safe is True

    def test_none_time_uses_current(self):
        mgr = TimeBasedRiskManager()
        # Should not crash when time is None
        is_safe, reason = mgr.is_safe_to_trade(None)
        # Result depends on current time, just ensure no crash
        assert isinstance(is_safe, bool)


# ---------------------------------------------------------------------------
# MaxDrawdownCircuitBreaker
# ---------------------------------------------------------------------------

class TestMaxDrawdownCircuitBreaker:
    def test_default_values(self):
        cb = MaxDrawdownCircuitBreaker()
        assert cb.max_drawdown_pct == 0.10
        assert cb.cooldown_hours == 24
        assert cb.peak_equity is None
        assert cb.triggered is False

    def test_not_triggered_initially(self):
        cb = MaxDrawdownCircuitBreaker()
        assert cb.is_triggered() is False

    def test_initial_drawdown_zero(self):
        cb = MaxDrawdownCircuitBreaker()
        assert cb.get_drawdown_pct() == 0.0

    def test_update_sets_peak(self):
        cb = MaxDrawdownCircuitBreaker()
        now = datetime.now()
        cb.update(10000.0, now)
        assert cb.peak_equity == 10000.0

    def test_update_increases_peak(self):
        cb = MaxDrawdownCircuitBreaker()
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(12000.0, now)
        assert cb.peak_equity == 12000.0

    def test_trigger_on_drawdown(self):
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(8500.0, now)  # 15% drawdown
        assert cb.is_triggered() is True

    def test_no_trigger_below_threshold(self):
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(9500.0, now)  # 5% drawdown
        assert cb.is_triggered() is False

    def test_exact_threshold_triggers(self):
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(9000.0, now)  # Exactly 10%
        assert cb.is_triggered() is True

    def test_reset_on_new_high(self):
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(8500.0, now)  # Trigger
        assert cb.is_triggered() is True
        cb.update(11000.0, now)  # New high resets
        assert cb.is_triggered() is False

    def test_cooldown_expires_after_first_update(self):
        """Cooldown: trigger, then update with same equity after cooldown.
        But drawdown check happens AFTER cooldown check, so if drawdown
        still exceeds threshold, it re-triggers. We test with recovery instead."""
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, cooldown_hours=1)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(8500.0, now)  # Trigger
        assert cb.is_triggered() is True
        # After cooldown, update with recovered equity (above peak - 10%)
        future = now + timedelta(hours=2)
        cb.update(9500.0, future)  # Only 5% DD from peak, should NOT re-trigger
        assert cb.is_triggered() is False

    def test_cooldown_not_yet_expired(self):
        cb = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10, cooldown_hours=24)
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(8500.0, now)  # Trigger
        # Check shortly after — cooldown hasn't expired
        cb.update(8500.0, now + timedelta(minutes=1))
        assert cb.is_triggered() is True

    def test_drawdown_pct_calculation(self):
        cb = MaxDrawdownCircuitBreaker()
        now = datetime.now()
        cb.update(10000.0, now)
        cb.update(8000.0, now)
        dd = cb.get_drawdown_pct()
        assert dd == pytest.approx(0.20)

    def test_zero_equity_drawdown(self):
        cb = MaxDrawdownCircuitBreaker()
        cb.current_equity = 0.0
        # peak_equity is None, so drawdown should be 0
        assert cb.get_drawdown_pct() == 0.0


# ---------------------------------------------------------------------------
# SurvivalRiskManager (unified)
# ---------------------------------------------------------------------------

class TestSurvivalRiskManager:
    def _make_long_pos(self):
        return Position(
            side=Side.LONG, entry_price=100.0, quantity=10.0,
            tp_price=105.0, sl_price=98.0,
            entry_time=datetime.now(), leverage=5,
        )

    def test_initialization(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        assert mgr.heat_manager is not None
        assert mgr.tiered_risk is not None
        assert mgr.volatility_manager is not None
        assert mgr.time_manager is not None
        assert mgr.drawdown_breaker is not None
        assert mgr.last_trade_time is None

    def test_can_open_position_success(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        safe_time = datetime(2025, 5, 19, 10, 30)  # Monday 10:30
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(),
            existing_positions=[],
            capital=10000.0,
            current_price=100.0,
            daily_pnl=0,
            consecutive_losses=0,
            current_time=safe_time,
        )
        assert can_open is True
        assert reason == ""

    def test_blocked_by_drawdown_circuit_breaker(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        now = datetime.now()
        mgr.drawdown_breaker.update(10000.0, now)
        mgr.drawdown_breaker.update(8000.0, now)  # 20% drawdown > 10% limit
        assert mgr.drawdown_breaker.is_triggered()

        safe_time = datetime(2025, 5, 19, 10, 30)
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(), existing_positions=[], capital=10000.0,
            current_price=100.0, daily_pnl=0, consecutive_losses=0,
            current_time=safe_time,
        )
        assert can_open is False
        assert "drawdown" in reason.lower() or "circuit" in reason.lower()

    def test_blocked_by_time(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        saturday = datetime(2025, 5, 17, 10, 30)
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(), existing_positions=[], capital=100000.0,
            current_price=100.0, daily_pnl=0, consecutive_losses=0,
            current_time=saturday,
        )
        assert can_open is False
        assert "time" in reason.lower()

    def test_blocked_by_too_soon_since_last_trade(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        safe_time = datetime(2025, 5, 19, 10, 30)
        mgr.last_trade_time = safe_time - timedelta(minutes=5)  # 5 min ago
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(), existing_positions=[], capital=100000.0,
            current_price=100.0, daily_pnl=0, consecutive_losses=0,
            current_time=safe_time,
        )
        assert can_open is False
        assert "too soon" in reason.lower()

    def test_allowed_after_cooldown(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        safe_time = datetime(2025, 5, 19, 10, 30)
        mgr.last_trade_time = safe_time - timedelta(minutes=20)  # 20 min ago
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(), existing_positions=[], capital=100000.0,
            current_price=100.0, daily_pnl=0, consecutive_losses=0,
            current_time=safe_time,
        )
        assert can_open is True

    def test_update_after_trade(self, risk_config, winning_trade):
        mgr = SurvivalRiskManager(risk_config)
        now = datetime.now()
        mgr.update_after_trade(winning_trade, capital=10000.0, current_time=now)
        assert mgr.last_trade_time == now
        assert mgr.drawdown_breaker.peak_equity == 10050.0  # capital + pnl

    def test_get_position_size_multiplier_default(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        mult = mgr.get_position_size_multiplier()
        assert mult == 1.0

    def test_get_position_size_multiplier_with_atr(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        mult = mgr.get_position_size_multiplier(atr=1.0)
        assert isinstance(mult, float)

    def test_get_survival_summary(self, risk_config):
        mgr = SurvivalRiskManager(risk_config)
        summary = mgr.get_survival_summary()
        assert isinstance(summary, str)
        assert "SURVIVAL" in summary

    def test_blocked_by_position_heat(self, risk_config):
        """Heat exceeds limit should block"""
        mgr = SurvivalRiskManager(risk_config)
        mgr.heat_manager.max_heat_per_setup = 0.001  # Very tight
        safe_time = datetime(2025, 5, 19, 10, 30)
        can_open, reason = mgr.can_open_position(
            self._make_long_pos(), existing_positions=[], capital=10000.0,
            current_price=100.0, daily_pnl=0, consecutive_losses=0,
            current_time=safe_time,
        )
        assert can_open is False
