"""
Unit tests for TradeExecutor
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from src.core.config import BotConfig, Side, Position
from src.bot.trade_executor import TradeExecutor


@pytest.fixture
def config():
    c = BotConfig()
    c.PAPER_TRADING = True
    c.LEVERAGE = 5
    return c


@pytest.fixture
def api():
    a = Mock()
    a.place_order = AsyncMock(
        return_value={"status": "ok", "response": {"oid": 12345}}
    )
    a.cancel_order = AsyncMock(return_value={"status": "ok"})
    return a


@pytest.fixture
def executor(api, config):
    return TradeExecutor(api, config)


@pytest.fixture
def sample_signal():
    return {
        "action": Side.LONG,
        "confidence": 70.0,
        "entry_price": 100.0,
        "tp_price": 105.0,
        "sl_price": 98.0,
        "quantity": 10.0,
    }


def _make_db():
    db = Mock()
    db.save_position = Mock()
    db.log_event = Mock()
    return db


def _make_telegram():
    tg = Mock()
    tg.notify_trade_entry = AsyncMock()
    tg.notify_trade_exit = AsyncMock()
    return tg


class TestTradeExecutorPlaceEntry:

    @pytest.mark.asyncio
    async def test_paper_entry_returns_position(self, executor, sample_signal):
        db = _make_db()
        tg = _make_telegram()
        pos = await executor.place_entry_order(sample_signal, db, tg)
        assert pos is not None
        assert pos.side == Side.LONG
        assert pos.entry_price == 100.0
        assert pos.quantity == 10.0
        assert pos.leverage == 5
        db.save_position.assert_called_once_with(pos)
        db.log_event.assert_called_once()
        tg.notify_trade_entry.assert_called_once_with(sample_signal)

    @pytest.mark.asyncio
    async def test_live_entry_calls_api(self, api, config, sample_signal):
        config.PAPER_TRADING = False
        executor = TradeExecutor(api, config)
        db = _make_db()
        tg = _make_telegram()
        pos = await executor.place_entry_order(sample_signal, db, tg)
        assert pos is not None
        api.place_order.assert_called_once()
        kwargs = api.place_order.call_args[1]
        assert kwargs["side"] == Side.LONG
        assert kwargs["price"] == 100.0
        assert kwargs["quantity"] == 10.0
        assert kwargs["order_type"] == "post_only"
        assert pos.oid == 12345

    @pytest.mark.asyncio
    async def test_live_entry_failure_returns_none(self, api, config, sample_signal):
        config.PAPER_TRADING = False
        api.place_order = AsyncMock(
            return_value={"status": "error", "msg": "Insufficient margin"}
        )
        executor = TradeExecutor(api, config)
        db = _make_db()
        tg = _make_telegram()
        pos = await executor.place_entry_order(sample_signal, db, tg)
        assert pos is None
        db.save_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_entry(self, executor):
        signal = {
            "action": Side.SHORT,
            "confidence": 70.0,
            "entry_price": 100.0,
            "tp_price": 95.0,
            "sl_price": 102.0,
            "quantity": 10.0,
        }
        db = _make_db()
        tg = _make_telegram()
        pos = await executor.place_entry_order(signal, db, tg)
        assert pos.side == Side.SHORT


class TestTradeExecutorCloseOnExchange:

    @pytest.mark.asyncio
    async def test_paper_mode_returns_true(self, executor):
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        result = await executor.close_position_on_exchange(pos, 105.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_live_mode_cancels_and_places(self, api, config):
        config.PAPER_TRADING = False
        executor = TradeExecutor(api, config)
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=999,
        )
        result = await executor.close_position_on_exchange(pos, 105.0)
        assert result is True
        api.cancel_order.assert_called_once_with(999)
        api.place_order.assert_called_once()
        kwargs = api.place_order.call_args[1]
        assert kwargs["side"] == Side.SHORT
        assert kwargs["reduce_only"] is True
        assert kwargs["order_type"] == "ioc"

    @pytest.mark.asyncio
    async def test_live_mode_close_failure(self, api, config):
        config.PAPER_TRADING = False
        api.place_order = AsyncMock(return_value={"status": "error", "msg": "Rejected"})
        executor = TradeExecutor(api, config)
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        result = await executor.close_position_on_exchange(pos, 105.0)
        assert result is False


class TestTradeExecutorSignalHandlers:

    def test_setup_signal_handlers(self, executor):
        """Verify setup_signal_handlers does not crash."""
        bot = Mock()
        bot.emergency_stop = False
        bot.force_close_all = False
        bot.circuit_breaker_triggered = False
        bot.circuit_breaker_until = None
        bot.consecutive_losses = 0
        # Just ensure no exception
        TradeExecutor.setup_signal_handlers(bot)

    def test_graceful_shutdown_handler(self):
        bot = Mock()
        bot.emergency_stop = False
        handler = TradeExecutor._make_graceful_shutdown_handler(bot)
        handler(15, None)  # SIGTERM
        assert bot.emergency_stop is True

    def test_force_close_handler(self):
        bot = Mock()
        bot.force_close_all = False
        handler = TradeExecutor._make_force_close_handler(bot)
        handler(None, None)
        assert bot.force_close_all is True

    def test_reset_cb_handler_active(self):
        bot = Mock()
        bot.circuit_breaker_triggered = True
        bot.circuit_breaker_until = datetime.now()
        bot.consecutive_losses = 5
        handler = TradeExecutor._make_reset_cb_handler(bot)
        handler(None, None)
        assert bot.circuit_breaker_triggered is False
        assert bot.circuit_breaker_until is None
        assert bot.consecutive_losses == 0

    def test_reset_cb_handler_inactive(self):
        bot = Mock()
        bot.circuit_breaker_triggered = False
        handler = TradeExecutor._make_reset_cb_handler(bot)
        handler(None, None)
        assert bot.circuit_breaker_triggered is False
