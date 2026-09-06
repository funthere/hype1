#!/usr/bin/env python3
"""Run walk-forward validation over a backfilled candle CSV.

Replays a strategy's signal engine over time-ordered out-of-sample folds
with conservative fill simulation and reports per-fold stats net of fees.

Usage:
    python3 scripts/run_walk_forward.py --csv data/HYPE_1h_90d.csv \
        --strategy trend --train 1200 --test 480
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.analytics import WalkForwardValidator
from src.core.config import BotConfig
from src.core.strategy import StrategyEngine
from src.strategy.trend_engine import TrendFollowingEngine
from src.strategy.trend_following import TrendFollowingConfig

FOLD_COLUMNS = (
    "fold",
    "train_candles",
    "test_candles",
    "trades",
    "wins",
    "win_rate",
    "net_pnl",
    "fees",
    "profit_factor",
    "expectancy",
)


def fmt_profit_factor(value: float) -> str:
    return "inf" if value == float("inf") else f"{value:.2f}"


def build_factory(strategy: str):
    if strategy == "momentum":

        def factory():
            return StrategyEngine(BotConfig(PAPER_TRADING=True))

        return factory
    if strategy == "trend":

        def factory():
            return TrendFollowingEngine(TrendFollowingConfig(PAPER_TRADING=True))

        return factory
    raise SystemExit(f"Unknown strategy '{strategy}' (use: momentum, trend)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation runner")
    parser.add_argument("--csv", type=str, required=True)
    parser.add_argument(
        "--strategy", type=str, choices=["momentum", "trend"], required=True
    )
    parser.add_argument(
        "--train", type=int, default=1200, help="Warm-up candles per fold"
    )
    parser.add_argument("--test", type=int, default=480, help="Test candles per fold")
    parser.add_argument("--fee", type=float, default=0.0005, help="Taker fee per side")
    parser.add_argument("--capital", type=float, default=10_000.0)
    args = parser.parse_args()

    candles = pd.read_csv(args.csv)
    if len(candles) <= args.train:
        raise SystemExit(
            f"CSV has {len(candles)} candles; need more than train size {args.train}"
        )

    validator = WalkForwardValidator(
        build_factory(args.strategy),
        train_size=args.train,
        test_size=args.test,
        fee_pct=args.fee,
        initial_capital=args.capital,
    )
    result = validator.run(candles)

    rows = [
        {
            "fold": fold.fold,
            "train_candles": fold.train_candles,
            "test_candles": fold.test_candles,
            "trades": fold.trades,
            "wins": fold.wins,
            "win_rate": round(fold.win_rate, 3),
            "net_pnl": round(fold.net_pnl, 2),
            "fees": round(fold.fees, 2),
            "profit_factor": fmt_profit_factor(fold.profit_factor),
            "expectancy": round(fold.expectancy, 4),
        }
        for fold in result.folds
    ]
    print(pd.DataFrame(rows, columns=FOLD_COLUMNS).to_string(index=False))
    print(
        "\nAGGREGATE "
        + json.dumps(
            {
                "folds": len(result.folds),
                "trades": result.total_trades,
                "net_pnl": round(result.total_net_pnl, 2),
                "profit_factor": fmt_profit_factor(result.aggregate_profit_factor),
            }
        )
    )


if __name__ == "__main__":
    main()
