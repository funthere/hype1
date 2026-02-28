"""
Trend-Following Strategies for HYPE/USDC on Hyperliquid
Optimized for volatile, trending assets
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple
from datetime import datetime, timedelta

from hype_king_bot import ATRIndicator, BacktestEngine, Side, Trade, HYPEKingConfig, OrderType


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class TrendConfig:
    """Base configuration for trend-following strategies"""
    ASSET: str = "HYPE"
    TIMEFRAME: str = "5m"
    LEVERAGE: int = 5
    ORDER_TYPE: OrderType = OrderType.LIMIT

    # Risk parameters
    RISK_PER_TRADE_PCT: float = 0.08
    TP_ATR_MULTIPLIER: float = 2.0
    SL_ATR_MULTIPLIER: float = 0.8

    # Risk management
    MAX_CONCURRENT_POSITIONS: int = 1
    TRADE_COOLDOWN_BARS: int = 6
    MAX_DAILY_TRADES: int = 15

    # Trailing stop
    USE_TRAILING_STOP: bool = True
    TRAIL_ACTIVATION_PCT: float = 0.5  # Activate at 0.5% profit
    TRAIL_STOP_ATR: float = 0.6

    # Fees
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0004


class EMAConfig(TrendConfig):
    """EMA Crossover Trend Following"""

    # EMA parameters for trend detection
    EMA_FAST: int = 8
    EMA_SLOW: int = 21
    EMA_SIGNAL: int = 50

    # Confirmation filters
    REQUIRE_PRICE_ALIGNMENT: bool = True  # Price must be on correct side of EMAs
    MIN_TREND_STRENGTH: float = 0.001  # Minimum separation between EMAs

    # Entry timing
    WAIT_FOR_CLOSE_CONFIRM: bool = True  # Enter on candle close after crossover

    CONFIDENCE_THRESHOLD: int = 55


class BreakoutConfig(TrendConfig):
    """Donchian Channel Breakout Strategy"""

    # Breakout parameters
    CHANNEL_PERIOD: int = 20  # Donchian channel period
    BREAKOUT_CONFIRMATION: bool = True  # Wait for confirmation candle

    # Filter for volatility
    MIN_ATR_MULT: float = 0.5  # Minimum ATR for valid breakout
    MAX_ATR_MULT: float = 3.0  # Maximum ATR (avoid chop)

    # Exit parameters
    CHANNEL_EXIT: bool = True  # Exit when price crosses back through mid

    CONFIDENCE_THRESHOLD: int = 60


class MomentumConfig(TrendConfig):
    """Momentum with Trend Following"""

    # Momentum parameters
    ROC_SHORT: int = 3
    ROC_LONG: int = 10

    # Momentum threshold
    MOMENTUM_THRESHOLD: float = 0.15  # % change required

    # Trend confirmation
    EMA_TREND_FILTER: int = 20
    VOLUME_CONFIRM: bool = True
    MIN_VOLUME_RATIO: float = 1.2

    # Exit
    MOMENTUM_EXIT: bool = True  # Exit when momentum reverses

    CONFIDENCE_THRESHOLD: int = 55


class TrendFollowingEngine(BacktestEngine):
    """Enhanced backtest engine for trend-following strategies"""

    def __init__(self, initial_capital: float = 10000.0, config: TrendConfig = None):
        if config is None:
            config = TrendConfig()
        # Initialize parent but override signal generation
        BacktestEngine.__init__(self, initial_capital, config)
        self.trend_config = config

    def run(self, df: pd.DataFrame, strategy='ema') -> dict:
        """
        Run trend-following backtest (overrides parent method)

        Args:
            df: DataFrame with OHLCV data
            strategy: 'ema', 'breakout', or 'momentum'
        """
        # Prepare data
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df['atr'] = self.atr.calculate(df)

        # Select signal generator
        signal_generators = {
            'ema': self.generate_ema_signals,
            'breakout': self.generate_breakout_signals,
            'momentum': self.generate_momentum_signals
        }

        if strategy not in signal_generators:
            raise ValueError(f"Unknown strategy: {strategy}. Choose: ema, breakout, momentum")

        # Generate signals (using trend-specific methods)
        df['signal'] = signal_generators[strategy](df)

    def generate_ema_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        EMA Crossover Trend Following

        Signals:
        - LONG: Fast EMA crosses above Slow EMA
        - SHORT: Fast EMA crosses below Slow EMA
        """
        df = df.copy()

        # Calculate EMAs
        ema_fast = df['close'].ewm(span=self.trend_config.EMA_FAST).mean()
        ema_slow = df['close'].ewm(span=self.trend_config.EMA_SLOW).mean()
        ema_signal = df['close'].ewm(span=self.trend_config.EMA_SIGNAL).mean()

        # Crossover detection
        ema_diff = ema_fast - ema_slow
        ema_diff_prev = ema_diff.shift(1)

        # Bullish crossover: fast crosses above slow
        bullish_cross = (ema_diff_prev <= 0) & (ema_diff > 0)
        # Bearish crossover: fast crosses below slow
        bearish_cross = (ema_diff_prev >= 0) & (ema_diff < 0)

        # Trend strength (separation between EMAs as % of price)
        trend_strength = abs(ema_fast - ema_slow) / df['close']

        # Price alignment check
        if self.trend_config.REQUIRE_PRICE_ALIGNMENT:
            long_aligned = df['close'] > ema_fast
            short_aligned = df['close'] < ema_fast
        else:
            long_aligned = True
            short_aligned = True

        # Base signal from crossover
        signal = np.where(
            bullish_cross & long_aligned & (trend_strength > self.trend_config.MIN_TREND_STRENGTH),
            75,
            np.where(
                bearish_cross & short_aligned & (trend_strength > self.trend_config.MIN_TREND_STRENGTH),
                25,
                50
            )
        )

        # Additional confirmation from EMA signal
        signal = signal * 0.7 + (50 + (ema_signal - df['close']) / df['close'] * 10000) * 0.3

        return pd.Series(np.clip(signal, 0, 100), index=df.index)

    def generate_breakout_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Donchian Channel Breakout Strategy

        Signals:
        - LONG: Price breaks above upper channel
        - SHORT: Price breaks below lower channel
        """
        df = df.copy()

        # Donchian channels
        upper_channel = df['high'].rolling(window=self.trend_config.CHANNEL_PERIOD).max()
        lower_channel = df['low'].rolling(window=self.trend_config.CHANNEL_PERIOD).min()
        mid_channel = (upper_channel + lower_channel) / 2

        # Breakout detection
        breaks_upper = df['close'] > upper_channel.shift(1)
        breaks_lower = df['close'] < lower_channel.shift(1)

        # Confirm breakout (next candle stays outside channel)
        if self.trend_config.BREAKOUT_CONFIRMATION:
            breaks_upper = breaks_upper & (df['close'].shift(1) > upper_channel.shift(2))
            breaks_lower = breaks_lower & (df['close'].shift(1) < lower_channel.shift(2))

        # Trend filter (EMA)
        ema_20 = df['close'].ewm(span=20).mean()
        uptrend = df['close'] > ema_20
        downtrend = df['close'] < ema_20

        # Only trade breakouts in trend direction
        long_signal = breaks_upper & uptrend
        short_signal = breaks_lower & downtrend

        # Volatility filter using ATR
        atr = self.atr.calculate(df)
        avg_atr = atr.rolling(window=20).mean()
        vol_ok = (atr / avg_atr).between(0.5, 3.0)

        long_signal = long_signal & vol_ok
        short_signal = short_signal & vol_ok

        # Generate confidence
        signal = np.where(
            long_signal,
            80,
            np.where(
                short_signal,
                20,
                50
            )
        )

        return pd.Series(signal, index=df.index)

    def generate_momentum_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Momentum with Trend Following

        Combines ROC momentum with trend confirmation
        """
        df = df.copy()

        # Multiple timeframe momentum
        roc_short = df['close'].pct_change(self.trend_config.ROC_SHORT) * 100
        roc_long = df['close'].pct_change(self.trend_config.ROC_LONG) * 100

        # Momentum score
        momentum_score = 50 + (roc_short * 5 + roc_long * 2)

        # Trend filter
        ema_20 = df['close'].ewm(span=self.trend_config.EMA_TREND_FILTER).mean()
        trend_up = df['close'] > ema_20
        trend_down = df['close'] < ema_20

        # Volume confirmation
        if self.trend_config.VOLUME_CONFIRM:
            vol_ma = df['volume'].rolling(window=20).mean()
            vol_ok = df['volume'] > vol_ma * self.trend_config.MIN_VOLUME_RATIO
        else:
            vol_ok = True

        # Combined signals
        long_setup = (momentum_score > 50 + self.trend_config.MOMENTUM_THRESHOLD) & trend_up & vol_ok
        short_setup = (momentum_score < 50 - self.trend_config.MOMENTUM_THRESHOLD) & trend_down & vol_ok

        # Base confidence from momentum strength
        confidence = np.where(
            long_setup,
            np.clip(50 + abs(momentum_score - 50), 60, 90),
            np.where(
                short_setup,
                np.clip(50 - abs(momentum_score - 50), 10, 40),
                50
            )
        )

        return pd.Series(confidence, index=df.index)

    def check_exits_with_trailing(self, row: pd.Series) -> List[Trade]:
        """Check exits with trailing stop support"""
        closed_trades = []
        high, low = row['high'], row['low']

        for trade in self.trades:
            if not trade.is_open:
                continue

            exit_triggered = False
            exit_price = None

            # Update trailing stop if configured
            if self.config.USE_TRAILING_STOP:
                if trade.side == Side.LONG:
                    unrealized_pct = (low - trade.entry_price) / trade.entry_price * 100
                    if unrealized_pct > self.config.TRAIL_ACTIVATION_PCT:
                        trail_price = low + (row.get('atr', 1) * self.config.TRAIL_STOP_ATR)
                        trade.sl_price = max(trade.sl_price, trail_price)
                else:
                    unrealized_pct = (trade.entry_price - high) / trade.entry_price * 100
                    if unrealized_pct > self.config.TRAIL_ACTIVATION_PCT:
                        trail_price = high - (row.get('atr', 1) * self.config.TRAIL_STOP_ATR)
                        trade.sl_price = min(trade.sl_price, trail_price)

            # Check exits
            if trade.side == Side.LONG:
                if low <= trade.sl_price:
                    exit_triggered = True
                    exit_price = trade.sl_price
                elif high >= trade.tp_price:
                    exit_triggered = True
                    exit_price = trade.tp_price
            else:
                if high >= trade.sl_price:
                    exit_triggered = True
                    exit_price = trade.sl_price
                elif low <= trade.tp_price:
                    exit_triggered = True
                    exit_price = trade.tp_price

            if exit_triggered:
                trade.exit_time = row.name
                trade.exit_price = exit_price

                if trade.side == Side.LONG:
                    price_diff = exit_price - trade.entry_price
                else:
                    price_diff = trade.entry_price - exit_price

                gross_pnl = price_diff * trade.quantity
                exit_fee = exit_price * trade.quantity * self.config.TAKER_FEE_PCT
                trade.fees_paid = trade.fees_paid + exit_fee
                self.total_fees += exit_fee
                trade.pnl = gross_pnl + trade.maker_rebate - exit_fee
                trade.pnl_pct = (trade.pnl / self.capital) * 100
                self.capital += trade.pnl

                if trade.pnl > 0:
                    self.winning_trades += 1
                else:
                    self.losing_trades += 1

                closed_trades.append(trade)

        return closed_trades

    def run_trend_backtest(self, df: pd.DataFrame, strategy='ema') -> dict:
        """
        Run trend-following backtest

        Args:
            df: DataFrame with OHLCV data
            strategy: 'ema', 'breakout', or 'momentum'
        """
        # Prepare data
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df['atr'] = self.atr.calculate(df)

        # Select signal generator
        signal_generators = {
            'ema': self.generate_ema_signals,
            'breakout': self.generate_breakout_signals,
            'momentum': self.generate_momentum_signals
        }

        if strategy not in signal_generators:
            raise ValueError(f"Unknown strategy: {strategy}. Choose: ema, breakout, momentum")

        # Generate signals
        df['signal'] = signal_generators[strategy](df)

        print(f"Running {strategy.upper()} Trend Following on {self.config.ASSET}")
        print(f"Confidence threshold: {self.trend_config.CONFIDENCE_THRESHOLD}")
        print("-" * 60)

        # Main loop
        for i, (idx, row) in enumerate(df.iterrows()):
            if i < 30:  # Warmup period
                continue

            # Reset daily counter
            current_date = idx.date()
            if self.last_date != current_date:
                self.trades_today = 0
                self.last_date = current_date

            # Check exits first
            if self.config.USE_TRAILING_STOP:
                self.check_exits_with_trailing(row)
            else:
                self.check_exits(row)

            # Check limit orders
            filled_orders = self.check_limit_orders(row)
            for order in filled_orders:
                self.open_trade(order, idx)
                self.last_entry_bar = i
                self.trades_today += 1

            # Check for new signals
            open_trades = sum(1 for t in self.trades if t.is_open)
            pending = len(self.pending_orders)
            cooldown_ok = (i - self.last_entry_bar) >= self.config.TRADE_COOLDOWN_BARS
            can_open = (open_trades + pending) < self.config.MAX_CONCURRENT_POSITIONS
            daily_ok = self.trades_today < self.config.MAX_DAILY_TRADES

            current_signal = row['signal']
            is_long = current_signal >= self.trend_config.CONFIDENCE_THRESHOLD
            is_short = current_signal <= (100 - self.trend_config.CONFIDENCE_THRESHOLD)

            if (is_long or is_short) and can_open and cooldown_ok and daily_ok:
                side = Side.LONG if is_long else Side.SHORT

                # Limit order placement
                mid_price = (row['open'] + row['close']) / 2
                limit_offset = 0.0005

                if side == Side.LONG:
                    limit_price = min(mid_price * (1 - limit_offset), row['close'] * 0.9998)
                    tp_price = limit_price + (row['atr'] * self.config.TP_ATR_MULTIPLIER)
                    sl_price = limit_price - (row['atr'] * self.config.SL_ATR_MULTIPLIER)
                else:
                    limit_price = max(mid_price * (1 + limit_offset), row['close'] * 1.0002)
                    tp_price = limit_price - (row['atr'] * self.config.TP_ATR_MULTIPLIER)
                    sl_price = limit_price + (row['atr'] * self.config.SL_ATR_MULTIPLIER)

                # Position sizing
                margin = self.capital * self.config.RISK_PER_TRADE_PCT
                notional = margin * self.config.LEVERAGE
                quantity = notional / limit_price

                self.place_limit_order(idx, side, limit_price, quantity, tp_price, sl_price)

            self.update_equity_curve(idx)

        # Close remaining trades
        if self.trades and any(t.is_open for t in self.trades):
            last_row = df.iloc[-1]
            last_price = last_row['close']
            for trade in self.trades:
                if trade.is_open:
                    trade.exit_time = df.index[-1]
                    trade.exit_price = last_price

                    if trade.side == Side.LONG:
                        price_diff = last_price - trade.entry_price
                    else:
                        price_diff = trade.entry_price - last_price

                    gross_pnl = price_diff * trade.quantity
                    exit_fee = last_price * trade.quantity * self.config.TAKER_FEE_PCT
                    trade.fees_paid = trade.fees_paid + exit_fee
                    trade.pnl = gross_pnl + trade.maker_rebate - exit_fee
                    trade.pnl_pct = (trade.pnl / self.capital) * 100
                    self.capital += trade.pnl

        return self.generate_results()
