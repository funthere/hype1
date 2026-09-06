#!/usr/bin/env python3
"""Bounded testnet execution smoke check.

Modes:
  self-test  Run the lifecycle check against a scripted gateway (no
             network, no credentials). Used by CI to prove the check
             itself works.
  live       Run against Hyperliquid TESTNET with real (testnet) funds.
             Requires USE_TESTNET=true and PRIVATE_KEY/ADDRESS in the
             environment plus an explicit --i-understand flag.

Exit codes: 0 = all steps passed, 1 = one or more steps failed,
2 = an order outcome was UNKNOWN (manual reconciliation required — do
not re-run until the account is reconciled; see docs/INCIDENT_RESPONSE.md).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import BotConfig
from src.exchange.connector import HyperliquidAPI
from src.execution import (
    OrderRequest,
    SmokeAborted,
    SubmissionStatus,
    TestnetSmokeCheck,
    summarize,
)
from src.execution.models import OrderSubmission


class ScriptedGateway:
    """Deterministic gateway for --mode self-test (mirrors the unit-test
    double; kept here so the CI self-test needs no test imports)."""

    coin = "PURR"
    mid = "30.0"

    def __init__(self):
        self.sz_decimals = 2
        self.open_orders = []
        self.fills = []
        self.positions = []
        self.next_oid = 100

    async def get_positions(self):
        from src.execution import PositionRead

        return PositionRead.success(self.positions)

    async def get_mids(self):
        return {self.coin: self.mid}

    async def round_size(self, coin, quantity):
        import math

        factor = 10**self.sz_decimals
        return math.floor(quantity * factor) / factor

    async def submit_order(self, request: OrderRequest) -> OrderSubmission:

        oid = self.next_oid
        self.next_oid += 1
        if request.order_type == "ioc":
            self.fills.append({"oid": oid, "sz": request.quantity, "px": request.price})
            if request.reduce_only:
                self.positions = []
            else:
                self.positions = [{"coin": request.coin, "szi": request.quantity}]
        else:
            self.open_orders.append({"oid": oid, "coin": request.coin})
        return OrderSubmission(
            status=SubmissionStatus.ACCEPTED,
            client_order_id=request.client_order_id,
            exchange_order_id=oid,
        )

    async def get_open_orders(self):
        return list(self.open_orders)

    async def cancel_order(self, oid):
        self.open_orders = [o for o in self.open_orders if o["oid"] != oid]
        return {"status": "ok"}

    async def get_recent_fills(self, limit=200):
        return self.fills[-limit:]


def print_steps(steps) -> None:
    for step in steps:
        marker = "PASS" if step.passed else "FAIL"
        print(f"  [{marker}] {step.name}: {step.detail}")


def build_live_gateway() -> HyperliquidAPI:
    load_env()
    if os.environ.get("USE_TESTNET", "").lower() not in ("true", "1", "yes"):
        raise SystemExit(
            "Refusing to run the live smoke: USE_TESTNET must be true. "
            "This check places real (testnet) orders."
        )
    private_key = os.environ.get("PRIVATE_KEY", "")
    address = os.environ.get("ADDRESS", "")
    if not private_key or not address:
        raise SystemExit(
            "Refusing to run the live smoke: PRIVATE_KEY and ADDRESS must "
            "be set to a dedicated testnet account."
        )
    config = BotConfig(
        USE_TESTNET=True,
        PAPER_TRADING=False,
        PRIVATE_KEY=private_key,
        ADDRESS=address,
    )
    config.validate()
    return HyperliquidAPI(config)


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded testnet smoke check")
    parser.add_argument(
        "--mode",
        choices=["self-test", "live"],
        default="self-test",
        help="self-test: scripted gateway, no network (CI default). "
        "live: real testnet orders with a dedicated testnet account.",
    )
    parser.add_argument("--coin", default="PURR", help="Testnet coin to exercise")
    parser.add_argument(
        "--notional",
        type=float,
        default=11.0,
        help="Approximate taker order notional in USD (default: 11)",
    )
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help="Required with --mode live; confirms you accept testnet order placement",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()

    if args.mode == "self-test":
        gateway = ScriptedGateway()
    else:
        if not args.i_understand:
            raise SystemExit("Refusing to run the live smoke without --i-understand.")
        gateway = build_live_gateway()

    check = TestnetSmokeCheck(gateway, coin=args.coin, notional=args.notional)
    try:
        steps = await check.run()
    except SmokeAborted as exc:
        print(f"ABORTED — order outcome UNKNOWN: {exc}")
        print(
            "Do not re-run. Reconcile the account against exchange state "
            "first (docs/INCIDENT_RESPONSE.md)."
        )
        return 2

    print_steps(steps)
    if summarize(steps):
        print("\nSMOKE PASSED — full lifecycle verified")
        return 0
    print("\nSMOKE FAILED — see failing steps above")
    return 1


def main() -> None:
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
