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
    ORDER_TYPE: OrderType = OrderType.MARKET  # Market orders for reliability

    # Strategy parameters - MEAN REVERSION
    CONFIDENCE_THRESHOLD: int = 62  # Signal threshold
    RISK_PER_TRADE_PCT: float = 0.08  # 8% risk per trade
    TP_ATR_MULTIPLIER: float = 1.5  # Tighter TP for mean reversion
    SL_ATR_MULTIPLIER: float = 0.6  # 0.6x ATR stop loss (2.5:1 R:R)

    # Dynamic position sizing based on signal strength
    USE_DYNAMIC_SIZING: bool = True
    MIN_RISK_PCT: float = 0.04  # Minimum risk
    MAX_RISK_PCT: float = 0.12  # Maximum risk

    # Risk management
    MAX_CONCURRENT_POSITIONS: int = 1
    TRADE_COOLDOWN_BARS: int = 15  # 75 minutes cooldown
    MAX_DAILY_TRADES: int = 10

    # Fee structure
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0004

    # Expected R:R ratio: 1.5:0.6 = 2.5:1 (needs 29% win rate)
    EXPECTED_RR_RATIO: float = 2.5


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
        MEAN REVERSION STRATEGY
        Works well in choppy, oscillating markets.

        - Buy when price is oversold below EMA
        - Sell when price is overbought above EMA
        - Uses RSI for entry timing
        - Uses Bollinger Bands for extremes

        Returns: Series of confidence values (0-100)
        - > 60 = LONG (oversold/buy)
        - < 40 = SHORT (overbought/sell)
        """
        df = df.copy()

        # === RSI (for OB/OS detection) ===
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # === BOLLINGER BANDS ===
        bb_mid = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        bb_upper = bb_mid + bb_std * 2
        bb_lower = bb_mid - bb_std * 2

        # Price position in BB (0-100, where 50=middle)
        bb_position = (df['close'] - bb_lower) / (bb_upper - bb_lower) * 100
        bb_position = bb_position.fillna(50)

        # === EMA for trend filter ===
        ema_20 = df['close'].ewm(span=20).mean()

        # === SIGNAL LOGIC ===
        # LONG: Oversold conditions
        long_setup = (rsi < 35) | (bb_position < 10)  # Strongly oversold
        long_confirm = df['close'] < ema_20  # Below EMA (good buy zone)

        # SHORT: Overbought conditions
        short_setup = (rsi > 65) | (bb_position > 90)  # Strongly overbought
        short_confirm = df['close'] > ema_20  # Above EMA (good sell zone)

        # Base signal
        signal = np.where(long_setup & long_confirm, 75,
                         np.where(short_setup & short_confirm, 25, 50))

        # Fine-tune with RSI
        signal = signal - (rsi - 50) * 0.3  # RSI pulls signal back to mean

        # Volume spike can trigger earlier entries
        vol_ma = df['volume'].rolling(window=20).mean()
        vol_spike = df['volume'] > vol_ma * 1.5
        signal = np.where(vol_spike & (rsi < 40), signal + 5, signal)
        signal = np.where(vol_spike & (rsi > 60), signal - 5, signal)

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

            # 1. Check exits on open trades
            self.check_exits(row)

            # 2. Check for new trade signals
            open_trades = sum(1 for t in self.trades if t.is_open)
            cooldown_ok = (i - self.last_entry_bar) >= self.config.TRADE_COOLDOWN_BARS
            can_open = open_trades < self.config.MAX_CONCURRENT_POSITIONS
            daily_limit_ok = self.trades_today < self.config.MAX_DAILY_TRADES

            # Signal checks
            is_long = current_signal > self.config.CONFIDENCE_THRESHOLD
            is_short = current_signal < (100 - self.config.CONFIDENCE_THRESHOLD)
            is_valid = is_long or is_short

            if is_valid and can_open and cooldown_ok and daily_limit_ok:
                side = Side.LONG if is_long else Side.SHORT

                # Dynamic position sizing based on signal strength
                signal_strength = abs(current_signal - 50) / 50  # 0 to 1
                if self.config.USE_DYNAMIC_SIZING:
                    risk_range = self.config.MAX_RISK_PCT - self.config.MIN_RISK_PCT
                    dynamic_risk = self.config.MIN_RISK_PCT + (risk_range * signal_strength)
                else:
                    dynamic_risk = self.config.RISK_PER_TRADE_PCT

                # Market order entry
                slippage = 0.00025
                if side == Side.LONG:
                    entry = row['close'] * (1 + slippage)
                    tp = entry + (current_atr * self.config.TP_ATR_MULTIPLIER)
                    sl = entry - (current_atr * self.config.SL_ATR_MULTIPLIER)
                else:
                    entry = row['close'] * (1 - slippage)
                    tp = entry - (current_atr * self.config.TP_ATR_MULTIPLIER)
                    sl = entry + (current_atr * self.config.SL_ATR_MULTIPLIER)

                # Calculate position size with dynamic risk
                margin = self.capital * dynamic_risk
                notional = margin * self.config.LEVERAGE
                qty = notional / entry

                # Create trade
                trade = Trade(
                    entry_time=idx,
                    side=side,
                    entry_price=entry,
                    quantity=qty,
                    leverage=self.config.LEVERAGE,
                    tp_price=tp,
                    sl_price=sl
                )

                entry_fee = entry * qty * self.config.TAKER_FEE_PCT
                trade.fees_paid = entry_fee
                self.total_fees += entry_fee

                self.trades.append(trade)
                self.total_trades += 1
                self.last_entry_bar = i
                self.trades_today += 1

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
