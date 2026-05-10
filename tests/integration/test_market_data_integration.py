"""
Integration tests for Market data WebSocket handling
"""

import pytest
import asyncio
import json
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from src.core.config import BotConfig
from src.exchange.market_data import MarketDataFeed


@pytest.mark.integration
class TestMarketDataIntegration:
    """Integration tests for market data WebSocket with mocked connections"""

    @pytest.fixture
    def mock_config(self):
        """Create test configuration"""
        config = BotConfig()
        config.PAPER_TRADING = True
        config.ASSET = "HYPE"
        config.TIMEFRAME = "15m"
        config.WS_URL = "wss://test-ws-url.com/ws"
        return config

    @pytest.fixture
    def sample_candles(self):
        """Generate sample candle data"""
        candles = []
        base_time = datetime.now()

        for i in range(10):
            candles.append(
                {
                    "t": int(
                        (base_time.timestamp() + i * 900) * 1000
                    ),  # 15min intervals
                    "o": 100.0 + i,
                    "h": 101.0 + i,
                    "l": 99.5 + i,
                    "c": 100.5 + i,
                    "v": 10000 + i * 100,
                }
            )

        return candles

    @pytest.mark.asyncio
    async def test_initialization(self, mock_config):
        """Test market data feed initialization"""
        feed = MarketDataFeed(mock_config)

        assert feed.config == mock_config
        assert feed.ws_url == mock_config.WS_URL
        assert feed.callbacks == []
        assert len(feed.candles) == 0
        assert feed.connected is False
        assert feed._reconnect_attempts == 0

    @pytest.mark.asyncio
    async def test_callback_registration(self, mock_config):
        """Test callback registration"""
        feed = MarketDataFeed(mock_config)

        callback = Mock()
        feed.on_candle_update(callback)

        assert callback in feed.callbacks
        assert len(feed.callbacks) == 1

    @pytest.mark.asyncio
    async def test_callback_removal(self, mock_config):
        """Test callback removal"""
        feed = MarketDataFeed(mock_config)

        callback1 = Mock()
        callback2 = Mock()

        feed.on_candle_update(callback1)
        feed.on_candle_update(callback2)

        assert len(feed.callbacks) == 2

        feed.remove_callback(callback1)

        assert len(feed.callbacks) == 1
        assert callback2 in feed.callbacks
        assert callback1 not in feed.callbacks

    @pytest.mark.asyncio
    async def test_connect_success(self, mock_config, sample_candles):
        """Test successful WebSocket connection"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            # Mock successful subscribe
            mock_ws.send = AsyncMock()

            # Mock message listener
            async def mock_listen():
                # Simulate receiving subscription confirmation
                pass

            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock()

            feed = MarketDataFeed(mock_config)
            await feed.connect()

            assert feed.connected is True
            assert feed._reconnect_attempts == 0

    @pytest.mark.asyncio
    async def test_subscribe_to_market_data(self, mock_config):
        """Test subscription to market data"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws
            mock_ws.send = AsyncMock()

            feed = MarketDataFeed(mock_config)
            await feed.connect()

            # Verify subscribe message was sent
            assert mock_ws.send.called

            sent_message = json.loads(mock_ws.send.call_args[0][0])
            assert sent_message["method"] == "subscribe"
            assert sent_message["subscription"]["type"] == "candle"
            assert sent_message["subscription"]["coin"] == mock_config.ASSET
            assert sent_message["subscription"]["interval"] == mock_config.TIMEFRAME

    @pytest.mark.asyncio
    async def test_process_candle_message(self, mock_config):
        """Test processing candle messages from WebSocket"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            # Mock message with candle data
            candle_message = {
                "channel": "candle",
                "data": {
                    "t": 1234567890000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 10000,
                },
            }

            feed = MarketDataFeed(mock_config)

            # Register callback
            callback_mock = Mock()
            feed.on_candle_update(callback_mock)

            # Process message
            await feed._handle_message(json.dumps(candle_message))

            # Verify callback was called
            assert callback_mock.called
            call_args = callback_mock.call_args[0][0]

            assert call_args["timestamp"].year == 2024
            assert call_args["open"] == 100.0
            assert call_args["close"] == 100.5
            assert call_args["volume"] == 10000

    @pytest.mark.asyncio
    async def test_multiple_callbacks(self, mock_config):
        """Test that multiple callbacks receive candle updates"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            candle_data = {
                "channel": "candle",
                "data": {
                    "t": 1234567890000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 10000,
                },
            }

            feed = MarketDataFeed(mock_config)

            # Register multiple callbacks
            callback1 = Mock()
            callback2 = Mock()
            callback3 = Mock()

            feed.on_candle_update(callback1)
            feed.on_candle_update(callback2)
            feed.on_candle_update(callback3)

            # Process message
            await feed._handle_message(json.dumps(candle_data))

            # All callbacks should be called
            assert callback1.called
            assert callback2.called
            assert callback3.called

    @pytest.mark.asyncio
    async def test_async_callback_handling(self, mock_config):
        """Test that async callbacks are properly awaited"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            async def async_callback(candle_data):
                # Simulate async processing
                await asyncio.sleep(0)
                return True

            candle_data = {
                "channel": "candle",
                "data": {
                    "t": 1234567890000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 10000,
                },
            }

            feed = MarketDataFeed(mock_config)
            feed.on_candle_update(async_callback)

            # Process message (async callback should be awaited)
            await feed._handle_message(json.dumps(candle_data))

            # Async callback should have been called
            assert async_callback.called

    @pytest.mark.asyncio
    async def test_current_candle_tracking(self, mock_config):
        """Test that current candle is tracked"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            candle_data = {
                "channel": "candle",
                "data": {
                    "t": 1234567890000,
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 10000,
                },
            }

            feed = MarketDataFeed(mock_config)

            # Before any message
            assert feed.current_candle is None

            # Process message
            await feed._handle_message(json.dumps(candle_data))

            # Current candle should be updated
            assert feed.current_candle is not None
            assert feed.current_candle["close"] == 100.5

    @pytest.mark.asyncio
    async def test_disconnect(self, mock_config):
        """Test WebSocket disconnection"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws
            mock_ws.close = AsyncMock()

            feed = MarketDataFeed(mock_config)
            feed._ws = mock_ws
            feed.connected = True

            await feed.disconnect()

            assert feed.connected is False
            mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_logic(self, mock_config):
        """Test reconnection logic"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            # Simulate connection failure
            mock_connect.side_effect = [ConnectionError("Connection failed")]

            feed = MarketDataFeed(mock_config)

            # Attempt to connect (will fail)
            await feed.connect()

            # Should increment reconnect attempts
            assert feed._reconnect_attempts == 1
            assert feed.connected is False

    @pytest.mark.asyncio
    async def test_max_reconnect_attempts(self, mock_config):
        """Test that max reconnect attempts is respected"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            AsyncMock()

            # Always fail
            mock_connect.side_effect = ConnectionError("Connection failed")

            feed = MarketDataFeed(mock_config)

            # Try to connect many times
            for _ in range(15):
                try:
                    await feed.connect()
                except ConnectionError:
                    pass

            # Should stop at max attempts
            assert feed._reconnect_attempts <= feed._max_reconnect_attempts

    @pytest.mark.asyncio
    async def test_historical_candles(self, mock_config):
        """Test getting historical candles"""
        feed = MarketDataFeed(mock_config)

        # Mock historical fetch
        import pandas as pd

        df = pd.DataFrame()
        feed.get_historical_candles = Mock(return_value=df)

        candles = await feed.get_historical_candles(interval="1h", limit=100)

        assert feed.get_historical_candles.called
        assert len(candles) == 0  # Mock returns empty

    @pytest.mark.asyncio
    async def test_is_connected_property(self, mock_config):
        """Test is_connected property"""
        feed = MarketDataFeed(mock_config)

        # Initially disconnected
        assert feed.is_connected is False

        # Mark as connected
        feed.connected = True
        assert feed.is_connected is True

    @pytest.mark.asyncio
    async def test_reconnect_attempts_property(self, mock_config):
        """Test reconnect_attempts property"""
        feed = MarketDataFeed(mock_config)

        assert feed.reconnect_attempts == 0

        feed._reconnect_attempts = 5

        assert feed.reconnect_attempts == 5


@pytest.mark.integration
class TestMarketDataFlow:
    """Integration tests for complete market data flow"""

    @pytest.fixture
    def mock_config(self):
        """Create test configuration"""
        config = BotConfig()
        config.PAPER_TRADING = True
        config.ASSET = "HYPE"
        config.TIMEFRAME = "15m"
        config.WS_URL = "wss://test-ws-url.com/ws"
        return config

    @pytest.mark.asyncio
    async def test_end_to_end_candle_flow(self, mock_config):
        """Test complete flow from connection to candle processing"""
        with patch("src.exchange.market_data.websockets.connect") as mock_connect:
            received_candles = []

            # Create callback to capture candles
            def capture_candle(candle):
                received_candles.append(candle)

            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws
            mock_ws.send = AsyncMock()

            # Generate candle messages
            candle_messages = [
                {
                    "channel": "candle",
                    "data": {
                        "t": int((1234567890000 + i * 900000)),
                        "o": 100.0 + i * 0.1,
                        "h": 101.0 + i * 0.2,
                        "l": 99.5 + i * 0.1,
                        "c": 100.5 + i * 0.1,
                        "v": 10000 + i * 500,
                    },
                }
                for i in range(5)
            ]

            # Mock listen to send messages
            async def mock_listen():
                for msg in candle_messages:
                    await feed._handle_message(json.dumps(msg))

            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock()

            feed = MarketDataFeed(mock_config)
            feed.on_candle_update(capture_candle)

            # Start connection and message loop
            await feed.connect()

            # Should have received all candles
            assert len(received_candles) == 5

            # Verify candle data
            assert received_candles[0]["close"] > received_candles[0]["open"]
