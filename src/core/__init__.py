"""Core trading bot components"""

from .models import Side, OrderStatus, PositionStatus, Position, Trade
from .config import BotConfig
from .strategy import StrategyEngine, RiskManager
from .multi_asset import (
    AssetConfig,
    MultiAssetSignal,
    AssetAllocationMethod,
    CorrelationFilter,
    MultiAssetStrategy,
    create_default_multi_asset_config,
)

__all__ = [
    "BotConfig",
    "Side",
    "OrderStatus",
    "PositionStatus",
    "Position",
    "Trade",
    "StrategyEngine",
    "RiskManager",
    # Multi-asset
    "AssetConfig",
    "MultiAssetSignal",
    "AssetAllocationMethod",
    "CorrelationFilter",
    "MultiAssetStrategy",
    "create_default_multi_asset_config",
]
