"""
Unit tests for HyperliquidAPI — src/exchange/connector.py

All SDK and network calls are mocked. Tests cover order placement,
cancellation, position queries, balance queries, error handling, and
spot market methods.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

from src.core.config import BotConfig, Side
from src.exchange.connector import HyperliquidAPI


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_PRIVATE_KEY = "0x" + "ab" * 32


@pytest.fixture
def mock_config():
    """Create a config with mock private key for testing"""
    config = BotConfig()
    config.PRIVATE_KEY = MOCK_PRIVATE_KEY
    config.PAPER_TRADING = False
    config.USE_TESTNET = False
    config.ASSET = "HYPE"
    config.ASSET_INDEX = 0
    return config


@pytest.fixture
def mock_config_with_account_address():
    config = BotConfig()
    config.PRIVATE_KEY = MOCK_PRIVATE_KEY
    config.ACCOUNT_ADDRESS = "0xaccount123"
    config.PAPER_TRADING = False
    return config


@pytest.fixture
def mock_sdk():
    """Patch the hyperliquid SDK and eth_account imports"""
    with patch("src.exchange.connector.Account") as mock_account_cls, \
         patch("src.exchange.connector.Info") as mock_info_cls, \
         patch("src.exchange.connector.Exchange") as mock_exchange_cls:

        # Mock account
        mock_account_instance = Mock()
        mock_account_instance.address = "0xTestAddress"
        mock_account_cls.from_key.return_value = mock_account_instance

        # Mock Info client
        mock_info_instance = Mock()
        mock_info_cls.return_value = mock_info_instance

        # Mock Exchange client
        mock_exchange_instance = Mock()
        mock_exchange_cls.return_value = mock_exchange_instance

        yield {
            "account_cls": mock_account_cls,
            "account": mock_account_instance,
            "info_cls": mock_info_cls,
            "info": mock_info_instance,
            "exchange_cls": mock_exchange_cls,
            "exchange": mock_exchange_instance,
        }


@pytest.fixture
def api(mock_config, mock_sdk):
    """Create a HyperliquidAPI instance with mocked SDK"""
    return HyperliquidAPI(mock_config)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestHyperliquidAPIInit:
    def test_initialization(self, mock_config, mock_sdk):
        api = HyperliquidAPI(mock_config)
        assert api.config == mock_config
        assert api.address == "0xTestAddress"
        assert api._connected is False
        assert api._last_error is None
        assert api._asset_index is None

    def test_account_address_passed_to_exchange(self, mock_config_with_account_address, mock_sdk):
        api = HyperliquidAPI(mock_config_with_account_address)
        # Exchange should have been called with account_address kwarg
        call_kwargs = mock_sdk["exchange_cls"].call_args
        assert call_kwargs[1].get("account_address") == "0xaccount123"

    def test_no_account_address_default(self, api, mock_sdk):
        # When no ACCOUNT_ADDRESS is set, Exchange constructor gets no extra kwargs
        call_args = mock_sdk["exchange_cls"].call_args
        assert call_args is not None
        assert "account_address" not in call_args[1]

    def test_is_connected_property(self, api):
        assert api.is_connected is False
        api._connected = True
        assert api.is_connected is True

    def test_last_error_property(self, api):
        assert api.last_error is None
        api._last_error = "Test error"
        assert api.last_error == "Test error"


# ---------------------------------------------------------------------------
# check_connection
# ---------------------------------------------------------------------------

class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_successful_connection(self, api, mock_sdk):
        # Mock get_asset_index to succeed
        api._asset_index = 5  # Pre-set to avoid meta call
        result = await api.check_connection()
        assert result is True
        assert api._connected is True
        assert api._last_error is None

    @pytest.mark.asyncio
    async def test_failed_connection(self, api, mock_sdk):
        api._asset_index = None
        # Make meta() raise an exception
        mock_sdk["info"].meta.side_effect = Exception("Network error")
        result = await api.check_connection()
        assert result is False
        assert api._connected is False
        assert api._last_error == "Network error"


# ---------------------------------------------------------------------------
# get_asset_index
# ---------------------------------------------------------------------------

class TestGetAssetIndex:
    @pytest.mark.asyncio
    async def test_cached_index_returned(self, api):
        api._asset_index = 7
        idx = await api.get_asset_index()
        assert idx == 7

    @pytest.mark.asyncio
    async def test_fetch_from_metadata(self, api, mock_sdk):
        api._asset_index = None
        api.config.ASSET = "HYPE"
        mock_sdk["info"].meta.return_value = {
            "universe": [
                {"name": "BTC"},
                {"name": "ETH"},
                {"name": "HYPE"},
            ]
        }
        idx = await api.get_asset_index()
        assert idx == 2
        assert api.config.ASSET_INDEX == 2

    @pytest.mark.asyncio
    async def test_asset_not_found(self, api, mock_sdk):
        api._asset_index = None
        api.config.ASSET = "NONEXISTENT"
        mock_sdk["info"].meta.return_value = {
            "universe": [{"name": "BTC"}, {"name": "ETH"}]
        }
        with pytest.raises(ValueError, match="not found"):
            await api.get_asset_index()

    @pytest.mark.asyncio
    async def test_meta_api_error(self, api, mock_sdk):
        api._asset_index = None
        mock_sdk["info"].meta.side_effect = Exception("API down")
        with pytest.raises(Exception, match="API down"):
            await api.get_asset_index()


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

class TestPlaceOrder:
    @pytest.mark.asyncio
    async def test_successful_limit_order(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {
            "status": "ok",
            "response": {"data": {"oid": 12345}},
        }
        result = await api.place_order(
            side=Side.LONG, price=100.0, quantity=5.0
        )
        assert result["status"] == "ok"
        mock_sdk["exchange"].order.assert_called_once()
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["is_buy"] is True
        assert call_kwargs["sz"] == 5.0
        assert call_kwargs["limit_px"] == 100.0

    @pytest.mark.asyncio
    async def test_short_order(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}
        result = await api.place_order(side=Side.SHORT, price=100.0, quantity=3.0)
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["is_buy"] is False

    @pytest.mark.asyncio
    async def test_order_types(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}

        # Limit (GTC)
        await api.place_order(side=Side.LONG, price=100.0, quantity=1.0, order_type="limit")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Gtc"}}

        # Post-only
        await api.place_order(side=Side.LONG, price=100.0, quantity=1.0, order_type="post_only")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Alo"}}

        # IOC
        await api.place_order(side=Side.LONG, price=100.0, quantity=1.0, order_type="ioc")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

    @pytest.mark.asyncio
    async def test_reduce_only_flag(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}
        await api.place_order(side=Side.LONG, price=100.0, quantity=1.0, reduce_only=True)
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["reduce_only"] is True

    @pytest.mark.asyncio
    async def test_cloid_passed(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}
        await api.place_order(side=Side.LONG, price=100.0, quantity=1.0, cloid="my_id")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["cloid"] == "my_id"

    @pytest.mark.asyncio
    async def test_order_failure_returns_error(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.return_value = {
            "status": "err",
            "response": {"error": "Insufficient margin"},
        }
        result = await api.place_order(side=Side.LONG, price=100.0, quantity=1.0)
        assert result["status"] == "error"
        assert "Insufficient margin" in result["msg"]

    @pytest.mark.asyncio
    async def test_order_exception_returns_error(self, api, mock_sdk):
        api._asset_index = 0
        mock_sdk["exchange"].order.side_effect = Exception("Timeout")
        result = await api.place_order(side=Side.LONG, price=100.0, quantity=1.0)
        assert result["status"] == "error"
        assert "Timeout" in result["msg"]


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_successful_cancel(self, api, mock_sdk):
        mock_sdk["exchange"].cancel.return_value = {"status": "ok"}
        result = await api.cancel_order(oid=12345)
        assert result["status"] == "ok"
        call_kwargs = mock_sdk["exchange"].cancel.call_args[1]
        assert call_kwargs["oid"] == 12345
        assert call_kwargs["coin"] == "HYPE"

    @pytest.mark.asyncio
    async def test_cancel_failure(self, api, mock_sdk):
        mock_sdk["exchange"].cancel.return_value = {
            "status": "err",
            "response": {"error": "Order not found"},
        }
        result = await api.cancel_order(oid=99999)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_cancel_exception(self, api, mock_sdk):
        mock_sdk["exchange"].cancel.side_effect = Exception("Network error")
        result = await api.cancel_order(oid=12345)
        assert result["status"] == "error"
        assert "Network error" in result["msg"]


# ---------------------------------------------------------------------------
# cancel_all_orders
# ---------------------------------------------------------------------------

class TestCancelAllOrders:
    @pytest.mark.asyncio
    async def test_cancel_all_with_orders(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = [
            {"oid": 1}, {"oid": 2}
        ]
        mock_sdk["exchange"].bulk_cancel.return_value = {"status": "ok"}
        result = await api.cancel_all_orders()
        assert result["status"] == "ok"
        mock_sdk["exchange"].bulk_cancel.assert_called_once()
        cancel_list = mock_sdk["exchange"].bulk_cancel.call_args[0][0]
        assert len(cancel_list) == 2

    @pytest.mark.asyncio
    async def test_cancel_all_no_orders(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = []
        result = await api.cancel_all_orders()
        assert result["status"] == "ok"
        mock_sdk["exchange"].bulk_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_all_failure(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = [{"oid": 1}]
        mock_sdk["exchange"].bulk_cancel.return_value = {
            "status": "err",
            "response": {"error": "Cancel failed"},
        }
        result = await api.cancel_all_orders()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_cancel_all_exception(self, api, mock_sdk):
        # When get_open_orders raises, cancel_all_orders wraps it and returns error.
        # However, the code calls self.get_open_orders() which returns [] on exception.
        # So cancel_all gets an empty list and returns {"status": "ok"}.
        # Let's test the actual behavior: get_open_orders error => empty list => ok
        mock_sdk["info"].open_orders.side_effect = Exception("Timeout")
        result = await api.cancel_all_orders()
        # The inner get_open_orders catches the exception and returns []
        # So cancel_all_orders sees no orders and returns ok
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# get_open_orders
# ---------------------------------------------------------------------------

class TestGetOpenOrders:
    @pytest.mark.asyncio
    async def test_returns_orders(self, api, mock_sdk):
        expected = [{"oid": 1}, {"oid": 2}]
        mock_sdk["info"].open_orders.return_value = expected
        orders = await api.get_open_orders()
        assert orders == expected

    @pytest.mark.asyncio
    async def test_returns_empty_on_none(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = None
        orders = await api.get_open_orders()
        assert orders == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, api, mock_sdk):
        mock_sdk["info"].open_orders.side_effect = Exception("API error")
        orders = await api.get_open_orders()
        assert orders == []


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "assetPositions": [
                {"position": {"coin": "HYPE", "szi": "10.0"}},
                {"position": {"coin": "BTC", "szi": "5.0"}},
            ]
        }
        positions = await api.get_positions()
        assert len(positions) == 2

    @pytest.mark.asyncio
    async def test_skips_empty_positions(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "assetPositions": [
                {"position": {"coin": "HYPE", "szi": "10.0"}},
                {"position": {}},  # Empty position
                {"position": None},  # None position
            ]
        }
        positions = await api.get_positions()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_no_address_returns_empty(self, api):
        api.address = None
        positions = await api.get_positions()
        assert positions == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, api, mock_sdk):
        mock_sdk["info"].user_state.side_effect = Exception("API error")
        positions = await api.get_positions()
        assert positions == []


# ---------------------------------------------------------------------------
# get_mids
# ---------------------------------------------------------------------------

class TestGetMids:
    @pytest.mark.asyncio
    async def test_returns_mids(self, api, mock_sdk):
        expected = {"HYPE": "100.5", "BTC": "50000.0"}
        mock_sdk["info"].all_mids.return_value = expected
        mids = await api.get_mids()
        assert mids == expected

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, api, mock_sdk):
        mock_sdk["info"].all_mids.side_effect = Exception("Error")
        mids = await api.get_mids()
        assert mids == {}


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------

class TestGetBalance:
    @pytest.mark.asyncio
    async def test_returns_balance(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "marginSummary": {
                "accountValue": "10000.0",
                "totalMarginUsed": "500.0",
            },
            "crossMarginSummary": {"totalNpos": "200.0"},
        }
        balance = await api.get_balance()
        assert balance["account_value"] == "10000.0"
        assert balance["total_margin_used"] == "500.0"
        assert balance["total_npos"] == "200.0"

    @pytest.mark.asyncio
    async def test_no_address_returns_empty(self, api):
        api.address = None
        balance = await api.get_balance()
        assert balance == {}

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, api, mock_sdk):
        mock_sdk["info"].user_state.side_effect = Exception("Error")
        balance = await api.get_balance()
        assert balance == {}


# ---------------------------------------------------------------------------
# get_user_state
# ---------------------------------------------------------------------------

class TestGetUserState:
    @pytest.mark.asyncio
    async def test_returns_state(self, api, mock_sdk):
        expected = {"marginSummary": {}, "assetPositions": []}
        mock_sdk["info"].user_state.return_value = expected
        state = await api.get_user_state()
        assert state == expected

    @pytest.mark.asyncio
    async def test_no_address_returns_empty(self, api):
        api.address = None
        state = await api.get_user_state()
        assert state == {}


# ---------------------------------------------------------------------------
# set_leverage
# ---------------------------------------------------------------------------

class TestSetLeverage:
    @pytest.mark.asyncio
    async def test_successful_set(self, api, mock_sdk):
        mock_sdk["exchange"].update_leverage.return_value = {"status": "ok"}
        result = await api.set_leverage(10)
        assert result["status"] == "ok"
        call_kwargs = mock_sdk["exchange"].update_leverage.call_args[1]
        assert call_kwargs["leverage"] == 10
        assert call_kwargs["coin"] == "HYPE"

    @pytest.mark.asyncio
    async def test_set_leverage_failure(self, api, mock_sdk):
        mock_sdk["exchange"].update_leverage.return_value = {
            "status": "err",
            "response": {"error": "Invalid leverage"},
        }
        result = await api.set_leverage(500)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_set_leverage_exception(self, api, mock_sdk):
        mock_sdk["exchange"].update_leverage.side_effect = Exception("Error")
        result = await api.set_leverage(5)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# get_order_status
# ---------------------------------------------------------------------------

class TestGetOrderStatus:
    @pytest.mark.asyncio
    async def test_found_order(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = [
            {"oid": 100}, {"oid": 200}, {"oid": 300}
        ]
        result = await api.get_order_status(200)
        assert result == {"oid": 200}

    @pytest.mark.asyncio
    async def test_not_found_order(self, api, mock_sdk):
        mock_sdk["info"].open_orders.return_value = [{"oid": 100}]
        result = await api.get_order_status(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self, api, mock_sdk):
        mock_sdk["info"].open_orders.side_effect = Exception("Error")
        result = await api.get_order_status(100)
        assert result is None


# ---------------------------------------------------------------------------
# get_recent_fills
# ---------------------------------------------------------------------------

class TestGetRecentFills:
    @pytest.mark.asyncio
    async def test_returns_fills(self, api, mock_sdk):
        fills = [{"fill": i} for i in range(10)]
        mock_sdk["info"].user_fills.return_value = fills
        result = await api.get_recent_fills(limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_no_address_returns_empty(self, api):
        api.address = None
        result = await api.get_recent_fills()
        assert result == []

    @pytest.mark.asyncio
    async def test_none_fills_returns_empty(self, api, mock_sdk):
        mock_sdk["info"].user_fills.return_value = None
        result = await api.get_recent_fills()
        assert result == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, api, mock_sdk):
        mock_sdk["info"].user_fills.side_effect = Exception("Error")
        result = await api.get_recent_fills()
        assert result == []


# ---------------------------------------------------------------------------
# Spot market methods
# ---------------------------------------------------------------------------

class TestPlaceSpotOrder:
    @pytest.mark.asyncio
    async def test_successful_spot_buy(self, api, mock_sdk):
        mock_sdk["exchange"].order.return_value = {
            "status": "ok",
            "response": {"data": {}},
        }
        result = await api.place_spot_order(
            coin="BTC", is_buy=True, price=50000.0, quantity=0.1
        )
        assert result["status"] == "ok"
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["coin"] == "BTC"
        assert call_kwargs["is_buy"] is True
        assert call_kwargs["sz"] == 0.1

    @pytest.mark.asyncio
    async def test_spot_sell(self, api, mock_sdk):
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}
        await api.place_spot_order(coin="ETH", is_buy=False, price=3000.0, quantity=1.0)
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["is_buy"] is False

    @pytest.mark.asyncio
    async def test_spot_order_types(self, api, mock_sdk):
        mock_sdk["exchange"].order.return_value = {"status": "ok", "response": {}}

        # IOC (default)
        await api.place_spot_order(coin="BTC", is_buy=True, price=100.0, quantity=1.0)
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

        # Post-only
        await api.place_spot_order(coin="BTC", is_buy=True, price=100.0, quantity=1.0, order_type="post_only")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Alo"}}

        # Limit (GTC)
        await api.place_spot_order(coin="BTC", is_buy=True, price=100.0, quantity=1.0, order_type="limit")
        call_kwargs = mock_sdk["exchange"].order.call_args[1]
        assert call_kwargs["order_type"] == {"limit": {"tif": "Gtc"}}

    @pytest.mark.asyncio
    async def test_spot_order_failure(self, api, mock_sdk):
        mock_sdk["exchange"].order.return_value = {
            "status": "err",
            "response": {"error": "No liquidity"},
        }
        result = await api.place_spot_order(coin="BTC", is_buy=True, price=100.0, quantity=1.0)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_spot_order_exception(self, api, mock_sdk):
        mock_sdk["exchange"].order.side_effect = Exception("Network error")
        result = await api.place_spot_order(coin="BTC", is_buy=True, price=100.0, quantity=1.0)
        assert result["status"] == "error"


class TestGetSpotBalance:
    @pytest.mark.asyncio
    async def test_found_balance(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "balances": [
                {"coin": "BTC", "total": "0.5"},
                {"coin": "ETH", "total": "10.0"},
            ]
        }
        balance = await api.get_spot_balance("BTC")
        assert balance == 0.5

    @pytest.mark.asyncio
    async def test_not_found_returns_zero(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "balances": [{"coin": "BTC", "total": "0.5"}]
        }
        balance = await api.get_spot_balance("DOGE")
        assert balance == 0.0

    @pytest.mark.asyncio
    async def test_case_insensitive(self, api, mock_sdk):
        mock_sdk["info"].user_state.return_value = {
            "balances": [{"coin": "btc", "total": "1.0"}]
        }
        balance = await api.get_spot_balance("BTC")
        assert balance == 1.0

    @pytest.mark.asyncio
    async def test_error_returns_zero(self, api, mock_sdk):
        mock_sdk["info"].user_state.side_effect = Exception("Error")
        balance = await api.get_spot_balance("BTC")
        assert balance == 0.0


class TestGetSpotMidPrice:
    @pytest.mark.asyncio
    async def test_found_price(self, api, mock_sdk):
        mock_sdk["info"].all_mids.return_value = {"BTC": "50000.0"}
        price = await api.get_spot_mid_price("BTC")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_found_with_at_prefix(self, api, mock_sdk):
        mock_sdk["info"].all_mids.return_value = {"@BTC": "50000.0"}
        price = await api.get_spot_mid_price("BTC")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, api, mock_sdk):
        mock_sdk["info"].all_mids.return_value = {"ETH": "3000.0"}
        price = await api.get_spot_mid_price("BTC")
        assert price is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self, api, mock_sdk):
        mock_sdk["info"].all_mids.side_effect = Exception("Error")
        price = await api.get_spot_mid_price("BTC")
        assert price is None
