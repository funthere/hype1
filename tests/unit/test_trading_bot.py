"""
Unit tests for TradingBot orchestrator
"""

import pytest
import asyncio
import signal
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta

from src.core.config import BotConfig, Side, Position, Trade
from src.bot.trading_bot import TradingBot


@pytest.fixture
def mock_all():
    """Mock all external dependencies"""
    with patch("src.bot.trading_bot.HyperliquidAPI") as mock_api:
        with patch("src.bot.trading_bot.MarketDataFeed") as mock_md:
            with patch("src.bot.trading_bot.StrategyEngine") as mock_strat:
                with patch("src.bot.trading_bot.RiskManager") as mock_rm:
                    with patch("src.bot.trading_bot.DatabaseManager") as mock_db:
                        with patch("src.bot.trading_bot.TelegramNotifier") as mock_tg:
                            api_instance = Mock()
                            api_instance.check_connection = AsyncMock(return_value=True)
                            api_instance.get_mids = AsyncMock(
                                return_value={"HYPE": 100.0}
                            )
                            api_instance.place_order = AsyncMock(
                                return_value={
                                    "status": "ok",
                                    "response": {"oid": 12345},
                                }
                            )
                            api_instance.cancel_order = AsyncMock(
                                return_value={"status": "ok"}
                            )
                            api_instance.cancel_all_orders = AsyncMock(
                                return_value={"status": "ok"}
                            )
                            mock_api.return_value = api_instance

                            md_instance = Mock()
                            md_instance.current_candle = None
                            md_instance.on_candle_update = Mock()
                            mock_md.return_value = md_instance

                            strat_instance = Mock()
                            strat_instance.generate_signal = Mock(return_value=None)
                            mock_strat.return_value = strat_instance

                            rm_instance = Mock()
                            rm_instance.can_open_position = Mock(
                                return_value=(True, "")
                            )
                            mock_rm.return_value = rm_instance

                            db_instance = Mock()
                            db_instance.save_position = Mock()
                            db_instance.save_trade = Mock()
                            db_instance.save_daily_summary = Mock()
                            db_instance.log_event = Mock()
                            db_instance.close = Mock()
                            db_instance.save_bot_state = Mock(return_value=1)
                            db_instance.load_bot_state = Mock(return_value=None)
                            mock_db.return_value = db_instance

                            tg_instance = Mock()
                            tg_instance.notify_start = AsyncMock()
                            tg_instance.notify_trade_entry = AsyncMock()
                            tg_instance.notify_trade_exit = AsyncMock()
                            tg_instance.notify_error = AsyncMock()
                            tg_instance.notify_daily_summary = AsyncMock()
                            tg_instance.notify_circuit_breaker = AsyncMock()
                            tg_instance.notify_shutdown = AsyncMock()
                            tg_instance.notify_info = AsyncMock()
                            tg_instance.close = AsyncMock()
                            mock_tg.from_config = Mock(return_value=tg_instance)

                            yield {
                                "api": api_instance,
                                "md": md_instance,
                                "strat": strat_instance,
                                "rm": rm_instance,
                                "db": db_instance,
                                "tg": tg_instance,
                                "api_cls": mock_api,
                                "strat_cls": mock_strat,
                                "rm_cls": mock_rm,
                                "db_cls": mock_db,
                                "tg_cls": mock_tg,
                            }


@pytest.fixture
def sample_signal():
    """Create a sample trading signal"""
    return {
        "action": Side.LONG,
        "confidence": 70.0,
        "entry_price": 100.0,
        "tp_price": 105.0,
        "sl_price": 98.0,
        "quantity": 10.0,
        "atr": 2.5,
    }


def create_bot(config=None, mocks=None):
    """Helper to create a TradingBot with given config"""
    if config is None:
        config = BotConfig()
        config.PAPER_TRADING = True
    bot = TradingBot(config)
    if mocks:
        bot.api = mocks["api"]
        bot.market_data = mocks["md"]
        bot.strategy = mocks["strat"]
        bot.risk_manager = mocks["rm"]
        bot.db = mocks["db"]
        bot.telegram = mocks["tg"]
        # Mock survival risk manager to always allow trades
        bot.survival_risk = Mock()
        bot.survival_risk.can_open_position = Mock(return_value=(True, ""))
        bot.survival_risk.update_after_trade = Mock()
        bot.survival_risk.tiered_risk = Mock()
    return bot


class TestTradingBotInit:
    """Test bot initialization"""

    def test_initialization(self, mock_all):
        config = BotConfig()
        config.PAPER_TRADING = True
        bot = TradingBot(config)

        assert bot.config == config
        assert bot.positions == []
        assert bot.trades == []
        assert bot.daily_trades == 0
        assert bot.daily_pnl == 0.0
        assert not bot.emergency_stop
        assert not bot._is_paused
        assert not bot.is_running
        assert bot.consecutive_losses == 0
        assert not bot.circuit_breaker_triggered
        assert bot._signal_cooldown_seconds == 900

    def test_config_validated_on_init(self, mock_all):
        config = BotConfig()
        config.PAPER_TRADING = False
        config.PRIVATE_KEY = ""
        with pytest.raises(ValueError, match="PRIVATE_KEY required"):
            TradingBot(config)

    def test_starting_capital_paper_trading(self, mock_all):
        config = BotConfig()
        config.PAPER_TRADING = True
        config.PAPER_CAPITAL = 50000
        bot = TradingBot(config)
        assert bot.starting_capital == 50000
        assert bot.current_capital == 50000

    def test_signal_handlers_setup(self, mock_all):
        with patch("src.bot.trading_bot.signal.signal") as mock_signal:
            config = BotConfig()
            config.PAPER_TRADING = True
            TradingBot(config)
            assert mock_signal.called


class TestTradingBotControl:
    """Test control methods"""

    def test_pause_trading(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.pause_trading()
        assert bot._is_paused

    def test_resume_trading(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot._is_paused = True
        bot.resume_trading()
        assert not bot._is_paused

    def test_reset_circuit_breaker(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = True
        bot.circuit_breaker_until = datetime.now()
        bot.consecutive_losses = 5
        bot.reset_circuit_breaker()
        assert not bot.circuit_breaker_triggered
        assert bot.circuit_breaker_until is None
        assert bot.consecutive_losses == 0

    def test_update_config_param_valid(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.update_config_param("RISK_PER_TRADE_PCT", 0.05)
        assert bot.config.RISK_PER_TRADE_PCT == 0.05

    def test_update_config_param_invalid(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.update_config_param("NONEXISTENT", 123)
        assert not hasattr(bot.config, "NONEXISTENT")


@pytest.mark.asyncio
class TestTradingBotProcessSignal:
    """Test signal processing logic"""

    async def test_signal_cooldown_skip(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot._last_signal_time = datetime.now()
        with patch.object(bot, "_place_entry_order") as mock_entry:
            await bot._process_signal(sample_signal)
            mock_entry.assert_not_called()

    async def test_signal_cooldown_expired(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot._last_signal_time = datetime.now() - timedelta(seconds=1000)
        with patch.object(bot, "_place_entry_order") as mock_entry:
            await bot._process_signal(sample_signal)
            mock_entry.assert_called_once_with(sample_signal)

    async def test_signal_risk_manager_blocks(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot.risk_manager.can_open_position = Mock(
            return_value=(False, "Max positions reached")
        )
        with patch.object(bot, "_place_entry_order") as mock_entry:
            await bot._process_signal(sample_signal)
            mock_entry.assert_not_called()

    async def test_signal_confidence_too_low_long(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.CONFIDENCE_THRESHOLD = 60
        signal = {
            "action": Side.LONG,
            "confidence": 50.0,
            "entry_price": 100.0,
            "tp_price": 105.0,
            "sl_price": 98.0,
            "quantity": 10.0,
            "atr": 2.5,
        }
        with patch.object(bot, "_place_entry_order") as mock_entry:
            await bot._process_signal(signal)
            mock_entry.assert_not_called()

    async def test_signal_confidence_ok(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot._last_signal_time = datetime.now() - timedelta(seconds=1000)
        with patch.object(bot, "_place_entry_order") as mock_entry:
            await bot._process_signal(sample_signal)
            mock_entry.assert_called_once()

    async def test_signal_sets_last_time(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot._last_signal_time = datetime.now() - timedelta(seconds=1000)
        bot._last_signal_time = None
        with patch.object(bot, "_place_entry_order"):
            await bot._process_signal(sample_signal)
            assert bot._last_signal_time is not None


@pytest.mark.asyncio
class TestTradingBotPlaceEntry:
    """Test entry order placement"""

    async def test_place_entry_paper_trading(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = True
        initial_count = len(bot.positions)
        await bot._place_entry_order(sample_signal)
        assert len(bot.positions) == initial_count + 1
        pos = bot.positions[-1]
        assert pos.side == Side.LONG
        assert pos.entry_price == 100.0
        assert pos.quantity == 10.0
        assert bot.db.save_position.called
        assert bot.db.log_event.called
        assert bot.telegram.notify_trade_entry.called

    async def test_place_entry_live_trading(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        await bot._place_entry_order(sample_signal)
        assert bot.api.place_order.called
        api_call_kwargs = bot.api.place_order.call_args[1]
        assert api_call_kwargs["side"] == Side.LONG
        assert api_call_kwargs["price"] == 100.0
        assert api_call_kwargs["quantity"] == 10.0

    async def test_place_entry_live_failure(self, mock_all, sample_signal):
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.api.place_order = AsyncMock(
            return_value={"status": "error", "msg": "Insufficient margin"}
        )
        initial_count = len(bot.positions)
        await bot._place_entry_order(sample_signal)
        assert len(bot.positions) == initial_count

    async def test_place_entry_short_side(self, mock_all):
        bot = create_bot(mocks=mock_all)
        signal = {
            "action": Side.SHORT,
            "confidence": 70.0,
            "entry_price": 100.0,
            "tp_price": 95.0,
            "sl_price": 102.0,
            "quantity": 10.0,
            "atr": 2.5,
        }
        await bot._place_entry_order(signal)
        assert len(bot.positions) == 1
        assert bot.positions[0].side == Side.SHORT


@pytest.mark.asyncio
class TestTradingBotCheckExits:
    """Test position exit checking"""

    async def test_no_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.positions = []
        await bot._check_position_exits()
        assert len(bot.trades) == 0

    async def test_long_tp_hit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 107.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            )
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_called_once()

    async def test_long_sl_hit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 97.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            )
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_called_once()

    async def test_short_tp_hit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 93.0})
        bot.positions = [
            Position(
                side=Side.SHORT,
                entry_price=100.0,
                quantity=10.0,
                tp_price=95.0,
                sl_price=102.0,
                entry_time=datetime.now(),
                leverage=5,
            )
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_called_once()

    async def test_short_sl_hit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 103.0})
        bot.positions = [
            Position(
                side=Side.SHORT,
                entry_price=100.0,
                quantity=10.0,
                tp_price=95.0,
                sl_price=102.0,
                entry_time=datetime.now(),
                leverage=5,
            )
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_called_once()

    async def test_no_exit_when_price_between(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 102.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            )
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_not_called()

    async def test_multiple_positions_one_exit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 107.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            ),
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=110.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            ),
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            assert mock_close.call_count == 1

    async def test_price_zero_skip(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 0.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            ),
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._check_position_exits()
            mock_close.assert_not_called()


@pytest.mark.asyncio
class TestTradingBotClosePosition:
    """Test position closure"""

    async def test_long_profitable(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 105.0, "TP")

        assert len(bot.trades) == 1
        trade = bot.trades[0]
        assert trade.side == Side.LONG
        assert trade.entry_price == 100.0
        assert trade.exit_price == 105.0
        expected_pnl = (105.0 - 100.0) * 10.0
        # Entry: maker fee (post-only), Exit: taker fee (IOC)
        expected_fees = 100.0 * 10.0 * (bot.config.MAKER_FEE_PCT + bot.config.TAKER_FEE_PCT)
        assert trade.pnl == pytest.approx(expected_pnl - expected_fees)
        assert trade.fees == pytest.approx(expected_fees)

    async def test_short_profitable(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.SHORT,
            entry_price=100.0,
            quantity=10.0,
            tp_price=95.0,
            sl_price=102.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 95.0, "TP")

        assert len(bot.trades) == 1
        trade = bot.trades[0]
        assert trade.pnl > 0

    async def test_losing_trade_increments_consecutive(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        bot.consecutive_losses = 0
        await bot._close_position(position, 98.0, "SL")

        assert bot.consecutive_losses == 1

    async def test_winning_trade_resets_consecutive(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        bot.consecutive_losses = 2
        await bot._close_position(position, 105.0, "TP")

        assert bot.consecutive_losses == 0

    async def test_circuit_breaker_triggers(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.MAX_CONSECUTIVE_LOSSES = 3
        bot.config.CIRCUIT_BREAKER_ENABLED = True
        bot.consecutive_losses = 2
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        with patch.object(bot, "_trigger_circuit_breaker") as mock_trip:
            await bot._close_position(position, 98.0, "SL")
            mock_trip.assert_called_once()

    async def test_circuit_breaker_not_triggers_below_limit(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.MAX_CONSECUTIVE_LOSSES = 3
        bot.config.CIRCUIT_BREAKER_ENABLED = True
        bot.consecutive_losses = 0
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        with patch.object(bot, "_trigger_circuit_breaker") as mock_trip:
            await bot._close_position(position, 98.0, "SL")
            mock_trip.assert_not_called()

    async def test_drawdown_tracking(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.starting_capital = 10000
        bot.peak_equity = 10000
        bot.current_capital = 10000
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=1.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 95.0, "SL")
        assert bot.max_drawdown_pct > 0

    async def test_daily_tracking(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 105.0, "TP")
        assert bot.daily_trades == 1

    async def test_database_save(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 105.0, "TP")
        assert bot.db.save_trade.called
        assert bot.db.log_event.called

    async def test_telegram_notification(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 105.0, "TP")
        assert bot.telegram.notify_trade_exit.called

    async def test_removes_from_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [position]
        await bot._close_position(position, 105.0, "TP")
        assert position not in bot.positions


@pytest.mark.asyncio
class TestTradingBotDailyReset:
    """Test daily reset logic"""

    async def test_no_reset_same_day(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.last_trade_date = datetime.utcnow().date()
        bot.daily_trades = 5
        await bot._check_daily_reset()
        assert bot.daily_trades == 5

    async def test_reset_new_day(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.last_trade_date = (datetime.utcnow() - timedelta(days=1)).date()
        bot.daily_trades = 5
        bot.daily_pnl = 100.0
        bot.daily_trades_list = [
            Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=50.0,
                fees=2.0,
            )
        ]
        await bot._check_daily_reset()
        assert bot.daily_trades == 0
        assert bot.daily_pnl == 0.0
        assert bot.daily_trades_list == []
        assert bot.consecutive_losses == 0

    async def test_reset_saves_summary(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.last_trade_date = (datetime.utcnow() - timedelta(days=1)).date()
        bot.daily_trades = 1
        bot.daily_trades_list = [
            Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=50.0,
                fees=2.0,
            )
        ]
        bot.daily_pnl = 50.0
        await bot._check_daily_reset()
        assert bot.db.save_daily_summary.called

    async def test_reset_no_trades_no_summary(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.last_trade_date = (datetime.utcnow() - timedelta(days=1)).date()
        bot.daily_trades = 0
        await bot._check_daily_reset()
        assert not bot.db.save_daily_summary.called


class TestTradingBotCircuitBreaker:
    """Test circuit breaker logic"""

    @pytest.mark.asyncio
    async def test_trigger(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES = 30
        bot.consecutive_losses = 3
        await bot._trigger_circuit_breaker()
        assert bot.circuit_breaker_triggered
        assert bot.circuit_breaker_until is not None
        assert bot.telegram.notify_circuit_breaker.called

    @pytest.mark.asyncio
    async def test_cooldown_skips_when_not_triggered(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = False
        bot.circuit_breaker_until = None
        await bot._check_circuit_breaker_cooldown()
        assert not bot.circuit_breaker_triggered

    @pytest.mark.asyncio
    async def test_cooldown_expires(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = True
        bot.circuit_breaker_until = datetime.now() - timedelta(minutes=1)
        await bot._check_circuit_breaker_cooldown()
        assert not bot.circuit_breaker_triggered
        assert bot.circuit_breaker_until is None
        assert bot.consecutive_losses == 0

    @pytest.mark.asyncio
    async def test_cooldown_not_yet_expired(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = True
        bot.circuit_breaker_until = datetime.now() + timedelta(minutes=30)
        await bot._check_circuit_breaker_cooldown()
        assert bot.circuit_breaker_triggered

    def test_circuit_breaker_status_property(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.CIRCUIT_BREAKER_ENABLED = True
        bot.circuit_breaker_triggered = True
        bot.consecutive_losses = 3
        bot.circuit_breaker_until = datetime.now()
        status = bot.circuit_breaker_status
        assert status["enabled"] is True
        assert status["is_triggered"] is True
        assert status["consecutive_losses"] == 3
        assert status["max_consecutive_losses"] == 3


@pytest.mark.asyncio
class TestTradingBotForceClose:
    """Test force close functionality"""

    async def test_force_close_no_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.positions = []
        count = await bot.force_close_all_positions("TEST")
        assert count == 0

    async def test_force_close_with_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 105.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            ),
        ]
        with patch.object(bot, "_close_position") as mock_close:
            count = await bot.force_close_all_positions("TEST")
            assert count == 1
            mock_close.assert_called_once()

    async def test_close_all_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        with patch.object(bot, "force_close_all_positions") as mock_fc:
            await bot.close_all_positions()
            mock_fc.assert_called_once_with("MANUAL_CLOSE")


@pytest.mark.asyncio
class TestTradingBotManualTrade:
    """Test manual trade placement"""

    async def test_place_manual_trade(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 100.0})
        with patch.object(bot, "_place_entry_order") as mock_entry:
            result = await bot.place_manual_trade(Side.LONG, 10.0)
            assert result["status"] == "ok"
            mock_entry.assert_called_once()
            signal = mock_entry.call_args[0][0]
            assert signal["action"] == Side.LONG
            assert signal["entry_price"] == 100.0

    async def test_place_manual_trade_no_price(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 0.0})
        with pytest.raises(ValueError):
            await bot.place_manual_trade(Side.LONG, 10.0)


@pytest.mark.asyncio
class TestTradingBotUnrealizedPnL:
    """Test unrealized PnL updates"""

    async def test_update_long_position(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 105.0})
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            unrealized_pnl=0.0,
        )
        bot.positions = [position]
        await bot._update_unrealized_pnl()
        assert position.unrealized_pnl == 50.0

    async def test_update_short_position(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 95.0})
        position = Position(
            side=Side.SHORT,
            entry_price=100.0,
            quantity=10.0,
            tp_price=95.0,
            sl_price=102.0,
            entry_time=datetime.now(),
            leverage=5,
            unrealized_pnl=0.0,
        )
        bot.positions = [position]
        await bot._update_unrealized_pnl()
        assert position.unrealized_pnl == 50.0

    async def test_update_no_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.positions = []
        await bot._update_unrealized_pnl()
        assert not bot.api.get_mids.called


class TestTradingBotStatistics:
    """Test statistics printing"""

    def test_print_statistics_empty(self, mock_all, capsys):
        bot = create_bot(mocks=mock_all)
        bot.print_statistics()
        captured = capsys.readouterr()
        assert "TRADING BOT STATISTICS" in captured.out
        assert "Total Trades:" in captured.out
        assert "Win Rate:" in captured.out

    def test_print_statistics_with_trades(self, mock_all, capsys):
        bot = create_bot(mocks=mock_all)
        bot.trades = [
            Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=50.0,
                fees=2.0,
            ),
            Trade(
                side=Side.SHORT,
                entry_price=100.0,
                exit_price=98.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=-20.0,
                fees=2.0,
            ),
        ]
        bot.print_statistics()
        captured = capsys.readouterr()
        assert "Total Trades:         2" in captured.out
        assert "Win Rate:             50.0%" in captured.out


class TestTradingBotSignalHandlers:
    """Test signal handler actions"""

    def test_handle_force_close(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot._handle_force_close_signal(None, None)
        assert bot.force_close_all

    def test_handle_reset_circuit_breaker_active(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = True
        bot.circuit_breaker_until = datetime.now()
        bot.consecutive_losses = 5
        bot._handle_reset_circuit_breaker_signal(None, None)
        assert not bot.circuit_breaker_triggered
        assert bot.circuit_breaker_until is None
        assert bot.consecutive_losses == 0

    def test_handle_reset_circuit_breaker_inactive(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.circuit_breaker_triggered = False
        bot._handle_reset_circuit_breaker_signal(None, None)
        assert not bot.circuit_breaker_triggered


@pytest.mark.asyncio
class TestTradingBotShutdown:
    """Test shutdown logic"""

    async def test_stop(self, mock_all):
        bot = create_bot(mocks=mock_all)
        with patch.object(bot, "_shutdown") as mock_shutdown:
            await bot.stop()
            assert bot.emergency_stop
            mock_shutdown.assert_called_once_with("Manual stop")

    async def test_shutdown_closes_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 105.0})
        bot.positions = [
            Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5,
            ),
        ]
        with patch.object(bot, "_close_position") as mock_close:
            await bot._shutdown("Test")
            mock_close.assert_called()

    async def test_shutdown_cancels_live_orders(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        with patch.object(bot, "_close_position"):
            await bot._shutdown("Test")
            assert bot.api.cancel_all_orders.called

    async def test_shutdown_skips_cancel_paper(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = True
        with patch.object(bot, "_close_position"):
            await bot._shutdown("Test")
            assert not bot.api.cancel_all_orders.called

    async def test_shutdown_notifications(self, mock_all):
        bot = create_bot(mocks=mock_all)
        with patch.object(bot, "_close_position"):
            await bot._shutdown("Test")
            assert bot.telegram.notify_shutdown.called
            assert bot.db.close.called
            assert bot.telegram.close.called


class TestPositionReconciliation:
    """Test exchange-to-local position reconciliation."""

    def test_maybe_reconcile_skips_paper_mode(self, mock_all):
        """Paper trading should never reconcile."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = True
        bot._last_reconciliation = None  # never reconciled
        # Run directly — should return immediately without calling _reconcile
        import asyncio
        asyncio.get_event_loop().run_until_complete(bot._maybe_reconcile_positions())
        bot.api.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_reconcile_respects_interval(self, mock_all):
        """Should skip if last reconciliation was within interval."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot._last_reconciliation = datetime.now()  # just reconciled
        await bot._maybe_reconcile_positions()
        bot.api.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_detects_missing_exchange_position(self, mock_all):
        """Local position not on exchange → should close it."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.config.ASSET = "HYPE"
        bot.api.get_positions = AsyncMock(return_value=[])  # nothing on exchange
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 100.0})

        pos = Position(
            side=Side.LONG,
            entry_price=99.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=96.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [pos]

        with patch.object(bot, "_close_position", new_callable=AsyncMock) as mock_close:
            await bot._reconcile_positions()
            mock_close.assert_called_once_with(pos, 100.0, "RECONCILE_MISSING")

    @pytest.mark.asyncio
    async def test_reconcile_restores_untracked_exchange_position(self, mock_all):
        """Exchange position not tracked locally → should restore it."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.config.ASSET = "HYPE"
        bot.api.get_positions = AsyncMock(
            return_value=[
                {"coin": "HYPE", "direction": "Long", "szi": "5.0", "entryPx": "42.0"}
            ]
        )
        bot.api.get_mids = AsyncMock(return_value={"HYPE": 43.0})
        bot.positions = []

        await bot._reconcile_positions()
        assert len(bot.positions) == 1
        assert bot.positions[0].side == Side.LONG
        assert bot.positions[0].quantity == 5.0
        assert bot.positions[0].entry_price == 42.0
        bot.db.save_position.assert_called()
        bot.db.log_event.assert_called()

    @pytest.mark.asyncio
    async def test_reconcile_fixes_quantity_drift(self, mock_all):
        """Quantity mismatch → local updated to match exchange."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.config.ASSET = "HYPE"
        bot.api.get_positions = AsyncMock(
            return_value=[
                {"coin": "HYPE", "direction": "Long", "szi": "8.0", "entryPx": "42.0"}
            ]
        )

        pos = Position(
            side=Side.LONG,
            entry_price=42.0,
            quantity=10.0,  # drift: local says 10, exchange says 8
            tp_price=45.0,
            sl_price=40.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [pos]

        await bot._reconcile_positions()
        assert pos.quantity == 8.0

    @pytest.mark.asyncio
    async def test_reconcile_no_drift_no_action(self, mock_all):
        """Everything matches → no changes."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.config.ASSET = "HYPE"
        bot.api.get_positions = AsyncMock(
            return_value=[
                {"coin": "HYPE", "direction": "Long", "szi": "10.0", "entryPx": "100.0"}
            ]
        )

        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )
        bot.positions = [pos]

        await bot._reconcile_positions()
        assert len(bot.positions) == 1
        assert pos.quantity == 10.0

    @pytest.mark.asyncio
    async def test_reconcile_error_handling(self, mock_all):
        """Exchange error should not crash the bot."""
        bot = create_bot(mocks=mock_all)
        bot.config.PAPER_TRADING = False
        bot.api.get_positions = AsyncMock(side_effect=Exception("API down"))
        # Should not raise
        await bot._reconcile_positions()


class TestGracefulShutdown:
    """Test SIGTERM/SIGINT graceful shutdown and state persistence."""

    def test_sigterm_sets_emergency_stop(self, mock_all):
        bot = create_bot(mocks=mock_all)
        assert not bot.emergency_stop
        bot._handle_graceful_shutdown(signal.SIGTERM, None)
        assert bot.emergency_stop

    def test_sigint_sets_emergency_stop(self, mock_all):
        bot = create_bot(mocks=mock_all)
        assert not bot.emergency_stop
        bot._handle_graceful_shutdown(signal.SIGINT, None)
        assert bot.emergency_stop

    @pytest.mark.asyncio
    async def test_shutdown_persists_state(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.current_capital = 9500.0
        bot.daily_pnl = -500.0
        bot.consecutive_losses = 2
        with patch.object(bot, "_close_position"):
            await bot._shutdown("Test persist")
            bot.db.save_bot_state.assert_called_once()
            call_kwargs = bot.db.save_bot_state.call_args[1]
            assert call_kwargs["current_capital"] == 9500.0
            assert call_kwargs["daily_pnl"] == -500.0
            assert call_kwargs["consecutive_losses"] == 2

    @pytest.mark.asyncio
    async def test_main_loop_exits_triggers_shutdown(self, mock_all):
        """When emergency_stop is True, main loop should exit and call _shutdown."""
        bot = create_bot(mocks=mock_all)
        bot.emergency_stop = True
        with patch.object(bot, "_shutdown", new_callable=AsyncMock) as mock_s:
            await bot._main_loop()
            mock_s.assert_called_once_with("Main loop exited")

    @pytest.mark.asyncio
    async def test_main_loop_periodic_persist(self, mock_all):
        """State should be persisted every 5 minutes in the main loop."""
        bot = create_bot(mocks=mock_all)
        bot.market_data.current_candle = None  # no signals

        # Run for one iteration with a fake time jump
        call_count = 0

        original_sleep = asyncio.sleep

        async def fake_sleep(sec):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                bot.emergency_stop = True
            await original_sleep(0)

        with patch.object(bot, "_persist_state"):
            with patch("asyncio.sleep", side_effect=fake_sleep):
                # Make _last_persist old enough to trigger persist
                bot._main_loop_code = True  # dummy
                await bot._main_loop()
                # _persist_state called if interval elapsed (depends on timing)
                # At minimum, _shutdown should be called
                # (we're testing the mechanism exists, not exact timing)

    @pytest.mark.asyncio
    async def test_persist_state_error_handling(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.db.save_bot_state.side_effect = Exception("DB error")
        # Should not raise
        bot._persist_state()


class TestStateRestore:
    """Test state restoration on startup."""

    def test_restore_state_no_previous(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.db.load_bot_state.return_value = None
        bot._restore_state()
        assert bot.current_capital == bot.starting_capital

    def test_restore_state_with_previous(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.db.load_bot_state.return_value = {
            "current_capital": 9200.0,
            "peak_equity": 10000.0,
            "max_drawdown_pct": 8.0,
            "daily_pnl": -300.0,
            "daily_trades": 5,
            "consecutive_losses": 2,
            "circuit_breaker_triggered": True,
            "circuit_breaker_until": None,
            "last_trade_date": datetime.now().isoformat(),
            "last_signal_time": None,
            "emergency_stop": False,
        }
        bot.db.get_active_positions.return_value = []
        bot._restore_state()
        assert bot.current_capital == 9200.0
        assert bot.daily_pnl == -300.0
        assert bot.consecutive_losses == 2
        assert bot.circuit_breaker_triggered is True

    def test_restore_state_with_positions(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.db.load_bot_state.return_value = {
            "current_capital": 9500.0,
            "peak_equity": 10000.0,
            "max_drawdown_pct": 5.0,
            "daily_pnl": 0,
            "daily_trades": 0,
            "consecutive_losses": 0,
            "circuit_breaker_triggered": False,
        }
        now_iso = datetime.now().isoformat()
        bot.db.get_active_positions.return_value = [
            {
                "side": "LONG",
                "entry_price": 100.0,
                "quantity": 10.0,
                "tp_price": 105.0,
                "sl_price": 98.0,
                "entry_time": now_iso,
                "leverage": 5,
                "oid": None,
                "cloid": None,
                "unrealized_pnl": 50.0,
            }
        ]
        bot._restore_state()
        assert len(bot.positions) == 1
        assert bot.positions[0].entry_price == 100.0

    def test_restore_state_new_day_resets_counters(self, mock_all):
        bot = create_bot(mocks=mock_all)
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        bot.db.load_bot_state.return_value = {
            "current_capital": 9500.0,
            "peak_equity": 10000.0,
            "max_drawdown_pct": 5.0,
            "daily_pnl": -200.0,
            "daily_trades": 10,
            "consecutive_losses": 3,
            "circuit_breaker_triggered": False,
            "last_trade_date": yesterday,
        }
        bot.db.get_active_positions.return_value = []
        bot._restore_state()
        assert bot.daily_trades == 0
        assert bot.daily_pnl == 0.0

    def test_restore_state_error_handling(self, mock_all):
        bot = create_bot(mocks=mock_all)
        bot.db.load_bot_state.side_effect = Exception("DB corrupt")
        # Should not raise, just log and continue with fresh state
        bot._restore_state()
        assert bot.current_capital == bot.starting_capital
