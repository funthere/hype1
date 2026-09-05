"""
Strategy Scorecard

Decision-layer analytics that let the data pick which strategy earns
capital. Records closed trades incrementally and answers the two questions
that matter for a multi-strategy setup:

1. How is the strategy doing *right now* (rolling window, net of costs)?
2. Should it be retired under the configured policy?

Semantics: ``Trade.pnl`` is interpreted as the realized net PnL of the
trade (as recorded by the strategies); ``Trade.fees`` is reported
separately for cost accounting but is not subtracted again.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..core.config import Trade

logger = logging.getLogger(__name__)


@dataclass
class RetirePolicy:
    """Thresholds for the auto-retire recommendation.

    All evaluation happens over the trailing ``window_days`` ending at the
    most recent trade, so an old winning history cannot mask a recent
    breakdown.
    """

    min_trades: int = 30
    window_days: int = 28
    min_profit_factor: float = 1.0
    min_expectancy: float = 0.0
    max_drawdown_pct: float = 0.20


@dataclass
class WeeklyStat:
    """Aggregated net PnL for one ISO calendar week."""

    iso_year: int
    iso_week: int
    trades: int
    net_pnl: float
    expectancy: float  # mean net pnl per trade that week


@dataclass
class ScorecardSnapshot:
    """Point-in-time performance summary."""

    total_trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float  # mean net pnl per trade
    profit_factor: float  # gross wins / gross losses (inf when no losses)
    total_net_pnl: float
    total_fees: float
    max_drawdown_pct: float
    weekly: List[WeeklyStat] = field(default_factory=list)


class StrategyScorecard:
    """Incremental scorecard for one strategy.

    Feed every closed trade to :meth:`record`; read :meth:`snapshot` for
    dashboards and :meth:`should_retire` for kill/keep decisions.
    """

    def __init__(
        self,
        name: str,
        policy: Optional[RetirePolicy] = None,
        initial_capital: float = 10_000.0,
    ):
        self.name = name
        self.policy = policy or RetirePolicy()
        self.initial_capital = initial_capital
        self._trades: List[Trade] = []

    def record(self, trade: Trade) -> None:
        """Record one closed trade."""
        self._trades.append(trade)

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    def snapshot(self) -> ScorecardSnapshot:
        """Compute the full performance snapshot over all recorded trades."""
        pnls = [t.pnl for t in self._trades]
        fees = [t.fees for t in self._trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = (
            (gross_wins / gross_losses) if gross_losses > 0 else float("inf")
        )

        return ScorecardSnapshot(
            total_trades=len(pnls),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(pnls) if pnls else 0.0,
            expectancy=(sum(pnls) / len(pnls)) if pnls else 0.0,
            profit_factor=profit_factor,
            total_net_pnl=sum(pnls),
            total_fees=sum(fees),
            max_drawdown_pct=self._max_drawdown_pct(pnls),
            weekly=self.weekly_summary(),
        )

    def weekly_summary(self) -> List[WeeklyStat]:
        """Bucket closed trades by ISO week, oldest first."""
        buckets: Dict[Tuple[int, int], List[float]] = defaultdict(list)
        for trade in self._trades:
            ts = self._trade_time(trade)
            if ts is None:
                continue
            iso = ts.isocalendar()
            buckets[(iso[0], iso[1])].append(trade.pnl)

        return [
            WeeklyStat(
                iso_year=year,
                iso_week=week,
                trades=len(pnls),
                net_pnl=sum(pnls),
                expectancy=sum(pnls) / len(pnls),
            )
            for (year, week), pnls in sorted(buckets.items())
        ]

    def should_retire(self) -> Tuple[bool, str]:
        """Evaluate the retire policy over the trailing window.

        Returns:
            (retire, reason). ``False`` with "insufficient data" while the
            window holds fewer than ``min_trades`` trades — a policy needs
            evidence before it kills a strategy.
        """
        if not self._trades:
            return False, "insufficient data (no trades)"

        latest = self._trade_time(self._trades[-1])
        if latest is None:
            return False, "insufficient data (trades lack timestamps)"

        window_start = latest - timedelta(days=self.policy.window_days)
        window = [
            t for t in self._trades if (self._trade_time(t) or latest) >= window_start
        ]
        if len(window) < self.policy.min_trades:
            return (
                False,
                f"insufficient data ({len(window)}/{self.policy.min_trades} "
                f"trades in {self.policy.window_days}d window)",
            )

        pnls = [t.pnl for t in window]
        expectancy = sum(pnls) / len(pnls)
        gross_wins = sum(p for p in pnls if p > 0)
        gross_losses = abs(sum(p for p in pnls if p < 0))
        profit_factor = (
            (gross_wins / gross_losses) if gross_losses > 0 else float("inf")
        )
        drawdown = self._max_drawdown_pct(pnls)

        if profit_factor < self.policy.min_profit_factor:
            return (
                True,
                f"profit factor {profit_factor:.2f} < "
                f"{self.policy.min_profit_factor:.2f} over "
                f"{self.policy.window_days}d window",
            )
        if expectancy < self.policy.min_expectancy:
            return (
                True,
                f"expectancy {expectancy:.4f} < {self.policy.min_expectancy:.4f} "
                f"over {self.policy.window_days}d window",
            )
        if drawdown > self.policy.max_drawdown_pct:
            return (
                True,
                f"drawdown {drawdown:.1%} > {self.policy.max_drawdown_pct:.1%} "
                f"over {self.policy.window_days}d window",
            )
        return False, "policy satisfied"

    def _max_drawdown_pct(self, pnls: List[float]) -> float:
        """Peak-to-trough decline of the cumulative pnl curve, relative to
        initial capital."""
        equity = self.initial_capital
        peak = equity
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd

    @staticmethod
    def _trade_time(trade: Trade) -> Optional[datetime]:
        """Timestamp used for windowing and weekly buckets."""
        return trade.exit_time or trade.entry_time
