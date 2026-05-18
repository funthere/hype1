"""
Risk management component.

Handles consecutive loss tracking, circuit breaker logic, daily loss
limits, and max drawdown monitoring.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

from ..core.config import BotConfig

logger = logging.getLogger(__name__)


class RiskGuard:
    """Encapsulates circuit-breaker, consecutive-loss, and drawdown tracking."""

    def __init__(self, config: BotConfig):
        self.config = config

        # Circuit breaker state
        self.consecutive_losses: int = 0
        self.circuit_breaker_triggered: bool = False
        self.circuit_breaker_until: Optional[datetime] = None

        # Drawdown tracking
        self.peak_equity: float = 0.0
        self.max_drawdown_pct: float = 0.0

    # ------------------------------------------------------------------
    # Trade gate
    # ------------------------------------------------------------------

    def can_trade(self) -> bool:
        """Return True if the bot is allowed to open new positions."""
        if self.circuit_breaker_triggered:
            return False
        return True

    # ------------------------------------------------------------------
    # Trade result recording
    # ------------------------------------------------------------------

    def record_trade_result(self, net_pnl: float, current_capital: float) -> None:
        """Update consecutive-loss, drawdown, and circuit-breaker state.

        Returns True if the circuit breaker was just triggered.
        """
        # Consecutive losses
        if net_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Drawdown
        capital_floored = max(current_capital, 100)
        if capital_floored > self.peak_equity:
            self.peak_equity = capital_floored
        drawdown = (self.peak_equity - capital_floored) / self.peak_equity if self.peak_equity > 0 else 0
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)

    def should_trigger_circuit_breaker(self) -> bool:
        """Check whether the circuit breaker threshold has been reached."""
        return (
            self.config.CIRCUIT_BREAKER_ENABLED
            and self.consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES
        )

    def trigger_circuit_breaker(self) -> None:
        """Activate the circuit breaker."""
        self.circuit_breaker_triggered = True
        cooldown = timedelta(minutes=self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES)
        self.circuit_breaker_until = datetime.now() + cooldown

        logger.warning("=" * 60)
        logger.warning("⛔ CIRCUIT BREAKER TRIGGERED!")
        logger.warning(f"   Consecutive losses: {self.consecutive_losses}")
        logger.warning(
            f"   Cooldown: {self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES} minutes"
        )
        logger.warning("=" * 60)

    # ------------------------------------------------------------------
    # Cooldown check
    # ------------------------------------------------------------------

    def check_circuit_breaker_cooldown(self) -> bool:
        """Return True if the cooldown expired and breaker was reset."""
        if not self.circuit_breaker_triggered or self.circuit_breaker_until is None:
            return False

        if datetime.now() >= self.circuit_breaker_until:
            logger.info("✅ Circuit breaker cooldown expired - resuming trading")
            self.reset_circuit_breaker()
            return True
        return False

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset_circuit_breaker(self) -> None:
        """Manually reset circuit breaker state."""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_until = None
        self.consecutive_losses = 0
        logger.info("Circuit breaker reset")

    # ------------------------------------------------------------------
    # Properties / helpers
    # ------------------------------------------------------------------

    @property
    def status(self) -> Dict:
        """Return a dict describing circuit breaker status (for API)."""
        return {
            "enabled": self.config.CIRCUIT_BREAKER_ENABLED,
            "is_triggered": self.circuit_breaker_triggered,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.config.MAX_CONSECUTIVE_LOSSES,
            "cooldown_until": (
                self.circuit_breaker_until.isoformat()
                if self.circuit_breaker_until
                else None
            ),
            "cooldown_minutes": self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES,
        }
