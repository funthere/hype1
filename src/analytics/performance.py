"""
Performance Analytics Module

Calculates advanced trading metrics including:
- Sharpe Ratio
- Sortino Ratio
- Calmar Ratio
- Maximum Drawdown
- Win/Loss Analysis
- Trade Duration Statistics
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.config import Trade

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Container for performance metrics"""

    # Return metrics
    total_return: float
    annualized_return: float
    cagr: float

    # Risk metrics
    volatility: float
    max_drawdown: float
    avg_drawdown: float

    # Risk-adjusted returns
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float

    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float

    # P&L statistics
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_trade: float

    # Duration statistics (hours)
    avg_trade_duration: float
    avg_win_duration: float
    avg_loss_duration: float

    # Streaks
    max_winning_streak: int
    max_losing_streak: int
    current_streak: int
    current_streak_type: str  # "win" or "loss"

    # Frequency
    avg_trades_per_day: float
    avg_trades_per_week: float


class PerformanceAnalyzer:
    """
    Advanced performance analysis for trading bot.

    Calculates comprehensive metrics from trade history
    including risk-adjusted returns and drawdown analysis.
    """

    # Risk-free rate for Sharpe/Sortino (annualized)
    RISK_FREE_RATE = 0.05  # 5% annual

    def __init__(self, initial_capital: float = 10000):
        """
        Initialize performance analyzer

        Args:
            initial_capital: Starting capital for calculations
        """
        self.initial_capital = initial_capital
        self.trades: List[Trade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []

    def add_trade(self, trade: Trade):
        """Add a trade to the analysis"""
        self.trades.append(trade)

        # Update equity curve
        if self.equity_curve:
            last_equity = self.equity_curve[-1][1]
        else:
            last_equity = self.initial_capital

        new_equity = last_equity + trade.pnl
        timestamp = trade.exit_time or datetime.now()
        self.equity_curve.append((timestamp, new_equity))

    def calculate_metrics(
        self, trades: Optional[List[Trade]] = None
    ) -> PerformanceMetrics:
        """
        Calculate all performance metrics

        Args:
            trades: Optional list of trades to analyze (uses stored trades if None)

        Returns:
            PerformanceMetrics dataclass with all calculated values
        """
        if trades is None:
            trades = self.trades

        if not trades:
            return self._empty_metrics()

        df = self._trades_to_dataframe(trades)

        # Calculate metrics
        return_metrics = {
            # Return metrics
            "total_return": self._total_return(df),
            "annualized_return": self._annualized_return(df),
            "cagr": self._cagr(df),
            # Risk metrics
            "volatility": self._volatility(df),
            "max_drawdown": self._max_drawdown(df),
            "avg_drawdown": self._avg_drawdown(df),
            # Risk-adjusted returns
            "sharpe_ratio": self._sharpe_ratio(df),
            "sortino_ratio": self._sortino_ratio(df),
            "calmar_ratio": self._calmar_ratio(df),
            # Trade statistics
            "total_trades": len(df),
            "winning_trades": len(df[df["pnl"] > 0]),
            "losing_trades": len(df[df["pnl"] < 0]),
            "win_rate": self._win_rate(df),
            "profit_factor": self._profit_factor(df),
            # P&L statistics
            "avg_win": self._avg_win(df),
            "avg_loss": self._avg_loss(df),
            "largest_win": self._largest_win(df),
            "largest_loss": self._largest_loss(df),
            "avg_trade": self._avg_trade(df),
            # Duration statistics
            "avg_trade_duration": self._avg_duration(df),
            "avg_win_duration": self._avg_win_duration(df),
            "avg_loss_duration": self._avg_loss_duration(df),
            # Streaks
            "max_winning_streak": self._max_winning_streak(df),
            "max_losing_streak": self._max_losing_streak(df),
            "current_streak": self._current_streak(df)[0],
            "current_streak_type": self._current_streak(df)[1],
            # Frequency
            "avg_trades_per_day": self._avg_trades_per_day(df),
            "avg_trades_per_week": self._avg_trades_per_week(df),
        }

        return PerformanceMetrics(**return_metrics)

    def _trades_to_dataframe(self, trades: List[Trade]) -> pd.DataFrame:
        """Convert trades to pandas DataFrame"""
        data = []

        for trade in trades:
            duration = None
            if trade.entry_time and trade.exit_time:
                duration = (
                    trade.exit_time - trade.entry_time
                ).total_seconds() / 3600  # hours

            data.append(
                {
                    "side": trade.side.value,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "quantity": trade.quantity,
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "pnl": trade.pnl,
                    "fees": trade.fees,
                    "net_pnl": trade.pnl - trade.fees,
                    "duration_hours": duration,
                }
            )

        df = pd.DataFrame(data)

        # Sort by exit time
        if "exit_time" in df.columns and not df["exit_time"].isna().all():
            df["exit_time"] = pd.to_datetime(df["exit_time"])
            df = df.sort_values("exit_time")

        return df

    # Return metrics

    def _total_return(self, df: pd.DataFrame) -> float:
        """Calculate total return percentage"""
        total_pnl = df["pnl"].sum()
        return (total_pnl / self.initial_capital) * 100

    def _annualized_return(self, df: pd.DataFrame) -> float:
        """Calculate annualized return"""
        total_return = self._total_return(df) / 100

        if len(df) < 2:
            return 0.0

        # Calculate time span in years
        start = df["exit_time"].min()
        end = df["exit_time"].max()
        years = (end - start).total_seconds() / (365.25 * 24 * 3600)

        if years <= 0:
            return 0.0

        return ((1 + total_return) ** (1 / years) - 1) * 100

    def _cagr(self, df: pd.DataFrame) -> float:
        """Calculate Compound Annual Growth Rate"""
        final_value = self.initial_capital + df["pnl"].sum()

        if len(df) < 2:
            return 0.0

        start = df["exit_time"].min()
        end = df["exit_time"].max()
        years = (end - start).total_seconds() / (365.25 * 24 * 3600)

        if years <= 0:
            return 0.0

        return ((final_value / self.initial_capital) ** (1 / years) - 1) * 100

    # Risk metrics

    def _volatility(self, df: pd.DataFrame) -> float:
        """Calculate annualized volatility of returns"""
        if len(df) < 2:
            return 0.0

        returns = df["pnl"] / self.initial_capital
        return returns.std() * np.sqrt(252) * 100  # Annualized, assuming daily trades

    def _max_drawdown(self, df: pd.DataFrame) -> float:
        """Calculate maximum drawdown percentage"""
        if len(df) < 1:
            return 0.0

        cumulative = df["pnl"].cumsum()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / self.initial_capital * 100

        return abs(drawdown.min())

    def _avg_drawdown(self, df: pd.DataFrame) -> float:
        """Calculate average drawdown percentage"""
        if len(df) < 2:
            return 0.0

        cumulative = df["pnl"].cumsum()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / self.initial_capital * 100

        # Only consider drawdown periods (negative values)
        dd_periods = drawdown[drawdown < 0]

        if len(dd_periods) == 0:
            return 0.0

        return abs(dd_periods.mean())

    # Risk-adjusted returns

    def _sharpe_ratio(self, df: pd.DataFrame) -> float:
        """Calculate Sharpe Ratio (annualized)"""
        if len(df) < 2:
            return 0.0

        excess_returns = (df["pnl"].mean() / self.initial_capital) - (
            self.RISK_FREE_RATE / 252
        )
        volatility = df["pnl"].std() / self.initial_capital

        if volatility == 0:
            return 0.0

        return (excess_returns / volatility) * np.sqrt(252)

    def _sortino_ratio(self, df: pd.DataFrame) -> float:
        """Calculate Sortino Ratio (downside deviation)"""
        if len(df) < 2:
            return 0.0

        mean_return = df["pnl"].mean() / self.initial_capital
        excess_return = mean_return - (self.RISK_FREE_RATE / 252)

        # Downside deviation (only negative returns)
        negative_returns = df["pnl"][df["pnl"] < 0] / self.initial_capital

        if len(negative_returns) == 0:
            return float("inf") if excess_return > 0 else 0.0

        downside_deviation = negative_returns.std()

        if downside_deviation == 0:
            return 0.0

        return (excess_return / downside_deviation) * np.sqrt(252)

    def _calmar_ratio(self, df: pd.DataFrame) -> float:
        """Calculate Calmar Ratio (CAGR / Max Drawdown)"""
        cagr = self._cagr(df) / 100
        max_dd = self._max_drawdown(df) / 100

        if max_dd == 0:
            return 0.0

        return cagr / max_dd

    # Trade statistics

    def _win_rate(self, df: pd.DataFrame) -> float:
        """Calculate win rate percentage"""
        if len(df) == 0:
            return 0.0

        winning = len(df[df["pnl"] > 0])
        return (winning / len(df)) * 100

    def _profit_factor(self, df: pd.DataFrame) -> float:
        """Calculate profit factor (gross wins / gross losses)"""
        gross_wins = df[df["pnl"] > 0]["pnl"].sum()
        gross_losses = abs(df[df["pnl"] < 0]["pnl"].sum())

        if gross_losses == 0:
            return 0.0 if gross_wins == 0 else float("inf")

        return gross_wins / gross_losses

    # P&L statistics

    def _avg_win(self, df: pd.DataFrame) -> float:
        """Calculate average winning trade"""
        wins = df[df["pnl"] > 0]["pnl"]
        return wins.mean() if len(wins) > 0 else 0.0

    def _avg_loss(self, df: pd.DataFrame) -> float:
        """Calculate average losing trade (negative value)"""
        losses = df[df["pnl"] < 0]["pnl"]
        return losses.mean() if len(losses) > 0 else 0.0

    def _largest_win(self, df: pd.DataFrame) -> float:
        """Calculate largest winning trade"""
        wins = df[df["pnl"] > 0]["pnl"]
        return wins.max() if len(wins) > 0 else 0.0

    def _largest_loss(self, df: pd.DataFrame) -> float:
        """Calculate largest losing trade (negative value)"""
        losses = df[df["pnl"] < 0]["pnl"]
        return losses.min() if len(losses) > 0 else 0.0

    def _avg_trade(self, df: pd.DataFrame) -> float:
        """Calculate average trade P&L"""
        return df["pnl"].mean() if len(df) > 0 else 0.0

    # Duration statistics

    def _avg_duration(self, df: pd.DataFrame) -> float:
        """Calculate average trade duration in hours"""
        durations = df["duration_hours"].dropna()
        return durations.mean() if len(durations) > 0 else 0.0

    def _avg_win_duration(self, df: pd.DataFrame) -> float:
        """Calculate average winning trade duration"""
        wins = df[df["pnl"] > 0]["duration_hours"].dropna()
        return wins.mean() if len(wins) > 0 else 0.0

    def _avg_loss_duration(self, df: pd.DataFrame) -> float:
        """Calculate average losing trade duration"""
        losses = df[df["pnl"] < 0]["duration_hours"].dropna()
        return losses.mean() if len(losses) > 0 else 0.0

    # Streaks

    def _max_winning_streak(self, df: pd.DataFrame) -> int:
        """Calculate maximum consecutive winning trades"""
        df["win"] = df["pnl"] > 0
        df["streak_group"] = (df["win"] != df["win"].shift()).cumsum()

        streaks = df.groupby(["win", "streak_group"]).size().reset_index(name="count")
        winning_streaks = streaks[streaks["win"]]["count"]

        return winning_streaks.max() if len(winning_streaks) > 0 else 0

    def _max_losing_streak(self, df: pd.DataFrame) -> int:
        """Calculate maximum consecutive losing trades"""
        df["win"] = df["pnl"] > 0
        df["streak_group"] = (df["win"] != df["win"].shift()).cumsum()

        streaks = df.groupby(["win", "streak_group"]).size().reset_index(name="count")
        losing_streaks = streaks[not streaks["win"]]["count"]

        return losing_streaks.max() if len(losing_streaks) > 0 else 0

    def _current_streak(self, df: pd.DataFrame) -> Tuple[int, str]:
        """Calculate current streak (count and type)"""
        if len(df) == 0:
            return 0, "none"

        # Work backwards from last trade
        current_count = 1
        is_winning = df.iloc[-1]["pnl"] > 0

        for i in range(len(df) - 2, -1, -1):
            if (df.iloc[i]["pnl"] > 0) == is_winning:
                current_count += 1
            else:
                break

        return current_count, "win" if is_winning else "loss"

    # Frequency

    def _avg_trades_per_day(self, df: pd.DataFrame) -> float:
        """Calculate average trades per day"""
        if len(df) < 2:
            return 0.0

        start = df["exit_time"].min()
        end = df["exit_time"].max()
        days = (end - start).total_seconds() / (24 * 3600)

        if days <= 0:
            return 0.0

        return len(df) / days

    def _avg_trades_per_week(self, df: pd.DataFrame) -> float:
        """Calculate average trades per week"""
        return self._avg_trades_per_day(df) * 7

    # Utility

    def _empty_metrics(self) -> PerformanceMetrics:
        """Return empty metrics object"""
        return PerformanceMetrics(
            total_return=0.0,
            annualized_return=0.0,
            cagr=0.0,
            volatility=0.0,
            max_drawdown=0.0,
            avg_drawdown=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            largest_win=0.0,
            largest_loss=0.0,
            avg_trade=0.0,
            avg_trade_duration=0.0,
            avg_win_duration=0.0,
            avg_loss_duration=0.0,
            max_winning_streak=0,
            max_losing_streak=0,
            current_streak=0,
            current_streak_type="none",
            avg_trades_per_day=0.0,
            avg_trades_per_week=0.0,
        )

    def get_equity_curve(self) -> List[Tuple[datetime, float]]:
        """Get equity curve as list of (timestamp, value) tuples"""
        return self.equity_curve.copy()

    def get_drawdown_curve(self) -> List[Tuple[datetime, float]]:
        """Get drawdown curve over time"""
        if not self.equity_curve:
            return []

        timestamps, values = zip(*self.equity_curve)
        cumulative = np.array(values)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = ((cumulative - running_max) / running_max) * 100

        return list(zip(timestamps, drawdown.tolist()))

    def generate_report(self) -> str:
        """Generate formatted performance report"""
        metrics = self.calculate_metrics()

        report = """
╔════════════════════════════════════════════════════════════╗
║              PERFORMANCE ANALYSIS REPORT                     ║
╚════════════════════════════════════════════════════════════╝

📊 RETURN METRICS
────────────────────────────────────────────────────────────
  Total Return:        {total_return:>8.2f}%
  Annualized Return:   {annualized_return:>8.2f}%
  CAGR:                {cagr:>8.2f}%

⚠️  RISK METRICS
────────────────────────────────────────────────────────────
  Volatility:          {volatility:>8.2f}%
  Max Drawdown:        {max_drawdown:>8.2f}%
  Avg Drawdown:        {avg_drawdown:>8.2f}%

📈 RISK-ADJUSTED RETURNS
────────────────────────────────────────────────────────────
  Sharpe Ratio:        {sharpe_ratio:>8.2f}
  Sortino Ratio:       {sortino_ratio:>8.2f}
  Calmar Ratio:        {calmar_ratio:>8.2f}

📋 TRADE STATISTICS
────────────────────────────────────────────────────────────
  Total Trades:        {total_trades:>8}
  Winning Trades:      {winning_trades:>8}
  Losing Trades:       {losing_trades:>8}
  Win Rate:            {win_rate:>7.1f}%
  Profit Factor:       {profit_factor:>8.2f}

💰 P&L STATISTICS
────────────────────────────────────────────────────────────
  Avg Win:             ${avg_win:>8.2f}
  Avg Loss:            ${avg_loss:>8.2f}
  Largest Win:         ${largest_win:>8.2f}
  Largest Loss:        ${largest_loss:>8.2f}
  Avg Trade:           ${avg_trade:>8.2f}

⏱️  DURATION STATISTICS
────────────────────────────────────────────────────────────
  Avg Duration:        {avg_trade_duration:>7.2f} hours
  Avg Win Duration:    {avg_win_duration:>7.2f} hours
  Avg Loss Duration:   {avg_loss_duration:>7.2f} hours

🔥 STREAKS
────────────────────────────────────────────────────────────
  Max Winning Streak:  {max_winning_streak:>8}
  Max Losing Streak:   {max_losing_streak:>8}
  Current Streak:      {current_streak:>8} ({current_streak_type})

📅 FREQUENCY
────────────────────────────────────────────────────────────
  Trades per Day:      {avg_trades_per_day:>8.2f}
  Trades per Week:     {avg_trades_per_week:>8.2f}
""".format(
            total_return=metrics.total_return,
            annualized_return=metrics.annualized_return,
            cagr=metrics.cagr,
            volatility=metrics.volatility,
            max_drawdown=metrics.max_drawdown,
            avg_drawdown=metrics.avg_drawdown,
            sharpe_ratio=metrics.sharpe_ratio,
            sortino_ratio=metrics.sortino_ratio,
            calmar_ratio=metrics.calmar_ratio,
            total_trades=metrics.total_trades,
            winning_trades=metrics.winning_trades,
            losing_trades=metrics.losing_trades,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            avg_win=metrics.avg_win,
            avg_loss=metrics.avg_loss,
            largest_win=metrics.largest_win,
            largest_loss=metrics.largest_loss,
            avg_trade=metrics.avg_trade,
            avg_trade_duration=metrics.avg_trade_duration,
            avg_win_duration=metrics.avg_win_duration,
            avg_loss_duration=metrics.avg_loss_duration,
            max_winning_streak=metrics.max_winning_streak,
            max_losing_streak=metrics.max_losing_streak,
            current_streak=metrics.current_streak,
            current_streak_type=metrics.current_streak_type,
            avg_trades_per_day=metrics.avg_trades_per_day,
            avg_trades_per_week=metrics.avg_trades_per_week,
        )

        return report
