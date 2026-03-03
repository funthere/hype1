"""
Adaptive Parameters Module

Implements self-optimizing parameters that adjust based on:
- Recent market conditions (volatility regime)
- Recent performance metrics
- Time of day patterns
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

import numpy as np
import pandas as pd

from ..core.config import BotConfig, Trade, Side

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Market volatility regimes"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class MarketPhase(Enum):
    """Market phase based on trend"""
    BULL = "bull"  # Uptrend
    BEAR = "bear"  # Downtrend
    RANGE = "range"  # Sideways
    TRANSITION = "transition"  # Uncertain


@dataclass
class AdaptiveParameters:
    """Adaptive trading parameters"""
    # Risk parameters
    risk_per_trade: float
    leverage: int
    confidence_threshold: int
    tp_multiplier: float
    sl_multiplier: float

    # Derived from market conditions
    volatility_regime: VolatilityRegime
    market_phase: MarketPhase

    # Timestamp
    updated_at: datetime


class VolatilityDetector:
    """
    Detect current volatility regime from price data.

    Uses rolling statistics to classify market into
    low/normal/high/extreme volatility regimes.
    """

    def __init__(self, window: int = 50, atr_window: int = 14):
        """
        Initialize volatility detector

        Args:
            window: Window for statistics calculation
            atr_window: ATR calculation period
        """
        self.window = window
        self.atr_window = atr_window
        self.price_history: deque = deque(maxlen=window)

        # Volatility percentiles for classification
        self.volt_percentiles: deque = deque(maxlen=1000)

    def update(self, price: float) -> VolatilityRegime:
        """
        Update with new price and return current regime

        Args:
            price: Current price

        Returns:
            Current volatility regime
        """
        self.price_history.append(price)

        if len(self.price_history) < self.atr_window + 10:
            return VolatilityRegime.NORMAL

        # Calculate realized volatility
        returns = np.diff(list(self.price_history))
        vol = np.std(returns) / np.mean(np.abs(returns)) if len(returns) > 0 else 0

        self.volt_percentiles.append(vol)

        if len(self.volt_percentiles) < 50:
            return VolatilityRegime.NORMAL

        # Classify based on historical percentiles
        percentiles = list(self.volt_percentiles)
        p25 = np.percentile(percentiles, 25)
        p50 = np.percentile(percentiles, 50)
        p75 = np.percentile(percentiles, 75)
        p90 = np.percentile(percentiles, 90)

        if vol <= p25:
            return VolatilityRegime.LOW
        elif vol <= p50:
            return VolatilityRegime.NORMAL
        elif vol <= p90:
            return VolatilityRegime.HIGH
        else:
            return VolatilityRegime.EXTREME


class TrendDetector:
    """
    Detect current market phase (bull/bear/range/transition).

    Uses EMA slopes and price relationships to classify trend.
    """

    def __init__(self, fast_ema: int = 20, slow_ema: int = 50):
        """
        Initialize trend detector

        Args:
            fast_ema: Fast EMA period
            slow_ema: Slow EMA period
        """
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.price_history: deque = deque(maxlen=slow_ema + 10)

    def update(self, price: float) -> MarketPhase:
        """
        Update with new price and return current phase

        Args:
            price: Current price

        Returns:
            Current market phase
        """
        self.price_history.append(price)

        if len(self.price_history) < self.slow_ema:
            return MarketPhase.TRANSITION

        prices = list(self.price_history)

        # Calculate EMAs
        ema_fast = self._calculate_ema(prices, self.fast_ema)
        ema_slow = self._calculate_ema(prices, self.slow_ema)

        # Calculate slopes (rate of change)
        if len(prices) >= self.fast_ema + 5:
            recent_fast = self._calculate_ema(prices[-5:], self.fast_ema)
            fast_slope = (ema_fast - recent_fast) / recent_fast
        else:
            fast_slope = 0

        # Determine phase
        price_vs_fast = (prices[-1] - ema_fast) / ema_fast
        fast_vs_slow = (ema_fast - ema_slow) / ema_slow

        # Classification logic
        if abs(fast_slope) < 0.001:  # Very small slope
            return MarketPhase.RANGE

        if price_vs_fast > 0.005 and fast_vs_slow > 0.002:
            return MarketPhase.BULL
        elif price_vs_fast < -0.005 and fast_vs_slow < -0.002:
            return MarketPhase.BEAR
        elif abs(fast_vs_slow) < 0.001:
            return MarketPhase.RANGE
        else:
            return MarketPhase.TRANSITION

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate EMA for given period"""
        if len(prices) < period:
            return sum(prices) / len(prices)

        multiplier = 2 / (period + 1)
        ema = prices[0]

        for price in prices[1:]:
            ema = (price - ema) * multiplier + ema

        return ema


class AdaptiveParameterManager:
    """
    Manage adaptive trading parameters based on market conditions.

    Adjusts risk, leverage, and signal thresholds dynamically
    to optimize performance in different market environments.
    """

    # Parameter adjustment factors
    VOLATILITY_MULTIPLIERS = {
        VolatilityRegime.LOW: {
            "leverage_factor": 1.2,
            "risk_factor": 1.2,
            "tp_factor": 0.8,
            "sl_factor": 0.8,
        },
        VolatilityRegime.NORMAL: {
            "leverage_factor": 1.0,
            "risk_factor": 1.0,
            "tp_factor": 1.0,
            "sl_factor": 1.0,
        },
        VolatilityRegime.HIGH: {
            "leverage_factor": 0.7,
            "risk_factor": 0.7,
            "tp_factor": 1.3,
            "sl_factor": 1.2,
        },
        VolatilityRegime.EXTREME: {
            "leverage_factor": 0.5,
            "risk_factor": 0.5,
            "tp_factor": 1.5,
            "sl_factor": 1.5,
        },
    }

    PHASE_ADJUSTMENTS = {
        MarketPhase.BULL: {
            "confidence_adjust": -5,  # More aggressive in uptrend
            "long_bias": 1.2,
            "short_bias": 0.8,
        },
        MarketPhase.BEAR: {
            "confidence_adjust": 5,  # More cautious in downtrend
            "long_bias": 0.8,
            "short_bias": 1.2,
        },
        MarketPhase.RANGE: {
            "confidence_adjust": 0,
            "long_bias": 1.0,
            "short_bias": 1.0,
        },
        MarketPhase.TRANSITION: {
            "confidence_adjust": 10,  # Very cautious in uncertain markets
            "long_bias": 1.0,
            "short_bias": 1.0,
        },
    }

    def __init__(
        self,
        base_config: BotConfig,
        adaptation_interval: int = 300,  # seconds
        min_interval: int = 60  # minimum seconds between adaptations
    ):
        """
        Initialize adaptive parameter manager

        Args:
            base_config: Base configuration with default parameters
            adaptation_interval: How often to recalculate parameters
            min_interval: Minimum time between adaptations
        """
        self.base_config = base_config
        self.adaptation_interval = adaptation_interval
        self.min_interval = min_interval

        # Detectors
        self.volatility_detector = VolatilityDetector()
        self.trend_detector = TrendDetector()

        # Current parameters
        self.current_params = self._create_base_params()

        # Performance tracking
        self.recent_trades: deque = deque(maxlen=50)
        self.last_adaptation = None

        # Performance-based adjustments
        self.performance_multiplier = 1.0

    def _create_base_params(self) -> AdaptiveParameters:
        """Create base parameters from config"""
        return AdaptiveParameters(
            risk_per_trade=self.base_config.RISK_PER_TRADE_PCT,
            leverage=self.base_config.LEVERAGE,
            confidence_threshold=self.base_config.CONFIDENCE_THRESHOLD,
            tp_multiplier=self.base_config.TP_ATR_MULTIPLIER,
            sl_multiplier=self.base_config.SL_ATR_MULTIPLIER,
            volatility_regime=VolatilityRegime.NORMAL,
            market_phase=MarketPhase.TRANSITION,
            updated_at=datetime.now()
        )

    def update_market_data(self, price: float) -> bool:
        """
        Update with new market data

        Args:
            price: Current price

        Returns:
            True if parameters were updated
        """
        # Detect market conditions
        vol_regime = self.volatility_detector.update(price)
        phase = self.trend_detector.update(price)

        # Check if we should adapt
        now = datetime.now()
        if self.last_adaptation and (now - self.last_adaptation).total_seconds() < self.min_interval:
            return False

        # Check if conditions changed significantly
        if (vol_regime == self.current_params.volatility_regime and
            phase == self.current_params.market_phase):
            return False

        # Calculate new parameters
        new_params = self._calculate_parameters(vol_regime, phase)

        # Update
        old_params = self.current_params
        self.current_params = new_params
        self.last_adaptation = now

        logger.info(
            f"Parameters adapted: {vol_regime.value} volatility, "
            f"{phase.value} phase | "
            f"Leverage: {old_params.leverage}->{new_params.leverage}, "
            f"Risk: {old_params.risk_per_trade:.2%}->{new_params.risk_per_trade:.2%}"
        )

        return True

    def _calculate_parameters(
        self,
        vol_regime: VolatilityRegime,
        phase: MarketPhase
    ) -> AdaptiveParameters:
        """Calculate adaptive parameters based on conditions"""
        # Get volatility adjustments
        vol_adj = self.VOLATILITY_MULTIPLIERS.get(vol_regime, self.VOLATILITY_MULTIPLIERS[VolatilityRegime.NORMAL])

        # Get phase adjustments
        phase_adj = self.PHASE_ADJUSTMENTS.get(phase, self.PHASE_ADJUSTMENTS[MarketPhase.TRANSITION])

        # Apply adjustments
        new_leverage = max(1, min(
            100,
            int(self.base_config.LEVERAGE * vol_adj["leverage_factor"] * self.performance_multiplier)
        ))

        new_risk = max(0.01, min(
            0.50,
            self.base_config.RISK_PER_TRADE_PCT * vol_adj["risk_factor"] * self.performance_multiplier
        ))

        new_tp = self.base_config.TP_ATR_MULTIPLIER * vol_adj["tp_factor"]
        new_sl = self.base_config.SL_ATR_MULTIPLIER * vol_adj["sl_factor"]

        # Confidence threshold adjustment
        new_confidence = max(30, min(90, (
            self.base_config.CONFIDENCE_THRESHOLD + phase_adj["confidence_adjust"]
        )))

        return AdaptiveParameters(
            risk_per_trade=new_risk,
            leverage=new_leverage,
            confidence_threshold=new_confidence,
            tp_multiplier=new_tp,
            sl_multiplier=new_sl,
            volatility_regime=vol_regime,
            market_phase=phase,
            updated_at=datetime.now()
        )

    def record_trade(self, trade: Trade):
        """Record a trade for performance tracking"""
        self.recent_trades.append(trade)

        # Update performance multiplier based on recent results
        if len(self.recent_trades) >= 20:
            self._update_performance_multiplier()

    def _update_performance_multiplier(self):
        """Update performance multiplier based on recent trades"""
        recent = list(self.recent_trades)[-20:]

        wins = len([t for t in recent if t.pnl > 0])
        win_rate = wins / len(recent)

        total_pnl = sum(t.pnl for t in recent)

        # Adjust multiplier based on performance
        # Good performance = increase risk slightly
        # Poor performance = decrease risk
        if win_rate > 0.5 and total_pnl > 0:
            self.performance_multiplier = min(1.5, self.performance_multiplier * 1.05)
        elif win_rate < 0.35 or total_pnl < 0:
            self.performance_multiplier = max(0.5, self.performance_multiplier * 0.95)

    def get_parameters(self) -> AdaptiveParameters:
        """Get current adaptive parameters"""
        return self.current_params

    def get_side_bias(self, side: Side) -> float:
        """
        Get confidence adjustment for a specific side

        Returns:
            Multiplier for confidence threshold (lower = easier to enter)
        """
        phase = self.current_params.market_phase
        phase_adj = self.PHASE_ADJUSTMENTS.get(phase, {})

        if side == Side.LONG:
            return 1.0 / phase_adj.get("long_bias", 1.0)
        else:
            return 1.0 / phase_adj.get("short_bias", 1.0)

    def should_filter_signal(self, signal_confidence: int, side: Side) -> bool:
        """
        Check if signal should be filtered based on adaptive parameters

        Args:
            signal_confidence: Raw signal confidence (0-100)
            side: Signal side

        Returns:
            True if signal should be filtered (not traded)
        """
        # Apply side bias
        side_bias = self.get_side_bias(side)
        adjusted_confidence = int(signal_confidence / side_bias)

        # Check against threshold
        threshold = self.current_params.confidence_threshold

        if side == Side.SHORT:
            threshold = 100 - threshold

        return adjusted_confidence < threshold

    def get_summary(self) -> str:
        """Get formatted summary of current parameters"""
        p = self.current_params

        return f"""
╔════════════════════════════════════════════════════════════╗
║           ADAPTIVE PARAMETERS SUMMARY                       ║
╚════════════════════════════════════════════════════════════╝

📊 MARKET CONDITIONS
────────────────────────────────────────────────────────────
  Volatility Regime:    {p.volatility_regime.value:>12}
  Market Phase:         {p.market_phase.value:>12}

⚙️  ADAPTIVE PARAMETERS
────────────────────────────────────────────────────────────
  Leverage:             {p.leverage:>12}x
  Risk per Trade:       {p.risk_per_trade:>12.1%}
  Confidence Threshold: {p.confidence_threshold:>12}
  TP Multiplier:        {p.tp_multiplier:>12.2f}x ATR
  SL Multiplier:        {p.sl_multiplier:>12.2f}x ATR

📈 PERFORMANCE ADJUSTMENT
────────────────────────────────────────────────────────────
  Performance Mult:     {self.performance_multiplier:>12.2f}x
  Last Adaptation:      {p.updated_at.strftime('%H:%M:%S'):>12}
"""
