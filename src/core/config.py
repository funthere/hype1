"""
Configuration and data models for the trading bot

Domain models (Side, OrderStatus, Position, Trade) are now defined in
src.core.models and re-exported here for backward compatibility.
"""

from dataclasses import dataclass
from typing import Optional

from hyperliquid.utils import constants

# Re-export unified domain models for backward compatibility
from src.core.models import Side, OrderStatus, Position, Trade  # noqa: F401


@dataclass
class BotConfig:
    """Bot configuration - supports both environment variables and direct assignment"""

    # Environment
    USE_TESTNET: bool = False
    PAPER_TRADING: bool = False

    # API URLs (auto-switches based on USE_TESTNET)
    @property
    def API_URL(self) -> str:
        if self.USE_TESTNET:
            return constants.TESTNET_API_URL
        return constants.MAINNET_API_URL

    @property
    def INFO_URL(self) -> str:
        if self.USE_TESTNET:
            return constants.TESTNET_API_URL
        return constants.MAINNET_API_URL

    @property
    def WS_URL(self) -> str:
        if self.USE_TESTNET:
            return "wss://api.hyperliquid-testnet.xyz/ws"
        return "wss://api.hyperliquid.xyz/ws"

    # Account
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # Paper Trading
    PAPER_CAPITAL: float = 10000

    # Trading
    ASSET: str = "HYPE"
    ASSET_INDEX: int = 0
    TIMEFRAME: str = "15m"
    LEVERAGE: int = 5

    # Strategy Parameters
    ROC_SHORT: int = 1
    ROC_LONG: int = 5
    MOMENTUM_THRESHOLD: float = 0.08
    CONFIDENCE_THRESHOLD: int = 45
    EMA_TREND_FILTER: int = 20

    # Risk Management
    RISK_PER_TRADE_PCT: float = 0.08
    TP_ATR_MULTIPLIER: float = 2.0
    SL_ATR_MULTIPLIER: float = 0.4
    MAX_POSITIONS: int = 2
    MAX_DAILY_TRADES: int = 20

    # Order Settings
    ORDER_TYPE: str = "limit"
    MIN_ORDER_SIZE: float = 10

    # Safety
    MAX_DAILY_LOSS_PCT: float = 0.15
    EMERGENCY_SHUTDOWN: bool = False

    # Circuit Breaker
    CIRCUIT_BREAKER_ENABLED: bool = True
    MAX_CONSECUTIVE_LOSSES: int = 3
    CIRCUIT_BREAKER_COOLDOWN_MINUTES: int = 30

    # Fees
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0004

    # Web UI / API
    WEB_UI_ENABLED: bool = True
    WEB_UI_HOST: str = "127.0.0.1"
    WEB_UI_PORT: int = 8000

    # Notifications
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Storage
    DATABASE_PATH: str = "trading_bot.db"

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Create config from environment variables"""
        from dotenv import load_dotenv
        import os

        load_dotenv()

        config = cls()

        # Apply environment variables
        for key, value in os.environ.items():
            if hasattr(config, key):
                attr_type = type(getattr(config, key))
                if attr_type is bool:
                    setattr(config, key, value.lower() in ("true", "1", "yes"))
                elif attr_type is int:
                    setattr(config, key, int(value))
                elif attr_type is float:
                    setattr(config, key, float(value))
                else:
                    setattr(config, key, str(value))

        return config

    def validate(self) -> bool:
        """Validate configuration"""
        if not self.PRIVATE_KEY and not self.PAPER_TRADING:
            raise ValueError("PRIVATE_KEY required for live trading")

        if self.RISK_PER_TRADE_PCT <= 0 or self.RISK_PER_TRADE_PCT > 1:
            raise ValueError("RISK_PER_TRADE_PCT must be between 0 and 1")

        if self.LEVERAGE < 1 or self.LEVERAGE > 100:
            raise ValueError("LEVERAGE must be between 1 and 100")

        return True
