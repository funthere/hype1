#!/usr/bin/env python3
"""Pre-registered parameter study over the walk-forward harness.

Every configuration below was committed to the repository BEFORE looking
at its results — the grid is small, named, and justified by the project's
own documents, not chosen after seeing outcomes. All results are reported;
nothing is silently dropped. This is hypothesis testing, not a search for
a winner: any configuration that survives still owes a fresh
out-of-sample window before promotion (the study window is the same one
the baselines ran on).

Usage:
    python3 scripts/run_parameter_study.py
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics import WalkForwardValidator
from src.core.config import BotConfig
from src.core.strategy import StrategyEngine
from src.strategy.trend_engine import TrendFollowingEngine
from src.strategy.trend_following import TrendFollowingConfig

HYPE_15M_CSV = Path("data/HYPE_15m_90d.csv")
ONE_HOUR_CSVS = {
    coin: Path(f"data/{coin}_1h_90d.csv") for coin in ("HYPE", "BTC", "ETH", "SOL")
}


@dataclass
class StudyConfig:
    name: str
    rationale: str
    strategy: str  # "momentum" | "trend"
    overrides: Dict = field(default_factory=dict)
    coins: List[str] = field(default_factory=lambda: ["HYPE"])


# ---------------------------------------------------------------------------
# Pre-registered configurations
# ---------------------------------------------------------------------------

MOMENTUM_CONFIGS = [
    StudyConfig(
        name="M0-baseline",
        rationale="Default BotConfig parameters; already measured (PF 0.44) — "
        "included as the control.",
        strategy="momentum",
    ),
    StudyConfig(
        name="M1-wide-stop",
        rationale="HYPE_REAL_RESULTS.md: 0.4x ATR stop interacts with 15m noise; "
        "test 1.5x stop with 3.0x target (same 2:1 shape).",
        strategy="momentum",
        overrides={"SL_ATR_MULTIPLIER": 1.5, "TP_ATR_MULTIPLIER": 3.0},
    ),
    StudyConfig(
        name="M2-conservative-doc-hypothesis",
        rationale="HYPE_REAL_RESULTS.md recommendations: confidence 70, TP 2.0, "
        "SL 0.8 — quality filter plus moderately wider stop.",
        strategy="momentum",
        overrides={
            "CONFIDENCE_THRESHOLD": 70,
            "TP_ATR_MULTIPLIER": 2.0,
            "SL_ATR_MULTIPLIER": 0.8,
        },
    ),
    StudyConfig(
        name="M3-slower-momentum",
        rationale="Fewer, longer-horizon signals: ROC_LONG 20 instead of 5 to "
        "cut fee churn (fees were ~2/3 of baseline losses).",
        strategy="momentum",
        overrides={"ROC_LONG": 20},
    ),
]

TREND_CONFIGS = [
    StudyConfig(
        name="T0-baseline",
        rationale="Post pullback-fix defaults; already measured per coin.",
        strategy="trend",
    ),
    StudyConfig(
        name="T1-stronger-regime",
        rationale="ADX threshold 30 -> 40: only the strongest trends qualify; "
        "targets the fold variance seen in the baseline study.",
        strategy="trend",
        overrides={"ADX_THRESHOLD": 40.0},
    ),
    StudyConfig(
        name="T2-wide-atr-stop",
        rationale="ATR stop 2.0 -> 3.0 with trailing 2.5 -> 3.5: give winners "
        "room; the baseline's fold losses were stop-clustered.",
        strategy="trend",
        overrides={"ATR_STOP_MULT": 3.0, "TRAILING_STOP_MULT": 3.5},
    ),
]

# Fold geometry mirrors the baseline study so numbers are comparable.
MOMENTUM_TRAIN, MOMENTUM_TEST = 2000, 1000
TREND_TRAIN, TREND_TEST = 1200, 480
FEE_PCT = 0.0005
CAPITAL = 10_000.0


def build_engine(config: StudyConfig) -> Callable[[], object]:
    if config.strategy == "momentum":

        def factory():
            return StrategyEngine(BotConfig(PAPER_TRADING=True, **config.overrides))

        return factory

    def factory():
        return TrendFollowingEngine(
            TrendFollowingConfig(PAPER_TRADING=True, **config.overrides)
        )

    return factory


def run_cell(config: StudyConfig, coin: str, csv_path: Path) -> Dict:
    candles = pd.read_csv(csv_path)
    train, test = (
        (MOMENTUM_TRAIN, MOMENTUM_TEST)
        if config.strategy == "momentum"
        else (TREND_TRAIN, TREND_TEST)
    )
    validator = WalkForwardValidator(
        build_engine(config),
        train_size=train,
        test_size=test,
        fee_pct=FEE_PCT,
        initial_capital=CAPITAL,
    )
    result = validator.run(candles)
    return {
        "config": config.name,
        "coin": coin,
        "trades": result.total_trades,
        "net_pnl": round(result.total_net_pnl, 2),
        "profit_factor": (
            "inf"
            if result.aggregate_profit_factor == float("inf")
            else round(result.aggregate_profit_factor, 2)
        ),
    }


def main() -> None:
    rows: List[Dict] = []

    for config in MOMENTUM_CONFIGS:
        rows.append(run_cell(config, "HYPE", HYPE_15M_CSV))

    for config in TREND_CONFIGS:
        for coin, csv_path in ONE_HOUR_CSVS.items():
            rows.append(run_cell(config, coin, csv_path))

    print(pd.DataFrame(rows).to_string(index=False))
    print(
        "\nPre-registered grid; all results disclosed. A surviving config "
        "still needs a fresh out-of-sample window before any promotion."
    )


if __name__ == "__main__":
    main()
