"""Typed execution-state models kept independent of exchange SDK payloads."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from ..core.config import Side


class ExecutionState(str, Enum):
    """Durable lifecycle of locally tracked exchange exposure."""

    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    EXIT_REQUESTED = "exit_requested"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED_CONFIRMED = "closed_confirmed"
    EXTERNALLY_CLOSED = "externally_closed"
    STATE_UNKNOWN = "state_unknown"
    RECOVERED_UNMANAGED = "recovered_unmanaged"


class SubmissionStatus(str, Enum):
    """Outcome of submitting an order, not the outcome of executing it."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OrderRequest:
    """A normalized, idempotent exchange order request."""

    coin: str
    side: Side
    quantity: float
    price: float
    client_order_id: Optional[str] = None
    reduce_only: bool = False
    order_type: str = "limit"


@dataclass(frozen=True)
class OrderSubmission:
    """Exchange acknowledgement for an order submission."""

    status: SubmissionStatus
    client_order_id: Optional[str]
    exchange_order_id: Optional[int] = None
    message: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExchangePosition:
    """Normalized exchange position observed in a successful snapshot."""

    coin: str
    side: Side
    quantity: float
    entry_price: float


@dataclass(frozen=True)
class Fill:
    """Normalized fill evidence used for authoritative trade accounting."""

    exchange_order_id: Optional[int]
    client_order_id: Optional[str]
    coin: str
    side: Side
    quantity: float
    price: float
    fee: float = 0.0
    fill_id: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class PositionRead(Sequence[dict[str, Any]]):
    """A position snapshot that cannot confuse an outage with a flat account.

    It implements ``Sequence`` for a short compatibility period with legacy
    callers that iterate or use ``len(await api.get_positions())``. Callers
    that make safety decisions must inspect ``available`` first.
    """

    available: bool
    positions: tuple[dict[str, Any], ...] = ()
    error: Optional[str] = None
    observed_at: datetime = field(default_factory=datetime.utcnow)

    def __len__(self) -> int:
        return len(self.positions)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.positions[index]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.positions)

    @classmethod
    def success(cls, positions: list[dict[str, Any]]) -> "PositionRead":
        return cls(available=True, positions=tuple(positions))

    @classmethod
    def unavailable(cls, error: str) -> "PositionRead":
        return cls(available=False, error=error)
