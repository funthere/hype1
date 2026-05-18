"""
Unit tests for RiskGuard
"""

import pytest
from datetime import datetime, timedelta

from src.core.config import BotConfig
from src.bot.risk_guard import RiskGuard


@pytest.fixture
def config():
    c = BotConfig()
    c.PAPER_TRADING = True
    c.CIRCUIT_BREAKER_ENABLED = True
    c.MAX_CONSECUTIVE_LOSSES = 3
    c.CIRCUIT_BREAKER_COOLDOWN_MINUTES = 30
    return c


@pytest.fixture
def rg(config):
    return RiskGuard(config)


class TestRiskGuardInit:

    def test_initial_state(self, rg):
        assert rg.consecutive_losses == 0
        assert rg.circuit_breaker_triggered is False
        assert rg.circuit_breaker_until is None
        assert rg.max_drawdown_pct == 0.0


class TestRiskGuardCanTrade:

    def test_can_trade_initially(self, rg):
        assert rg.can_trade() is True

    def test_cannot_trade_when_triggered(self, rg):
        rg.circuit_breaker_triggered = True
        assert rg.can_trade() is False


class TestRiskGuardRecordResult:

    def test_losing_trade_increments(self, rg):
        rg.record_trade_result(-50.0, 9500.0)
        assert rg.consecutive_losses == 1

    def test_winning_trade_resets(self, rg):
        rg.consecutive_losses = 2
        rg.record_trade_result(50.0, 10500.0)
        assert rg.consecutive_losses == 0

    def test_drawdown_tracking(self, rg):
        rg.peak_equity = 10000.0
        rg.record_trade_result(-500.0, 9500.0)
        assert rg.peak_equity == 10000.0
        assert rg.max_drawdown_pct > 0
        assert rg.max_drawdown_pct == 500.0 / 10000.0

    def test_peak_updates_on_gain(self, rg):
        rg.peak_equity = 10000.0
        rg.record_trade_result(1000.0, 11000.0)
        assert rg.peak_equity == 11000.0


class TestRiskGuardCircuitBreaker:

    def test_should_trigger_at_threshold(self, rg):
        rg.consecutive_losses = 3
        assert rg.should_trigger_circuit_breaker() is True

    def test_should_not_trigger_below_threshold(self, rg):
        rg.consecutive_losses = 2
        assert rg.should_trigger_circuit_breaker() is False

    def test_should_not_trigger_when_disabled(self, rg):
        rg.config.CIRCUIT_BREAKER_ENABLED = False
        rg.consecutive_losses = 5
        assert rg.should_trigger_circuit_breaker() is False

    def test_trigger_sets_state(self, rg):
        rg.trigger_circuit_breaker()
        assert rg.circuit_breaker_triggered is True
        assert rg.circuit_breaker_until is not None
        assert rg.circuit_breaker_until > datetime.now()

    def test_cooldown_not_triggered(self, rg):
        assert rg.check_circuit_breaker_cooldown() is False

    def test_cooldown_expires(self, rg):
        rg.circuit_breaker_triggered = True
        rg.circuit_breaker_until = datetime.now() - timedelta(minutes=1)
        result = rg.check_circuit_breaker_cooldown()
        assert result is True
        assert rg.circuit_breaker_triggered is False
        assert rg.circuit_breaker_until is None
        assert rg.consecutive_losses == 0

    def test_cooldown_not_yet_expired(self, rg):
        rg.circuit_breaker_triggered = True
        rg.circuit_breaker_until = datetime.now() + timedelta(minutes=30)
        result = rg.check_circuit_breaker_cooldown()
        assert result is False
        assert rg.circuit_breaker_triggered is True


class TestRiskGuardReset:

    def test_reset(self, rg):
        rg.circuit_breaker_triggered = True
        rg.circuit_breaker_until = datetime.now()
        rg.consecutive_losses = 5
        rg.reset_circuit_breaker()
        assert rg.circuit_breaker_triggered is False
        assert rg.circuit_breaker_until is None
        assert rg.consecutive_losses == 0


class TestRiskGuardStatus:

    def test_status_property(self, rg):
        rg.circuit_breaker_triggered = True
        rg.consecutive_losses = 3
        rg.circuit_breaker_until = datetime.now()
        status = rg.status
        assert status["enabled"] is True
        assert status["is_triggered"] is True
        assert status["consecutive_losses"] == 3
        assert status["max_consecutive_losses"] == 3
        assert status["cooldown_minutes"] == 30
