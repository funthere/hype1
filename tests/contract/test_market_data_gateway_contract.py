"""
Exchange gateway contract tests.

Verifies that the Hyperliquid adapter satisfies the execution-layer gateway
protocols (trading and market data) that strategies depend on, and that the
market-data methods return raw exchange data or raise — never silently
return empty data on an outage.
"""

from unittest.mock import Mock, patch

import pytest

from src.core.config import BotConfig
from src.exchange.connector import HyperliquidAPI
from src.execution import MarketDataGateway, TradingGateway
from src.strategy.cross_exchange_arb import CrossExchangeArbConfig
from src.strategy.cross_exchange_arb import CrossExchangeArbStrategy
from src.strategy.funding_rate_arb import FundingArbConfig, FundingRateArbStrategy
from src.strategy.trend_following import TrendFollowingConfig, TrendFollowingStrategy

TEST_PRIVATE_KEY = "0x" + "1" * 64

META_AND_CTXS = (
    {"universe": [{"name": "HYPE"}, {"name": "ETH"}]},
    [
        {"funding": "0.0001", "markPx": "30", "midPx": "30", "openInterest": "100"},
        {"funding": "0.0002", "markPx": "3000", "midPx": "3000", "openInterest": "5"},
    ],
)

CANDLES = [
    {"t": 1, "o": "30", "c": "31", "h": "32", "l": "29", "v": "100"},
    {"t": 2, "o": "31", "c": "30.5", "h": "31.5", "l": "30", "v": "90"},
]


def build_api() -> HyperliquidAPI:
    """Build a HyperliquidAPI with mocked SDK clients (no network)."""
    config = BotConfig()
    config.PAPER_TRADING = False
    config.USE_TESTNET = True
    config.PRIVATE_KEY = TEST_PRIVATE_KEY
    with (
        patch("src.exchange.connector.Exchange"),
        patch("src.exchange.connector.Info") as mock_info,
        patch("src.exchange.connector.Account"),
    ):
        mock_info.return_value = Mock()
        return HyperliquidAPI(config)


@pytest.mark.contract
class TestGatewayProtocolConformance:
    def test_connector_satisfies_trading_gateway(self):
        assert isinstance(build_api(), TradingGateway)

    def test_connector_satisfies_market_data_gateway(self):
        assert isinstance(build_api(), MarketDataGateway)


@pytest.mark.contract
class TestMarketDataMethods:
    @pytest.fixture
    def api(self):
        return build_api()

    @pytest.mark.asyncio
    async def test_get_meta_and_asset_ctxs_returns_raw_pair(self, api):
        api.info.meta_and_asset_ctxs = Mock(return_value=META_AND_CTXS)

        meta, ctxs = await api.get_meta_and_asset_ctxs()

        assert meta["universe"][0]["name"] == "HYPE"
        assert len(ctxs) == 2

    @pytest.mark.asyncio
    async def test_get_meta_and_asset_ctxs_raises_on_malformed(self, api):
        api.info.meta_and_asset_ctxs = Mock(return_value=None)

        with pytest.raises(ValueError, match="unexpected format"):
            await api.get_meta_and_asset_ctxs()

    @pytest.mark.asyncio
    async def test_get_candles_returns_raw_list(self, api):
        api.info.candles_snapshot = Mock(return_value=CANDLES)

        candles = await api.get_candles("HYPE", "1h", 1000, 2000)

        assert candles == CANDLES

    @pytest.mark.asyncio
    async def test_get_candles_empty_snapshot_returns_empty_list(self, api):
        api.info.candles_snapshot = Mock(return_value=None)

        assert await api.get_candles("HYPE", "1h", 1000, 2000) == []


@pytest.mark.contract
class TestStrategyGatewayDependency:
    """Strategies must consume market data through the gateway port."""

    def _mock_api(self):
        return Mock()

    def test_trend_following_defaults_market_data_to_api(self):
        api = self._mock_api()
        strategy = TrendFollowingStrategy(TrendFollowingConfig(), api, db=None)
        assert strategy._market_data is api

    def test_trend_following_accepts_injected_gateway(self):
        api = self._mock_api()
        gateway = Mock()
        strategy = TrendFollowingStrategy(
            TrendFollowingConfig(), api, db=None, market_data=gateway
        )
        assert strategy._market_data is gateway

    def test_funding_arb_defaults_market_data_to_api(self):
        api = self._mock_api()
        strategy = FundingRateArbStrategy(FundingArbConfig(), api, db=None)
        assert strategy._market_data is api

    def test_cross_exchange_takes_market_data_port(self):
        gateway = Mock()
        strategy = CrossExchangeArbStrategy(CrossExchangeArbConfig(), gateway, db=None)
        assert strategy._market_data is gateway
