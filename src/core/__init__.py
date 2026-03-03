"""Core trading bot components"""

from .config import BotConfig, Side, OrderStatus, Position, Trade
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
