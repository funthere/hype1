"""
Multi-asset trading support with correlation filtering
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from enum import Enum

import numpy as np
import pandas as pd

from .config import BotConfig, Side, Position

logger = logging.getLogger(__name__)


class AssetAllocationMethod(Enum):
    """Methods for allocating capital across assets"""

    EQUAL_WEIGHT = "equal_weight"
    RISK_PARITY = "risk_parity"
    SIGNAL_STRENGTH = "signal_strength"
    VOLATILITY_TARGET = "volatility_target"


@dataclass
class AssetConfig:
    """Configuration for a single asset"""

    symbol: str
    weight: float = 1.0  # Relative weight in portfolio
    max_positions: int = 1  # Max concurrent positions for this asset
    min_signal_confidence: int = 45  # Min confidence for this asset
    enabled: bool = True

    # Risk parameters (overrides global if set)
    leverage: Optional[int] = None
    risk_per_trade: Optional[float] = None


@dataclass
class MultiAssetSignal:
    """Signal for a specific asset"""

    asset: str
    action: Side
    confidence: float
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    atr: float
    timestamp: datetime = field(default_factory=datetime.now)


class CorrelationFilter:
    """
    Filter assets based on correlation to avoid overexposure.

    Uses rolling correlation to identify highly correlated assets
    and limit exposure to correlated positions.
    """

    def __init__(self, max_correlation: float = 0.7, window: int = 50):
        """
        Initialize correlation filter

        Args:
            max_correlation: Maximum allowed correlation (0-1)
            window: Rolling window for correlation calculation
        """
        self.max_correlation = max_correlation
        self.window = window
        self.price_history: Dict[str, List[float]] = {}

    def update_price(self, asset: str, price: float):
        """Update price history for an asset"""
        if asset not in self.price_history:
            self.price_history[asset] = []

        self.price_history[asset].append(price)

        # Keep only recent prices
        if len(self.price_history[asset]) > self.window * 2:
            self.price_history[asset] = self.price_history[asset][-self.window * 2 :]

    def get_correlation(self, asset1: str, asset2: str) -> Optional[float]:
        """Calculate return correlation between two assets.

        Correlation is computed on period-over-period returns, not price
        levels: level correlation is dominated by shared trends and reads
        ~1.0 for any two appreciating assets, which would block every
        combination regardless of genuine co-movement.
        """
        if asset1 not in self.price_history or asset2 not in self.price_history:
            return None

        prices1 = self.price_history[asset1]
        prices2 = self.price_history[asset2]

        # Need minimum data points (plus one to form returns)
        min_len = min(len(prices1), len(prices2))
        if min_len < self.window + 1:
            return None

        # Align on the most recent window and convert levels to returns
        prices1_recent = np.asarray(prices1[-min_len:], dtype=float)
        prices2_recent = np.asarray(prices2[-min_len:], dtype=float)
        if np.any(prices1_recent[:-1] <= 0) or np.any(prices2_recent[:-1] <= 0):
            return None

        returns1 = np.diff(prices1_recent) / prices1_recent[:-1]
        returns2 = np.diff(prices2_recent) / prices2_recent[:-1]

        # Zero return variance (e.g. a perfectly steady series) carries no
        # co-movement information; corrcoef would return NaN or float noise.
        if np.std(returns1) < 1e-10 or np.std(returns2) < 1e-10:
            return 0.0

        # Calculate correlation
        try:
            corr = np.corrcoef(returns1, returns2)[0, 1]
            return corr if not np.isnan(corr) else 0.0
        except Exception:
            return 0.0

    def is_correlated(self, asset: str, existing_positions: List[Position]) -> bool:
        """
        Check if asset is correlated with any existing position

        Args:
            asset: Asset to check
            existing_positions: List of current positions

        Returns:
            True if asset is correlated with any existing position
        """
        for position in existing_positions:
            # Extract asset from position (assuming position has asset field)
            pos_asset = getattr(position, "asset", None)
            if not pos_asset:
                continue

            correlation = self.get_correlation(asset, pos_asset)
            if correlation is not None and abs(correlation) > self.max_correlation:
                logger.info(
                    f"Correlation filter: {asset} correlated with {pos_asset} "
                    f"(corr={correlation:.2f})"
                )
                return True

        return False

    def get_correlation_matrix(self, assets: List[str]) -> pd.DataFrame:
        """Get correlation matrix for all assets"""
        matrix_data = {}

        for asset1 in assets:
            row = {}
            for asset2 in assets:
                corr = self.get_correlation(asset1, asset2)
                row[asset2] = corr if corr is not None else 0.0
            matrix_data[asset1] = row

        return pd.DataFrame(matrix_data)


class MultiAssetStrategy:
    """
    Multi-asset trading strategy with correlation filtering
    and position allocation.
    """

    def __init__(
        self,
        base_config: BotConfig,
        assets: List[AssetConfig],
        allocation_method: AssetAllocationMethod = AssetAllocationMethod.EQUAL_WEIGHT,
        max_correlation: float = 0.7,
        volatility_target: float = 0.02,
    ):
        """
        Initialize multi-asset strategy

        Args:
            base_config: Base bot configuration
            assets: List of asset configurations
            allocation_method: How to allocate capital across assets
            max_correlation: Max correlation filter threshold
            volatility_target: Per-asset daily return-vol target used by
                ``VOLATILITY_TARGET`` allocation (e.g. 0.02 = 2%)
        """
        self.base_config = base_config
        self.assets = {a.symbol: a for a in assets if a.enabled}
        self.allocation_method = allocation_method
        self.volatility_target = volatility_target
        self.correlation_filter = CorrelationFilter(max_correlation=max_correlation)

        # State
        self.asset_signals: Dict[str, MultiAssetSignal] = {}
        self.asset_positions: Dict[str, List[Position]] = {
            asset: [] for asset in self.assets
        }

        # Calculate allocations
        self.allocations = self._calculate_allocations()

    def _calculate_allocations(self) -> Dict[str, float]:
        """Calculate capital allocation for each asset.

        EQUAL_WEIGHT and SIGNAL_STRENGTH are static and resolved at
        construction. RISK_PARITY and VOLATILITY_TARGET need realized
        volatility, which is unavailable before price data arrives; they
        start from equal weights and are refined by
        :meth:`recalculate_allocations` as history accumulates.
        """
        allocations = {}

        if self.allocation_method == AssetAllocationMethod.EQUAL_WEIGHT:
            # Equal weight per asset
            weight = 1.0 / len(self.assets)
            for asset in self.assets:
                allocations[asset] = weight

        elif self.allocation_method == AssetAllocationMethod.SIGNAL_STRENGTH:
            # Weight by signal strength (updated dynamically)
            for asset in self.assets:
                allocations[asset] = self.assets[asset].weight / sum(
                    a.weight for a in self.assets.values()
                )

        elif self.allocation_method == AssetAllocationMethod.RISK_PARITY:
            # Equal risk contribution: inverse-volatility weights once
            # realized volatility is observable (starts equal-weighted)
            for asset in self.assets:
                allocations[asset] = 1.0 / len(self.assets)

        else:  # VOLATILITY_TARGET
            # Inverse-volatility weighting scaled to the target: assets with
            # higher realized volatility get smaller weights (starts
            # equal-weighted until history arrives)
            for asset in self.assets:
                allocations[asset] = 1.0 / len(self.assets)

        return allocations

    def _realized_volatilities(self) -> Dict[str, Optional[float]]:
        """Return per-asset standard deviation of returns from price history."""
        vols: Dict[str, Optional[float]] = {}
        for asset in self.assets:
            prices = self.correlation_filter.price_history.get(asset, [])
            if len(prices) < self.correlation_filter.window + 1:
                vols[asset] = None
                continue
            arr = np.asarray(prices[-(self.correlation_filter.window + 1) :], float)
            if np.any(arr[:-1] <= 0):
                vols[asset] = None
                continue
            returns = np.diff(arr) / arr[:-1]
            std = float(np.std(returns, ddof=1)) if len(returns) > 1 else None
            vols[asset] = std if std and std > 0 else None
        return vols

    def recalculate_allocations(self) -> Dict[str, float]:
        """Recompute volatility-based allocations from accumulated prices.

        RISK_PARITY weights are inversely proportional to each asset's
        realized return volatility. VOLATILITY_TARGET additionally caps
        each asset's contribution at ``volatility_target`` before
        normalizing. Both fall back to the current allocations per asset
        while volatility is still unknown.
        """
        if self.allocation_method not in (
            AssetAllocationMethod.RISK_PARITY,
            AssetAllocationMethod.VOLATILITY_TARGET,
        ):
            return self.allocations

        vols = self._realized_volatilities()
        raw: Dict[str, float] = {}
        for asset in self.assets:
            vol = vols.get(asset)
            if vol is None:
                # Not enough history: keep the previous weight for this asset
                raw[asset] = self.allocations.get(asset, 1.0 / len(self.assets))
                continue
            if self.allocation_method == AssetAllocationMethod.RISK_PARITY:
                raw[asset] = 1.0 / vol
            else:  # VOLATILITY_TARGET
                target = self.volatility_target
                contribution = min(1.0, target / vol) if target > 0 else 1.0
                raw[asset] = contribution

        total = sum(raw.values())
        if total <= 0:
            return self.allocations

        self.allocations = {asset: w / total for asset, w in raw.items()}
        return self.allocations

    def update_asset_price(self, asset: str, price: float) -> None:
        """Update price for correlation calculation"""
        self.correlation_filter.update_price(asset, price)
        # Volatility-based allocations refine as history accumulates
        self.recalculate_allocations()

    def can_trade_asset(
        self, asset: str, signal: MultiAssetSignal, all_positions: List[Position]
    ) -> Tuple[bool, str]:
        """
        Check if asset can be traded

        Returns:
            Tuple of (can_trade, reason)
        """
        asset_config = self.assets.get(asset)
        if not asset_config:
            return False, f"Asset {asset} not configured"

        if not asset_config.enabled:
            return False, f"Asset {asset} is disabled"

        # Check max positions for this asset
        current_positions = self.asset_positions.get(asset, [])
        if len(current_positions) >= asset_config.max_positions:
            return (
                False,
                f"Max positions ({asset_config.max_positions}) reached for {asset}",
            )

        # Check correlation filter
        if self.correlation_filter.is_correlated(asset, all_positions):
            return False, f"Asset {asset} is correlated with existing positions"

        # Check confidence threshold
        min_conf = asset_config.min_signal_confidence
        if signal.action == Side.SHORT:
            min_conf = 100 - min_conf

        if signal.confidence < min_conf:
            return (
                False,
                f"Signal confidence ({signal.confidence}) below threshold ({min_conf})",
            )

        return True, ""

    def calculate_position_size(
        self, asset: str, signal: MultiAssetSignal, total_capital: float
    ) -> float:
        """
        Calculate position size for an asset

        Args:
            asset: Asset symbol
            signal: Trading signal
            total_capital: Total account capital

        Returns:
            Position quantity
        """
        allocation = self.allocations.get(asset, 1.0 / len(self.assets))
        asset_config = self.assets.get(asset)

        # Get risk per trade (asset-specific or global)
        if asset_config and asset_config.risk_per_trade is not None:
            risk_pct = asset_config.risk_per_trade
        else:
            risk_pct = self.base_config.RISK_PER_TRADE_PCT

        # Get leverage (asset-specific or global)
        if asset_config and asset_config.leverage is not None:
            leverage = asset_config.leverage
        else:
            leverage = self.base_config.LEVERAGE

        # Calculate position size
        allocated_capital = total_capital * allocation
        margin = allocated_capital * risk_pct
        notional = margin * leverage
        quantity = notional / signal.entry_price

        return quantity

    def add_position(self, asset: str, position: Position):
        """Add a position to the asset's position list"""
        if asset not in self.asset_positions:
            self.asset_positions[asset] = []

        # Add asset attribute to position
        position.asset = asset
        self.asset_positions[asset].append(position)

    def remove_position(self, asset: str, position: Position):
        """Remove a position from the asset's position list"""
        if asset in self.asset_positions and position in self.asset_positions[asset]:
            self.asset_positions[asset].remove(position)

    def get_all_positions(self) -> List[Position]:
        """Get all positions across all assets"""
        all_positions = []
        for positions in self.asset_positions.values():
            all_positions.extend(positions)
        return all_positions

    def get_total_exposure(self, current_prices: Dict[str, float]) -> Dict:
        """
        Calculate total portfolio exposure

        Returns:
            Dict with exposure metrics
        """
        total_value = 0
        total_long = 0
        total_short = 0

        for asset, positions in self.asset_positions.items():
            price = current_prices.get(asset, 0)
            for position in positions:
                value = position.quantity * price

                if position.side == Side.LONG:
                    total_long += value
                else:
                    total_short += value

                total_value += abs(value)

        return {
            "total_exposure": total_value,
            "long_exposure": total_long,
            "short_exposure": total_short,
            "net_exposure": total_long - total_short,
            "num_positions": sum(len(p) for p in self.asset_positions.values()),
            "num_assets_with_positions": sum(
                1 for p in self.asset_positions.values() if p
            ),
        }

    def get_asset_summary(self) -> pd.DataFrame:
        """Get summary of all assets"""
        data = []
        for asset, config in self.assets.items():
            positions = self.asset_positions.get(asset, [])
            allocation = self.allocations.get(asset, 0)

            data.append(
                {
                    "Asset": asset,
                    "Weight": config.weight,
                    "Allocation": f"{allocation:.1%}",
                    "Positions": len(positions),
                    "Max Positions": config.max_positions,
                    "Min Confidence": config.min_signal_confidence,
                    "Enabled": config.enabled,
                }
            )

        return pd.DataFrame(data)


def create_default_multi_asset_config(
    assets: List[str], equal_weights: bool = True
) -> List[AssetConfig]:
    """
    Create default asset configurations for a list of assets

    Args:
        assets: List of asset symbols
        equal_weights: Whether to use equal weights

    Returns:
        List of AssetConfig objects
    """
    configs = []

    for asset in assets:
        weight = 1.0 if equal_weights else 1.0  # Can be customized
        configs.append(
            AssetConfig(
                symbol=asset,
                weight=weight,
                max_positions=1,
                min_signal_confidence=45,
                enabled=True,
            )
        )

    return configs
