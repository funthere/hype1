"""
Pytest configuration and fixtures
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.core.config import BotConfig, Side, Position, Trade
from src.core.strategy import StrategyEngine, RiskManager


@pytest.fixture
def sample_config():
    """Create a sample configuration for testing"""
    config = BotConfig()
    config.PAPER_TRADING = True
    config.ASSET = "HYPE"
    config.TIMEFRAME = "15m"
    config.LEVERAGE = 5
    config.RISK_PER_TRADE_PCT = 0.08
    config.TP_ATR_MULTIPLIER = 2.0
    config.SL_ATR_MULTIPLIER = 0.4
    config.MAX_POSITIONS = 2
    config.MAX_DAILY_TRADES = 20
    config.MAX_CONSECUTIVE_LOSSES = 3
    config.CONFIDENCE_THRESHOLD = 45
    return config


@pytest.fixture
def sample_candles():
    """Create sample candle data for testing"""
    np.random.seed(42)

    n_candles = 100
    base_price = 100.0

    timestamps = pd.date_range(
        start=datetime.now() - timedelta(hours=n_candles),
        periods=n_candles,
        freq="15min",
    )

    # Generate random walk prices
    price_changes = np.random.normal(0, 0.5, n_candles)
    prices = base_price + np.cumsum(price_changes)

    candles = []
    for i, ts in enumerate(timestamps):
        open_price = prices[i]
        close_price = prices[i] + np.random.normal(0, 0.1)
        high_price = max(open_price, close_price) + abs(np.random.normal(0, 0.2))
        low_price = min(open_price, close_price) - abs(np.random.normal(0, 0.2))
        volume = abs(np.random.normal(10000, 2000))

        candles.append(
            {
                "timestamp": ts,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )

    return candles


@pytest.fixture
def sample_position():
    """Create a sample position for testing"""
    return Position(
        side=Side.LONG,
        entry_price=100.0,
        quantity=10.0,
        tp_price=105.0,
        sl_price=98.0,
        entry_time=datetime.now(),
        leverage=5,
    )


@pytest.fixture
def sample_trade():
    """Create a sample completed trade for testing"""
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=105.0,
        quantity=10.0,
        entry_time=datetime.now() - timedelta(hours=1),
        exit_time=datetime.now(),
        pnl=50.0,
        fees=2.0,
    )


@pytest.fixture
def strategy_engine(sample_config):
    """Create a strategy engine for testing"""
    return StrategyEngine(sample_config)


@pytest.fixture
def risk_manager(sample_config):
    """Create a risk manager for testing"""
    return RiskManager(sample_config)
