"""
Extended unit tests for Configuration — FundingArbConfig, TrendFollowingConfig,
and additional BotConfig edge cases not covered by test_config.py
"""

import pytest
from datetime import datetime

from src.core.config import BotConfig, Side, OrderStatus, Position, Trade
from src.strategy.funding_rate_arb import FundingArbConfig
from src.strategy.trend_following import TrendFollowingConfig


# ---------------------------------------------------------------------------
# FundingArbConfig
# ---------------------------------------------------------------------------

class TestFundingArbConfigDefaults:
    """Test FundingArbConfig default values"""

    def test_default_values(self):
        cfg = FundingArbConfig()
        assert cfg.PAPER_TRADING is True
        assert cfg.USE_TESTNET is False
        assert cfg.PRIVATE_KEY == ""
        assert cfg.ADDRESS == ""
        assert cfg.ACCOUNT_ADDRESS is None
        assert cfg.PAPER_CAPITAL == 10_000.0
        assert cfg.ENTRY_THRESHOLD == 0.0003
        assert cfg.EXIT_THRESHOLD == 0.0001
        assert cfg.POSITION_SIZE_PCT == 0.10
        assert cfg.LEVERAGE == 3
        assert cfg.MAX_CONCURRENT_POSITIONS == 3
        assert cfg.MAX_HOLD_HOURS == 72.0
        assert cfg.MAX_LOSS_PCT == 0.05
        assert cfg.CHECK_INTERVAL == 300
        assert cfg.COINS is None
        assert cfg.TAKER_FEE_PCT == 0.0005
        assert cfg.SPOT_HEDGE_ENABLED is True
        assert cfg.DATABASE_PATH == "trading_bot.db"

    def test_spot_eligible_coins_default(self):
        cfg = FundingArbConfig()
        assert "BTC" in cfg.SPOT_ELIGIBLE_COINS
        assert "ETH" in cfg.SPOT_ELIGIBLE_COINS
        assert "HYPE" in cfg.SPOT_ELIGIBLE_COINS

    def test_custom_spot_eligible_coins(self):
        cfg = FundingArbConfig(SPOT_ELIGIBLE_COINS=["DOGE", "SHIB"])
        assert cfg.SPOT_ELIGIBLE_COINS == ["DOGE", "SHIB"]


class TestFundingArbConfigValidation:
    """Test FundingArbConfig.validate()"""

    def test_valid_defaults(self):
        cfg = FundingArbConfig()
        assert cfg.validate() is True

    def test_invalid_position_size_pct_zero(self):
        cfg = FundingArbConfig(POSITION_SIZE_PCT=0)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_invalid_position_size_pct_negative(self):
        cfg = FundingArbConfig(POSITION_SIZE_PCT=-0.1)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_invalid_position_size_pct_over_one(self):
        cfg = FundingArbConfig(POSITION_SIZE_PCT=1.5)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_invalid_entry_threshold_zero(self):
        cfg = FundingArbConfig(ENTRY_THRESHOLD=0)
        with pytest.raises(ValueError, match="ENTRY_THRESHOLD"):
            cfg.validate()

    def test_invalid_entry_threshold_negative(self):
        cfg = FundingArbConfig(ENTRY_THRESHOLD=-0.001)
        with pytest.raises(ValueError, match="ENTRY_THRESHOLD"):
            cfg.validate()

    def test_invalid_exit_threshold_negative(self):
        cfg = FundingArbConfig(EXIT_THRESHOLD=-0.001)
        with pytest.raises(ValueError, match="EXIT_THRESHOLD"):
            cfg.validate()

    def test_invalid_leverage_too_low(self):
        cfg = FundingArbConfig(LEVERAGE=0)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_invalid_leverage_too_high(self):
        cfg = FundingArbConfig(LEVERAGE=101)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_live_trading_requires_private_key(self):
        cfg = FundingArbConfig(PAPER_TRADING=False, PRIVATE_KEY="")
        with pytest.raises(ValueError, match="PRIVATE_KEY"):
            cfg.validate()

    def test_live_trading_with_private_key_ok(self):
        cfg = FundingArbConfig(
            PAPER_TRADING=False, PRIVATE_KEY="0x" + "ab" * 32
        )
        assert cfg.validate() is True

    def test_exit_threshold_zero_ok(self):
        cfg = FundingArbConfig(EXIT_THRESHOLD=0)
        assert cfg.validate() is True

    def test_position_size_pct_boundary_one(self):
        cfg = FundingArbConfig(POSITION_SIZE_PCT=1.0)
        assert cfg.validate() is True


# ---------------------------------------------------------------------------
# TrendFollowingConfig
# ---------------------------------------------------------------------------

class TestTrendFollowingConfigDefaults:
    """Test TrendFollowingConfig default values"""

    def test_default_values(self):
        cfg = TrendFollowingConfig()
        assert cfg.PAPER_TRADING is True
        assert cfg.USE_TESTNET is False
        assert cfg.PRIVATE_KEY == ""
        assert cfg.ADDRESS == ""
        assert cfg.ACCOUNT_ADDRESS is None
        assert cfg.PAPER_CAPITAL == 10_000.0
        assert cfg.FAST_EMA_PERIOD == 9
        assert cfg.SLOW_EMA_PERIOD == 21
        assert cfg.TREND_EMA_PERIOD == 50
        assert cfg.ADX_PERIOD == 14
        assert cfg.ADX_THRESHOLD == 20.0
        assert cfg.ATR_PERIOD == 14
        assert cfg.REQUIRE_PULLBACK is True
        assert cfg.PULLBACK_ATR_MULT == 0.5
        assert cfg.VOLUME_FILTER is True
        assert cfg.VOLUME_MULT == 1.2
        assert cfg.POSITION_SIZE_PCT == 0.10
        assert cfg.LEVERAGE == 3
        assert cfg.ATR_STOP_MULT == 2.0
        assert cfg.ATR_TP_MULT == 4.0
        assert cfg.TRAILING_STOP_MULT == 2.5
        assert cfg.USE_TRAILING_STOP is True
        assert cfg.MAX_CONCURRENT_POSITIONS == 3
        assert cfg.MAX_HOLD_HOURS == 168.0
        assert cfg.MAX_LOSS_PCT == 0.05
        assert cfg.CHECK_INTERVAL == 300
        assert cfg.CANDLE_INTERVAL == "1h"
        assert cfg.TAKER_FEE_PCT == 0.0005
        assert cfg.DATABASE_PATH == "trend_following.db"
        assert cfg.COINS is None

    def test_custom_coins(self):
        cfg = TrendFollowingConfig(COINS=["BTC", "ETH", "SOL"])
        assert cfg.COINS == ["BTC", "ETH", "SOL"]


class TestTrendFollowingConfigValidation:
    """Test TrendFollowingConfig.validate()"""

    def test_valid_defaults(self):
        cfg = TrendFollowingConfig()
        assert cfg.validate() is True

    def test_invalid_position_size_pct_zero(self):
        cfg = TrendFollowingConfig(POSITION_SIZE_PCT=0)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_invalid_position_size_pct_over_one(self):
        cfg = TrendFollowingConfig(POSITION_SIZE_PCT=2.0)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_fast_ema_ge_slow_ema(self):
        cfg = TrendFollowingConfig(FAST_EMA_PERIOD=21, SLOW_EMA_PERIOD=21)
        with pytest.raises(ValueError, match="FAST_EMA_PERIOD"):
            cfg.validate()

    def test_fast_ema_greater_than_slow_ema(self):
        cfg = TrendFollowingConfig(FAST_EMA_PERIOD=30, SLOW_EMA_PERIOD=21)
        with pytest.raises(ValueError, match="FAST_EMA_PERIOD"):
            cfg.validate()

    def test_fast_less_than_slow_ok(self):
        cfg = TrendFollowingConfig(FAST_EMA_PERIOD=8, SLOW_EMA_PERIOD=20)
        assert cfg.validate() is True

    def test_invalid_leverage_too_low(self):
        cfg = TrendFollowingConfig(LEVERAGE=0)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_invalid_leverage_too_high(self):
        cfg = TrendFollowingConfig(LEVERAGE=200)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_live_trading_requires_private_key(self):
        cfg = TrendFollowingConfig(PAPER_TRADING=False, PRIVATE_KEY="")
        with pytest.raises(ValueError, match="PRIVATE_KEY"):
            cfg.validate()

    def test_live_trading_with_private_key_ok(self):
        cfg = TrendFollowingConfig(
            PAPER_TRADING=False, PRIVATE_KEY="0x" + "ab" * 32
        )
        assert cfg.validate() is True

    def test_position_size_pct_boundary_one(self):
        cfg = TrendFollowingConfig(POSITION_SIZE_PCT=1.0)
        assert cfg.validate() is True

    def test_leverage_boundary_1(self):
        cfg = TrendFollowingConfig(LEVERAGE=1)
        assert cfg.validate() is True

    def test_leverage_boundary_100(self):
        cfg = TrendFollowingConfig(LEVERAGE=100)
        assert cfg.validate() is True


# ---------------------------------------------------------------------------
# BotConfig — additional edge cases
# ---------------------------------------------------------------------------

class TestBotConfigExtendedEdgeCases:
    """Extended edge cases for BotConfig beyond test_config.py"""

    def test_validate_risk_pct_zero(self):
        """Risk pct of 0 should be invalid"""
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.RISK_PER_TRADE_PCT = 0
        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            config.validate()

    def test_validate_risk_pct_negative(self):
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.RISK_PER_TRADE_PCT = -0.5
        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            config.validate()

    def test_validate_risk_pct_boundary(self):
        """Risk pct of exactly 1.0 should be invalid (must be < 1, the check is >1)"""
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.RISK_PER_TRADE_PCT = 1.0
        # The condition is: RISK_PER_TRADE_PCT <= 0 or RISK_PER_TRADE_PCT > 1
        # So 1.0 should pass (not > 1)
        assert config.validate() is True

    def test_validate_leverage_zero(self):
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.LEVERAGE = 0
        with pytest.raises(ValueError, match="LEVERAGE"):
            config.validate()

    def test_validate_leverage_negative(self):
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.LEVERAGE = -5
        with pytest.raises(ValueError, match="LEVERAGE"):
            config.validate()

    def test_validate_leverage_boundary_1(self):
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.LEVERAGE = 1
        assert config.validate() is True

    def test_validate_leverage_boundary_100(self):
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64
        config.LEVERAGE = 100
        assert config.validate() is True

    def test_info_url_matches_api_url(self):
        """INFO_URL should be the same as API_URL"""
        config = BotConfig()
        assert config.API_URL == config.INFO_URL

    def test_info_url_testnet(self):
        config = BotConfig()
        config.USE_TESTNET = True
        assert "testnet" in config.INFO_URL.lower()

    def test_from_env_float_parsing(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.15")
        monkeypatch.setenv("TP_ATR_MULTIPLIER", "3.5")
        config = BotConfig.from_env()
        assert config.RISK_PER_TRADE_PCT == 0.15
        assert config.TP_ATR_MULTIPLIER == 3.5

    def test_from_env_int_parsing(self, monkeypatch):
        monkeypatch.setenv("LEVERAGE", "20")
        monkeypatch.setenv("MAX_POSITIONS", "5")
        config = BotConfig.from_env()
        assert config.LEVERAGE == 20
        assert config.MAX_POSITIONS == 5

    def test_from_env_unknown_vars_ignored(self, monkeypatch):
        """Unknown env vars should be silently ignored"""
        monkeypatch.setenv("UNKNOWN_VARIABLE_XYZ", "test")
        config = BotConfig.from_env()
        assert not hasattr(config, "UNKNOWN_VARIABLE_XYZ")

    def test_from_env_yes_boolean(self, monkeypatch):
        monkeypatch.setenv("EMERGENCY_SHUTDOWN", "yes")
        config = BotConfig.from_env()
        assert config.EMERGENCY_SHUTDOWN is True

    def test_from_env_no_boolean(self, monkeypatch):
        monkeypatch.setenv("EMERGENCY_SHUTDOWN", "no")
        config = BotConfig.from_env()
        assert config.EMERGENCY_SHUTDOWN is False

    def test_default_web_ui_settings(self):
        config = BotConfig()
        assert config.WEB_UI_ENABLED is True
        assert config.WEB_UI_HOST == "127.0.0.1"
        assert config.WEB_UI_PORT == 8000

    def test_default_fee_settings(self):
        config = BotConfig()
        assert config.MAKER_FEE_PCT == -0.0002  # Negative means rebate
        assert config.TAKER_FEE_PCT == 0.0004

    def test_default_telegram_disabled(self):
        config = BotConfig()
        assert config.TELEGRAM_ENABLED is False
        assert config.TELEGRAM_BOT_TOKEN == ""
        assert config.TELEGRAM_CHAT_ID == ""


# ---------------------------------------------------------------------------
# Position / Trade edge cases
# ---------------------------------------------------------------------------

class TestPositionEdgeCases:
    """Additional Position dataclass edge cases"""

    def test_position_default_oid_cloid_none(self):
        pos = Position(
            side=Side.LONG,
            entry_price=50.0,
            quantity=1.0,
            tp_price=55.0,
            sl_price=48.0,
            entry_time=datetime.now(),
            leverage=3,
        )
        assert pos.oid is None
        assert pos.cloid is None

    def test_position_zero_quantity(self):
        pos = Position(
            side=Side.SHORT,
            entry_price=100.0,
            quantity=0.0,
            tp_price=90.0,
            sl_price=110.0,
            entry_time=datetime.now(),
            leverage=1,
        )
        assert pos.quantity == 0.0

    def test_position_negative_unrealized_pnl(self):
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=95.0,
            entry_time=datetime.now(),
            leverage=5,
            unrealized_pnl=-150.0,
        )
        assert pos.unrealized_pnl == -150.0


class TestTradeEdgeCases:
    """Additional Trade dataclass edge cases"""

    def test_trade_without_exit_time(self):
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=5.0,
            entry_time=datetime.now(),
        )
        assert trade.exit_time is None
        assert trade.pnl == 0.0
        assert trade.fees == 0.0
        assert trade.notes == ""

    def test_trade_negative_pnl(self):
        trade = Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=110.0,
            quantity=5.0,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            pnl=-50.0,
            fees=1.0,
            notes="Bad trade",
        )
        assert trade.pnl == -50.0
        assert trade.notes == "Bad trade"

    def test_trade_zero_pnl(self):
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=100.0,
            quantity=5.0,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            pnl=0.0,
            fees=0.5,
        )
        assert trade.pnl == 0.0
