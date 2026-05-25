"""
Tests for the cross-exchange funding rate arbitrage strategy.
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.strategy.cross_exchange_arb import (
    ArbPosition,
    ArbSide,
    CrossExchangeArbConfig,
    CrossExchangeArbStrategy,
    PairStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> CrossExchangeArbConfig:
    return CrossExchangeArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10000.0,
        COINS=["BTC", "ETH", "SOL"],
        ENTRY_THRESHOLD=0.0001,
        EXIT_THRESHOLD=0.00003,
        POSITION_SIZE_PCT=0.10,
        MAX_POSITION_SIZE_USD=5000.0,
        LEVERAGE=3,
        MAX_CONCURRENT_POSITIONS=3,
        MAX_HOLD_HOURS=72.0,
        SCAN_INTERVAL=10,
        DATABASE_PATH=":memory:",
    )


@pytest.fixture
def mock_hl_info():
    """Mock HyperLiquid Info SDK object."""
    info = MagicMock()
    info.meta_and_asset_ctxs = MagicMock(return_value=(
        {
            "universe": [
                {"name": "BTC"},
                {"name": "ETH"},
                {"name": "SOL"},
                {"name": "DOGE"},
            ]
        },
        [
            {"funding": "0.0012", "markPx": "68000"},   # BTC: 0.00015/hr
            {"funding": "0.0008", "markPx": "3500"},    # ETH: 0.0001/hr
            {"funding": "-0.0008", "markPx": "150"},    # SOL: -0.0001/hr
            {"funding": "0.0000", "markPx": "0.10"},    # DOGE: 0
        ],
    ))
    return info


@pytest.fixture
def mock_dydx_client():
    """Mock dYdX client."""
    client = MagicMock()
    client.get_all_funding_rates = AsyncMock(return_value={
        "BTC-USD": {"ticker": "BTC-USD", "rate_hourly": 0.00005, "oracle_px": 67990},
        "ETH-USD": {"ticker": "ETH-USD", "rate_hourly": 0.00015, "oracle_px": 3498},
        "SOL-USD": {"ticker": "SOL-USD", "rate_hourly": -0.00005, "oracle_px": 149.5},
    })
    client.healthcheck = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.log_event = MagicMock()
    db.close = MagicMock()
    return db


@pytest.fixture
def strategy(config, mock_hl_info, mock_db):
    strat = CrossExchangeArbStrategy(config, mock_hl_info, mock_db)
    return strat


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_values(self, config):
        assert config.PAPER_TRADING is True
        assert config.PAPER_CAPITAL == 10000.0
        assert config.COINS == ["BTC", "ETH", "SOL"]
        assert config.ENTRY_THRESHOLD == 0.0001
        assert config.LEVERAGE == 3

    def test_validate_ok(self, config):
        assert config.validate() is True

    def test_validate_bad_entry_threshold(self, config):
        config.ENTRY_THRESHOLD = 0
        with pytest.raises(ValueError, match="ENTRY_THRESHOLD"):
            config.validate()

    def test_validate_bad_exit_threshold(self, config):
        config.EXIT_THRESHOLD = -1
        with pytest.raises(ValueError, match="EXIT_THRESHOLD"):
            config.validate()

    def test_validate_empty_coins(self, config):
        config.COINS = []
        with pytest.raises(ValueError, match="COINS"):
            config.validate()


# ---------------------------------------------------------------------------
# Data fetching tests
# ---------------------------------------------------------------------------


class TestFetching:
    @pytest.mark.asyncio
    async def test_fetch_hl_rates(self, strategy):
        rates = await strategy.fetch_hl_funding_rates()
        assert "BTC" in rates
        assert "ETH" in rates
        assert "SOL" in rates
        # DOGE should not be included (not in COINS)
        assert "DOGE" not in rates
        # BTC rate: 0.0012 / 8 = 0.00015/hr
        assert abs(rates["BTC"]["rate_hourly"] - 0.00015) < 1e-10
        assert rates["BTC"]["mark_px"] == 68000.0

    @pytest.mark.asyncio
    async def test_fetch_dydx_rates(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        rates = await strategy.fetch_dydx_funding_rates()
        assert "BTC" in rates
        assert rates["BTC"]["rate_hourly"] == 0.00005

    @pytest.mark.asyncio
    async def test_fetch_dydx_no_client(self, strategy):
        rates = await strategy.fetch_dydx_funding_rates()
        assert rates == {}


# ---------------------------------------------------------------------------
# Position opening tests
# ---------------------------------------------------------------------------


class TestOpening:
    @pytest.mark.asyncio
    async def test_open_position_short_hl(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        pos_id = await strategy.open_position(
            coin="BTC",
            arb_side=ArbSide.SHORT_HL_LONG_DYDX,
            hl_rate=0.00015,
            dydx_rate=0.00005,
            hl_price=68000.0,
            dydx_price=67990.0,
        )
        assert pos_id is not None
        assert pos_id in strategy._positions
        pos = strategy._positions[pos_id]
        assert pos.coin == "BTC"
        assert pos.arb_side == ArbSide.SHORT_HL_LONG_DYDX
        assert pos.hl_side == "SHORT"
        assert pos.dydx_side == "LONG"
        assert pos.status == PairStatus.OPEN
        assert abs(pos.entry_spread - 0.0001) < 1e-10  # 0.00015 - 0.00005

    @pytest.mark.asyncio
    async def test_open_position_long_hl(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        pos_id = await strategy.open_position(
            coin="ETH",
            arb_side=ArbSide.LONG_HL_SHORT_DYDX,
            hl_rate=0.0001,
            dydx_rate=0.00015,
            hl_price=3500.0,
            dydx_price=3498.0,
        )
        assert pos_id is not None
        pos = strategy._positions[pos_id]
        assert pos.hl_side == "LONG"
        assert pos.dydx_side == "SHORT"

    @pytest.mark.asyncio
    async def test_no_duplicate_position(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        id1 = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        id2 = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68100.0, 68000.0,
        )
        assert id1 is not None
        assert id2 is None  # duplicate, should be rejected

    @pytest.mark.asyncio
    async def test_max_concurrent_positions(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        strategy.config.MAX_CONCURRENT_POSITIONS = 1

        id1 = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        id2 = await strategy.open_position(
            "ETH", ArbSide.LONG_HL_SHORT_DYDX,
            0.0001, 0.00015, 3500.0, 3498.0,
        )
        assert id1 is not None
        assert id2 is None  # max reached

    @pytest.mark.asyncio
    async def test_paper_fees_deducted(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        initial_capital = strategy._paper_capital

        await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        # Fees should have been deducted
        assert strategy._paper_capital < initial_capital


# ---------------------------------------------------------------------------
# Position closing tests
# ---------------------------------------------------------------------------


class TestClosing:
    @pytest.mark.asyncio
    async def test_close_position(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        pos_id = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        assert pos_id is not None

        result = await strategy.close_position(
            pos_id,
            reason="spread_narrowed",
            hl_price=68050.0,
            dydx_price=68040.0,
        )
        assert result is True
        pos = strategy._positions[pos_id]
        assert pos.status == PairStatus.CLOSED
        assert pos.close_reason == "spread_narrowed"

    @pytest.mark.asyncio
    async def test_close_nonexistent(self, strategy):
        result = await strategy.close_position("nonexistent", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_close_already_closed(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        pos_id = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        await strategy.close_position(pos_id, "test")
        result = await strategy.close_position(pos_id, "test2")
        assert result is False


# ---------------------------------------------------------------------------
# Run cycle / exit logic tests
# ---------------------------------------------------------------------------


class TestRunCycle:
    @pytest.mark.asyncio
    async def test_run_cycle_opens_and_closes(self, strategy, mock_dydx_client):
        """Simulate a full cycle where spread triggers entry."""
        strategy.set_dydx_client(mock_dydx_client)

        await strategy.run_cycle()
        # BTC spread = 0.00015 - 0.00005 = 0.0001 >= ENTRY_THRESHOLD → should open
        # ETH spread = 0.0001 - 0.00015 = -0.00005 < ENTRY_THRESHOLD → skip
        # SOL spread = -0.0001 - (-0.00005) = -0.00005 < ENTRY_THRESHOLD → skip
        open_count = sum(
            1 for p in strategy._positions.values()
            if p.status == PairStatus.OPEN
        )
        assert open_count >= 1


# ---------------------------------------------------------------------------
# Status tests
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_initial_status(self, strategy):
        status = strategy.get_status()
        assert status["cycle"] == 0
        assert status["paper_trading"] is True
        assert status["capital"] == 10000.0
        assert status["summary"]["open_count"] == 0
        assert status["summary"]["closed_count"] == 0

    @pytest.mark.asyncio
    async def test_status_after_open(self, strategy, mock_dydx_client):
        strategy.set_dydx_client(mock_dydx_client)
        await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        status = strategy.get_status()
        assert status["summary"]["open_count"] == 1
        assert len(status["positions"]["open"]) == 1


# ---------------------------------------------------------------------------
# Funding accumulation tests
# ---------------------------------------------------------------------------


class TestFundingAccumulation:
    @pytest.mark.asyncio
    async def test_funding_accumulated_for_short_hl(self, strategy, mock_dydx_client):
        """SHORT on HL with positive rate should accumulate funding."""
        strategy.set_dydx_client(mock_dydx_client)
        pos_id = await strategy.open_position(
            "BTC", ArbSide.SHORT_HL_LONG_DYDX,
            0.00015, 0.00005, 68000.0, 67990.0,
        )
        pos = strategy._positions[pos_id]

        # Simulate 1 hour of funding
        pos.last_funding_time = time.time() - 3600
        await strategy._accumulate_funding(
            pos,
            hl_rate=0.00015,
            dydx_rate=0.00005,
            now=time.time(),
        )
        # HL: SHORT receives funding = notional * rate * hours = 1000 * 0.00015 * 1 = 0.15
        # dYdX: LONG pays funding = notional * rate * hours = 1000 * 0.00005 * 1 = 0.05
        # Net = 0.15 - 0.05 = 0.10
        assert pos.total_funding_collected > 0
