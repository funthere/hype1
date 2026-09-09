"""
Unit tests for Funding Rate Arbitrage Strategy — spot hedge (delta-neutral)
"""

import json
import pytest
from pathlib import Path
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
def config(tmp_path):
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
        DATABASE_PATH=str(tmp_path / "funding_arb_test.db"),
    )
    cfg.validate()
    return cfg


@pytest.fixture
def config_no_hedge(tmp_path):
    """Config with spot hedge disabled."""
    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        SPOT_HEDGE_ENABLED=False,
        DATABASE_PATH=str(tmp_path / "funding_arb_test.db"),
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

    async def test_hedge_disabled_no_hedge(self, config_no_hedge, mock_api, mock_db):
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
        mock_api.get_balance = AsyncMock(return_value={"account_value": "10000.0"})

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


# ---------------------------------------------------------------------------
# Paper capital persistence tests
# ---------------------------------------------------------------------------


class TestPaperCapitalPersistence:
    @pytest.mark.asyncio
    async def test_capital_restored_across_restart(self, config, mock_api, mock_db):
        """Closing a position at a loss must persist; a new strategy instance
        must resume from that capital, not reset to PAPER_CAPITAL."""
        config.SPOT_HEDGE_ENABLED = False  # isolate the perp leg's PnL
        strategy = FundingRateArbStrategy(config, mock_api, mock_db)
        pos_id = await strategy.open_position(
            "BTC", PositionSide.SHORT, 0.001, 50_000.0
        )
        assert pos_id is not None

        closed = await strategy.close_position(pos_id, "rate_reverted", 51_000.0)
        assert closed is True
        capital_after = strategy._paper_capital
        assert capital_after < 10_000.0  # fees + adverse move

        restarted = FundingRateArbStrategy(config, mock_api, mock_db)
        assert restarted._paper_capital == pytest.approx(capital_after)

    @pytest.mark.asyncio
    async def test_persistence_disabled_resets_capital(
        self, tmp_path, mock_api, mock_db
    ):
        cfg = FundingArbConfig(
            PAPER_TRADING=True,
            PAPER_CAPITAL=10_000.0,
            PERSIST_PAPER_CAPITAL=False,
            DATABASE_PATH=str(tmp_path / "funding_arb_test.db"),
        )
        strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
        pos_id = await strategy.open_position(
            "BTC", PositionSide.SHORT, 0.001, 50_000.0
        )
        await strategy.close_position(pos_id, "rate_reverted", 51_000.0)

        restarted = FundingRateArbStrategy(cfg, mock_api, mock_db)
        assert restarted._paper_capital == 10_000.0
        assert not (tmp_path / "funding_arb_test.db.paper_state.json").exists()

    def test_memory_db_never_writes_state(self, mock_api, mock_db):
        cfg = FundingArbConfig(
            PAPER_TRADING=True,
            DATABASE_PATH=":memory:",
        )
        strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
        assert strategy._paper_state_path is None


# ---------------------------------------------------------------------------
# Scan cache tests (display loop must not re-scan the API)
# ---------------------------------------------------------------------------


class TestScanCache:
    @pytest.mark.asyncio
    async def test_last_opportunities_populated_after_scan(
        self, config, mock_db, mock_api
    ):
        meta = {"universe": [{"name": "BTC"}, {"name": "ETH"}]}
        ctxs = [
            {"funding": "0.001", "markPx": "50000", "midPx": "50000"},
            {"funding": "0.00001", "markPx": "3000", "midPx": "3000"},
        ]

        class ScriptedGateway:
            async def get_meta_and_asset_ctxs(self):
                return meta, ctxs

            async def get_candles(self, coin, interval, start_ms, end_ms):
                return []

        strategy = FundingRateArbStrategy(
            config, mock_api, mock_db, market_data=ScriptedGateway()
        )
        rates = await strategy.scan_funding_rates()
        assert len(rates) == 2
        cached = strategy.last_opportunities
        assert cached == rates
        # Mutating the returned copy must not affect the strategy's cache
        cached.clear()
        assert strategy.last_opportunities == rates


# ---------------------------------------------------------------------------
# Live order direction (regression: the strategy's own PositionSide enum
# compared False against Side and would have sold every live LONG entry)
# ---------------------------------------------------------------------------


class TestLiveOrderDirection:
    @pytest.mark.asyncio
    async def test_open_long_places_buy(self, mock_api, mock_db):
        from src.core.config import Side as CoreSide

        cfg = FundingArbConfig(
            PAPER_TRADING=False,
            USE_TESTNET=True,
            PRIVATE_KEY="0x" + "1" * 64,
            ADDRESS="0x" + "1" * 40,
            SPOT_HEDGE_ENABLED=False,
        )
        cfg.validate()
        strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
        strategy.api.place_order = AsyncMock(return_value={"status": "ok"})
        strategy.api.get_balance = AsyncMock(return_value={"account_value": 10_000})

        await strategy.open_position(
            "BTC", PositionSide.LONG, rate=0.001, mark_px=50_000.0
        )

        kwargs = strategy.api.place_order.call_args.kwargs
        assert kwargs["side"] == CoreSide.LONG

    @pytest.mark.asyncio
    async def test_close_short_places_buy(self, mock_api, mock_db):
        from src.core.config import Side as CoreSide

        cfg = FundingArbConfig(
            PAPER_TRADING=False,
            USE_TESTNET=True,
            PRIVATE_KEY="0x" + "1" * 64,
            ADDRESS="0x" + "1" * 40,
            SPOT_HEDGE_ENABLED=False,
        )
        cfg.validate()
        strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
        strategy.api.place_order = AsyncMock(return_value={"status": "ok"})
        pos_id = await strategy.open_position(
            "BTC", PositionSide.SHORT, rate=0.001, mark_px=50_000.0
        )
        strategy.api.place_order.reset_mock()

        closed = await strategy.close_position(
            pos_id, "rate_reverted", current_price=49_000.0
        )

        assert closed is True
        kwargs = strategy.api.place_order.call_args.kwargs
        assert kwargs["side"] == CoreSide.LONG  # closing a SHORT buys back
        assert kwargs["reduce_only"] is True


# ---------------------------------------------------------------------------
# Paper-state restart continuity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_position_survives_restart(tmp_path, mock_api, mock_db):
    """An open paper position must be restored by the next process, not
    orphaned — capital alone loses the position book."""
    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        DATABASE_PATH=str(tmp_path / "funding_arb_test.db"),
    )
    cfg.validate()
    strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
    pos_id = await strategy.open_position("BTC", PositionSide.SHORT, 0.001, 50_000.0)
    capital_after_open = strategy._paper_capital

    restarted = FundingRateArbStrategy(cfg, mock_api, mock_db)

    assert restarted._paper_capital == capital_after_open
    restored = restarted._positions.get(pos_id)
    assert restored is not None
    assert restored.status == PositionStatus.OPEN
    assert restored.coin == "BTC"
    assert restored.side == PositionSide.SHORT
    assert restored.entry_price == 50_000.0
    assert restored.quantity == strategy._positions[pos_id].quantity


@pytest.mark.asyncio
async def test_closed_history_survives_restart(tmp_path, mock_api, mock_db):
    """Closed-trade history is the scorecard's evidence — a restart must
    preserve it, not reset the count to zero."""
    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        DATABASE_PATH=str(tmp_path / "funding_arb_test.db"),
    )
    cfg.validate()
    strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)
    pos_id = await strategy.open_position("ETH", PositionSide.LONG, -0.0005, 2_000.0)
    await strategy.close_position(pos_id, "rate_reverted", 2_100.0)
    expected_pnl = strategy.get_status()["summary"]["total_pnl"]
    assert strategy.get_status()["summary"]["closed_count"] == 1

    restarted = FundingRateArbStrategy(cfg, mock_api, mock_db)
    summary = restarted.get_status()["summary"]

    assert summary["closed_count"] == 1
    assert summary["total_pnl"] == pytest.approx(expected_pnl)
    assert restarted._paper_capital == pytest.approx(strategy._paper_capital)


def test_legacy_capital_only_state_still_loads(tmp_path, mock_api, mock_db):
    """State files written before position persistence existed (capital
    only) must still restore capital without crashing."""
    db_path = tmp_path / "funding_arb_legacy.db"
    state_path = Path(str(db_path) + ".paper_state.json")
    state_path.write_text(json.dumps({"paper_capital": 5_000.0, "saved_at": 0.0}))

    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        DATABASE_PATH=str(db_path),
    )
    cfg.validate()
    strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)

    assert strategy._paper_capital == 5_000.0
    assert strategy._positions == {}


def test_corrupt_position_entry_skipped_not_fatal(tmp_path, mock_api, mock_db):
    """One bad entry in the state file must not discard capital or the
    rest of the position book."""
    db_path = tmp_path / "funding_arb_corrupt.db"
    state_path = Path(str(db_path) + ".paper_state.json")
    state_path.write_text(
        json.dumps(
            {
                "paper_capital": 9_000.0,
                "open_positions": [
                    {
                        "id": "good1",
                        "coin": "BTC",
                        "side": "SHORT",
                        "entry_rate": 0.001,
                        "entry_price": 50_000.0,
                        "quantity": 0.02,
                        "notional": 1_000.0,
                        "entry_time": 1.0,
                        "last_funding_time": 1.0,
                    },
                    {"id": "bad1", "side": "NOT_A_SIDE"},
                ],
                "closed_positions": [],
            }
        )
    )

    cfg = FundingArbConfig(
        PAPER_TRADING=True,
        PAPER_CAPITAL=10_000.0,
        DATABASE_PATH=str(db_path),
    )
    cfg.validate()
    strategy = FundingRateArbStrategy(cfg, mock_api, mock_db)

    assert strategy._paper_capital == 9_000.0
    assert set(strategy._positions) == {"good1"}
    assert strategy._positions["good1"].side == PositionSide.SHORT
