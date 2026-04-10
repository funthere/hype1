"""
Mock objects and fixtures for integration tests
"""

from unittest.mock import Mock, MagicMock, AsyncMock
from datetime import datetime, timedelta
import asyncio
import pandas as pd

from src.core.config import BotConfig, Side, Position, Trade


@pytest.fixture
def mock_hyperliquid_api():
    """Mock HyperliquidAPI for testing"""
    api = Mock()
    
    # Connection check
    api.check_connection = AsyncMock(return_value=True)
    
    # Asset index
    api.get_asset_index = AsyncMock(return_value=0)
    
    # Orders
    api.place_order = AsyncMock(return_value={
        "status": "ok",
        "response": {"oid": 12345}
    })
    api.cancel_order = AsyncMock(return_value={"status": "ok"})
    api.cancel_all_orders = AsyncMock(return_value={"status": "ok"})
    api.get_open_orders = Mock(return_value=[])
    
    # Positions and balances
    api.get_positions = Mock(return_value=[])
    api.get_mids = Mock(return_value={"HYPE": 100.0})
    api.get_balance = AsyncMock(return_value={
        "account_value": 10000.0,
        "total_margin_used": 0.0,
    })
    
    return api


@pytest.fixture
def mock_market_data_feed():
    """Mock MarketDataFeed for testing"""
    feed = Mock()
    
    feed.callbacks = []
    feed.candles = []
    feed.current_candle = None
    feed.connected = False
    feed._ws = None
    feed._reconnect_attempts = 0
    
    # Mock methods
    feed.on_candle_update = Mock()
    feed.remove_callback = Mock()
    feed.connect = AsyncMock()
    feed.disconnect = AsyncMock()
    feed.get_historical_candles = Mock(return_value=pd.DataFrame())
    
    return feed


@pytest.fixture
def mock_telegram_notifier():
    """Mock TelegramNotifier for testing"""
    notifier = Mock()
    
    notifier._enabled = True
    notifier._client = AsyncMock()
    notifier.bot_token = "test_token"
    notifier.chat_id = "123456"
    
    # Mock async methods
    async def mock_send(message, parse_mode="Markdown"):
        return True
    
    notifier._send_message = AsyncMock(side_effect=mock_send)
    
    return notifier


@pytest.fixture
def mock_database_manager():
    """Mock DatabaseManager for testing"""
    db = Mock()
    
    # Create mock connection
    db.conn = Mock()
    db.conn.cursor = Mock(return_value=Mock())
    db.conn.commit = Mock()
    
    # Mock methods
    db.save_position = Mock(return_value=1)
    db.save_trade = Mock(return_value=1)
    db.save_daily_summary = Mock(return_value=1)
    db.log_event = Mock()
    db.get_trades = Mock(return_value=[])
    db.get_active_positions = Mock(return_value=[])
    db.get_trade_stats = Mock(return_value={
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
    })
    db.close = Mock()
    
    return db


@pytest.fixture
def sample_candles():
    """Generate sample candle data for testing"""
    np.random.seed(42)
    
    n_candles = 100
    base_price = 100.0
    
    timestamps = pd.date_range(
        start=datetime.now() - timedelta(hours=n_candles),
        periods=n_candles,
        freq="15min"
    )
    
    # Generate random walk prices
    import numpy as np
    price_changes = np.random.normal(0, 0.5, n_candles)
    prices = base_price + np.cumsum(price_changes)
    
    candles = []
    for i, ts in enumerate(timestamps):
        open_price = prices[i]
        close_price = prices[i] + np.random.normal(0, 0.1)
        high_price = max(open_price, close_price) + abs(np.random.normal(0, 0.2))
        low_price = min(open_price, close_price) - abs(np.random.normal(0, 0.2))
        volume = abs(np.random.normal(10000, 2000))
        
        candles.append({
            "timestamp": ts,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume
        })
    
    return candles


@pytest.fixture
def sample_positions():
    """Generate sample positions for testing"""
    return [
        Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now() - timedelta(hours=1),
            leverage=5,
            unrealized_pnl=50.0,
        ),
        Position(
            side=Side.SHORT,
            entry_price=100.0,
            quantity=10.0,
            tp_price=95.0,
            sl_price=102.0,
            entry_time=datetime.now() - timedelta(minutes=30),
            leverage=5,
            unrealized_pnl=-20.0,
        ),
    ]


@pytest.fixture
def sample_trades():
    """Generate sample trades for testing"""
    return [
        Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=2),
            exit_time=datetime.now() - timedelta(hours=1),
            pnl=50.0,
            fees=2.0,
        ),
        Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=98.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now() - timedelta(minutes=30),
            pnl=-20.0,
            fees=2.0,
        ),
        Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=102.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=4),
            exit_time=datetime.now() - timedelta(hours=3),
            pnl=20.0,
            fees=2.0,
        ),
    ]


@pytest.fixture
def async_event_loop():
    """Create an event loop for async tests"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()