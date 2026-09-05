#!/usr/bin/env python3
"""Backfill candle history into data/ for walk-forward validation.

Fetches through the MarketDataGateway seam (public data; a dummy key keeps
the connector constructor happy without any real account access).

Usage:
    python3 scripts/backfill_candles.py --coin HYPE --interval 1h --days 90
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import backfill
from src.core.config import BotConfig
from src.exchange.connector import HyperliquidAPI

DUMMY_KEY = "0x" + "0" * 63 + "1"
DUMMY_ADDRESS = "0x" + "0" * 39 + "1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill candles via the gateway seam"
    )
    parser.add_argument("--coin", type=str, default="HYPE")
    parser.add_argument("--interval", type=str, default="1h")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output CSV (default: data/{coin}_{interval}_{days}d.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    bot_config = BotConfig(
        USE_TESTNET=False,
        PAPER_TRADING=True,
        PRIVATE_KEY=DUMMY_KEY,
        ADDRESS=DUMMY_ADDRESS,
    )
    api = HyperliquidAPI(bot_config)

    frame = asyncio.run(backfill(api, args.coin, args.interval, args.days))
    if frame.empty:
        print(f"No candle data returned for {args.coin} {args.interval}")
        sys.exit(1)

    out = (
        Path(args.out)
        if args.out
        else Path("data") / f"{args.coin}_{args.interval}_{args.days}d.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)

    days_covered = (frame["timestamp"].max() - frame["timestamp"].min()) / 86_400_000
    print(
        f"{args.coin} {args.interval}: {len(frame)} candles "
        f"({days_covered:.1f} days) -> {out}"
    )


if __name__ == "__main__":
    main()
