"""
Strategy engine for signal generation
"""

import logging
from typing import Optional, Dict

import numpy as np
import pandas as pd

from ..core.config import BotConfig, Side

logger = logging.getLogger(__name__)


class StrategyEngine:
    """
    Signal generation using Ultra-Optimized Momentum strategy.

    Generates LONG/SHORT signals based on:
    - Rate of Change (ROC) momentum
    - EMA trend filter
    - Volume confirmation
    - ATR-based take profit and stop loss
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.candles: pd.DataFrame = pd.DataFrame()
        self.max_candles = 200

        # Performance tracking
        self._signals_generated = 0
        self._last_signal_time = None

    def update_candle(self, candle: Dict):
        """
        Update internal candle storage with new data

        Args:
            candle: Dictionary with timestamp, open, high, low, close, volume
        """
        new_row = pd.DataFrame([candle])
        new_row.set_index("timestamp", inplace=True)

        self.candles = pd.concat([self.candles, new_row])

        # Keep only recent candles
        if len(self.candles) > self.max_candles:
            self.candles = self.candles.iloc[-self.max_candles:]

    def generate_signal(self, capital: float = None) -> Optional[Dict]:
        """
        Generate trading signal based on current market conditions

        Args:
            capital: Current capital for position sizing (uses config if None)

        Returns:
            Dict with signal info or None if no signal:
            {
                'action': Side.LONG or Side.SHORT,
                'confidence': 0-100,
                'entry_price': float,
                'tp_price': float,
                'sl_price': float,
                'quantity': float,
                'atr': float
            }
        """
        if len(self.candles) < 30:
            return None

        df = self.candles.copy()

        # Calculate indicators
        signal = self._calculate_indicators_and_signal(df, capital)

        if signal:
            self._signals_generated += 1
            self._last_signal_time = pd.Timestamp.now()

        return signal

    def _calculate_indicators_and_signal(
        self, df: pd.DataFrame, capital: float = None
    ) -> Optional[Dict]:
        """Calculate indicators and generate signal"""

        # Rate of Change
        roc_short = df["close"].pct_change(self.config.ROC_SHORT) * 100
        roc_long = df["close"].pct_change(self.config.ROC_LONG) * 100

        # Momentum score
        momentum_score = 50 + (roc_short * 5 + roc_long * 2)

        # EMA trend filter
        ema_20 = df["close"].ewm(span=self.config.EMA_TREND_FILTER).mean()
        trend_up = df["close"].iloc[-1] > ema_20.iloc[-1]
        trend_down = df["close"].iloc[-1] < ema_20.iloc[-1]

        # Volume confirmation
        vol_ma = df["volume"].rolling(window=20).mean()
        vol_ok = df["volume"].iloc[-1] > vol_ma.iloc[-1] * 1.2

        # ATR for TP/SL
        atr = self._calculate_atr(df)
        current_price = df["close"].iloc[-1]

        # Determine capital for position sizing
        if capital is None:
            capital = (
                self.config.PAPER_CAPITAL
                if self.config.PAPER_TRADING
                else 10000.0
            )

        # Generate signals
        long_setup = (
            momentum_score.iloc[-1] > 50 + self.config.MOMENTUM_THRESHOLD
            and trend_up
            and vol_ok
        )

        short_setup = (
            momentum_score.iloc[-1] < 50 - self.config.MOMENTUM_THRESHOLD
            and trend_down
            and vol_ok
        )

        signal = None

        if long_setup:
            tp_price = current_price + (atr * self.config.TP_ATR_MULTIPLIER)
            sl_price = current_price - (atr * self.config.SL_ATR_MULTIPLIER)

            quantity = self._calculate_position_size(capital, current_price, atr)

            signal = {
                "action": Side.LONG,
                "confidence": min(100, momentum_score.iloc[-1]),
                "entry_price": current_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "quantity": quantity,
                "atr": atr,
            }

        elif short_setup:
            tp_price = current_price - (atr * self.config.TP_ATR_MULTIPLIER)
            sl_price = current_price + (atr * self.config.SL_ATR_MULTIPLIER)

            quantity = self._calculate_position_size(capital, current_price, atr)

            signal = {
                "action": Side.SHORT,
                "confidence": min(100, 100 - momentum_score.iloc[-1]),
                "entry_price": current_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "quantity": quantity,
                "atr": atr,
            }

        if signal:
            logger.info(
                f"Signal: {signal['action'].value} @ {signal['entry_price']:.4f} | "
                f"TP: {signal['tp_price']:.4f} | SL: {signal['sl_price']:.4f} | "
                f"Qty: {signal['quantity']:.2f} | Conf: {signal['confidence']:.1f}"
            )

        return signal

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range"""
        df = df.copy()

        df["tr"] = df["high"] - df["low"]
        df["prev_close"] = df["close"].shift(1)
        df["tr1"] = abs(df["high"] - df["prev_close"])
        df["tr2"] = abs(df["low"] - df["prev_close"])
        df["true_range"] = df[["tr", "tr1", "tr2"]].max(axis=1)

        atr = df["true_range"].rolling(window=period).mean().iloc[-1]
        return float(atr) if not np.isnan(atr) else 0.001

    def _calculate_position_size(
        self, capital: float, price: float, atr: float
    ) -> float:
        """
        Calculate position size based on risk parameters

        Uses the formula: margin = capital * risk_pct, notional = margin * leverage
        """
        margin = capital * self.config.RISK_PER_TRADE_PCT
        notional = margin * self.config.LEVERAGE
        quantity = notional / price

        return float(quantity)

    def get_signals_count(self) -> int:
        """Get total number of signals generated"""
        return self._signals_generated

    def reset(self):
        """Reset strategy state"""
        self.candles = pd.DataFrame()
        self._signals_generated = 0
        self._last_signal_time = None


class RiskManager:
    """
    Risk management and position sizing logic

    Handles:
    - Position size calculation
    - Portfolio risk limits
    - Daily loss limits
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.starting_capital = (
            config.PAPER_CAPITAL if config.PAPER_TRADING else 10000.0
        )

    def can_open_position(
        self,
        open_positions: int,
        daily_trades: int,
        daily_pnl: float,
        circuit_breaker_active: bool = False,
    ) -> tuple[bool, str]:
        """
        Check if new position can be opened

        Returns:
            Tuple of (can_open, reason)
        """
        if circuit_breaker_active:
            return False, "Circuit breaker active"

        if open_positions >= self.config.MAX_POSITIONS:
            return False, f"Max positions ({self.config.MAX_POSITIONS}) reached"

        if daily_trades >= self.config.MAX_DAILY_TRADES:
            return False, f"Max daily trades ({self.config.MAX_DAILY_TRADES}) reached"

        if daily_pnl < -self.config.MAX_DAILY_LOSS_PCT * self.starting_capital:
            return False, f"Daily loss limit ({self.config.MAX_DAILY_LOSS_PCT:.1%}) reached"

        return True, ""

    def calculate_position_size(
        self, capital: float, entry_price: float, stop_price: float
    ) -> float:
        """
        Calculate position size based on risk per trade

        Args:
            capital: Current account capital
            entry_price: Entry price
            stop_price: Stop loss price

        Returns:
            Position quantity
        """
        risk_amount = capital * self.config.RISK_PER_TRADE_PCT
        price_risk = abs(entry_price - stop_price) / entry_price

        if price_risk == 0:
            return 0

        # Calculate position size based on risk
        position_value = risk_amount / price_risk

        # Apply leverage
        notional = position_value * self.config.LEVERAGE

        quantity = notional / entry_price

        return float(quantity)

    def reset_daily(self):
        """Reset daily tracking"""
        self.daily_pnl = 0.0
        self.daily_trades = 0
