"""
Unit tests for Funding Rate Arbitrage Strategy — spot hedge (delta-neutral)
"""

import pytest
from unittest.mock import AsyncMock, Mock

from src.strategy.funding_rate_arb import (
    FundingArbConfig,
    FundingRateArbStrategy,
    PositionSide,
    PositionStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    """Create a test config with spot hedge enabled."""
    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        ENTRY_THRESHOLD=0.0003,
        EXIT_THRESHOLD=0.0001,
        LEVERAGE=3,
        MAX_CONCURRENT_POSITIONS=3,
        CHECK_INTERVAL=60,
        SPOT_HEDGE_ENABLED=True,
        SPOT_ELIGIBLE_COINS=["BTC", "ETH", "SOL", "HYPE"],
    )
    cfg.validate()
    return cfg


@pytest.fixture
def config_no_hedge():
    """Config with spot hedge disabled."""
    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        SPOT_HEDGE_ENABLED=False,
    )
    cfg.validate()
    return cfg


@pytest.fixture
def mock_api():
    """Mock API connector."""
    api = Mock()
    api.place_spot_order = AsyncMock(return_value={"status": "ok", "response": {}})
    api.place_order = AsyncMock(return_value={"status": "ok", "response": {}})
    api.get_mids = AsyncMock(return_value={"BTC": "50000.0"})
    api.get_balance = AsyncMock(return_value={"account_value": "10000.0"})
    return api


@pytest.fixture
def mock_db():
    """Mock database."""
    db = Mock()
    db.log_event = Mock()
    return db


@pytest.fixture
def strategy(config, mock_api, mock_db):
    """Create strategy instance with mocks."""
    return FundingRateArbStrategy(config, mock_api, mock_db)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestFundingArbConfig:
    def test_default_spot_eligible_coins(self):
        cfg = FundingArbConfig()
        assert "BTC" in cfg.SPOT_ELIGIBLE_COINS
        assert "ETH" in cfg.SPOT_ELIGIBLE_COINS

    def test_custom_spot_eligible_coins(self):
        cfg = FundingArbConfig(SPOT_ELIGIBLE_COINS=["DOGE", "SHIB"])
        assert cfg.SPOT_ELIGIBLE_COINS == ["DOGE", "SHIB"]

    def test_spot_hedge_enabled_default(self):
        cfg = FundingArbConfig()
        assert cfg.SPOT_HEDGE_ENABLED is True


# ---------------------------------------------------------------------------
# Spot hedge decision tests
# ---------------------------------------------------------------------------


class TestSpotHedgeDecision:
    def test_eligible_coin_hedge(self, strategy):
        assert strategy._should_spot_hedge("BTC") is True
        assert strategy._should_spot_hedge("btc") is True  # case-insensitive

    def test_ineligible_coin_no_hedge(self, strategy):
        assert strategy._should_spot_hedge("PEPE") is False
        assert strategy._should_spot_hedge("FARTCOIN") is False

    def test_disabled_no_hedge(self, config_no_hedge, mock_api, mock_db):
        s = FundingRateArbStrategy(config_no_hedge, mock_api, mock_db)
        assert s._should_spot_hedge("BTC") is False


# ---------------------------------------------------------------------------
# Open position with spot hedge tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenPositionWithSpotHedge:
    async def test_short_gets_spot_hedge(self, strategy):
        """SHORT perp on eligible coin should get a spot BUY hedge."""
        pid = await strategy.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        assert pid is not None
        pos = strategy._positions[pid]
        assert pos.spot_hedge_enabled is True
        assert pos.spot_quantity > 0
        assert pos.spot_entry_price == 50000.0

    async def test_long_no_spot_hedge(self, strategy):
        """LONG perp should NOT get spot hedge (delta already favorable)."""
        pid = await strategy.open_position(
            coin="ETH",
            side=PositionSide.LONG,
            rate=-0.0005,
            mark_px=3000.0,
        )
        assert pid is not None
        pos = strategy._positions[pid]
        # LONG perp — no spot hedge needed
        assert pos.spot_hedge_enabled is False

    async def test_ineligible_coin_no_hedge(self, strategy):
        """Ineligible coin should NOT get spot hedge."""
        pid = await strategy.open_position(
            coin="PEPE",
            side=PositionSide.SHORT,
            rate=0.0010,
            mark_px=0.01,
        )
        assert pid is not None
        pos = strategy._positions[pid]
        assert pos.spot_hedge_enabled is False

    async def test_hedge_disabled_no_hedge(
        self, config_no_hedge, mock_api, mock_db
    ):
        """When hedge disabled, no spot hedge even for eligible coins."""
        s = FundingRateArbStrategy(config_no_hedge, mock_api, mock_db)
        pid = await s.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        assert pid is not None
        pos = s._positions[pid]
        assert pos.spot_hedge_enabled is False


# ---------------------------------------------------------------------------
# Close position with spot hedge tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestClosePositionWithSpotHedge:
    async def test_close_short_with_hedge(self, strategy):
        """Closing SHORT+spot should sell spot and add spot PnL."""
        pid = await strategy.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        pos = strategy._positions[pid]
        assert pos.spot_hedge_enabled is True

        # Close at same price → spot PnL ~0 (minus fees)
        result = await strategy.close_position(pid, "rate_reverted", 50000.0)
        assert result is True
        assert pos.status == PositionStatus.CLOSED
        assert pos.spot_quantity == 0.0  # Reset after close

    async def test_close_short_spot_profit(self, strategy):
        """Closing SHORT+spot when price went up → spot gains offset perp loss."""
        pid = await strategy.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        pos = strategy._positions[pid]

        # Price went UP → perp SHORT loses, spot LONG gains
        result = await strategy.close_position(pid, "rate_reverted", 51000.0)
        assert result is True
        # Spot PnL should be positive (bought at 50000, now 51000)
        assert pos.spot_realized_pnl > 0

    async def test_close_short_spot_loss(self, strategy):
        """Closing SHORT+spot when price went down → spot loss, perp gains."""
        pid = await strategy.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        pos = strategy._positions[pid]

        # Price went DOWN → perp SHORT gains, spot LONG loses
        result = await strategy.close_position(pid, "rate_reverted", 49000.0)
        assert result is True
        # Spot PnL should be negative
        assert pos.spot_realized_pnl < 0

    async def test_close_no_hedge_no_spot_pnl(self, strategy):
        """Closing position without hedge should have zero spot PnL."""
        pid = await strategy.open_position(
            coin="PEPE",  # Not in SPOT_ELIGIBLE_COINS
            side=PositionSide.SHORT,
            rate=0.0010,
            mark_px=0.01,
        )
        pos = strategy._positions[pid]
        assert pos.spot_hedge_enabled is False

        result = await strategy.close_position(pid, "rate_reverted", 0.01)
        assert result is True
        assert pos.spot_realized_pnl == 0.0


# ---------------------------------------------------------------------------
# Status display tests
# ---------------------------------------------------------------------------


class TestStatusWithSpotHedge:
    @pytest.mark.asyncio
    async def test_status_includes_spot_fields(self, strategy):
        _pid = await strategy.open_position(
            coin="SOL",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=150.0,
        )
        status = strategy.get_status()
        open_positions = status["positions"]["open"]
        assert len(open_positions) == 1
        p = open_positions[0]
        assert "spot_hedged" in p
        assert p["spot_hedged"] is True
        assert "spot_qty" in p
        assert p["spot_qty"] > 0
        assert "spot_entry" in p
        assert p["spot_entry"] == 150.0


# ---------------------------------------------------------------------------
# Live mode spot order tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLiveSpotOrders:
    async def test_live_short_opens_spot_buy(self, mock_api, mock_db):
        """Live mode should call place_spot_order for SHORT."""
        config = FundingArbConfig(
            PAPER_TRADING=False,
            PRIVATE_KEY="0x" + "1" * 64,
            SPOT_HEDGE_ENABLED=True,
            SPOT_ELIGIBLE_COINS=["BTC"],
        )
        # Mock get_balance for position sizing
        mock_api.get_balance = AsyncMock(
            return_value={"account_value": "10000.0"}
        )

        s = FundingRateArbStrategy(config, mock_api, mock_db)
        # Patch _get_available_capital to avoid live API call
        s._get_available_capital = AsyncMock(return_value=10000.0)

        pid = await s.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        assert pid is not None
        # Should have called place_spot_order with is_buy=True
        mock_api.place_spot_order.assert_called_once_with(
            coin="BTC",
            is_buy=True,
            price=50000.0,
            quantity=pytest.approx(0.02, abs=0.001),
            order_type="ioc",
        )

    async def test_live_close_sells_spot(self, mock_api, mock_db):
        """Live mode should call place_spot_order with is_buy=False on close."""
        config = FundingArbConfig(
            PAPER_TRADING=False,
            PRIVATE_KEY="0x" + "1" * 64,
            SPOT_HEDGE_ENABLED=True,
            SPOT_ELIGIBLE_COINS=["BTC"],
        )

        s = FundingRateArbStrategy(config, mock_api, mock_db)
        s._get_available_capital = AsyncMock(return_value=10000.0)

        pid = await s.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )

        # Close
        result = await s.close_position(pid, "rate_reverted", 49000.0)
        assert result is True

        # Should have called place_spot_order with is_buy=False
        close_call = mock_api.place_spot_order.call_args_list[-1]
        assert close_call[1]["is_buy"] is False
        assert close_call[1]["coin"] == "BTC"

    async def test_live_spot_buy_failure_continues(self, mock_api, mock_db):
        """If spot BUY fails, perp position should still be tracked."""
        config = FundingArbConfig(
            PAPER_TRADING=False,
            PRIVATE_KEY="0x" + "1" * 64,
            SPOT_HEDGE_ENABLED=True,
            SPOT_ELIGIBLE_COINS=["BTC"],
        )
        # First call (spot buy) fails
        mock_api.place_spot_order = AsyncMock(
            return_value={"status": "error", "msg": "Insufficient spot liquidity"}
        )

        s = FundingRateArbStrategy(config, mock_api, mock_db)
        s._get_available_capital = AsyncMock(return_value=10000.0)

        pid = await s.open_position(
            coin="BTC",
            side=PositionSide.SHORT,
            rate=0.0005,
            mark_px=50000.0,
        )
        assert pid is not None
        # Position should exist but NOT be spot hedged
        pos = s._positions[pid]
        assert pos.spot_hedge_enabled is False
