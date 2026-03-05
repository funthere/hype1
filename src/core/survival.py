"""
Survival-Focused Configuration for HYPE Trading Bot

This module provides conservative risk settings designed for long-term survival
rather than maximum profit.
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

from ..core.config import BotConfig


class RiskProfile(Enum):
    """Risk profiles with different survival vs return tradeoffs"""
    ULTRA_CONSERVATIVE = "ultra_conservative"  # Maximum survival
    CONSERVATIVE = "conservative"              # Balance tilted to survival
    MODERATE = "moderate"                      # Balanced approach
    AGGRESSIVE = "aggressive"                  # Higher risk, lower survival


@dataclass
class SurvivalConfig:
    """
    Survival-focused configuration overrides.

    These values override the base BotConfig when applied.
    """

    # Daily loss limits (most critical for survival)
    MAX_DAILY_LOSS_PCT: float = 0.03           # 3% daily stop (was 15%)
    MAX_DRAWDOWN_FROM_PEAK_PCT: float = 0.10  # 10% max drawdown shutdown

    # Position sizing (reduced for survival)
    RISK_PER_TRADE_PCT: float = 0.02           # 2% per trade (was 8%)
    MAX_POSITION_HEAT_PCT: float = 0.05        # 5% max per "trade idea"

    # Leverage (lowered for survival)
    LEVERAGE: int = 3                           # 3x (was 5x)
    VOLATILITY_LEVERAGE_REDUCTION: bool = True  # Reduce leverage in high vol

    # Circuit breaker (faster intervention)
    MAX_CONSECUTIVE_LOSSES: int = 2            # 2 losses (was 3)
    CIRCUIT_BREAKER_COOLDOWN_MINUTES: int = 60 # 60 min (was 30)

    # Trading frequency limits
    MAX_DAILY_TRADES: int = 10                 # 10 trades (was 20)
    MIN_TIME_BETWEEN_TRADES_MINUTES: int = 15  # 15 min between trades

    # Time-based restrictions
    AVOID_LOW_LIQUIDITY_HOURS: bool = True     # Avoid weekends/holidays
    AVOID_FIRST_LAST_MINUTES: int = 30         # Avoid open/close volatility

    # Tiered risk reduction
    TIERED_RISK_REDUCTION: bool = True         # Reduce risk after losses
    TIER_1_AFTER_LOSSES: int = 2              # Reduce after 2 losses
    TIER_1_REDUCTION_PCT: float = 0.50         # Cut risk by 50%
    TIER_2_DAILY_LOSS_PCT: float = 0.02       # 2% daily loss triggers tier 2
    TIER_2_REDUCTION_PCT: float = 0.25         # Cut risk by 75%

    # Position heat (don't add to losing positions)
    POSITION_HEAT_LIMIT: int = 1               # Max 1 position per direction per asset
    REDUCE_ON_HEAT: bool = True                # Reduce size when adding to same direction

    # Profit management (lock in profits)
    PARTIAL_TP_ENABLED: bool = True            # Take partial profits
    PARTIAL_TP_AT_R: float = 1.0               # At 1x risk
    PARTIAL_TP_PCT: float = 0.50               # Close 50%
    MOVE_TO_BREAKEVEN_AT_R: float = 1.0        # Move SL to breakeven at 1R


class SurvivalBotConfig(BotConfig):
    """
    Extended BotConfig with survival-focused defaults.

    Use this instead of BotConfig for production trading where
    survival is more important than maximum profit.
    """

    # Override with survival-focused defaults
    def __init__(self, risk_profile: RiskProfile = RiskProfile.CONSERVATIVE):
        # Initialize parent
        super().__init__()

        # Apply risk profile
        self._apply_risk_profile(risk_profile)

    def _apply_risk_profile(self, profile: RiskProfile):
        """Apply risk profile settings"""

        survival = SurvivalConfig()

        if profile == RiskProfile.ULTRA_CONSERVATIVE:
            self.MAX_DAILY_LOSS_PCT = 0.01          # 1% daily
            self.RISK_PER_TRADE_PCT = 0.01          # 1% per trade
            self.LEVERAGE = 2
            self.MAX_DAILY_TRADES = 5
            self.MAX_CONSECUTIVE_LOSSES = 1

        elif profile == RiskProfile.CONSERVATIVE:
            self.MAX_DAILY_LOSS_PCT = survival.MAX_DAILY_LOSS_PCT
            self.RISK_PER_TRADE_PCT = survival.RISK_PER_TRADE_PCT
            self.LEVERAGE = survival.LEVERAGE
            self.MAX_DAILY_TRADES = survival.MAX_DAILY_TRADES
            self.MAX_CONSECUTIVE_LOSSES = survival.MAX_CONSECUTIVE_LOSSES

        elif profile == RiskProfile.MODERATE:
            self.MAX_DAILY_LOSS_PCT = 0.05          # 5% daily
            self.RISK_PER_TRADE_PCT = 0.04          # 4% per trade
            self.LEVERAGE = 5
            self.MAX_DAILY_TRADES = 15
            self.MAX_CONSECUTIVE_LOSSES = 3

        elif profile == RiskProfile.AGGRESSIVE:
            # Original aggressive settings
            self.MAX_DAILY_LOSS_PCT = 0.15
            self.RISK_PER_TRADE_PCT = 0.08
            self.LEVERAGE = 5
            self.MAX_DAILY_TRADES = 20
            self.MAX_CONSECUTIVE_LOSSES = 3

        # Survival-specific settings (apply to all profiles)
        self.CIRCUIT_BREAKER_ENABLED = True
        self.CIRCUIT_BREAKER_COOLDOWN_MINUTES = survival.CIRCUIT_BREAKER_COOLDOWN_MINUTES
        self.EMERGENCY_SHUTDOWN = False

    def validate_survival_settings(self) -> bool:
        """
        Validate that settings are within survival bounds.

        Raises ValueError if settings are too aggressive.
        """
        # Survival-specific validations
        if self.MAX_DAILY_LOSS_PCT > 0.05:
            raise ValueError(
                f"MAX_DAILY_LOSS_PCT ({self.MAX_DAILY_LOSS_PCT:.1%}) "
                f"exceeds survival limit (5%). Use Aggressive profile for higher risk."
            )

        if self.RISK_PER_TRADE_PCT > 0.05:
            raise ValueError(
                f"RISK_PER_TRADE_PCT ({self.RISK_PER_TRADE_PCT:.1%}) "
                f"exceeds survival limit (5%)."
            )

        if self.LEVERAGE > 5:
            raise ValueError(
                f"LEVERAGE ({self.LEVERAGE}x) exceeds survival limit (5x)."
            )

        # Run parent validation
        return super().validate()


def create_survival_config(
    profile: RiskProfile = RiskProfile.CONSERVATIVE,
    private_key: str = "",
    paper_trading: bool = True
) -> SurvivalBotConfig:
    """
    Factory function to create a survival-focused bot configuration.

    Args:
        profile: Risk profile (default: CONSERVATIVE)
        private_key: Wallet private key
        paper_trading: Enable paper trading mode

    Returns:
        Configured SurvivalBotConfig instance
    """
    config = SurvivalBotConfig(profile)
    config.PRIVATE_KEY = private_key
    config.PAPER_TRADING = paper_trading

    # Validate
    config.validate_survival_settings()

    return config


# Preset configurations for quick deployment
SURVIVAL_PRESETS = {
    "paper_trading_safe": {
        "profile": RiskProfile.CONSERVATIVE,
        "paper_trading": True,
    },
    "testnet_learning": {
        "profile": RiskProfile.MODERATE,
        "paper_trading": False,
        "USE_TESTNET": True,
    },
    "mainnet_cautious": {
        "profile": RiskProfile.CONSERVATIVE,
        "paper_trading": False,
    },
}
