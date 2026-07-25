"""
Unit tests for BaseStrategyConfig and config inheritance.

Tests cover:
  - BaseStrategyConfig defaults
  - from_env() with mocked environment variables
  - Validation rules (invalid leverage, negative risk, etc.)
  - Inheritance correctness for FundingArbConfig and TrendFollowingConfig
  - BotConfig from_env() no longer iterates os.environ.items()
"""

import pytest

from src.core.base_config import BaseStrategyConfig
from src.core.config import BotConfig
from src.strategy.funding_rate_arb import FundingArbConfig
from src.strategy.trend_following import TrendFollowingConfig


# =====================================================================
# BaseStrategyConfig defaults
# =====================================================================


class TestBaseStrategyConfigDefaults:
    """Test that BaseStrategyConfig has sensible defaults."""

    def test_default_values(self):
        cfg = BaseStrategyConfig()
        assert cfg.USE_TESTNET is False
        assert cfg.PAPER_TRADING is True
        assert cfg.PRIVATE_KEY == ""
        assert cfg.ADDRESS == ""
        assert cfg.ACCOUNT_ADDRESS is None
        assert cfg.PAPER_CAPITAL == 10_000.0
        assert cfg.ASSET == "HYPE"
        assert cfg.TIMEFRAME == "15m"
        assert cfg.LEVERAGE == 2
        assert cfg.RISK_PER_TRADE_PCT == 0.005
        assert cfg.POSITION_SIZE_PCT == 0.10
        assert cfg.MAX_POSITIONS == 1
        assert cfg.MAX_DAILY_TRADES == 5
        assert cfg.MAX_DAILY_LOSS_PCT == 0.02
        assert cfg.MAX_LOSS_PCT == 0.05
        assert cfg.EMERGENCY_SHUTDOWN is False
        assert cfg.MAKER_FEE_PCT == -0.0002
        assert cfg.TAKER_FEE_PCT == 0.0005
        assert cfg.DATABASE_PATH == "trading_bot.db"

    def test_override_defaults(self):
        cfg = BaseStrategyConfig(
            LEVERAGE=10,
            ASSET="BTC",
            RISK_PER_TRADE_PCT=0.02,
        )
        assert cfg.LEVERAGE == 10
        assert cfg.ASSET == "BTC"
        assert cfg.RISK_PER_TRADE_PCT == 0.02


# =====================================================================
# from_env()
# =====================================================================


class TestFromEnv:
    """Test explicit from_env() with mocked env vars."""

    def test_from_env_reads_explicit_keys(self, monkeypatch):
        """from_env should only read from the _ENV_VAR_KEYS allow-list."""
        monkeypatch.setenv("LEVERAGE", "10")
        monkeypatch.setenv("ASSET", "ETH")
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.05")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.LEVERAGE == 10
        assert cfg.ASSET == "ETH"
        assert cfg.RISK_PER_TRADE_PCT == 0.05

    def test_from_env_ignores_unknown_vars(self, monkeypatch):
        """from_env must NOT pick up random env vars not in the allow-list."""
        monkeypatch.setenv("RANDOM_VAR_ABC", "should_not_appear")
        monkeypatch.setenv("LEVERAGE", "7")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.LEVERAGE == 7
        assert not hasattr(cfg, "RANDOM_VAR_ABC")

    def test_from_env_boolean_parsing(self, monkeypatch):
        monkeypatch.setenv("USE_TESTNET", "true")
        monkeypatch.setenv("PAPER_TRADING", "1")
        monkeypatch.setenv("EMERGENCY_SHUTDOWN", "yes")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.USE_TESTNET is True
        assert cfg.PAPER_TRADING is True
        assert cfg.EMERGENCY_SHUTDOWN is True

    def test_from_env_false_boolean_parsing(self, monkeypatch):
        monkeypatch.setenv("USE_TESTNET", "false")
        monkeypatch.setenv("PAPER_TRADING", "0")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.USE_TESTNET is False
        assert cfg.PAPER_TRADING is False

    def test_from_env_int_parsing(self, monkeypatch):
        monkeypatch.setenv("LEVERAGE", "20")
        monkeypatch.setenv("MAX_POSITIONS", "5")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.LEVERAGE == 20
        assert cfg.MAX_POSITIONS == 5
        assert isinstance(cfg.LEVERAGE, int)
        assert isinstance(cfg.MAX_POSITIONS, int)

    def test_from_env_float_parsing(self, monkeypatch):
        monkeypatch.setenv("RISK_PER_TRADE_PCT", "0.15")
        monkeypatch.setenv("PAPER_CAPITAL", "50000.0")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.RISK_PER_TRADE_PCT == 0.15
        assert cfg.PAPER_CAPITAL == 50_000.0
        assert isinstance(cfg.RISK_PER_TRADE_PCT, float)

    def test_from_env_unsets_remain_default(self, monkeypatch):
        """Env vars not set should retain defaults."""
        # Only set one var
        monkeypatch.setenv("LEVERAGE", "3")

        cfg = BaseStrategyConfig.from_env()

        assert cfg.LEVERAGE == 3
        assert cfg.ASSET == "HYPE"  # default
        assert cfg.PAPER_TRADING is True  # default

    def test_from_env_does_not_iterate_environ_items(self, monkeypatch):
        """Verify that os.environ.items() is not used by injecting a
        dangerous env var that has a name matching a config field but is
        NOT in the allow-list."""
        # PATH is always in os.environ but not in _ENV_VAR_KEYS
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("LEVERAGE", "8")

        cfg = BaseStrategyConfig.from_env()

        # PATH should never be set as a config field
        assert not hasattr(cfg, "PATH")
        assert cfg.LEVERAGE == 8


# =====================================================================
# Validation
# =====================================================================


class TestBaseValidation:
    """Test common validation rules from BaseStrategyConfig.validate()."""

    def test_validate_success_paper_trading(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True)
        assert cfg.validate() is True

    def test_validate_missing_private_key_live(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=False, PRIVATE_KEY="")
        with pytest.raises(ValueError, match="PRIVATE_KEY"):
            cfg.validate()

    def test_validate_private_key_with_live(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=False, PRIVATE_KEY="0x" + "a" * 64)
        assert cfg.validate() is True

    def test_validate_negative_risk(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, RISK_PER_TRADE_PCT=-0.1)
        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            cfg.validate()

    def test_validate_risk_too_high(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, RISK_PER_TRADE_PCT=1.5)
        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            cfg.validate()

    def test_validate_risk_at_zero(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, RISK_PER_TRADE_PCT=0)
        with pytest.raises(ValueError, match="RISK_PER_TRADE_PCT"):
            cfg.validate()

    def test_validate_invalid_leverage_low(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, LEVERAGE=0)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_validate_invalid_leverage_high(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, LEVERAGE=101)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_validate_leverage_boundary_1(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, LEVERAGE=1)
        assert cfg.validate() is True

    def test_validate_leverage_boundary_100(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, LEVERAGE=100)
        assert cfg.validate() is True

    def test_validate_position_size_pct_negative(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, POSITION_SIZE_PCT=-0.1)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_validate_position_size_pct_over_1(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, POSITION_SIZE_PCT=1.5)
        with pytest.raises(ValueError, match="POSITION_SIZE_PCT"):
            cfg.validate()

    def test_validate_max_positions_zero(self):
        cfg = BaseStrategyConfig(PAPER_TRADING=True, MAX_POSITIONS=0)
        with pytest.raises(ValueError, match="MAX_POSITIONS"):
            cfg.validate()


# =====================================================================
# BotConfig inheritance
# =====================================================================


class TestBotConfigInheritance:
    """Test BotConfig inherits from BaseStrategyConfig correctly."""

    def test_botconfig_is_subclass(self):
        assert issubclass(BotConfig, BaseStrategyConfig)

    def test_botconfig_has_base_fields(self):
        cfg = BotConfig()
        assert hasattr(cfg, "LEVERAGE")
        assert hasattr(cfg, "RISK_PER_TRADE_PCT")
        assert hasattr(cfg, "ASSET")
        assert hasattr(cfg, "PAPER_TRADING")

    def test_botconfig_has_own_fields(self):
        cfg = BotConfig()
        assert hasattr(cfg, "ROC_SHORT")
        assert hasattr(cfg, "ROC_LONG")
        assert hasattr(cfg, "MOMENTUM_THRESHOLD")
        assert hasattr(cfg, "TELEGRAM_ENABLED")
        assert hasattr(cfg, "WEB_UI_PORT")

    def test_botconfig_defaults(self):
        cfg = BotConfig()
        # BotConfig overrides PAPER_TRADING to False
        assert cfg.PAPER_TRADING is False
        assert cfg.LEVERAGE == 2
        assert cfg.ASSET == "HYPE"

    def test_botconfig_from_env(self, monkeypatch):
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

    def test_botconfig_from_env_ignores_random(self, monkeypatch):
        """BotConfig.from_env must not pick up random env vars."""
        monkeypatch.setenv("RANDOM_STUFF", "ignore_me")
        monkeypatch.setenv("LEVERAGE", "15")

        cfg = BotConfig.from_env()

        assert cfg.LEVERAGE == 15
        assert not hasattr(cfg, "RANDOM_STUFF")

    def test_botconfig_api_url_mainnet(self):
        cfg = BotConfig()
        cfg.USE_TESTNET = False
        assert "hyperliquid" in cfg.API_URL.lower()
        assert "testnet" not in cfg.API_URL.lower()

    def test_botconfig_api_url_testnet(self):
        cfg = BotConfig()
        cfg.USE_TESTNET = True
        assert "testnet" in cfg.API_URL.lower()

    def test_botconfig_validate_delegates_to_base(self):
        """BotConfig.validate() should run base class validations."""
        cfg = BotConfig()
        cfg.PAPER_TRADING = True
        cfg.LEVERAGE = 200  # Invalid
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()


# =====================================================================
# FundingArbConfig inheritance
# =====================================================================


class TestFundingArbConfigInheritance:
    """Test FundingArbConfig inherits from BaseStrategyConfig."""

    def test_is_subclass(self):
        assert issubclass(FundingArbConfig, BaseStrategyConfig)

    def test_has_base_fields(self):
        cfg = FundingArbConfig()
        assert hasattr(cfg, "LEVERAGE")
        assert hasattr(cfg, "RISK_PER_TRADE_PCT")
        assert hasattr(cfg, "PAPER_TRADING")
        assert hasattr(cfg, "TAKER_FEE_PCT")

    def test_has_own_fields(self):
        cfg = FundingArbConfig()
        assert hasattr(cfg, "ENTRY_THRESHOLD")
        assert hasattr(cfg, "EXIT_THRESHOLD")
        assert hasattr(cfg, "SPOT_HEDGE_ENABLED")
        assert hasattr(cfg, "MAX_CONCURRENT_POSITIONS")

    def test_defaults(self):
        cfg = FundingArbConfig()
        assert cfg.LEVERAGE == 3
        assert cfg.PAPER_TRADING is True
        assert cfg.ENTRY_THRESHOLD == 0.0003
        assert cfg.TAKER_FEE_PCT == 0.0005

    def test_post_init_spot_coins(self):
        cfg = FundingArbConfig()
        assert cfg.SPOT_ELIGIBLE_COINS is not None
        assert "BTC" in cfg.SPOT_ELIGIBLE_COINS

    def test_custom_spot_coins(self):
        cfg = FundingArbConfig(SPOT_ELIGIBLE_COINS=["DOGE"])
        assert cfg.SPOT_ELIGIBLE_COINS == ["DOGE"]

    def test_validate_inherits_base(self):
        """validate() should check base rules (leverage, risk, etc.)."""
        cfg = FundingArbConfig(LEVERAGE=0)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_validate_specific_entry_threshold(self):
        cfg = FundingArbConfig(ENTRY_THRESHOLD=-0.001)
        with pytest.raises(ValueError, match="ENTRY_THRESHOLD"):
            cfg.validate()

    def test_validate_specific_exit_threshold(self):
        cfg = FundingArbConfig(EXIT_THRESHOLD=-0.001)
        with pytest.raises(ValueError, match="EXIT_THRESHOLD"):
            cfg.validate()

    def test_validate_valid(self):
        cfg = FundingArbConfig()
        assert cfg.validate() is True

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("LEVERAGE", "5")
        monkeypatch.setenv("ENTRY_THRESHOLD", "0.001")

        cfg = FundingArbConfig.from_env()

        assert cfg.LEVERAGE == 5
        assert cfg.ENTRY_THRESHOLD == 0.001


# =====================================================================
# TrendFollowingConfig inheritance
# =====================================================================


class TestTrendFollowingConfigInheritance:
    """Test TrendFollowingConfig inherits from BaseStrategyConfig."""

    def test_is_subclass(self):
        assert issubclass(TrendFollowingConfig, BaseStrategyConfig)

    def test_has_base_fields(self):
        cfg = TrendFollowingConfig()
        assert hasattr(cfg, "LEVERAGE")
        assert hasattr(cfg, "RISK_PER_TRADE_PCT")
        assert hasattr(cfg, "PAPER_TRADING")
        assert hasattr(cfg, "TAKER_FEE_PCT")

    def test_has_own_fields(self):
        cfg = TrendFollowingConfig()
        assert hasattr(cfg, "FAST_EMA_PERIOD")
        assert hasattr(cfg, "SLOW_EMA_PERIOD")
        assert hasattr(cfg, "ADX_THRESHOLD")
        assert hasattr(cfg, "ATR_STOP_MULT")

    def test_defaults(self):
        cfg = TrendFollowingConfig()
        assert cfg.LEVERAGE == 3
        assert cfg.PAPER_TRADING is True
        assert cfg.FAST_EMA_PERIOD == 9
        assert cfg.SLOW_EMA_PERIOD == 21
        assert cfg.DATABASE_PATH == "trend_following.db"

    def test_validate_inherits_base(self):
        cfg = TrendFollowingConfig(LEVERAGE=0)
        with pytest.raises(ValueError, match="LEVERAGE"):
            cfg.validate()

    def test_validate_specific_ema(self):
        cfg = TrendFollowingConfig(FAST_EMA_PERIOD=30, SLOW_EMA_PERIOD=10)
        with pytest.raises(ValueError, match="FAST_EMA_PERIOD"):
            cfg.validate()

    def test_validate_valid(self):
        cfg = TrendFollowingConfig()
        assert cfg.validate() is True

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("LEVERAGE", "7")
        monkeypatch.setenv("FAST_EMA_PERIOD", "12")

        cfg = TrendFollowingConfig.from_env()

        assert cfg.LEVERAGE == 7
        assert cfg.FAST_EMA_PERIOD == 12


# =====================================================================
# Cross-config: all share the same base
# =====================================================================


class TestCommonBase:
    """Verify all config classes share common fields from BaseStrategyConfig."""

    @pytest.mark.parametrize(
        "config_cls",
        [BotConfig, FundingArbConfig, TrendFollowingConfig],
    )
    def test_inherits_base(self, config_cls):
        assert issubclass(config_cls, BaseStrategyConfig)

    @pytest.mark.parametrize(
        "config_cls",
        [BotConfig, FundingArbConfig, TrendFollowingConfig],
    )
    def test_has_validate(self, config_cls):
        cfg = config_cls()
        assert callable(getattr(cfg, "validate"))

    @pytest.mark.parametrize(
        "config_cls",
        [BotConfig, FundingArbConfig, TrendFollowingConfig],
    )
    def test_has_from_env(self, config_cls):
        assert callable(getattr(config_cls, "from_env"))

    @pytest.mark.parametrize(
        "config_cls,field_name",
        [
            (BotConfig, "LEVERAGE"),
            (FundingArbConfig, "LEVERAGE"),
            (TrendFollowingConfig, "LEVERAGE"),
            (BotConfig, "RISK_PER_TRADE_PCT"),
            (FundingArbConfig, "RISK_PER_TRADE_PCT"),
            (TrendFollowingConfig, "RISK_PER_TRADE_PCT"),
            (BotConfig, "TAKER_FEE_PCT"),
            (FundingArbConfig, "TAKER_FEE_PCT"),
            (TrendFollowingConfig, "TAKER_FEE_PCT"),
        ],
    )
    def test_common_field_exists(self, config_cls, field_name):
        cfg = config_cls()
        assert hasattr(cfg, field_name)
