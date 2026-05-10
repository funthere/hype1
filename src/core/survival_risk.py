"""
Survival-Focused Risk Manager

Advanced risk controls designed for long-term survival:
- Position heat management
- Tiered risk reduction
- Volatility-adjusted leverage
- Maximum drawdown circuit breaker
- Time-based trading restrictions
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..core.config import BotConfig, Position, Side, Trade

logger = logging.getLogger(__name__)


class PositionHeatManager:
    """
    Track and limit "position heat" - exposure in similar trades.

    Prevents adding to losing positions and limits total exposure
    per trading idea.
    """

    def __init__(self, max_heat_per_setup: float = 0.05):
        """
        Initialize position heat manager

        Args:
            max_heat_per_setup: Max risk % per "setup" (e.g., all LONG in HYPE)
        """
        self.max_heat_per_setup = max_heat_per_setup
        self.position_heat: Dict[str, float] = {}  # setup -> current risk

    def get_setup_key(self, position: Position) -> str:
        """Get unique key for this position setup"""
        return f"{position.side.value}_{position.leverage}x"

    def calculate_position_risk(self, position: Position, entry_price: float) -> float:
        """Calculate risk amount of a position"""
        # Risk = distance to stop loss in dollar terms
        if position.side == Side.LONG:
            stop_distance = (
                position.entry_price - position.sl_price
            ) / position.entry_price
        else:
            stop_distance = (
                position.sl_price - position.entry_price
            ) / position.entry_price

        # Notional at risk
        notional_risk = position.quantity * entry_price * stop_distance
        return abs(notional_risk)

    def can_add_position(
        self,
        new_position: Position,
        existing_positions: List[Position],
        capital: float,
        current_price: float,
    ) -> Tuple[bool, str]:
        """
        Check if new position can be added without exceeding heat limits

        Returns:
            Tuple of (can_add, reason)
        """
        # Calculate current heat
        current_heat = 0.0

        for pos in existing_positions:
            setup_key = self.get_setup_key(pos)
            setup_risk = self.calculate_position_risk(pos, current_price)

            # Check if same setup as new position
            if setup_key == self.get_setup_key(new_position):
                current_heat += setup_risk

        # Calculate new position risk
        new_risk = self.calculate_position_risk(new_position, current_price)
        total_heat = current_heat + new_risk
        heat_pct = total_heat / capital

        if heat_pct > self.max_heat_per_setup:
            return (
                False,
                f"Position heat ({heat_pct:.1%}) exceeds limit ({self.max_heat_per_setup:.1%})",
            )

        # Check if we're adding to a losing position
        for pos in existing_positions:
            if (
                pos.side == new_position.side
                and pos.unrealized_pnl < 0
                and self.get_setup_key(pos) == self.get_setup_key(new_position)
            ):
                return False, "Cannot add to losing position in same setup"

        return True, ""

    def on_position_closed(self, position: Position):
        """Remove heat from closed position"""
        setup_key = self.get_setup_key(position)
        if setup_key in self.position_heat:
            del self.position_heat[setup_key]


class TieredRiskManager:
    """
    Reduce risk exposure after losses to protect capital.

    Implements progressive risk reduction:
    - Tier 1: After N consecutive losses → 50% risk reduction
    - Tier 2: After daily loss exceeds X% → 75% risk reduction
    """

    def __init__(
        self,
        tier_1_after_losses: int = 2,
        tier_1_reduction: float = 0.5,
        tier_2_daily_loss_pct: float = 0.02,
        tier_2_reduction: float = 0.25,
    ):
        self.tier_1_after_losses = tier_1_after_losses
        self.tier_1_reduction = tier_1_reduction
        self.tier_2_daily_loss_pct = tier_2_daily_loss_pct
        self.tier_2_reduction = tier_2_reduction

        self.current_tier = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0

    def update(self, trade: Trade, daily_pnl: float, consecutive_losses: int):
        """Update tier status after a trade"""
        self.daily_pnl = daily_pnl
        self.consecutive_losses = consecutive_losses

        # Check tier 2 (daily loss exceeded)
        if self.daily_pnl < -self.tier_2_daily_loss_pct:
            self.current_tier = 2
        # Check tier 1 (consecutive losses)
        elif self.consecutive_losses >= self.tier_1_after_losses:
            self.current_tier = 1
        else:
            self.current_tier = 0

    def get_risk_multiplier(self) -> float:
        """Get current risk multiplier based on tier"""
        if self.current_tier == 1:
            return self.tier_1_reduction
        elif self.current_tier == 2:
            return self.tier_2_reduction
        else:
            return 1.0

    def reset(self):
        """Reset tier status (new day or manual reset)"""
        self.current_tier = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0


class VolatilityRiskManager:
    """
    Adjust leverage and position sizing based on market volatility.

    In high volatility, reduce exposure to survive large swings.
    """

    def __init__(
        self,
        atr_window: int = 14,
        vol_multiplier_threshold: float = 1.5,
        leverage_reduction_factor: float = 0.6,
    ):
        self.atr_window = atr_window
        self.vol_multiplier_threshold = vol_multiplier_threshold
        self.leverage_reduction_factor = leverage_reduction_factor

        self.atr_history: List[float] = []
        self.base_leverage = 5

    def update_atr(self, atr: float):
        """Update ATR history"""
        self.atr_history.append(atr)
        if len(self.atr_history) > self.atr_window * 2:
            self.atr_history = self.atr_history[-self.atr_window * 2 :]

    def get_volatility_multiplier(self) -> float:
        """
        Calculate volatility multiplier based on recent ATR

        Returns:
            Multiplier < 1.0 if volatility is elevated
        """
        if len(self.atr_history) < self.atr_window:
            return 1.0

        recent_atr = np.mean(self.atr_history[-self.atr_window :])
        baseline_atr = np.mean(
            self.atr_history[-self.atr_window * 2 : -self.atr_window]
        )

        if baseline_atr == 0:
            return 1.0

        vol_multiplier = recent_atr / baseline_atr

        # If volatility is elevated, reduce leverage
        if vol_multiplier > self.vol_multiplier_threshold:
            return self.leverage_reduction_factor

        return 1.0

    def get_adjusted_leverage(self, base_leverage: int) -> int:
        """Get leverage adjusted for current volatility"""
        vol_mult = self.get_volatility_multiplier()
        adjusted = int(base_leverage * vol_mult)
        return max(1, min(adjusted, 10))  # Clamp between 1x and 10x


class TimeBasedRiskManager:
    """
    Restrict trading during dangerous times.

    Avoid:
    - Low liquidity hours (weekends, overnight)
    - Major economic announcements
    - Market open/close volatility
    """

    def __init__(
        self,
        avoid_first_last_minutes: int = 30,
        avoid_weekends: bool = True,
        timezone: str = "UTC",
    ):
        self.avoid_first_last_minutes = avoid_first_last_minutes
        self.avoid_weekends = avoid_weekends
        self.timezone = timezone

    def is_safe_to_trade(
        self, current_time: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Check if current time is safe for trading

        Returns:
            Tuple of (is_safe, reason if not safe)
        """
        if current_time is None:
            current_time = datetime.utcnow()

        # Check weekend
        if self.avoid_weekends:
            # Monday = 0, Friday = 4
            if current_time.weekday() >= 5:  # Saturday or Sunday
                return False, "Weekend - low liquidity"

        # Check market open/close (avoid first/last N minutes)
        if self.avoid_first_last_minutes:
            hour = current_time.hour
            minute = current_time.minute

            # Crypto markets are 24/7, but can still have volatility patterns
            # Avoid common "candle" times (hourly opens)
            if minute < self.avoid_first_last_minutes or minute >= (
                60 - self.avoid_first_last_minutes
            ):
                # Check if this is a significant time
                if hour in [0, 8, 12, 16, 20]:  # Common trading session starts
                    return (
                        False,
                        f"Market hour boundary - avoid ±{self.avoid_first_last_minutes}min",
                    )

        return True, ""


class MaxDrawdownCircuitBreaker:
    """
    Circuit breaker based on maximum drawdown from peak equity.

    More aggressive than daily loss limit - shuts down trading
    if account drops too far from its all-time high.
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.10,  # 10% max drawdown
        cooldown_hours: int = 24,  # 24 hour cooldown
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.cooldown_hours = cooldown_hours

        self.peak_equity: Optional[float] = None
        self.current_equity: float = 0.0
        self.triggered = False
        self.triggered_at: Optional[datetime] = None
        self.reset_at: Optional[datetime] = None

    def update(self, current_equity: float, current_time: datetime):
        """Update equity and check drawdown"""
        self.current_equity = current_equity

        # Update peak
        if self.peak_equity is None or current_equity > self.peak_equity:
            self.peak_equity = current_equity
            # Reset circuit breaker if we made a new high
            if self.triggered:
                self.triggered = False
                self.triggered_at = None
                logger.info("✅ Max drawdown circuit breaker reset - new equity high")

        # Check if we should re-enable after cooldown
        if self.triggered and self.reset_at:
            if current_time >= self.reset_at:
                self.triggered = False
                self.triggered_at = None
                self.reset_at = None
                logger.info("✅ Max drawdown circuit breaker cooldown expired")

        # Check drawdown
        if self.peak_equity and not self.triggered:
            drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity

            if drawdown_pct >= self.max_drawdown_pct:
                self.triggered = True
                self.triggered_at = current_time
                self.reset_at = current_time + timedelta(hours=self.cooldown_hours)

                logger.warning("=" * 60)
                logger.warning("⛔ MAX DRAWDOWN CIRCUIT BREAKER TRIGGERED!")
                logger.warning("=" * 60)
                logger.warning(f"   Drawdown: {drawdown_pct:.1%}")
                logger.warning(f"   Limit: {self.max_drawdown_pct:.1%}")
                logger.warning(f"   Cooldown: {self.cooldown_hours} hours")
                logger.warning("=" * 60)

    def is_triggered(self) -> bool:
        """Check if circuit breaker is currently triggered"""
        return self.triggered

    def get_drawdown_pct(self) -> float:
        """Get current drawdown percentage"""
        if self.peak_equity and self.peak_equity > 0:
            return (self.peak_equity - self.current_equity) / self.peak_equity
        return 0.0


class SurvivalRiskManager:
    """
    Unified survival-focused risk manager.

    Combines all survival mechanisms into one interface.
    """

    def __init__(self, config: BotConfig):
        self.config = config

        # Initialize all survival components
        self.heat_manager = PositionHeatManager(
            max_heat_per_setup=config.MAX_DAILY_LOSS_PCT
        )
        self.tiered_risk = TieredRiskManager()
        self.volatility_manager = VolatilityRiskManager()
        self.time_manager = TimeBasedRiskManager()
        self.drawdown_breaker = MaxDrawdownCircuitBreaker(max_drawdown_pct=0.10)

        # State
        self.last_trade_time: Optional[datetime] = None

    def can_open_position(
        self,
        position,
        existing_positions: List[Position],
        capital: float,
        current_price: float,
        daily_pnl: float,
        consecutive_losses: int,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Comprehensive check if position can be opened

        Returns:
            Tuple of (can_open, reason)
        """
        # 1. Check time-based restrictions
        time_safe, time_reason = self.time_manager.is_safe_to_trade(current_time)
        if not time_safe:
            return False, f"Time restriction: {time_reason}"

        # 2. Check max drawdown circuit breaker
        if self.drawdown_breaker.is_triggered():
            return (
                False,
                f"Max drawdown circuit breaker active ({self.drawdown_breaker.get_drawdown_pct():.1%})",
            )

        # 3. Check position heat
        can_add, heat_reason = self.heat_manager.can_add_position(
            position, existing_positions, capital, current_price
        )
        if not can_add:
            return False, heat_reason

        # 4. Check minimum time between trades
        if self.last_trade_time and current_time:
            min_between = timedelta(minutes=15)
            if current_time - self.last_trade_time < min_between:
                return False, "Too soon since last trade"

        # 5. Apply tiered risk multiplier
        self.tiered_risk.daily_pnl = daily_pnl
        self.tiered_risk.consecutive_losses = consecutive_losses

        return True, ""

    def update_after_trade(self, trade: Trade, capital: float, current_time: datetime):
        """Update risk managers after a trade"""
        self.last_trade_time = current_time

        # Update tiered risk
        # (Note: consecutive_losses and daily_pnl would be tracked by main bot)

        # Update drawdown breaker
        new_equity = capital + trade.pnl
        self.drawdown_breaker.update(new_equity, current_time)

    def get_position_size_multiplier(self, atr: Optional[float] = None) -> float:
        """Get combined risk multiplier from all sources"""
        # Volatility multiplier
        if atr:
            self.volatility_manager.update_atr(atr)
        vol_mult = self.volatility_manager.get_volatility_multiplier()

        # Tiered risk multiplier
        tier_mult = self.tiered_risk.get_risk_multiplier()

        return vol_mult * tier_mult

    def get_survival_summary(self) -> str:
        """Get formatted survival status summary"""
        return """
╔════════════════════════════════════════════════════════════╗
║              SURVIVAL RISK MANAGER STATUS                    ║
╚════════════════════════════════════════════════════════════╝

⚠️  MAX DRAWDOWN
────────────────────────────────────────────────────────────
  Current DD:         {dd:>8.1%}
  Limit:              {limit:>8.1%}
  Status:              {status}

🔥 POSITION HEAT
────────────────────────────────────────────────────────────
  Max per Setup:       {heat_max:>8.1%}

📊 TIERED RISK
────────────────────────────────────────────────────────────
  Current Tier:        {tier:>8}
  Risk Multiplier:     {risk_mult:>8.2f}x

📈 VOLATILITY ADJUSTMENT
────────────────────────────────────────────────────────────
  Vol Multiplier:      {vol_mult:>8.2f}x
  Adjusted Leverage:   {lev:>8}x

⏰ TIME CHECKS
────────────────────────────────────────────────────────────
  Safe to Trade:       {time_safe:>8}
""".format(
            dd=self.drawdown_breaker.get_drawdown_pct(),
            limit=self.drawdown_breaker.max_drawdown_pct,
            status="TRIGGERED" if self.drawdown_breaker.is_triggered() else "OK",
            heat_max=self.heat_manager.max_heat_per_setup,
            tier=self.tiered_risk.current_tier,
            risk_mult=self.tiered_risk.get_risk_multiplier(),
            vol_mult=self.volatility_manager.get_volatility_multiplier(),
            lev=self.volatility_manager.get_adjusted_leverage(5),
            time_safe="YES" if self.time_manager.is_safe_to_trade()[0] else "NO",
        )
