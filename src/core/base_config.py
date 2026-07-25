"""
Base configuration for all trading strategies.

Provides common fields and validation logic shared across strategy configs:
  - BotConfig (main bot)
  - FundingArbConfig (funding rate arbitrage)
  - TrendFollowingConfig (trend following)
  - SurvivalBotConfig (survival-focused)
"""

import logging
import os
from dataclasses import dataclass, fields
from typing import ClassVar, Optional, Tuple

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class BaseStrategyConfig:
    """Base configuration dataclass shared by all strategy configs.

    Provides:
      - Common trading fields (leverage, risk, positions, fees, etc.)
      - Explicit ``from_env()`` classmethod that reads only known env vars
      - ``validate()`` with common validation rules

    Subclasses should add strategy-specific fields and extend ``validate()``.
    """

    # ------------------------------------------------------------------ #
    # Environment / mode
    # ------------------------------------------------------------------ #
    USE_TESTNET: bool = False
    PAPER_TRADING: bool = True

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Capital (paper mode)
    # ------------------------------------------------------------------ #
    PAPER_CAPITAL: float = 10_000.0

    # ------------------------------------------------------------------ #
    # Trading parameters
    # ------------------------------------------------------------------ #
    ASSET: str = "HYPE"
    TIMEFRAME: str = "15m"
    LEVERAGE: int = 2

    # ------------------------------------------------------------------ #
    # Risk management
    # ------------------------------------------------------------------ #
    RISK_PER_TRADE_PCT: float = 0.005
    POSITION_SIZE_PCT: float = 0.10
    MAX_POSITIONS: int = 1
    MAX_DAILY_TRADES: int = 5
    MAX_POSITION_NOTIONAL_PCT: float = 0.20
    MAX_POSITION_NOTIONAL_USD: float = 2_000.0

    # ------------------------------------------------------------------ #
    # Limits
    # ------------------------------------------------------------------ #
    MAX_DAILY_LOSS_PCT: float = 0.02
    MAX_LOSS_PCT: float = 0.05
    EMERGENCY_SHUTDOWN: bool = False

    # ------------------------------------------------------------------ #
    # Fees
    # ------------------------------------------------------------------ #
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0005

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    DATABASE_PATH: str = "trading_bot.db"

    # ------------------------------------------------------------------ #
    # Explicit list of env-var names that are safe to read.
    # Subclasses can override to add their own keys.
    # ------------------------------------------------------------------ #
    _ENV_VAR_KEYS: ClassVar[Tuple[str, ...]] = (
        "USE_TESTNET",
        "PAPER_TRADING",
        "PRIVATE_KEY",
        "ADDRESS",
        "ACCOUNT_ADDRESS",
        "PAPER_CAPITAL",
        "ASSET",
        "TIMEFRAME",
        "LEVERAGE",
        "RISK_PER_TRADE_PCT",
        "POSITION_SIZE_PCT",
        "MAX_POSITIONS",
        "MAX_DAILY_TRADES",
        "MAX_POSITION_NOTIONAL_PCT",
        "MAX_POSITION_NOTIONAL_USD",
        "MAX_DAILY_LOSS_PCT",
        "MAX_LOSS_PCT",
        "EMERGENCY_SHUTDOWN",
        "MAKER_FEE_PCT",
        "TAKER_FEE_PCT",
        "DATABASE_PATH",
    )

    # ------------------------------------------------------------------ #
    # Helpers for type coercion
    # ------------------------------------------------------------------ #

    @classmethod
    def _coerce_value(cls, value: str, target_type: type):
        """Convert a string env-var value to *target_type*."""
        if target_type is bool:
            return value.lower() in ("true", "1", "yes")
        if target_type is int:
            return int(value)
        if target_type is float:
            return float(value)
        return str(value)

    # ------------------------------------------------------------------ #
    # from_env
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls) -> "BaseStrategyConfig":
        """Create a config instance from explicitly-named environment variables.

        Unlike the old ``os.environ.items()`` approach, this method iterates
        over a fixed allow-list (``_ENV_VAR_KEYS``) and only sets attributes
        that actually exist on the dataclass.  Unknown env vars are ignored.
        """
        load_dotenv()

        config = cls()

        # Build a set of valid field names for this (sub)class
        valid_fields = {f.name for f in fields(config) if not f.name.startswith("_")}

        # Get the _ENV_VAR_KEYS from the class (allows subclasses to extend)
        env_keys = getattr(cls, "_ENV_VAR_KEYS", ())

        for key in env_keys:
            value = os.environ.get(key)
            if value is None:
                continue
            if key not in valid_fields:
                continue

            current = getattr(config, key, None)
            attr_type = type(current) if current is not None else str
            try:
                coerced = cls._coerce_value(value, attr_type)
                setattr(config, key, coerced)
            except (ValueError, TypeError):
                logger.warning(
                    "Could not parse env var %s=%r, keeping default", key, value
                )

        return config

    # ------------------------------------------------------------------ #
    # validate
    # ------------------------------------------------------------------ #

    def validate(self) -> bool:
        """Validate common configuration values.

        Subclasses should call ``super().validate()`` and then add their
        own checks.

        Raises:
            ValueError: If any value is out of bounds.
        """
        if not self.PRIVATE_KEY and not self.PAPER_TRADING:
            raise ValueError("PRIVATE_KEY required for live trading")

        if self.RISK_PER_TRADE_PCT <= 0 or self.RISK_PER_TRADE_PCT > 1:
            raise ValueError("RISK_PER_TRADE_PCT must be between 0 and 1")

        if self.POSITION_SIZE_PCT <= 0 or self.POSITION_SIZE_PCT > 1:
            raise ValueError("POSITION_SIZE_PCT must be in (0, 1]")

        if self.LEVERAGE < 1 or self.LEVERAGE > 100:
            raise ValueError("LEVERAGE must be between 1 and 100")

        if self.MAX_POSITIONS < 1:
            raise ValueError("MAX_POSITIONS must be >= 1")

        if self.MAX_DAILY_LOSS_PCT <= 0 or self.MAX_DAILY_LOSS_PCT > 1:
            raise ValueError("MAX_DAILY_LOSS_PCT must be between 0 and 1")

        if not 0 < self.MAX_POSITION_NOTIONAL_PCT <= 1:
            raise ValueError("MAX_POSITION_NOTIONAL_PCT must be in (0, 1]")
        if self.MAX_POSITION_NOTIONAL_USD <= 0:
            raise ValueError("MAX_POSITION_NOTIONAL_USD must be positive")

        if not self.PAPER_TRADING and not self.USE_TESTNET:
            self._validate_mainnet_policy()

        return True

    def _validate_mainnet_policy(self) -> None:
        """Enforce hard mainnet ceilings after all overrides are applied."""
        if self.RISK_PER_TRADE_PCT > 0.01:
            raise ValueError("Mainnet RISK_PER_TRADE_PCT may not exceed 1%")
        if self.LEVERAGE > 2:
            raise ValueError("Mainnet LEVERAGE may not exceed 2x")
        if self.MAX_DAILY_LOSS_PCT > 0.03:
            raise ValueError("Mainnet MAX_DAILY_LOSS_PCT may not exceed 3%")
        if self.MAX_POSITIONS > 1:
            raise ValueError("Mainnet MAX_POSITIONS is limited to 1")
        if self.MAX_DAILY_TRADES > 5:
            raise ValueError("Mainnet MAX_DAILY_TRADES is limited to 5")
        if not 0 < self.MAX_POSITION_NOTIONAL_PCT <= 0.20:
            raise ValueError("Mainnet MAX_POSITION_NOTIONAL_PCT must be in (0, 20%]")
        if self.MAX_POSITION_NOTIONAL_USD <= 0:
            raise ValueError("Mainnet MAX_POSITION_NOTIONAL_USD must be positive")
