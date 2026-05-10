"""
Unit tests for Configuration and Data Models
"""

import pytest
from datetime import datetime

from src.core.config import BotConfig, Side, OrderStatus, Position, Trade


class TestSide:
    """Test Side enum"""

    def test_side_values(self):
        """Test side enum values"""
        assert Side.LONG.value == "LONG"
        assert Side.SHORT.value == "SHORT"


class TestOrderStatus:
    """Test OrderStatus enum"""

    def test_order_status_values(self):
        """Test order status enum values"""
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.OPEN.value == "open"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"


class TestPosition:
    """Test Position dataclass"""

    def test_position_creation(self):
        """Test position object creation"""
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
        )

        assert position.side == Side.LONG
        assert position.entry_price == 100.0
        assert position.quantity == 10.0
        assert position.tp_price == 105.0
        assert position.sl_price == 98.0
        assert position.leverage == 5
        assert position.status == OrderStatus.OPEN
        assert position.unrealized_pnl == 0.0
        assert position.oid is None
        assert position.cloid is None

    def test_position_with_optional_fields(self):
        """Test position with optional fields"""
        position = Position(
            side=Side.SHORT,
            entry_price=100.0,
            quantity=10.0,
            tp_price=95.0,
            sl_price=102.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=12345,
            cloid="test_cloid",
            status=OrderStatus.FILLED,
            unrealized_pnl=50.0,
        )

        assert position.oid == 12345
        assert position.cloid == "test_cloid"
        assert position.status == OrderStatus.FILLED
        assert position.unrealized_pnl == 50.0


class TestTrade:
    """Test Trade dataclass"""

    def test_trade_creation(self):
        """Test trade object creation"""
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=datetime.now() - __import__("datetime").timedelta(hours=1),
            exit_time=datetime.now(),
            pnl=50.0,
            fees=2.0,
        )

        assert trade.side == Side.LONG
        assert trade.entry_price == 100.0
        assert trade.exit_price == 105.0
        assert trade.quantity == 10.0
        assert trade.pnl == 50.0
        assert trade.fees == 2.0
        assert trade.notes == ""

    def test_trade_with_notes(self):
        """Test trade with notes field"""
        trade = Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=95.0,
            quantity=10.0,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            pnl=50.0,
            fees=2.0,
            notes="Good trade setup",
        )

        assert trade.notes == "Good trade setup"


class TestBotConfig:
    """Test BotConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        config = BotConfig()

        assert config.USE_TESTNET is False
        assert config.PAPER_TRADING is False
        assert config.ASSET == "HYPE"
        assert config.TIMEFRAME == "15m"
        assert config.LEVERAGE == 5
        assert config.RISK_PER_TRADE_PCT == 0.08
        assert config.TP_ATR_MULTIPLIER == 2.0
        assert config.SL_ATR_MULTIPLIER == 0.4
        assert config.MAX_POSITIONS == 2
        assert config.MAX_DAILY_TRADES == 20

    def test_api_url_mainnet(self):
        """Test API URL for mainnet"""
        config = BotConfig()
        config.USE_TESTNET = False

        assert (
            "mainnet" in config.API_URL.lower()
            or "hyperliquid" in config.API_URL.lower()
        )
        assert "testnet" not in config.API_URL.lower()

    def test_api_url_testnet(self):
        """Test API URL for testnet"""
        config = BotConfig()
        config.USE_TESTNET = True

        assert "testnet" in config.API_URL.lower()

    def test_ws_url_mainnet(self):
        """Test WebSocket URL for mainnet"""
        config = BotConfig()
        config.USE_TESTNET = False

        assert "wss://api.hyperliquid.xyz/ws" == config.WS_URL

    def test_ws_url_testnet(self):
        """Test WebSocket URL for testnet"""
        config = BotConfig()
        config.USE_TESTNET = True

        assert "testnet" in config.WS_URL

    def test_validate_success(self):
        """Test configuration validation with valid values"""
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64  # Valid hex key
        config.PAPER_TRADING = True

        # Should not raise
        config.validate()

    def test_validate_missing_private_key(self):
        """Test configuration validation without private key"""
        config = BotConfig()
        config.PRIVATE_KEY = ""
        config.PAPER_TRADING = False

        with pytest.raises(ValueError, match="PRIVATE_KEY"):
            config.validate()

    def test_validate_invalid_risk_pct(self):
        """Test configuration validation with invalid risk percentage"""
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64  # Set private key first
        config.RISK_PER_TRADE_PCT = 1.5  # > 100%

        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            config.validate()

    def test_validate_invalid_leverage(self):
        """Test configuration validation with invalid leverage"""
        config = BotConfig()
        config.PRIVATE_KEY = "0x" + "1" * 64  # Set private key first
        config.LEVERAGE = 150  # > 100

        with pytest.raises(ValueError, match="LEVERAGE"):
            config.validate()

    def test_paper_trading_no_private_key(self):
        """Test that paper trading doesn't require private key"""
        config = BotConfig()
        config.PRIVATE_KEY = ""
        config.PAPER_TRADING = True

        # Should not raise
        config.validate()

    def test_from_env(self, monkeypatch):
        """Test configuration from environment variables"""

        monkeypatch.setenv("USE_TESTNET", "true")
        monkeypatch.setenv("LEVERAGE", "10")
        monkeypatch.setenv("ASSET", "BTC")
        monkeypatch.setenv("TELEGRAM_ENABLED", "true")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        config = BotConfig.from_env()

        assert config.USE_TESTNET is True
        assert config.LEVERAGE == 10
        assert config.ASSET == "BTC"
        assert config.TELEGRAM_ENABLED is True
        assert config.TELEGRAM_BOT_TOKEN == "test_token"
        assert config.TELEGRAM_CHAT_ID == "123456"

    def test_from_env_boolean_parsing(self, monkeypatch):
        """Test boolean parsing from environment"""

        test_cases = [
            ("true", True),
            ("True", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("0", False),
        ]

        for env_val, expected in test_cases:
            monkeypatch.setenv("PAPER_TRADING", env_val)
            config = BotConfig.from_env()

            assert config.PAPER_TRADING == expected, f"Failed for {env_val}"
