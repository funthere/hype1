"""
Bounded testnet execution smoke check.

Exercises the live order lifecycle end to end — resting order + cancel,
taker fill + reconciliation, flatten + flat verification — against a
testnet account. This is the runtime verification paper trading
structurally cannot provide: it exists because paper mode bypasses the
order API and cannot catch live-path failures (an order-direction bug
that paper results were silent about was found by code reading).

The check is deliberately bounded:
  - one resting order priced far from mid (never fills), then cancelled
  - one minimal-notional taker order, then flattened
  - an UNKNOWN submission aborts immediately without retrying — the
    caller must reconcile manually per the incident runbook

The check targets the concrete ``HyperliquidAPI`` surface; tests drive it
with a scripted gateway so CI never touches a network.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from ..core.config import Side
from .models import OrderRequest, SubmissionStatus

logger = logging.getLogger(__name__)


@dataclass
class SmokeStep:
    """Result of one lifecycle step."""

    name: str
    passed: bool
    detail: str

    @classmethod
    def ok(cls, name: str, detail: str = "") -> "SmokeStep":
        return cls(name=name, passed=True, detail=detail)

    @classmethod
    def fail(cls, name: str, detail: str) -> "SmokeStep":
        return cls(name=name, passed=False, detail=detail)


class SmokeAborted(Exception):
    """An order outcome was UNKNOWN — stop; manual reconciliation required."""


class TestnetSmokeCheck:
    """Run the bounded lifecycle smoke against an exchange adapter.

    The gateway argument is duck-typed on the ``HyperliquidAPI`` surface
    used below (positions, mids, submit, open orders, cancel, fills, size
    rounding). Tests inject a scripted double; the CLI wires the real
    connector in testnet mode.
    """

    def __init__(
        self,
        gateway,
        coin: str,
        notional: float = 11.0,
        fill_search_limit: int = 200,
    ):
        if notional <= 0:
            raise ValueError("notional must be positive")
        self.gateway = gateway
        self.coin = coin
        self.notional = notional
        self.fill_search_limit = fill_search_limit

    async def run(self) -> List[SmokeStep]:
        steps: List[SmokeStep] = []
        mid = await self._check_positions_and_mid(steps)
        if mid is None:
            return steps

        size = await self._check_size(steps, mid)
        if size is None:
            return steps

        await self._check_resting_order_lifecycle(steps, mid, size)
        await self._check_taker_fill(steps, mid, size)
        await self._check_flattened(steps)
        return steps

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    async def _check_positions_and_mid(self, steps) -> Optional[float]:
        read = await self.gateway.get_positions()
        if not read.available:
            steps.append(
                SmokeStep.fail(
                    "positions_available",
                    f"position snapshot unavailable: {read.error}",
                )
            )
            return None
        steps.append(
            SmokeStep.ok(
                "positions_available", f"{len(read)} open position(s) observed"
            )
        )

        mids = await self.gateway.get_mids()
        mid = float(mids.get(self.coin, 0) or 0)
        if mid <= 0:
            steps.append(SmokeStep.fail("mid_price", f"no mid for {self.coin}"))
            return None
        steps.append(SmokeStep.ok("mid_price", f"{self.coin} mid {mid}"))
        return mid

    async def _check_size(self, steps, mid: float) -> Optional[float]:
        raw_size = self.notional / mid
        size = await self.gateway.round_size(self.coin, raw_size)
        if size <= 0:
            steps.append(
                SmokeStep.fail(
                    "size_rounding",
                    f"notional {self.notional} rounds to zero size at mid {mid}",
                )
            )
            return None
        steps.append(SmokeStep.ok("size_rounding", f"size {size}"))
        return size

    async def _check_resting_order_lifecycle(
        self, steps, mid: float, size: float
    ) -> None:
        resting_price = round(mid * 0.5, 6)  # far below mid: must never fill
        request = OrderRequest(
            coin=self.coin,
            side=Side.LONG,
            quantity=size,
            price=resting_price,
            client_order_id="smoke-resting",
            order_type="limit",
        )
        submission = await self.gateway.submit_order(request)
        if submission.status == SubmissionStatus.UNKNOWN:
            raise SmokeAborted(f"resting order outcome UNKNOWN: {submission.message}")
        if submission.status != SubmissionStatus.ACCEPTED:
            steps.append(
                SmokeStep.fail(
                    "resting_order_accepted", submission.message or "rejected"
                )
            )
            return
        oid = submission.exchange_order_id
        if oid is None:
            steps.append(
                SmokeStep.fail("resting_order_accepted", "accepted without an order id")
            )
            return
        steps.append(
            SmokeStep.ok("resting_order_accepted", f"oid {oid} @ {resting_price}")
        )

        open_orders = await self.gateway.get_open_orders()
        if not any(o.get("oid") == oid for o in open_orders):
            steps.append(
                SmokeStep.fail("resting_order_visible", f"oid {oid} not in open orders")
            )
            return
        steps.append(SmokeStep.ok("resting_order_visible", f"oid {oid} listed"))

        cancel = await self.gateway.cancel_order(oid)
        if cancel.get("status") != "ok":
            steps.append(
                SmokeStep.fail(
                    "resting_order_cancelled", cancel.get("msg", "cancel failed")
                )
            )
            return
        open_orders = await self.gateway.get_open_orders()
        if any(o.get("oid") == oid for o in open_orders):
            steps.append(
                SmokeStep.fail(
                    "resting_order_cancelled", f"oid {oid} still listed after cancel"
                )
            )
            return
        steps.append(SmokeStep.ok("resting_order_cancelled", f"oid {oid} gone"))

    async def _check_taker_fill(self, steps, mid: float, size: float) -> None:
        request = OrderRequest(
            coin=self.coin,
            side=Side.LONG,
            quantity=size,
            price=round(mid * 1.02, 6),  # crosses: taker
            client_order_id="smoke-taker",
            order_type="ioc",
        )
        submission = await self.gateway.submit_order(request)
        if submission.status == SubmissionStatus.UNKNOWN:
            raise SmokeAborted(f"taker order outcome UNKNOWN: {submission.message}")
        if submission.status != SubmissionStatus.ACCEPTED:
            steps.append(
                SmokeStep.fail("taker_filled", submission.message or "rejected")
            )
            return
        if submission.exchange_order_id is None:
            steps.append(SmokeStep.fail("taker_filled", "accepted without an order id"))
            return

        fills = await self.gateway.get_recent_fills(self.fill_search_limit)
        matched = [
            f
            for f in fills
            if f.get("oid") == submission.exchange_order_id
            and float(f.get("sz", 0) or 0) > 0
        ]
        if not matched:
            steps.append(
                SmokeStep.fail(
                    "taker_filled",
                    f"no fill found for oid {submission.exchange_order_id}",
                )
            )
            return
        steps.append(
            SmokeStep.ok(
                "taker_filled",
                f"oid {submission.exchange_order_id} filled {matched[0].get('sz')}"
                f" @ {matched[0].get('px')}",
            )
        )

    async def _check_flattened(self, steps) -> None:
        read = await self.gateway.get_positions()
        if not read.available:
            steps.append(
                SmokeStep.fail(
                    "flattened", f"post-trade snapshot unavailable: {read.error}"
                )
            )
            return

        position = next(
            (
                p
                for p in read.positions
                if p.get("coin") == self.coin and abs(float(p.get("szi", 0) or 0)) > 0
            ),
            None,
        )
        if position is None:
            steps.append(SmokeStep.ok("flattened", f"no open {self.coin} position"))
            return

        # Flatten with a reduce-only IOC in the opposite direction.
        held = float(position.get("szi", 0) or 0)
        flatten_side = Side.SHORT if held > 0 else Side.LONG
        mids = await self.gateway.get_mids()
        mid = float(mids.get(self.coin, 0) or 0)
        if mid <= 0:
            steps.append(
                SmokeStep.fail("flattened", "no mid to price the flatten order")
            )
            return
        # Cross the book: sell below mid to close a long, buy above to
        # close a short.
        flatten_price = round(mid * (0.98 if held > 0 else 1.02), 6)
        close_request = OrderRequest(
            coin=self.coin,
            side=flatten_side,
            quantity=abs(held),
            price=flatten_price,
            client_order_id="smoke-flatten",
            order_type="ioc",
            reduce_only=True,
        )
        submission = await self.gateway.submit_order(close_request)
        if submission.status == SubmissionStatus.UNKNOWN:
            raise SmokeAborted(f"flatten order outcome UNKNOWN: {submission.message}")
        if submission.status != SubmissionStatus.ACCEPTED:
            steps.append(
                SmokeStep.fail("flattened", submission.message or "flatten rejected")
            )
            return

        read = await self.gateway.get_positions()
        still_open = read.available and any(
            p.get("coin") == self.coin and abs(float(p.get("szi", 0) or 0)) > 0
            for p in read.positions
        )
        if still_open:
            steps.append(
                SmokeStep.fail(
                    "flattened",
                    f"{self.coin} position still open after flatten order",
                )
            )
            return
        steps.append(
            SmokeStep.ok("flattened", f"closed {held} {self.coin} via reduce-only IOC")
        )


def summarize(steps: List[SmokeStep]) -> bool:
    """True when every step passed."""
    return all(step.passed for step in steps)
