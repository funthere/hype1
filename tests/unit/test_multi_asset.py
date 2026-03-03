"""
Unit tests for Multi-Asset Strategy
"""

import pytest
import numpy as np
from datetime import datetime, timedelta

from src.core.config import BotConfig, Side, Position
from src.core.multi_asset import (
    AssetConfig,
    MultiAssetSignal,
    AssetAllocationMethod,
    CorrelationFilter,
    MultiAssetStrategy,
    create_default_multi_asset_config,
)


class TestAssetConfig:
    """Test AssetConfig dataclass"""

    def test_asset_config_creation(self):
        """Test asset config creation"""
        config = AssetConfig(
            symbol="HYPE",
            weight=1.0,
            max_positions=2,
            min_signal_confidence=50,
            enabled=True
        )

        assert config.symbol == "HYPE"
        assert config.weight == 1.0
        assert config.max_positions == 2
        assert config.min_signal_confidence == 50
        assert config.enabled is True


class TestMultiAssetSignal:
    """Test MultiAssetSignal dataclass"""

    def test_signal_creation(self):
        """Test signal creation"""
        signal = MultiAssetSignal(
            asset="HYPE",
            action=Side.LONG,
            confidence=75.0,
            entry_price=100.0,
            tp_price=105.0,
            sl_price=98.0,
            quantity=10.0,
            atr=2.0
        )

        assert signal.asset == "HYPE"
        assert signal.action == Side.LONG
        assert signal.confidence == 75.0


class TestCorrelationFilter:
    """Test CorrelationFilter"""

    def test_initialization(self):
        """Test correlation filter initialization"""
        filter = CorrelationFilter(max_correlation=0.7, window=50)

        assert filter.max_correlation == 0.7
        assert filter.window == 50
        assert filter.price_history == {}

    def test_update_price(self):
        """Test price update"""
        filter = CorrelationFilter()

        filter.update_price("HYPE", 100.0)
        filter.update_price("HYPE", 101.0)

        assert "HYPE" in filter.price_history
        assert len(filter.price_history["HYPE"]) == 2

    def test_correlation_calculation(self):
        """Test correlation calculation"""
        filter = CorrelationFilter(window=10)

        # Add correlated prices
        for i in range(20):
            base = 100 + i
            filter.update_price("HYPE", base + np.random.randn() * 0.5)
            filter.update_price("ETH", base * 2 + np.random.randn() * 1.0)

        corr = filter.get_correlation("HYPE", "ETH")

        # Should have a correlation value (not None)
        assert corr is not None
        assert -1 <= corr <= 1

    def test_is_correlated(self):
        """Test correlation check with positions"""
        filter = CorrelationFilter(max_correlation=0.7, window=20)

        # Add price history
        for i in range(30):
            base = 100 + i
            filter.update_price("HYPE", base + np.random.randn() * 0.5)
            filter.update_price("ETH", base * 2 + np.random.randn() * 1.0)
            filter.update_price("BTC", base * 0.5 + np.random.randn() * 2.0)

        # Create position with asset attribute
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5
        )
        position.asset = "ETH"

        # Check correlation (HYPE and ETH should be correlated)
        is_correlated = filter.is_correlated("HYPE", [position])

        # Result depends on random data, but should not crash
        assert isinstance(is_correlated, bool)

    def test_correlation_matrix(self):
        """Test correlation matrix generation"""
        filter = CorrelationFilter(window=10)

        assets = ["HYPE", "ETH", "BTC"]

        # Add price history
        for i in range(20):
            base = 100 + i
            filter.update_price("HYPE", base + np.random.randn() * 0.5)
            filter.update_price("ETH", base * 2 + np.random.randn() * 1.0)
            filter.update_price("BTC", base * 0.5 + np.random.randn() * 2.0)

        matrix = filter.get_correlation_matrix(assets)

        assert matrix.shape == (3, 3)
        assert list(matrix.columns) == assets
        assert list(matrix.index) == assets


class TestMultiAssetStrategy:
    """Test MultiAssetStrategy"""

    def test_initialization(self, sample_config):
        """Test multi-asset strategy initialization"""
        assets = [
            AssetConfig(symbol="HYPE", weight=1.0),
            AssetConfig(symbol="ETH", weight=0.5),
        ]

        strategy = MultiAssetStrategy(
            base_config=sample_config,
            assets=assets,
            allocation_method=AssetAllocationMethod.EQUAL_WEIGHT
        )

        assert len(strategy.assets) == 2
        assert "HYPE" in strategy.assets
        assert "ETH" in strategy.assets
        assert strategy.allocation_method == AssetAllocationMethod.EQUAL_WEIGHT

    def test_equal_weight_allocation(self, sample_config):
        """Test equal weight allocation"""
        assets = [
            AssetConfig(symbol="HYPE", weight=1.0),
            AssetConfig(symbol="ETH", weight=2.0),
            AssetConfig(symbol="BTC", weight=1.0),
        ]

        strategy = MultiAssetStrategy(
            base_config=sample_config,
            assets=assets,
            allocation_method=AssetAllocationMethod.EQUAL_WEIGHT
        )

        # Equal weight should give 1/3 to each
        assert abs(strategy.allocations["HYPE"] - 1/3) < 0.01
        assert abs(strategy.allocations["ETH"] - 1/3) < 0.01
        assert abs(strategy.allocations["BTC"] - 1/3) < 0.01

    def test_can_trade_asset_success(self, sample_config):
        """Test can_trade_asset when conditions are met"""
        assets = [AssetConfig(symbol="HYPE", weight=1.0)]
        strategy = MultiAssetStrategy(sample_config, assets)

        signal = MultiAssetSignal(
            asset="HYPE",
            action=Side.LONG,
            confidence=70,
            entry_price=100.0,
            tp_price=105.0,
            sl_price=98.0,
            quantity=10.0,
            atr=2.0
        )

        can_trade, reason = strategy.can_trade_asset("HYPE", signal, [])

        assert can_trade is True
        assert reason == ""

    def test_can_trade_asset_max_positions(self, sample_config):
        """Test can_trade_asset when max positions reached"""
        assets = [AssetConfig(symbol="HYPE", weight=1.0, max_positions=1)]
        strategy = MultiAssetStrategy(sample_config, assets)

        signal = MultiAssetSignal(
            asset="HYPE",
            action=Side.LONG,
            confidence=70,
            entry_price=100.0,
            tp_price=105.0,
            sl_price=98.0,
            quantity=10.0,
            atr=2.0
        )

        # Add existing position
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5
        )
        position.asset = "HYPE"

        strategy.add_position("HYPE", position)

        can_trade, reason = strategy.can_trade_asset("HYPE", signal, [])

        assert can_trade is False
        assert "Max positions" in reason

    def test_can_trade_asset_disabled(self, sample_config):
        """Test can_trade_asset for disabled asset"""
        # Include both enabled and disabled assets
        assets = [
            AssetConfig(symbol="HYPE", weight=1.0, enabled=False),
            AssetConfig(symbol="ETH", weight=1.0, enabled=True)
        ]
        strategy = MultiAssetStrategy(sample_config, assets)

        signal = MultiAssetSignal(
            asset="HYPE",
            action=Side.LONG,
            confidence=70,
            entry_price=100.0,
            tp_price=105.0,
            sl_price=98.0,
            quantity=10.0,
            atr=2.0
        )

        # HYPE should not be in strategy assets since it's disabled
        assert "HYPE" not in strategy.assets

        # Trying to trade a disabled asset should fail
        can_trade, reason = strategy.can_trade_asset("HYPE", signal, [])

        assert can_trade is False
        # The asset was filtered out during initialization
        assert "not configured" in reason.lower()

    def test_calculate_position_size(self, sample_config):
        """Test position size calculation"""
        assets = [AssetConfig(symbol="HYPE", weight=1.0)]
        strategy = MultiAssetStrategy(sample_config, assets)

        signal = MultiAssetSignal(
            asset="HYPE",
            action=Side.LONG,
            confidence=70,
            entry_price=100.0,
            tp_price=105.0,
            sl_price=98.0,
            quantity=10.0,
            atr=2.0
        )

        quantity = strategy.calculate_position_size("HYPE", signal, 10000)

        assert quantity > 0

    def test_add_remove_position(self, sample_config):
        """Test adding and removing positions"""
        assets = [AssetConfig(symbol="HYPE", weight=1.0)]
        strategy = MultiAssetStrategy(sample_config, assets)

        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5
        )

        strategy.add_position("HYPE", position)

        assert len(strategy.asset_positions["HYPE"]) == 1
        assert len(strategy.get_all_positions()) == 1

        strategy.remove_position("HYPE", position)

        assert len(strategy.asset_positions["HYPE"]) == 0
        assert len(strategy.get_all_positions()) == 0

    def test_get_total_exposure(self, sample_config):
        """Test total exposure calculation"""
        assets = [
            AssetConfig(symbol="HYPE", weight=1.0),
            AssetConfig(symbol="ETH", weight=1.0),
        ]
        strategy = MultiAssetStrategy(sample_config, assets)

        # Add positions
        for asset in ["HYPE", "ETH"]:
            position = Position(
                side=Side.LONG,
                entry_price=100.0,
                quantity=10.0,
                tp_price=105.0,
                sl_price=98.0,
                entry_time=datetime.now(),
                leverage=5
            )
            strategy.add_position(asset, position)

        current_prices = {"HYPE": 105.0, "ETH": 110.0}
        exposure = strategy.get_total_exposure(current_prices)

        assert exposure["num_positions"] == 2
        assert exposure["long_exposure"] > 0
        assert exposure["short_exposure"] == 0

    def test_get_asset_summary(self, sample_config):
        """Test asset summary generation"""
        assets = [
            AssetConfig(symbol="HYPE", weight=1.0, max_positions=2),
            AssetConfig(symbol="ETH", weight=0.5, enabled=True),
        ]
        strategy = MultiAssetStrategy(sample_config, assets)

        summary = strategy.get_asset_summary()

        assert len(summary) == 2
        assert summary.iloc[0]["Asset"] == "HYPE"
        assert summary.iloc[0]["Max Positions"] == 2


class TestCreateDefaultMultiAssetConfig:
    """Test default multi-asset config creation"""

    def test_equal_weights(self):
        """Test equal weight config creation"""
        assets = create_default_multi_asset_config(["HYPE", "ETH", "BTC"])

        assert len(assets) == 3
        assert all(a.weight == 1.0 for a in assets)
        assert all(a.enabled for a in assets)

    def test_custom_weights(self):
        """Test custom weight config creation"""
        assets = create_default_multi_asset_config(
            ["HYPE", "ETH"],
            equal_weights=False
        )

        assert len(assets) == 2
        assert all(a.weight == 1.0 for a in assets)  # Default is still 1.0
