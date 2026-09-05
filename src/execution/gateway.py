"""Execution-gateway contract and deterministic in-memory implementation.

The in-memory gateway is used to exercise lifecycle transitions without a
network connection. The Hyperliquid adapter keeps its SDK payloads at the
exchange boundary and exposes equivalent normalized requests/results.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..core.config import Side
from .models import (
    OrderRequest,
    OrderSubmission,
    PositionRead,
    SubmissionStatus,
)


@runtime_checkable
class TradingGateway(Protocol):
    """Minimal exchange contract required by the execution lifecycle."""

    async def submit_order(self, request: OrderRequest) -> OrderSubmission: ...

    async def get_positions(self) -> PositionRead: ...

    async def get_recent_fills(self, limit: int = 100) -> list[dict]: ...


@runtime_checkable
class MarketDataGateway(Protocol):
    """Read-only market-data contract for strategies.

    Strategies depend on this port instead of constructing exchange SDK
    clients themselves; only exchange adapters implement the SDK directly.
    """

    async def get_meta_and_asset_ctxs(self) -> tuple[dict, list[dict]]: ...

    async def get_candles(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> list[dict]: ...


@dataclass
class InMemoryGateway:
    """Scriptable exchange substitute for deterministic lifecycle tests.

    Tests set ``submission_status`` and use ``fill_order`` to control whether
    an accepted intent becomes exposure. No real account or order API is used.
    """

    submission_status: SubmissionStatus = SubmissionStatus.ACCEPTED
    positions: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    unavailable_error: str | None = None
    _next_order_id: int = 1

    async def submit_order(self, request: OrderRequest) -> OrderSubmission:
        order_id = self._next_order_id
        self._next_order_id += 1
        return OrderSubmission(
            status=self.submission_status,
            client_order_id=request.client_order_id,
            exchange_order_id=order_id
            if self.submission_status == SubmissionStatus.ACCEPTED
            else None,
            message=(
                "scripted unknown outcome"
                if self.submission_status == SubmissionStatus.UNKNOWN
                else None
            ),
        )

    async def get_positions(self) -> PositionRead:
        if self.unavailable_error:
            return PositionRead.unavailable(self.unavailable_error)
        return PositionRead.success(self.positions)

    async def get_recent_fills(self, limit: int = 100) -> list[dict]:
        return self.fills[-limit:]

    def set_positions(self, positions: Iterable[dict]) -> None:
        self.positions = list(positions)

    def add_fill(
        self,
        order_id: int,
        coin: str,
        side: Side,
        quantity: float,
        price: float,
        fee: float = 0.0,
    ) -> None:
        self.fills.append(
            {
                "oid": order_id,
                "coin": coin,
                "side": side.value,
                "sz": quantity,
                "px": price,
                "fee": fee,
                "time": datetime.utcnow().timestamp() * 1000,
            }
        )
