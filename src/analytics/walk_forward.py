"""
Walk-Forward Validation Harness

Replays a signal engine over historical candles in time-ordered folds so
strategy parameters are only ever evaluated on data that follows the
context the engine saw — the minimum bar a strategy must clear before any
live consideration.

The harness is evaluation-only: it replays a factory of engines (anything
matching the ``StrategyEngine`` surface of ``update_candle`` +
``generate_signal``) against candle frames and reports per-fold stats net
of fees. Parameter search is deliberately out of scope; optimization on
train windows belongs to the caller.

Fill simulation is deliberately conservative: when a candle touches both
take-profit and stop-loss, the stop is assumed to fill first.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SimulatedTrade:
    """One simulated round trip inside a test fold."""

    side: str  # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float  # net of fees
    fees: float
    exit_reason: str  # "take_profit", "stop_loss", "fold_end"


@dataclass
class FoldResult:
    """Performance of one out-of-sample test window."""

    fold: int
    train_candles: int
    test_candles: int
    trades: int
    wins: int
    net_pnl: float
    fees: float
    profit_factor: float  # inf when there are no losing trades
    expectancy: float
    trades_list: List[SimulatedTrade] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


@dataclass
class WalkForwardResult:
    """Aggregate outcome across all folds."""

    folds: List[FoldResult] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return sum(f.trades for f in self.folds)

    @property
    def total_net_pnl(self) -> float:
        return sum(f.net_pnl for f in self.folds)

    @property
    def aggregate_profit_factor(self) -> float:
        wins = sum(t.pnl for f in self.folds for t in f.trades_list if t.pnl > 0)
        losses = abs(sum(t.pnl for f in self.folds for t in f.trades_list if t.pnl < 0))
        return wins / losses if losses > 0 else float("inf")


class WalkForwardValidator:
    """Replay an engine factory over rolling out-of-sample windows.

    Args:
        engine_factory: Zero-argument callable returning a fresh engine per
            fold exposing ``update_candle(dict)`` and
            ``generate_signal(capital)`` (the ``StrategyEngine`` surface).
        train_size: Warm-up candles fed before each test window.
        test_size: Out-of-sample candles per fold.
        fee_pct: Taker fee per side, applied on entry and exit notional.
        initial_capital: Account size used for engine position sizing.
        min_candles: candle count required for the engine to signal;
            candles shorter than ``train_size + test_size`` yield no folds.
    """

    def __init__(
        self,
        engine_factory: Callable[[], object],
        train_size: int = 500,
        test_size: int = 250,
        fee_pct: float = 0.0005,
        initial_capital: float = 10_000.0,
    ):
        if train_size <= 0 or test_size <= 0:
            raise ValueError("train_size and test_size must be positive")
        self.engine_factory = engine_factory
        self.train_size = train_size
        self.test_size = test_size
        self.fee_pct = fee_pct
        self.initial_capital = initial_capital

    def run(self, candles: pd.DataFrame) -> WalkForwardResult:
        """Execute all folds over the candle frame (must be time-ordered)."""
        result = WalkForwardResult()
        total = len(candles)
        fold = 0
        start = self.train_size
        while start < total:
            test_end = min(start + self.test_size, total)
            trades = self._run_fold(candles.iloc[:start], candles.iloc[start:test_end])
            result.folds.append(self._summarize(fold, start, test_end - start, trades))
            fold += 1
            start = test_end
        return result

    # ------------------------------------------------------------------
    # Fold execution
    # ------------------------------------------------------------------

    def _run_fold(
        self, train: pd.DataFrame, test: pd.DataFrame
    ) -> List[SimulatedTrade]:
        engine = self.engine_factory()
        for _, candle in train.iterrows():
            engine.update_candle(self._candle_dict(candle))

        trades: List[SimulatedTrade] = []
        open_trade: Optional[dict] = None

        for _, candle in test.iterrows():
            candle_dict = self._candle_dict(candle)
            engine.update_candle(candle_dict)

            if open_trade is not None:
                exit_price, reason = self._check_exit(open_trade, candle)
                if exit_price is not None:
                    trades.append(self._close_trade(open_trade, exit_price, reason))
                    open_trade = None
                continue

            signal = engine.generate_signal(self.initial_capital)
            if signal:
                open_trade = {
                    "side": signal["action"].value,
                    "entry_price": float(candle_dict["close"]),
                    "tp_price": float(signal["tp_price"]),
                    "sl_price": float(signal["sl_price"]),
                    "quantity": float(signal["quantity"]),
                }

        if open_trade is not None:
            # Force-close at the final test close: an out-of-sample fold
            # must not report an open position as a win-in-waiting.
            last_close = float(self._candle_dict(test.iloc[-1])["close"])
            trades.append(self._close_trade(open_trade, last_close, "fold_end"))

        return trades

    def _check_exit(self, trade: dict, candle: pd.Series):
        """Resolve tp/sl against one candle. Both touched → stop first."""
        high = float(candle["high"])
        low = float(candle["low"])
        if trade["side"] == "LONG":
            if low <= trade["sl_price"]:
                return trade["sl_price"], "stop_loss"
            if high >= trade["tp_price"]:
                return trade["tp_price"], "take_profit"
        else:
            if high >= trade["sl_price"]:
                return trade["sl_price"], "stop_loss"
            if low <= trade["tp_price"]:
                return trade["tp_price"], "take_profit"
        return None, None

    def _close_trade(
        self, trade: dict, exit_price: float, reason: str
    ) -> SimulatedTrade:
        qty = trade["quantity"]
        direction = 1.0 if trade["side"] == "LONG" else -1.0
        gross = (exit_price - trade["entry_price"]) * direction * qty
        fees = (trade["entry_price"] + exit_price) * qty * self.fee_pct
        return SimulatedTrade(
            side=trade["side"],
            entry_price=trade["entry_price"],
            exit_price=exit_price,
            quantity=qty,
            pnl=gross - fees,
            fees=fees,
            exit_reason=reason,
        )

    @staticmethod
    def _candle_dict(row: pd.Series) -> dict:
        return {
            "timestamp": row.get("timestamp", row.name),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }

    def _summarize(
        self,
        fold: int,
        train_candles: int,
        test_candles: int,
        trades: List[SimulatedTrade],
    ) -> FoldResult:
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_losses = abs(sum(losses))
        return FoldResult(
            fold=fold,
            train_candles=train_candles,
            test_candles=test_candles,
            trades=len(trades),
            wins=len(wins),
            net_pnl=sum(pnls),
            fees=sum(t.fees for t in trades),
            profit_factor=(sum(wins) / gross_losses)
            if gross_losses > 0
            else float("inf"),
            expectancy=(sum(pnls) / len(pnls)) if pnls else 0.0,
            trades_list=trades,
        )
