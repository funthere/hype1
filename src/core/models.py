"""
Unified domain models for the HyperLiquid trading bot.

All shared enums and dataclasses (Side, OrderStatus, PositionStatus,
Position, Trade) are defined here to avoid duplication across modules.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Side(Enum):
    """Trade / position direction."""

    LONG = "LONG"
    SHORT = "SHORT"


class OrderStatus(Enum):
    """Order lifecycle status (used by the core trading bot)."""

    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(Enum):
    """Position lifecycle status (used by strategy modules)."""

    OPEN = "open"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """Open position tracking (core trading bot)."""

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
    """Completed trade tracking."""

    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    fees: float = 0.0
    notes: str = ""  # Optional notes for trade journal
