"""
Integration tests for Exchange connector
"""

import asyncio
import pytest
from unittest.mock import Mock, patch

from src.core.config import BotConfig, Side
from src.exchange.connector import HyperliquidAPI


@pytest.fixture(autouse=True)
def mock_to_thread(monkeypatch):
    """Make asyncio.to_thread call functions directly for testing"""

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)


@pytest.mark.integration
class TestHyperliquidAPIIntegration:
    """Integration tests for HyperliquidAPI with mocked exchange"""

    @pytest.fixture
    def mock_config(self):
        """Create test configuration"""
        config = BotConfig()
        config.PAPER_TRADING = False
        config.USE_TESTNET = True
        config.ASSET = "HYPE"
        config.PRIVATE_KEY = "0x" + "1" * 64
        return config

    @pytest.fixture
    def mock_exchange_api(self, mock_config):
        """Create HyperliquidAPI with mocked exchange"""
        with patch("src.exchange.connector.Exchange") as mock_exchange:
            with patch("src.exchange.connector.Info") as mock_info:
                with patch("src.exchange.connector.Account"):
                    # Setup mocks
                    mock_info.return_value = Mock()
                    mock_info.return_value.meta = Mock(
                        return_value={
                            "universe": [
                                {"name": "HYPE"},
                                {"name": "ETH"},
                                {"name": "BTC"},
                            ]
                        }
                    )

                    mock_exchange_instance = Mock()
                    mock_exchange_instance.order = Mock(
                        return_value={"status": "ok", "response": {"oid": 12345}}
                    )

                    mock_exchange_instance.cancel = Mock(return_value={"status": "ok"})
                    mock_exchange_instance.bulk_cancel = Mock(
                        return_value={"status": "ok"}
                    )

                    mock_exchange_instance.update_leverage = Mock(
                        return_value={"status": "ok"}
                    )

                    mock_account_instance = Mock()
                    mock_account_instance.from_key = Mock(
                        return_value=mock_account_instance
                    )
                    mock_account_instance.address = "0xTestAddress"

                    # Link mock exchange instance so Exchange(...) returns it
                    mock_exchange.return_value = mock_exchange_instance

                    # Create API instance
                    api = HyperliquidAPI(mock_config)

                    yield api

    def test_initialization(self, mock_config):
        """Test API initialization"""
        with patch("src.exchange.connector.Exchange"):
            with patch("src.exchange.connector.Info"):
                with patch("src.exchange.connector.Account"):
                    api = HyperliquidAPI(mock_config)

                    assert api.config == mock_config
                    assert api._asset_index is None

    @pytest.mark.asyncio
    async def test_check_connection_success(self, mock_exchange_api):
        """Test successful connection check"""
        result = await mock_exchange_api.check_connection()

        assert result is True
        assert mock_exchange_api._connected is True
        assert mock_exchange_api._last_error is None

    @pytest.mark.asyncio
    async def test_check_connection_failure(self, mock_config):
        """Test failed connection check"""
        with patch("src.exchange.connector.Exchange"):
            with patch("src.exchange.connector.Info") as mock_info_class:
                with patch("src.exchange.connector.Account"):
                    mock_info_class.return_value = Mock()
                    mock_info_class.return_value.meta = Mock(
                        side_effect=Exception("Connection failed")
                    )

                    api = HyperliquidAPI(mock_config)

                    result = await api.check_connection()

                    assert result is False
                    assert api._connected is False
                    assert api._last_error is not None

    @pytest.mark.asyncio
    async def test_get_asset_index(self, mock_exchange_api):
        """Test getting asset index"""
        asset_index = await mock_exchange_api.get_asset_index()

        assert asset_index == 0
        assert mock_exchange_api._asset_index == 0
        assert mock_exchange_api.config.ASSET_INDEX == 0

    @pytest.mark.asyncio
    async def test_place_order_success(self, mock_exchange_api):
        """Test successful order placement"""
        result = await mock_exchange_api.place_order(
            side=Side.LONG, price=100.0, quantity=10.0
        )

        assert result["status"] == "ok"
        assert "response" in result
        assert result["response"]["oid"] == 12345

    @pytest.mark.asyncio
    async def test_place_order_with_options(self, mock_exchange_api):
        """Test order placement with options"""
        result = await mock_exchange_api.place_order(
            side=Side.SHORT,
            price=100.0,
            quantity=10.0,
            reduce_only=True,
            cloid="test_cloid_123",
        )

        assert result["status"] == "ok"
        assert "response" in result

    @pytest.mark.asyncio
    async def test_place_order_failure(self, mock_exchange_api):
        """Test order placement failure"""
        with patch.object(mock_exchange_api.exchange, "order") as mock_order:
            mock_order.return_value = {
                "status": "error",
                "response": {"error": "Insufficient balance"},
            }

            result = await mock_exchange_api.place_order(
                side=Side.LONG, price=100.0, quantity=10.0
            )

            assert result["status"] == "error"
            assert "msg" in result

    @pytest.mark.asyncio
    async def test_cancel_order(self, mock_exchange_api):
        """Test order cancellation"""
        result = await mock_exchange_api.cancel_order(oid=12345)

        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, mock_exchange_api):
        """Test cancelling all orders"""
        # Mock having open orders
        with patch.object(mock_exchange_api, "get_open_orders") as mock_get_orders:
            mock_get_orders.return_value = [
                {"oid": 12345, "coin": "HYPE"},
                {"oid": 12346, "coin": "HYPE"},
            ]

            result = await mock_exchange_api.cancel_all_orders()

            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_get_open_orders(self, mock_exchange_api):
        """Test getting open orders"""
        with patch.object(mock_exchange_api.info, "open_orders") as mock_open_orders:
            mock_open_orders.return_value = [
                {"oid": 12345, "side": "B", "limitPx": 100.0}
            ]

            orders = await mock_exchange_api.get_open_orders()

            assert len(orders) == 1
            assert orders[0]["oid"] == 12345

    @pytest.mark.asyncio
    async def test_get_positions(self, mock_exchange_api):
        """Test getting positions"""
        with patch.object(mock_exchange_api.info, "user_state") as mock_user_state:
            mock_user_state.return_value = {
                "assetPositions": [
                    {
                        "position": {
                            "coin": "HYPE",
                            "szi": 0.5,
                            "entryPx": 100.0,
                            "unrealizedPnl": 50.0,
                        }
                    }
                ]
            }

            positions = await mock_exchange_api.get_positions()

            assert len(positions) == 1
            assert positions[0]["coin"] == "HYPE"
            assert positions[0]["szi"] == 0.5

    @pytest.mark.asyncio
    async def test_get_mids(self, mock_exchange_api):
        """Test getting mid prices"""
        with patch.object(mock_exchange_api.info, "all_mids") as mock_mids:
            mock_mids.return_value = {"HYPE": 100.5, "ETH": 2000.0, "BTC": 50000.0}

            mids = await mock_exchange_api.get_mids()

            assert mids["HYPE"] == 100.5
            assert mids["ETH"] == 2000.0

    @pytest.mark.asyncio
    async def test_get_balance(self, mock_exchange_api):
        """Test getting account balance"""
        with patch.object(mock_exchange_api.info, "user_state") as mock_user_state:
            mock_user_state.return_value = {
                "marginSummary": {"accountValue": 10500.0, "totalMarginUsed": 500.0},
                "crossMarginSummary": {"totalNpos": 1000.0},
            }

            balance = await mock_exchange_api.get_balance()

            assert balance["account_value"] == 10500.0
            assert balance["total_margin_used"] == 500.0
            assert balance["total_npos"] == 1000.0

    @pytest.mark.asyncio
    async def test_set_leverage(self, mock_exchange_api):
        """Test setting leverage"""
        result = await mock_exchange_api.set_leverage(leverage=10)

        assert result["status"] == "ok"
        mock_exchange_api.exchange.update_leverage.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_order_status(self, mock_exchange_api):
        """Test getting order status"""
        with patch.object(mock_exchange_api, "get_open_orders") as mock_get_orders:
            mock_get_orders.return_value = [
                {"oid": 12345, "side": "B", "status": "open"}
            ]

            status = await mock_exchange_api.get_order_status(oid=12345)

            assert status is not None
            assert status["oid"] == 12345
            assert status["status"] == "open"

    @pytest.mark.asyncio
    async def test_get_recent_fills(self, mock_exchange_api):
        """Test getting recent fills"""
        with patch.object(mock_exchange_api.info, "user_fills") as mock_fills:
            mock_fills.return_value = [
                {
                    "oid": 12345,
                    "coin": "HYPE",
                    "px": 100.0,
                    "sz": 10.0,
                    "time": 1234567890,
                }
            ]

            fills = await mock_exchange_api.get_recent_fills(limit=10)

            assert len(fills) == 1
            assert fills[0]["oid"] == 12345

    def test_is_connected_property(self, mock_exchange_api):
        """Test is_connected property"""
        # Test when connected
        mock_exchange_api._connected = True
        assert mock_exchange_api.is_connected is True

        # Test when disconnected
        mock_exchange_api._connected = False
        assert mock_exchange_api.is_connected is False

    def test_last_error_property(self, mock_exchange_api):
        """Test last_error property"""
        # Test with error
        mock_exchange_api._last_error = "Connection timeout"
        assert mock_exchange_api.last_error == "Connection timeout"

        # Test without error
        mock_exchange_api._last_error = None
        assert mock_exchange_api.last_error is None

    @pytest.mark.asyncio
    async def test_error_handling(self, mock_exchange_api):
        """Test error handling in API calls"""
        with patch.object(mock_exchange_api, "get_asset_index") as mock_get_asset:
            mock_get_asset.side_effect = Exception("Network error")

            # Should raise the exception
            with pytest.raises(Exception):
                await mock_exchange_api.get_asset_index()


@pytest.mark.integration
class TestExchangeAPIIntegration:
    """Integration tests with real API structure"""

    @pytest.fixture
    def config_with_api_wallet(self):
        """Create config with API wallet"""
        config = BotConfig()
        config.ACCOUNT_ADDRESS = "0xWalletAddress"
        config.USE_TESTNET = True
        config.ASSET = "HYPE"
        config.PRIVATE_KEY = "0x" + "1" * 64
        return config

    def test_exchange_with_api_wallet(self, config_with_api_wallet):
        """Test that exchange is created with API wallet"""
        with patch("src.exchange.connector.Exchange") as mock_exchange:
            HyperliquidAPI(config_with_api_wallet)

            # Exchange should be created with account_address
            mock_exchange.assert_called_once()
            call_kwargs = mock_exchange.call_args[1]
            assert "account_address" in call_kwargs
            assert (
                call_kwargs["account_address"] == config_with_api_wallet.ACCOUNT_ADDRESS
            )


@pytest.mark.integration
class TestExchangeConnectivity:
    """Integration tests for connectivity and reconnection"""

    @pytest.fixture
    def mock_config(self):
        """Create test configuration"""
        config = BotConfig()
        config.PAPER_TRADING = True
        config.USE_TESTNET = True
        config.ASSET = "HYPE"
        config.PRIVATE_KEY = "0x" + "1" * 64
        return config

    def test_asset_index_caching(self, mock_config):
        """Test that asset index is cached"""
        with patch("src.exchange.connector.Exchange"):
            with patch("src.exchange.connector.Info") as mock_info:
                api = HyperliquidAPI(mock_config)

                # Mock info.meta
                mock_info.return_value.meta = Mock(
                    return_value={"universe": [{"name": "HYPE"}]}
                )

                # First call should fetch from exchange
                asset_index_1 = (
                    api.get_asset_index()
                    if hasattr(api.get_asset_index, "__self__")
                    else None
                )
                if asset_index_1 is not None:
                    import asyncio

                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(asset_index_1)

                # Mock meta to avoid actual API call
                original_meta = mock_info.return_value.meta
                mock_info.return_value.meta = Mock(
                    return_value={"universe": [{"name": "HYPE"}, {"name": "ETH"}]}
                )

                # Second call should use cache
                asset_index_2 = (
                    api.get_asset_index()
                    if hasattr(api.get_asset_index, "__self__")
                    else None
                )
                if asset_index_2 is not None:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(asset_index_2)

                # First call should have queried universe once
                assert original_meta.call_count == 1


class TestOrderTypes:
    """Test order type parameter in place_order."""

    @pytest.fixture
    def api_with_mock(self):
        """Create API with mocked exchange for order type tests."""
        config = BotConfig()
        config.PAPER_TRADING = False
        config.USE_TESTNET = True
        config.ASSET = "HYPE"
        config.PRIVATE_KEY = "0x" + "1" * 64

        with patch("src.exchange.connector.Exchange") as mock_exchange:
            with patch("src.exchange.connector.Info") as mock_info:
                with patch("src.exchange.connector.Account"):
                    mock_info.return_value = Mock()
                    mock_info.return_value.meta = Mock(
                        return_value={"universe": [{"name": "HYPE"}]}
                    )
                    mock_exchange_instance = Mock()
                    mock_exchange_instance.order = Mock(
                        return_value={"status": "ok", "response": {"oid": 123}}
                    )
                    mock_exchange.return_value = mock_exchange_instance
                    api = HyperliquidAPI(config)
                    yield api, mock_exchange_instance

    @pytest.mark.asyncio
    async def test_place_order_post_only(self, api_with_mock):
        """Test post-only order (Alo tif)"""
        api, exchange = api_with_mock
        result = await api.place_order(
            side=Side.LONG, price=100.0, quantity=10.0, order_type="post_only"
        )
        assert result["status"] == "ok"
        call_kwargs = exchange.order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Alo"}}

    @pytest.mark.asyncio
    async def test_place_order_ioc(self, api_with_mock):
        """Test IOC order"""
        api, exchange = api_with_mock
        result = await api.place_order(
            side=Side.SHORT, price=105.0, quantity=5.0, order_type="ioc"
        )
        assert result["status"] == "ok"
        call_kwargs = exchange.order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

    @pytest.mark.asyncio
    async def test_place_order_default_gtc(self, api_with_mock):
        """Test default order type is GTC"""
        api, exchange = api_with_mock
        result = await api.place_order(
            side=Side.LONG, price=100.0, quantity=10.0
        )
        assert result["status"] == "ok"
        call_kwargs = exchange.order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Gtc"}}
