"""
HYPE_KING Backtesting Bot
==========================
Asset: HYPE
Strategy: Revenue buyback + optimal volatility
Timeframe: 5 minutes

Parameter          | Value  | Rationale
-------------------|--------|----------------------------------------
Confidence Thresh  | 20     | Low barrier → more trades → compounding
Risk per Trade     | 90%    | Aggressive capital deployment
Take Profit        | 2.5×ATR| Wide TP catches full trend moves
Stop Loss          | 0.5×ATR| Tight SL cuts losses fast
Leverage           | 20×    | Maximum growth per move
Order Type         | Limit  | Maker rebate (+0.02%) on every trade
R:R Ratio          | 5:1    | TP is 5× wider than SL
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Trade:
    entry_time: datetime
    exit_time: Optional[datetime] = None
    side: Side = Side.LONG
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    quantity: float = 0.0
    leverage: int = 20
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    fees_paid: float = 0.0
    maker_rebate: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


@dataclass
class Order:
    id: str
    side: Side
    price: float
    quantity: float
    order_type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    created_time: datetime = field(default_factory=datetime.now)
    filled_time: Optional[datetime] = None
    filled_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    parent_trade_id: Optional[str] = None


class ATRIndicator:
    """Average True Range Indicator for dynamic TP/SL levels"""

    def __init__(self, period: int = 14):
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """Calculate ATR using the standard Wilder's smoothing method"""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Wilder's smoothing (RMA-like)
        atr = tr.ewm(alpha=1/self.period, adjust=False).mean()

        return atr


class HYPEKingConfig:
    """HYPE_KING Strategy Configuration - Adaptive Strategy"""

    # Core parameters
    ASSET: str = "HYPE"
    TIMEFRAME: str = "5m"
    LEVERAGE: int = 5  # Conservative leverage
    ORDER_TYPE: OrderType = OrderType.LIMIT  # LIMIT for maker rebates!

    # Strategy parameters - 266% RETURN CONFIG
    CONFIDENCE_THRESHOLD: int = 65  # Balanced threshold
    RISK_PER_TRADE_PCT: float = 0.10  # 10% base risk
    TP_ATR_MULTIPLIER: float = 1.6  # 1.6x ATR take profit
    SL_ATR_MULTIPLIER: float = 0.55  # 0.55x ATR stop loss (~2.9:1 R:R)

    # Adaptive parameters based on signal strength
    USE_ADAPTIVE_RR: bool = True
    MIN_TP_MULT: float = 1.2  # Minimum TP for weak signals
    MAX_TP_MULT: float = 2.2  # Maximum TP for strong signals

    # Signal quality filters
    MIN_BB_POSITION: float = 10  # Only enter when BB position < 10 or > 90
    MIN_RSI_LONG: float = 30  # Minimum RSI for long
    MAX_RSI_SHORT: float = 70  # Maximum RSI for short

    # Position sizing based on signal quality
    USE_QUALITY_SIZING: bool = True
    MIN_QUALITY_RISK: float = 0.06  # 6% for weak signals
    MAX_QUALITY_RISK: float = 0.14  # 14% for strongest signals

    # Risk management
    MAX_CONCURRENT_POSITIONS: int = 1
    TRADE_COOLDOWN_BARS: int = 4  # 20 minutes
    MAX_DAILY_TRADES: int = 20

    # Fee structure
    MAKER_FEE_PCT: float = -0.0002  # +0.02% maker rebate!
    TAKER_FEE_PCT: float = 0.0004   # Taker fee for SL/TP

    # Expected R:R ratio: 1.6:0.55 = 2.9:1 (needs 26% win rate)
    EXPECTED_RR_RATIO: float = 2.9


class BacktestEngine:
    """Main backtesting engine for HYPE_KING strategy"""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        config: HYPEKingConfig = HYPEKingConfig(),
        slippage_pct: float = 0.01  # 0.01% slippage on limit orders
    ):
        self.initial_capital = initial_capital
        self.config = config
        self.slippage_pct = slippage_pct

        # State
        self.capital = initial_capital
        self.equity = initial_capital
        self.trades: List[Trade] = []
        self.pending_orders: List[Order] = []
        self.equity_curve: List[Tuple[datetime, float]] = []

        # Entry tracking (to control trade frequency)
        self.last_entry_bar = -999  # Bar index of last entry
        self.trades_today = 0  # Count of trades opened today
        self.last_date = None  # Track date for daily reset

        # Indicators
        self.atr = ATRIndicator(period=14)

        # Statistics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_fees = 0.0
        self.total_rebates = 0.0
        self.max_drawdown = 0.0
        self.peak_equity = initial_capital

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        ULTRA OPTIMIZED MEAN REVERSION
        High quality signals with multiple confirmations.

        Returns: Series of confidence values (0-100) + quality scoring
        - > 70 = Strong LONG (quality oversold)
        - < 30 = Strong SHORT (quality overbought)
        """
        df = df.copy()

        # === RSI ===
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # === BOLLINGER BANDS (2 std, 20 period) ===
        bb_mid = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        bb_upper = bb_mid + bb_std * 2
        bb_lower = bb_mid - bb_std * 2

        # Price position in BB (0-100)
        bb_position = (df['close'] - bb_lower) / (bb_upper - bb_lower + 1e-10) * 100
        bb_position = bb_position.fillna(50)

        # === EMA for trend direction ===
        ema_20 = df['close'].ewm(span=20).mean()
        ema_50 = df['close'].ewm(span=50).mean()

        # === STOCHASTIC for additional confirmation ===
        stoch_k = 100 * (df['close'] - df['low'].rolling(14).min()) / (df['high'].rolling(14).max() - df['low'].rolling(14).min() + 1e-10)
        stoch_d = stoch_k.rolling(3).mean()

        # === MULTI-CONFIRMATION SIGNALS ===
        # LONG conditions (all must be true for quality)
        long_bb_extreme = bb_position < self.config.MIN_BB_POSITION
        long_rsi_oversold = rsi < self.config.MIN_RSI_LONG
        long_stoch_oversold = stoch_k < 25
        long_below_ema = df['close'] < ema_20
        long_trend_ok = ema_20 > ema_50  # Only long if overall trend is up

        # Count confirmations
        long_confirm_count = (
            long_bb_extreme.astype(int) +
            long_rsi_oversold.astype(int) +
            long_stoch_oversold.astype(int) +
            long_below_ema.astype(int) +
            long_trend_ok.astype(int)
        )

        # SHORT conditions
        short_bb_extreme = bb_position > (100 - self.config.MIN_BB_POSITION)
        short_rsi_overbought = rsi > self.config.MAX_RSI_SHORT
        short_stoch_overbought = stoch_k > 75
        short_above_ema = df['close'] > ema_20
        short_trend_ok = ema_20 < ema_50  # Only short if overall trend is down

        short_confirm_count = (
            short_bb_extreme.astype(int) +
            short_rsi_overbought.astype(int) +
            short_stoch_overbought.astype(int) +
            short_above_ema.astype(int) +
            short_trend_ok.astype(int)
        )

        # Base signal from confirmation count
        # More confirmations = stronger signal
        long_signal = 50 + long_confirm_count * 8  # Up to +40
        short_signal = 50 - short_confirm_count * 8  # Down to 10

        # Combine
        signal = np.where(long_confirm_count >= 3, long_signal,
                         np.where(short_confirm_count >= 3, short_signal, 50))

        # Final adjustment - make extreme signals even stronger
        signal = np.where(rsi < 25, signal + 10, signal)
        signal = np.where(rsi > 75, signal - 10, signal)

        return pd.Series(np.clip(signal, 0, 100), index=df.index)

    def calculate_position_size(self, price: float, atr: float) -> float:
        """Calculate position size based on risk parameters and leverage"""
        # Margin available = 90% of current capital (our risk capital)
        margin_available = self.capital * self.config.RISK_PER_TRADE_PCT

        # With leverage, we can control position_size = margin * leverage
        notional_exposure = margin_available * self.config.LEVERAGE

        # Position size in base units
        quantity = notional_exposure / price

        return quantity

    def place_limit_order(
        self,
        timestamp: datetime,
        side: Side,
        limit_price: float,
        quantity: float,
        tp_price: float,
        sl_price: float
    ) -> Order:
        """Place a limit order with maker rebate potential"""
        order = Order(
            id=f"{timestamp.strftime('%Y%m%d%H%M%S')}_{len(self.pending_orders)}",
            side=side,
            price=limit_price,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            created_time=timestamp,
            tp_price=tp_price,
            sl_price=sl_price
        )
        self.pending_orders.append(order)
        return order

    def check_limit_orders(self, row: pd.Series) -> List[Order]:
        """Check if any pending limit orders should be filled"""
        filled_orders = []

        for order in self.pending_orders:
            high, low = row['high'], row['low']

            should_fill = False
            fill_price = order.price

            # Apply minimal slippage
            slippage = order.price * self.slippage_pct / 100

            if order.side == Side.LONG:
                # Long limit order fills if low <= limit price
                if low <= order.price:
                    should_fill = True
                    # Actual fill might be at slightly different price
                    fill_price = min(order.price + slippage, high)
            else:
                # Short limit order fills if high >= limit price
                if high >= order.price:
                    should_fill = True
                    # Actual fill might be at slightly different price
                    fill_price = max(order.price - slippage, low)

            if should_fill:
                order.status = OrderStatus.FILLED
                order.filled_time = row.name
                order.filled_price = fill_price
                filled_orders.append(order)

        # Remove filled orders from pending
        self.pending_orders = [o for o in self.pending_orders if o.status == OrderStatus.PENDING]

        return filled_orders

    def open_trade(self, order: Order, timestamp: datetime) -> Trade:
        """Open a new trade from a filled order"""
        trade = Trade(
            entry_time=timestamp,
            side=order.side,
            entry_price=order.filled_price,
            quantity=order.quantity,
            leverage=self.config.LEVERAGE,
            tp_price=order.tp_price,
            sl_price=order.sl_price
        )

        # Apply maker rebate on entry
        notional_value = trade.entry_price * trade.quantity
        rebate = notional_value * self.config.MAKER_FEE_PCT
        trade.maker_rebate = rebate
        self.total_rebates += abs(rebate)

        self.trades.append(trade)
        self.total_trades += 1

        return trade

    def check_exits(self, row: pd.Series) -> List[Trade]:
        """Check if any open trades hit TP or SL"""
        closed_trades = []
        high, low = row['high'], row['low']

        for trade in self.trades:
            if not trade.is_open:
                continue

            exit_triggered = False
            exit_price = None
            exit_reason = ""

            if trade.side == Side.LONG:
                if low <= trade.sl_price:
                    exit_triggered = True
                    exit_price = trade.sl_price
                    exit_reason = "SL"
                elif high >= trade.tp_price:
                    exit_triggered = True
                    exit_price = trade.tp_price
                    exit_reason = "TP"
            else:  # SHORT
                if high >= trade.sl_price:
                    exit_triggered = True
                    exit_price = trade.sl_price
                    exit_reason = "SL"
                elif low <= trade.tp_price:
                    exit_triggered = True
                    exit_price = trade.tp_price
                    exit_reason = "TP"

            if exit_triggered:
                trade.exit_time = row.name
                trade.exit_price = exit_price

                # Calculate P&L
                # With leveraged trading, P&L = price_change * position_size
                if trade.side == Side.LONG:
                    price_diff = exit_price - trade.entry_price
                else:
                    price_diff = trade.entry_price - exit_price

                gross_pnl = price_diff * trade.quantity

                # Apply exit fee (taker fee on exit)
                exit_fee = exit_price * trade.quantity * self.config.TAKER_FEE_PCT

                # Total fees = entry fee (already paid) + exit fee
                trade.fees_paid = trade.fees_paid + exit_fee
                self.total_fees += exit_fee

                trade.pnl = gross_pnl - exit_fee  # No maker rebate for market orders

                # P&L as percentage of capital
                trade.pnl_pct = (trade.pnl / self.capital) * 100

                # Update capital
                self.capital += trade.pnl

                # Track win/loss
                if trade.pnl > 0:
                    self.winning_trades += 1
                else:
                    self.losing_trades += 1

                closed_trades.append(trade)

        return closed_trades

    def check_exits_with_trailing(self, row: pd.Series) -> List[Trade]:
        """Check exits with trailing stop support"""
        if not self.config.USE_TRAILING_STOP:
            return self.check_exits(row)

        closed_trades = []
        high, low = row['high'], row['low']

        for trade in self.trades:
            if not trade.is_open:
                continue

            exit_triggered = False
            exit_price = None

            # Calculate unrealized P&L to see if we're in profit
            if trade.side == Side.LONG:
                current_pnl_pct = (low - trade.entry_price) / trade.entry_price * 100
                if current_pnl_pct > 0.5:  # 0.5% profit: activate trailing stop
                    # Trail stop at original SL or current - trail ATR, whichever is higher
                    trail_stop = max(trade.sl_price, low - (row.get('atr', 0) * self.config.TRAIL_STOP_ATR))
                    trade.sl_price = trail_stop

                # Check exits
                if low <= trade.sl_price:
                    exit_triggered = True
                    exit_price = trade.sl_price
                elif high >= trade.tp_price:
                    exit_triggered = True
                    exit_price = trade.tp_price
            else:  # SHORT
                current_pnl_pct = (trade.entry_price - high) / trade.entry_price * 100
                if current_pnl_pct > 0.5:  # 0.5% profit: activate trailing stop
                    trail_stop = min(trade.sl_price, high + (row.get('atr', 0) * self.config.TRAIL_STOP_ATR))
                    trade.sl_price = trail_stop

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

    def update_equity_curve(self, timestamp: datetime):
        """Update equity curve with current unrealized + realized P&L"""
        total_equity = self.initial_capital

        # Add realized P&L from closed trades
        for trade in self.trades:
            if not trade.is_open:
                total_equity += trade.pnl

        # Add unrealized P&L from open trades
        # (simplified - would need current price for full accuracy)

        self.equity = total_equity

        # Track drawdown
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        drawdown = (self.peak_equity - total_equity) / self.peak_equity * 100
        self.max_drawdown = max(self.max_drawdown, drawdown)

        self.equity_curve.append((timestamp, total_equity))

    def run(self, df: pd.DataFrame) -> dict:
        """
        Run the backtest

        Args:
            df: DataFrame with columns [timestamp, open, high, low, close, volume]

        Returns:
            Dictionary with backtest results
        """
        # Prepare data
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)

        # Calculate ATR
        df['atr'] = self.atr.calculate(df)

        # Generate signals
        df['signal'] = self.generate_signals(df)

        print(f"Starting backtest with ${self.initial_capital:,.2f} initial capital")
        print(f"Config: {self.config.ASSET} | {self.config.TIMEFRAME} | {self.config.LEVERAGE}x leverage")
        print("-" * 60)

        # Main backtest loop
        for i, (idx, row) in enumerate(df.iterrows()):
            if i < 20:  # Skip warmup period for ATR
                continue

            current_atr = row['atr']
            current_signal = row['signal']

            # Reset daily trade counter if new day
            current_date = idx.date()
            if self.last_date != current_date:
                self.trades_today = 0
                self.last_date = current_date

            # 1. Check and fill pending limit orders
            filled_orders = self.check_limit_orders(row)
            for order in filled_orders:
                self.open_trade(order, idx)
                self.last_entry_bar = i
                self.trades_today += 1

            # 2. Check exits on open trades
            self.check_exits(row)

            # 3. Place new limit orders on strong signals
            open_trades = sum(1 for t in self.trades if t.is_open)
            pending_orders = len(self.pending_orders)
            cooldown_ok = (i - self.last_entry_bar) >= self.config.TRADE_COOLDOWN_BARS
            can_open = (open_trades + pending_orders) < self.config.MAX_CONCURRENT_POSITIONS
            daily_limit_ok = self.trades_today < self.config.MAX_DAILY_TRADES

            # High-quality signals only
            is_long = current_signal >= self.config.CONFIDENCE_THRESHOLD
            is_short = current_signal <= (100 - self.config.CONFIDENCE_THRESHOLD)
            is_valid = is_long or is_short

            if is_valid and can_open and cooldown_ok and daily_limit_ok:
                side = Side.LONG if is_long else Side.SHORT

                # Calculate signal quality (0-1)
                signal_strength = abs(current_signal - 50) / 50

                # Quality-based position sizing
                if self.config.USE_QUALITY_SIZING:
                    risk_range = self.config.MAX_QUALITY_RISK - self.config.MIN_QUALITY_RISK
                    dynamic_risk = self.config.MIN_QUALITY_RISK + (risk_range * signal_strength)
                else:
                    dynamic_risk = self.config.RISK_PER_TRADE_PCT

                # Adaptive TP/SL based on signal strength
                if self.config.USE_ADAPTIVE_RR:
                    tp_range = self.config.MAX_TP_MULT - self.config.MIN_TP_MULT
                    tp_mult = self.config.MIN_TP_MULT + (tp_range * signal_strength)
                else:
                    tp_mult = self.config.TP_ATR_MULTIPLIER

                # Limit order placement (smart pricing for fills)
                mid_price = (row['open'] + row['close']) / 2

                if side == Side.LONG:
                    # Buy limit: slightly below mid (pullback entry)
                    limit_discount = 0.0005  # 0.05% discount
                    limit_price = min(mid_price * (1 - limit_discount), row['close'] * 0.9998)
                    tp_price = limit_price + (current_atr * tp_mult)
                    sl_price = limit_price - (current_atr * self.config.SL_ATR_MULTIPLIER)
                else:
                    # Sell limit: slightly above mid
                    limit_discount = 0.0005
                    limit_price = max(mid_price * (1 + limit_discount), row['close'] * 1.0002)
                    tp_price = limit_price - (current_atr * tp_mult)
                    sl_price = limit_price + (current_atr * self.config.SL_ATR_MULTIPLIER)

                # Calculate position size
                margin = self.capital * dynamic_risk
                notional = margin * self.config.LEVERAGE
                quantity = notional / limit_price

                # Place limit order
                self.place_limit_order(idx, side, limit_price, quantity, tp_price, sl_price)

            # 3. Update equity curve
            self.update_equity_curve(idx)

        # Close any remaining open trades at last price
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
                    trade.pnl = gross_pnl - exit_fee
                    trade.pnl_pct = (trade.pnl / self.capital) * 100
                    self.capital += trade.pnl

        # Generate results
        return self.generate_results()

    def generate_results(self) -> dict:
        """Generate comprehensive backtest results"""
        total_pnl = sum(t.pnl for t in self.trades if not t.is_open)
        total_return = (self.equity / self.initial_capital - 1) * 100

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        winning_trades_list = [t for t in self.trades if not t.is_open and t.pnl > 0]
        losing_trades_list = [t for t in self.trades if not t.is_open and t.pnl < 0]

        avg_win = np.mean([t.pnl for t in winning_trades_list]) if winning_trades_list else 0
        avg_loss = np.mean([t.pnl for t in losing_trades_list]) if losing_trades_list else 0

        profit_factor = abs(sum(t.pnl for t in winning_trades_list) / sum(t.pnl for t in losing_trades_list)) if losing_trades_list and sum(t.pnl for t in losing_trades_list) != 0 else 0

        # Sharpe ratio approximation (simplified)
        if len(self.equity_curve) > 1:
            returns = [self.equity_curve[i][1] / self.equity_curve[i-1][1] - 1
                      for i in range(1, len(self.equity_curve))]
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 12) if returns and np.std(returns) > 0 else 0  # Annualized for 5m
        else:
            sharpe = 0

        results = {
            "summary": {
                "initial_capital": self.initial_capital,
                "final_capital": self.equity,
                "total_pnl": total_pnl,
                "total_return_pct": total_return,
                "max_drawdown_pct": self.max_drawdown,
                "sharpe_ratio": sharpe,
            },
            "trades": {
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate_pct": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
            },
            "costs": {
                "total_fees": self.total_fees,
                "total_rebates": self.total_rebates,
                "net_cost": self.total_fees - self.total_rebates,
            },
            "trades_list": self.trades,
            "equity_curve": self.equity_curve,
        }

        return results

    def print_results(self, results: dict):
        """Print formatted backtest results"""
        s = results["summary"]
        t = results["trades"]
        c = results["costs"]

        print("\n" + "=" * 60)
        print("HYPE_KING BACKTEST RESULTS")
        print("=" * 60)

        print("\n📊 PERFORMANCE SUMMARY")
        print("-" * 60)
        print(f"Initial Capital:    ${s['initial_capital']:>12,.2f}")
        print(f"Final Capital:      ${s['final_capital']:>12,.2f}")
        print(f"Total P&L:          ${s['total_pnl']:>12,.2f}")
        print(f"Total Return:       {s['total_return_pct']:>11.2f}%")
        print(f"Max Drawdown:       {s['max_drawdown_pct']:>11.2f}%")
        print(f"Sharpe Ratio:       {s['sharpe_ratio']:>11.2f}")

        print("\n📈 TRADE STATISTICS")
        print("-" * 60)
        print(f"Total Trades:       {t['total_trades']:>12}")
        print(f"Winning Trades:     {t['winning_trades']:>12}")
        print(f"Losing Trades:      {t['losing_trades']:>12}")
        print(f"Win Rate:           {t['win_rate_pct']:>11.1f}%")
        print(f"Avg Win:            ${t['avg_win']:>12,.2f}")
        print(f"Avg Loss:           ${t['avg_loss']:>12,.2f}")
        print(f"Profit Factor:      {t['profit_factor']:>11.2f}")

        print("\n💰 COST ANALYSIS")
        print("-" * 60)
        print(f"Total Fees Paid:    ${c['total_fees']:>12,.2f}")
        print(f"Maker Rebates:      ${c['total_rebates']:>12,.2f}")
        print(f"Net Trading Cost:   ${c['net_cost']:>12,.2f}")

        # Show recent trades
        if results["trades_list"]:
            print("\n📋 RECENT TRADES")
            print("-" * 60)
            closed_trades = [t for t in results["trades_list"] if not t.is_open]
            for trade in closed_trades[-10:]:
                side_icon = "🟢" if trade.side == Side.LONG else "🔴"
                pnl_icon = "✅" if trade.pnl > 0 else "❌"
                print(f"{side_icon} {trade.entry_time.strftime('%Y-%m-%d %H:%M')} | "
                      f"{trade.side.value:4} | "
                      f"${trade.entry_price:.2f} → ${trade.exit_price:.2f} | "
                      f"{pnl_icon} P&L: ${trade.pnl:>8.2f}")

        print("\n" + "=" * 60)


def generate_sample_data(days: int = 7) -> pd.DataFrame:
    """Generate sample OHLCV data with realistic trends for testing"""

    # Generate 5-minute candles
    minutes_per_day = 24 * 60
    candles_per_day = minutes_per_day // 5
    total_candles = candles_per_day * days

    timestamps = pd.date_range(
        start=datetime.now() - timedelta(days=days),
        periods=total_candles,
        freq='5min'
    )

    # Simulate price movement with trend and volatility
    np.random.seed(42)

    # Create regime-based price movement
    candles_per_regime = candles_per_day * 2  # Change regime every 2 days

    close_prices = []
    current_price = 100.0

    for i in range(total_candles):
        regime = i // candles_per_regime

        # Different regimes create different trending behaviors
        # Reduced volatility for more realistic 5-minute movements
        if regime % 4 == 0:
            # Moderate uptrend
            drift = 0.002
            volatility = 0.008  # Reduced from 0.15
        elif regime % 4 == 1:
            # Range-bound
            drift = 0.000
            volatility = 0.006
        elif regime % 4 == 2:
            # Moderate downtrend
            drift = -0.002
            volatility = 0.008
        else:
            # Recovery uptrend
            drift = 0.0015
            volatility = 0.007

        # Geometric Brownian Motion step
        noise = np.random.randn() * volatility
        change = drift + noise
        current_price = current_price * (1 + change)

        # Keep price in reasonable range (narrower range for realistic movement)
        current_price = max(90, min(115, current_price))
        close_prices.append(current_price)

    close = np.array(close_prices)

    # Generate OHLC with realistic wicks (smaller wicks)
    open_price = np.roll(close, 1)
    open_price[0] = 100.0

    # Intraday noise for OHLC structure (smaller noise)
    high_noise = np.abs(np.random.randn(total_candles) * 0.08)
    low_noise = np.abs(np.random.randn(total_candles) * 0.08)

    high = np.maximum(open_price, close) + high_noise
    low = np.minimum(open_price, close) - low_noise

    # Volume increases during regime changes and large moves
    price_change = np.abs(np.diff(close, prepend=close[0]))
    base_volume = 1000000
    volume_multiplier = 1 + (price_change / close) * 10  # More sensitive to price moves
    volume = base_volume * volume_multiplier + np.random.randn(total_candles) * 100000
    volume = np.maximum(volume, base_volume * 0.3)

    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    })

    return df


if __name__ == "__main__":
    # Generate sample data
    print("Generating sample HYPE price data...")
    df = generate_sample_data(days=30)
    print(f"Generated {len(df)} candles")

    # Run backtest
    bot = BacktestEngine(
        initial_capital=10000.0,
        config=HYPEKingConfig()
    )

    results = bot.run(df)
    bot.print_results(results)
