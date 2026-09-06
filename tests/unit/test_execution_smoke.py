"""
Unit tests for the bounded testnet smoke check.

Every step runs against a scripted gateway double; CI never touches a
network or a real account.
"""

from unittest.mock import AsyncMock

import pytest

from src.core.config import Side
from src.execution import (
    OrderRequest,
    SubmissionStatus,
    TestnetSmokeCheck,
    summarize,
)
from src.execution.models import OrderSubmission, PositionRead


class ScriptedExchange:
    """Scripted stand-in for the HyperliquidAPI surface the smoke uses."""

    drop_fills = False

    def __init__(self, mid="30.0", sz_decimals=2):
        self.mid = mid
        self.sz_decimals = sz_decimals
        self.positions: list = []
        self.open_orders: list = []
        self.fills: list = []
        self.next_oid = 100
        self.submission_status = SubmissionStatus.ACCEPTED
        self.submission_message: str | None = None
        self.cancel_result = {"status": "ok"}
        self.positions_available = True
        self.positions_error = "rpc down"

    # -- gateway surface ------------------------------------------------

    async def get_positions(self):
        if not self.positions_available:
            return PositionRead.unavailable(self.positions_error)
        return PositionRead.success(self.positions)

    async def get_mids(self):
        return {self._coin: self.mid}

    async def round_size(self, coin, quantity):
        factor = 10**self.sz_decimals
        import math

        return math.floor(quantity * factor) / factor

    async def submit_order(self, request: OrderRequest) -> OrderSubmission:
        oid = self.next_oid
        status = self.submission_status
        self.next_oid += 1
        if status == SubmissionStatus.ACCEPTED:
            self.open_orders.append({"oid": oid, "coin": request.coin})
            self._absorb(request, oid)
        return OrderSubmission(
            status=status,
            client_order_id=request.client_order_id,
            exchange_order_id=oid if status == SubmissionStatus.ACCEPTED else None,
            message=self.submission_message,
        )

    async def get_open_orders(self):
        return list(self.open_orders)

    async def cancel_order(self, oid):
        return self.cancel_result

    async def get_recent_fills(self, limit=200):
        return self.fills[-limit:]

    # -- scripting helpers ----------------------------------------------

    _coin = "PURR"

    def _absorb(self, request: OrderRequest, oid: int):
        """Simulate exchange behaviour per order kind."""
        if request.order_type == "ioc":
            # taker: fills immediately, no resting order
            self.open_orders = [o for o in self.open_orders if o["oid"] != oid]
            if not self.drop_fills:
                self.fills.append(
                    {
                        "oid": oid,
                        "coin": request.coin,
                        "side": "B" if request.side in (Side.LONG, "LONG") else "S",
                        "sz": request.quantity,
                        "px": request.price,
                    }
                )
            if request.reduce_only:
                self.positions = []
            else:
                self.positions = [{"coin": request.coin, "szi": request.quantity}]
        # resting GTC stays in open_orders until cancelled

    async def cancel(self, oid):  # pragma: no cover - unused alias
        raise NotImplementedError


@pytest.fixture
def exchange():
    return ScriptedExchange()


@pytest.fixture
def smoke(exchange):
    checker = TestnetSmokeCheck(exchange, coin="PURR", notional=11.0)

    # Route cancel through the scripted book so the visibility assertions
    # observe the removal.
    async def cancel_order(oid):
        exchange.open_orders = [o for o in exchange.open_orders if o["oid"] != oid]
        return exchange.cancel_result

    checker.gateway.cancel_order = AsyncMock(side_effect=cancel_order)
    return checker


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_all_steps_pass(self, smoke):
        steps = await smoke.run()

        names = [s.name for s in steps]
        assert names == [
            "positions_available",
            "mid_price",
            "size_rounding",
            "resting_order_accepted",
            "resting_order_visible",
            "resting_order_cancelled",
            "taker_filled",
            "flattened",
        ]
        assert summarize(steps) is True

    @pytest.mark.asyncio
    async def test_size_is_floored_to_precision(self, smoke):
        steps = await smoke.run()
        size_step = next(s for s in steps if s.name == "size_rounding")
        assert size_step.detail == "size 0.36"  # 11/30 floored to 2 dp

    @pytest.mark.asyncio
    async def test_coarse_precision_rejects_too_small_notional(self, exchange):
        exchange.sz_decimals = 0  # 11/30 = 0.36 floors to 0 -> smoke fails
        checker = TestnetSmokeCheck(exchange, coin="PURR", notional=11.0)
        steps = await checker.run()
        size_step = next(s for s in steps if s.name == "size_rounding")
        assert size_step.passed is False
        assert "rounds to zero" in size_step.detail


class TestFailurePaths:
    @pytest.mark.asyncio
    async def test_position_outage_fails_fast(self, smoke):
        smoke.gateway.positions_available = False
        steps = await smoke.run()
        assert len(steps) == 1
        assert steps[0].name == "positions_available"
        assert summarize(steps) is False

    @pytest.mark.asyncio
    async def test_unknown_submission_aborts_without_retry(self, smoke, exchange):
        exchange.submission_status = SubmissionStatus.UNKNOWN
        exchange.submission_message = "scripted unknown"
        with pytest.raises(Exception, match="UNKNOWN"):
            await smoke.run()

    @pytest.mark.asyncio
    async def test_rejected_resting_order_fails_step(self, smoke, exchange):
        exchange.submission_status = SubmissionStatus.REJECTED
        exchange.submission_message = "rejected by testnet"
        steps = await smoke.run()
        assert any(s.name == "resting_order_accepted" and not s.passed for s in steps)

    @pytest.mark.asyncio
    async def test_cancel_failure_fails_step(self, smoke, exchange):
        exchange.cancel_result = {"status": "error", "msg": "cancel denied"}
        steps = await smoke.run()
        assert any(s.name == "resting_order_cancelled" and not s.passed for s in steps)

    @pytest.mark.asyncio
    async def test_missing_fill_fails_step(self, smoke, exchange):
        exchange.drop_fills = True  # taker "fills" but reconciliation finds nothing
        steps = await smoke.run()
        assert any(s.name == "taker_filled" and not s.passed for s in steps)

    @pytest.mark.asyncio
    async def test_open_position_after_taker_fails_flatten(self, smoke, exchange):
        # Suppress the reduce-only simulation: leave the position standing
        def absorb_without_netting(request, oid):
            if request.order_type == "ioc":
                exchange.open_orders = [
                    o for o in exchange.open_orders if o["oid"] != oid
                ]
                exchange.fills.append(
                    {
                        "oid": oid,
                        "coin": request.coin,
                        "sz": request.quantity,
                        "px": request.price,
                    }
                )
                if not request.reduce_only:
                    exchange.positions = [
                        {"coin": request.coin, "szi": request.quantity}
                    ]
                # reduce_only does nothing -> position remains open

        exchange._absorb = absorb_without_netting
        steps = await smoke.run()
        assert any(s.name == "flattened" and not s.passed for s in steps)

    @pytest.mark.asyncio
    async def test_zero_notional_rejected(self):
        with pytest.raises(ValueError):
            TestnetSmokeCheck(ScriptedExchange(), coin="PURR", notional=0)
