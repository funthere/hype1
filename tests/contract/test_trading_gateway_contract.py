"""
Trading-gateway contract tests for the Hyperliquid adapter.

Pins the behavior of every order- and account-facing connector method with
mocked SDK clients: happy paths, exchange rejections, and transport
failures. Nothing here touches the network.
"""

from unittest.mock import Mock, patch

import pytest

from src.core.config import BotConfig, Side
from src.exchange.connector import HyperliquidAPI
from src.execution import SubmissionStatus

TEST_PRIVATE_KEY = "0x" + "1" * 64

UNIVERSE = {"universe": [{"name": "HYPE"}, {"name": "ETH"}]}


def build_api(asset: str = "HYPE") -> HyperliquidAPI:
    """HyperliquidAPI with mocked SDK clients (no network, no wallet)."""
    config = BotConfig()
    config.PAPER_TRADING = False
    config.USE_TESTNET = True
    config.ASSET = asset
    config.PRIVATE_KEY = TEST_PRIVATE_KEY
    with (
        patch("src.exchange.connector.Exchange"),
        patch("src.exchange.connector.Info") as mock_info,
        patch("src.exchange.connector.Account"),
    ):
        mock_info.return_value = Mock()
        return HyperliquidAPI(config)


@pytest.fixture
def api():
    api = build_api()
    api.info.meta = Mock(return_value=UNIVERSE)
    yield api


def ok_order_response(oid=12345):
    return {"status": "ok", "response": {"oid": oid}}


def err_order_response(msg="insufficient margin"):
    return {"status": "err", "response": {"error": msg}}


@pytest.mark.contract
class TestSubmitOrderOutcomes:
    @pytest.mark.asyncio
    async def test_transport_error_maps_to_unknown(self, api):
        api.exchange.order = Mock(side_effect=ConnectionError("socket closed"))

        submission = await api.submit_order(
            Mock(
                coin="HYPE",
                side=Side.LONG,
                quantity=1.0,
                price=30.0,
                client_order_id="clid-1",
                order_type="limit",
            )
        )

        assert submission.status == SubmissionStatus.UNKNOWN
        assert submission.client_order_id == "clid-1"
        assert "socket closed" in submission.message
        # Order writes must never be retried: exactly one attempt
        assert api.exchange.order.call_count == 1

    @pytest.mark.asyncio
    async def test_exchange_rejection_maps_to_rejected(self, api):
        api.exchange.order = Mock(return_value=err_order_response())

        submission = await api.submit_order(
            Mock(
                coin="HYPE",
                side=Side.SHORT,
                quantity=1.0,
                price=30.0,
                client_order_id="clid-2",
                order_type="limit",
            )
        )

        assert submission.status == SubmissionStatus.REJECTED
        assert submission.message == "insufficient margin"

    @pytest.mark.asyncio
    async def test_unknown_never_treated_as_accepted(self, api):
        """An ambiguous transport failure must not carry an order id."""
        api.exchange.order = Mock(side_effect=TimeoutError("timed out"))

        submission = await api.submit_order(
            Mock(
                coin="HYPE",
                side=Side.LONG,
                quantity=1.0,
                price=30.0,
                client_order_id="clid-3",
                order_type="limit",
            )
        )

        assert submission.status == SubmissionStatus.UNKNOWN
        assert submission.exchange_order_id is None


@pytest.mark.contract
class TestOrderTypeMapping:
    def test_post_only_maps_to_alo(self):
        assert HyperliquidAPI._order_type("post_only") == {"limit": {"tif": "Alo"}}

    def test_ioc_maps_to_ioc(self):
        assert HyperliquidAPI._order_type("ioc") == {"limit": {"tif": "Ioc"}}

    def test_limit_maps_to_gtc(self):
        assert HyperliquidAPI._order_type("limit") == {"limit": {"tif": "Gtc"}}

    def test_unknown_defaults_to_gtc(self):
        assert HyperliquidAPI._order_type("market") == {"limit": {"tif": "Gtc"}}


@pytest.mark.contract
class TestExtractOrderId:
    def test_top_level_oid(self):
        assert HyperliquidAPI._extract_order_id({"oid": 7}) == 7

    def test_order_nested_oid(self):
        assert HyperliquidAPI._extract_order_id({"order": {"oid": 9}}) == 9

    def test_data_oid(self):
        assert HyperliquidAPI._extract_order_id({"data": {"oid": "11"}}) == 11

    def test_non_numeric_candidates_skipped(self):
        response = {"oid": "not-a-number", "order": {"oid": 12}}
        assert HyperliquidAPI._extract_order_id(response) == 12

    def test_missing_oid_returns_none(self):
        assert HyperliquidAPI._extract_order_id({}) is None


@pytest.mark.contract
class TestPlaceOrderWrapper:
    @pytest.mark.asyncio
    async def test_accepted_includes_oid(self, api):
        api.exchange.order = Mock(return_value=ok_order_response(oid=77))

        result = await api.place_order(Side.LONG, price=30.0, quantity=2.0)

        assert result["status"] == "ok"
        assert result["response"]["oid"] == 77

    @pytest.mark.asyncio
    async def test_unknown_outcome(self, api):
        api.exchange.order = Mock(side_effect=ConnectionError("reset"))

        result = await api.place_order(Side.LONG, price=30.0, quantity=2.0)

        assert result["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_rejected_outcome(self, api):
        api.exchange.order = Mock(return_value=err_order_response("bad px"))

        result = await api.place_order(Side.LONG, price=30.0, quantity=2.0)

        assert result["status"] == "error"
        assert result["msg"] == "bad px"


@pytest.mark.contract
class TestCancelPaths:
    @pytest.mark.asyncio
    async def test_cancel_order_ok(self, api):
        api.exchange.cancel = Mock(return_value={"status": "ok"})
        assert await api.cancel_order(42) == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_cancel_order_exchange_error(self, api):
        api.exchange.cancel = Mock(
            return_value={"status": "err", "response": {"error": "no such order"}}
        )
        result = await api.cancel_order(42)
        assert result == {"status": "error", "msg": "no such order"}

    @pytest.mark.asyncio
    async def test_cancel_order_exception(self, api):
        api.exchange.cancel = Mock(side_effect=RuntimeError("boom"))
        result = await api.cancel_order(42)
        assert result["status"] == "error"
        assert "boom" in result["msg"]

    @pytest.mark.asyncio
    async def test_cancel_all_with_open_orders(self, api):
        api.info.open_orders = Mock(return_value=[{"oid": 1}, {"oid": 2}])
        api.exchange.bulk_cancel = Mock(return_value={"status": "ok"})
        assert await api.cancel_all_orders() == {"status": "ok"}
        assert api.exchange.bulk_cancel.call_count == 1

    @pytest.mark.asyncio
    async def test_cancel_all_without_orders_is_noop(self, api):
        api.info.open_orders = Mock(return_value=[])
        api.exchange.bulk_cancel = Mock()
        assert await api.cancel_all_orders() == {"status": "ok"}
        api.exchange.bulk_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_all_rejection(self, api):
        api.info.open_orders = Mock(return_value=[{"oid": 1}])
        api.exchange.bulk_cancel = Mock(
            return_value={"status": "err", "response": {"error": "denied"}}
        )
        result = await api.cancel_all_orders()
        assert result == {"status": "error", "msg": "denied"}

    @pytest.mark.asyncio
    async def test_cancel_all_survives_open_orders_outage(self, api):
        """get_open_orders fails safe to an empty list, so cancel_all
        degrades to a no-op rather than raising."""
        api.info.open_orders = Mock(side_effect=RuntimeError("net down"))
        assert await api.cancel_all_orders() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_get_open_orders_exception_returns_empty(self, api):
        api.info.open_orders = Mock(side_effect=RuntimeError("net down"))
        assert await api.get_open_orders() == []


@pytest.mark.contract
class TestPositionAndAccountReads:
    @pytest.mark.asyncio
    async def test_get_positions_without_address_is_unavailable(self, api):
        api.address = ""
        read = await api.get_positions()
        assert read.available is False
        assert "address" in read.error

    @pytest.mark.asyncio
    async def test_get_positions_outage_is_unavailable_not_flat(self, api):
        api.info.user_state = Mock(side_effect=RuntimeError("rpc down"))
        read = await api.get_positions()
        assert read.available is False
        assert "rpc down" in read.error

    @pytest.mark.asyncio
    async def test_get_mids_exception_returns_empty(self, api):
        api.info.all_mids = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_mids() == {}

    @pytest.mark.asyncio
    async def test_get_balance_happy_path(self, api):
        api.info.user_state = Mock(
            return_value={
                "marginSummary": {"accountValue": "1000", "totalMarginUsed": "10"},
                "crossMarginSummary": {"totalNpos": "3"},
            }
        )
        balance = await api.get_balance()
        assert balance["account_value"] == "1000"
        assert balance["total_margin_used"] == "10"
        assert balance["total_npos"] == "3"

    @pytest.mark.asyncio
    async def test_get_balance_without_address(self, api):
        api.address = ""
        assert await api.get_balance() == {}

    @pytest.mark.asyncio
    async def test_get_balance_exception(self, api):
        api.info.user_state = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_balance() == {}

    @pytest.mark.asyncio
    async def test_get_user_state_exception(self, api):
        api.info.user_state = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_user_state() == {}


@pytest.mark.contract
class TestLeverageAndOrderStatus:
    @pytest.mark.asyncio
    async def test_set_leverage_ok(self, api):
        api.exchange.update_leverage = Mock(return_value={"status": "ok"})
        assert await api.set_leverage(2) == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_set_leverage_rejected(self, api):
        api.exchange.update_leverage = Mock(
            return_value={"status": "err", "response": {"error": "too high"}}
        )
        result = await api.set_leverage(50)
        assert result == {"status": "error", "msg": "too high"}

    @pytest.mark.asyncio
    async def test_set_leverage_exception(self, api):
        api.exchange.update_leverage = Mock(side_effect=RuntimeError("boom"))
        result = await api.set_leverage(2)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_order_status_found(self, api):
        api.info.open_orders = Mock(return_value=[{"oid": 1}, {"oid": 5, "px": "30"}])
        status = await api.get_order_status(5)
        assert status == {"oid": 5, "px": "30"}

    @pytest.mark.asyncio
    async def test_get_order_status_not_found(self, api):
        api.info.open_orders = Mock(return_value=[{"oid": 1}])
        assert await api.get_order_status(99) is None

    @pytest.mark.asyncio
    async def test_get_order_status_exception(self, api):
        api.info.open_orders = Mock(side_effect=RuntimeError("net down"))
        assert await api.get_order_status(1) is None


@pytest.mark.contract
class TestRecentFills:
    @pytest.mark.asyncio
    async def test_recent_fills_respects_limit(self, api):
        api.info.user_fills = Mock(return_value=[{"oid": i} for i in range(10)])
        fills = await api.get_recent_fills(limit=3)
        assert len(fills) == 3

    @pytest.mark.asyncio
    async def test_recent_fills_without_address(self, api):
        api.address = ""
        assert await api.get_recent_fills() == []

    @pytest.mark.asyncio
    async def test_recent_fills_exception(self, api):
        api.info.user_fills = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_recent_fills() == []


@pytest.mark.contract
class TestClosePosition:
    @pytest.mark.asyncio
    async def test_close_with_explicit_price_ok(self, api):
        api.exchange.order = Mock(return_value=ok_order_response())

        result = await api.close_position(Side.LONG, quantity=1.0, price=30.5)

        assert result["status"] == "ok"
        # Closing a LONG means a reduce-only SHORT order
        kwargs = api.exchange.order.call_args.kwargs
        assert kwargs["is_buy"] is False
        assert kwargs["reduce_only"] is True
        assert kwargs["order_type"] == {"limit": {"tif": "Ioc"}}

    @pytest.mark.asyncio
    async def test_close_without_price_uses_mid(self, api):
        api.exchange.order = Mock(return_value=ok_order_response())
        api.info.all_mids = Mock(return_value={"HYPE": "29.9"})

        result = await api.close_position(Side.SHORT, quantity=1.0)

        assert result["status"] == "ok"
        kwargs = api.exchange.order.call_args.kwargs
        assert kwargs["is_buy"] is True  # closing a SHORT buys back
        assert kwargs["limit_px"] == 29.9

    @pytest.mark.asyncio
    async def test_close_without_valid_price_fails(self, api):
        api.info.all_mids = Mock(return_value={})
        result = await api.close_position(Side.LONG, quantity=1.0)
        assert result == {"status": "error", "msg": "Cannot close: no valid price"}

    @pytest.mark.asyncio
    async def test_close_exchange_rejection(self, api):
        api.exchange.order = Mock(return_value=err_order_response("ioc reject"))
        result = await api.close_position(Side.LONG, quantity=1.0, price=30.0)
        assert result == {"status": "error", "msg": "ioc reject"}

    @pytest.mark.asyncio
    async def test_close_exception(self, api):
        api.exchange.order = Mock(side_effect=RuntimeError("boom"))
        result = await api.close_position(Side.LONG, quantity=1.0, price=30.0)
        assert result["status"] == "error"


@pytest.mark.contract
class TestFundingRateReads:
    @pytest.mark.asyncio
    async def test_funding_rate_happy_path(self, api):
        api.info.meta_and_asset_ctxs = Mock(
            return_value=(
                UNIVERSE,
                [
                    {"funding": "0.0001", "markPx": "30", "midPx": "29.9"},
                    {"funding": "0.0002", "markPx": "3000", "midPx": "2999"},
                ],
            )
        )
        rate = await api.get_funding_rate("HYPE")
        assert rate["funding_rate"] == pytest.approx(0.0001)
        assert rate["mark_px"] == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_funding_rate_unknown_coin(self, api):
        api.info.meta_and_asset_ctxs = Mock(
            return_value=(UNIVERSE, [{"funding": "0.0001", "markPx": "30"}])
        )
        assert await api.get_funding_rate("NOPE") == {}

    @pytest.mark.asyncio
    async def test_funding_rate_malformed_payload(self, api):
        api.info.meta_and_asset_ctxs = Mock(return_value=None)
        assert await api.get_funding_rate() == {}

    @pytest.mark.asyncio
    async def test_funding_rate_exception(self, api):
        api.info.meta_and_asset_ctxs = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_funding_rate() == {}


@pytest.mark.contract
class TestSpotMethods:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "order_type,expected_tif",
        [
            ("ioc", "Ioc"),
            ("post_only", "Alo"),
            ("limit", "Gtc"),
        ],
    )
    async def test_place_spot_order_tif_mapping(self, api, order_type, expected_tif):
        api.exchange.order = Mock(return_value=ok_order_response())

        result = await api.place_spot_order(
            coin="BTC",
            is_buy=True,
            price=60000.0,
            quantity=0.1,
            order_type=order_type,
        )

        assert result["status"] == "ok"
        assert api.exchange.order.call_args.kwargs["order_type"] == {
            "limit": {"tif": expected_tif}
        }

    @pytest.mark.asyncio
    async def test_place_spot_order_rejected(self, api):
        api.exchange.order = Mock(return_value=err_order_response("bad spot px"))
        result = await api.place_spot_order(
            coin="BTC", is_buy=True, price=60000.0, quantity=0.1
        )
        assert result == {"status": "error", "msg": "bad spot px"}

    @pytest.mark.asyncio
    async def test_place_spot_order_exception(self, api):
        api.exchange.order = Mock(side_effect=RuntimeError("boom"))
        result = await api.place_spot_order(
            coin="BTC", is_buy=True, price=60000.0, quantity=0.1
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_spot_balance_found(self, api):
        api.info.user_state = Mock(
            return_value={"balances": [{"coin": "btc", "total": "1.5"}]}
        )
        assert await api.get_spot_balance("BTC") == 1.5

    @pytest.mark.asyncio
    async def test_get_spot_balance_missing(self, api):
        api.info.user_state = Mock(return_value={"balances": []})
        assert await api.get_spot_balance("BTC") == 0.0

    @pytest.mark.asyncio
    async def test_get_spot_balance_exception(self, api):
        api.info.user_state = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_spot_balance("BTC") == 0.0

    @pytest.mark.asyncio
    async def test_get_spot_mid_price_key_variants(self, api):
        api.info.all_mids = Mock(return_value={"@BTC": "60000.5"})
        assert await api.get_spot_mid_price("BTC") == 60000.5

    @pytest.mark.asyncio
    async def test_get_spot_mid_price_missing(self, api):
        api.info.all_mids = Mock(return_value={})
        assert await api.get_spot_mid_price("BTC") is None

    @pytest.mark.asyncio
    async def test_get_spot_mid_price_exception(self, api):
        api.info.all_mids = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.get_spot_mid_price("BTC") is None


@pytest.mark.contract
class TestAssetIndexLookup:
    @pytest.mark.asyncio
    async def test_unknown_coin_raises(self, api):
        with pytest.raises(ValueError, match="not found in universe"):
            await api.get_asset_index("NOPE")

    @pytest.mark.asyncio
    async def test_lookup_failure_propagates(self, api):
        api.info.meta = Mock(side_effect=RuntimeError("rpc down"))
        with pytest.raises(RuntimeError):
            await api.get_asset_index()

    @pytest.mark.asyncio
    async def test_request_scoped_coin_does_not_pollute_cache(self, api):
        cached = await api.get_asset_index()  # HYPE -> 0, cached
        eth_index = await api.get_asset_index("ETH")

        assert cached == 0
        assert eth_index == 1
        assert api._asset_index == 0  # config asset cache unchanged


@pytest.mark.contract
class TestConnectionState:
    @pytest.mark.asyncio
    async def test_properties_reflect_last_check(self, api):
        api.info.meta = Mock(return_value=UNIVERSE)
        assert await api.check_connection() is True
        assert api.is_connected is True
        assert api.last_error is None

        # Every check must probe the network: no cached shortcut may report
        # healthy while the exchange is unreachable.
        api.info.meta = Mock(side_effect=RuntimeError("rpc down"))
        assert await api.check_connection() is False
        assert api.is_connected is False
        assert "rpc down" in api.last_error

    @pytest.mark.asyncio
    async def test_repeat_checks_probe_the_network(self, api):
        """Regression: the check used get_asset_index, which serves the
        configured asset from cache — a repeat check reported healthy
        without touching the network."""
        api.info.meta = Mock(return_value=UNIVERSE)
        assert await api.check_connection() is True
        assert await api.check_connection() is True
        assert api.info.meta.call_count >= 2  # probed twice, not cached


@pytest.mark.contract
class TestSideCoercion:
    """Regression: the connector compared `side == Side.LONG` against plain
    strings and foreign enums, evaluating False — every live order from the
    strategies would have been a SELL, including LONG entries. Any side-like
    value must coerce at the boundary."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_side", ["BUY", "", 123, None])
    async def test_unusable_side_raises(self, api, bad_side):
        with pytest.raises(ValueError):
            await api.place_order(side=bad_side, price=30.0, quantity=1.0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "side_value,expected_is_buy",
        [
            (Side.LONG, True),
            (Side.SHORT, False),
            ("LONG", True),
            ("SHORT", False),
        ],
    )
    async def test_place_order_direction(self, api, side_value, expected_is_buy):
        api.exchange.order = Mock(return_value=ok_order_response())

        await api.place_order(side=side_value, price=30.0, quantity=1.0)

        assert api.exchange.order.call_args.kwargs["is_buy"] is expected_is_buy

    @pytest.mark.asyncio
    async def test_foreign_enum_with_matching_value_is_accepted(self, api):
        from enum import Enum

        class ForeignSide(Enum):
            LONG = "LONG"

        api.exchange.order = Mock(return_value=ok_order_response())
        await api.place_order(side=ForeignSide.LONG, price=30.0, quantity=1.0)
        assert api.exchange.order.call_args.kwargs["is_buy"] is True
