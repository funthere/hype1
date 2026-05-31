"""
Configuration and data models for the trading bot
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from hyperliquid.utils import constants

from .base_config import BaseStrategyConfig


class Side(Enum):
    """Trade side"""

    LONG = "LONG"
    SHORT = "SHORT"


class OrderStatus(Enum):
    """Order status"""

    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Position:
    """Open position tracking"""

    side: Side
    entry_price: float
    quantity: float
    tp_price: float
    sl_price: float
    entry_time: datetime
    leverage: int
    oid: Optional[int] = None
    cloid: Optional[str] = None
    status: OrderStatus = OrderStatus.OPEN
    unrealized_pnl: float = 0.0


@dataclass
class Trade:
    """Completed trade tracking"""

    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    fees: float = 0.0
    notes: str = ""  # Optional notes for trade journal


@dataclass
class BotConfig(BaseStrategyConfig):
    """Bot configuration - supports both environment variables and direct assignment.

    Inherits common fields from BaseStrategyConfig and adds bot-specific
    settings such as API URL switching, strategy parameters, and notifications.
    """

    # Override defaults for bot-specific values
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

    # Asset index
    ASSET_INDEX: int = 0

    # Strategy Parameters
    ROC_SHORT: int = 1
    ROC_LONG: int = 5
    MOMENTUM_THRESHOLD: float = 0.08
    CONFIDENCE_THRESHOLD: int = 45
    EMA_TREND_FILTER: int = 20

    # Risk Management (additional)
    TP_ATR_MULTIPLIER: float = 2.0
    SL_ATR_MULTIPLIER: float = 0.4

    # Order Settings
    ORDER_TYPE: str = "limit"
    MIN_ORDER_SIZE: float = 10

    # Circuit Breaker
    CIRCUIT_BREAKER_ENABLED: bool = True
    MAX_CONSECUTIVE_LOSSES: int = 3
    CIRCUIT_BREAKER_COOLDOWN_MINUTES: int = 30

    # Web UI / API
    WEB_UI_ENABLED: bool = True
    WEB_UI_HOST: str = "127.0.0.1"
    WEB_UI_PORT: int = 8000

    # Notifications
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Explicit env vars for BotConfig (extends base set)
    _ENV_VAR_KEYS = (
        # Base keys
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
        "MAX_DAILY_LOSS_PCT",
        "MAX_LOSS_PCT",
        "EMERGENCY_SHUTDOWN",
        "MAKER_FEE_PCT",
        "TAKER_FEE_PCT",
        "DATABASE_PATH",
        # Bot-specific keys
        "ASSET_INDEX",
        "ROC_SHORT",
        "ROC_LONG",
        "MOMENTUM_THRESHOLD",
        "CONFIDENCE_THRESHOLD",
        "EMA_TREND_FILTER",
        "TP_ATR_MULTIPLIER",
        "SL_ATR_MULTIPLIER",
        "ORDER_TYPE",
        "MIN_ORDER_SIZE",
        "CIRCUIT_BREAKER_ENABLED",
        "MAX_CONSECUTIVE_LOSSES",
        "CIRCUIT_BREAKER_COOLDOWN_MINUTES",
        "WEB_UI_ENABLED",
        "WEB_UI_HOST",
        "WEB_UI_PORT",
        "TELEGRAM_ENABLED",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    )

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Create config from explicitly-named environment variables.

        Overrides the base ``from_env()`` to return a ``BotConfig`` instance
        and uses ``BotConfig._ENV_VAR_KEYS`` for the allow-list.
        """
        return BaseStrategyConfig.from_env.__func__(cls)

    def validate(self) -> bool:
        """Validate configuration"""
        # Run base validations first
        super().validate()
        return True
