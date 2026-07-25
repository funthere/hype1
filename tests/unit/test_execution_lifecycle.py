"""Lifecycle and risk policy regression tests."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.bot.trading_bot import TradingBot
from src.core.config import BotConfig, Position, Side
from src.core.strategy import calculate_stop_risk_quantity
from src.execution import PositionRead


def paper_config() -> BotConfig:
    return BotConfig(PAPER_TRADING=True, WEB_UI_ENABLED=False)


def position() -> Position:
    return Position(
        side=Side.LONG,
        entry_price=100.0,
        quantity=10.0,
        tp_price=105.0,
        sl_price=98.0,
        entry_time=datetime.now(),
        leverage=2,
        asset="HYPE",
    )


@pytest.mark.unit
class TestRiskPolicy:
    def test_stop_risk_sizing_does_not_multiply_by_leverage(self):
        config = paper_config()
        config.RISK_PER_TRADE_PCT = 0.005
        config.LEVERAGE = 2
        config.MAX_POSITION_NOTIONAL_PCT = 1.0
        config.MAX_POSITION_NOTIONAL_USD = 1_000_000
        quantity = calculate_stop_risk_quantity(config, 10_000, 100, 98)
        assert quantity == 25.0  # $50 intended loss / $2 stop distance

    def test_notional_cap_limits_stop_risk_size(self):
        config = paper_config()
        config.MAX_POSITION_NOTIONAL_USD = 500.0
        assert calculate_stop_risk_quantity(config, 10_000, 100, 98) == 5.0

    def test_mainnet_policy_rejects_unsafe_override(self):
        config = BotConfig(
            PAPER_TRADING=False,
            USE_TESTNET=False,
            PRIVATE_KEY="test",
            LEVERAGE=3,
        )
        with pytest.raises(ValueError, match="LEVERAGE"):
            config.validate()


@pytest.mark.asyncio
class TestExitLifecycle:
    @pytest.fixture
    def bot(self):
        config = paper_config()
        with (
            patch("src.bot.trading_bot.HyperliquidAPI"),
            patch("src.bot.trading_bot.MarketDataFeed"),
            patch("src.bot.trading_bot.DatabaseManager") as db_cls,
        ):
            bot = TradingBot(config)
            bot.db = db_cls.return_value
            bot.api.get_recent_fills = AsyncMock(return_value=[])
            bot.telegram = None
            bot.survival_risk = Mock()
            bot.survival_risk.tiered_risk = Mock()
            bot.adaptive_params = Mock()
            bot.performance_analyzer = Mock()
            return bot

    async def test_rejected_live_exit_keeps_local_position(self, bot):
        bot.config.PAPER_TRADING = False
        local_position = position()
        bot.positions = [local_position]
        bot.api.place_order = AsyncMock(
            return_value={"status": "error", "msg": "rejected"}
        )

        assert not await bot._close_position(local_position, 99.0, "SL")
        assert local_position in bot.positions
        assert local_position.execution_state == "open"
        bot.db.save_trade.assert_not_called()

    async def test_flat_snapshot_never_submits_second_exit(self, bot):
        bot.config.PAPER_TRADING = False
        local_position = position()
        bot.positions = [local_position]
        bot.api.get_positions = AsyncMock(return_value=PositionRead.success([]))

        assert await bot._reconcile_positions()
        assert local_position.execution_state == "externally_closed"
        assert local_position not in bot.positions

    async def test_unfilled_exit_remains_pending_without_trade(self, bot):
        bot.config.PAPER_TRADING = False
        local_position = position()
        bot.positions = [local_position]
        bot.api.place_order = AsyncMock(
            return_value={"status": "ok", "response": {"oid": 7}}
        )
        bot.api.get_positions = AsyncMock(
            return_value=PositionRead.success(
                [{"coin": "HYPE", "direction": "Long", "szi": "10"}]
            )
        )

        assert not await bot._close_position(local_position, 99.0, "SL")
        assert local_position.execution_state == "exit_requested"
        assert local_position in bot.positions
        bot.db.save_trade.assert_not_called()

    async def test_unavailable_snapshot_changes_nothing(self, bot):
        bot.config.PAPER_TRADING = False
        local_position = position()
        bot.positions = [local_position]
        bot.api.get_positions = AsyncMock(
            return_value=PositionRead.unavailable("timeout")
        )

        assert not await bot._reconcile_positions()
        assert local_position in bot.positions
        assert local_position.execution_state == "open"
        assert bot.is_paused

    async def test_paper_exit_finalizes_once(self, bot):
        local_position = position()
        bot.positions = [local_position]

        assert await bot._close_position(local_position, 105.0, "TP")
        assert local_position not in bot.positions
        assert local_position.execution_state == "closed_confirmed"
        bot.db.save_trade.assert_called_once()
        bot.db.close_position_by_uid.assert_called_once_with(local_position.id)
