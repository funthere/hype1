"""
Unit tests for PositionManager
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime, timedelta

from src.core.config import BotConfig, Side, Position, OrderStatus
from src.bot.position_manager import PositionManager


@pytest.fixture
def pm():
    return PositionManager()


@pytest.fixture
def config():
    c = BotConfig()
    c.PAPER_TRADING = True
    c.ASSET = "HYPE"
    c.LEVERAGE = 5
    return c


def _make_pos(side=Side.LONG, entry=100.0, qty=10.0):
    return Position(
        side=side,
        entry_price=entry,
        quantity=qty,
        tp_price=entry * 1.05,
        sl_price=entry * 0.98,
        entry_time=datetime.now(),
        leverage=5,
    )


class TestPositionManagerCRUD:

    def test_add_position(self, pm):
        pos = _make_pos()
        pm.add_position(pos)
        assert len(pm.positions) == 1
        assert pm.positions[0] is pos

    def test_remove_position(self, pm):
        pos = _make_pos()
        pm.add_position(pos)
        assert pm.remove_position(pos) is True
        assert len(pm.positions) == 0

    def test_remove_position_not_found(self, pm):
        pos = _make_pos()
        assert pm.remove_position(pos) is False

    def test_get_position_no_side(self, pm):
        pos = _make_pos()
        pm.add_position(pos)
        assert pm.get_position() is pos

    def test_get_position_by_side(self, pm):
        long_pos = _make_pos(Side.LONG)
        short_pos = _make_pos(Side.SHORT)
        pm.add_position(long_pos)
        pm.add_position(short_pos)
        assert pm.get_position(Side.LONG) is long_pos
        assert pm.get_position(Side.SHORT) is short_pos

    def test_get_position_empty(self, pm):
        assert pm.get_position() is None
        assert pm.get_position(Side.LONG) is None

    def test_get_all_positions(self, pm):
        p1 = _make_pos(Side.LONG)
        p2 = _make_pos(Side.SHORT)
        pm.add_position(p1)
        pm.add_position(p2)
        all_pos = pm.get_all_positions()
        assert len(all_pos) == 2
        assert all_pos is not pm.positions  # should be a copy


class TestPositionManagerUnrealizedPnL:

    @pytest.mark.asyncio
    async def test_update_long(self, pm, config):
        pos = _make_pos(Side.LONG, 100.0, 10.0)
        pos.unrealized_pnl = 0.0
        pm.add_position(pos)

        api = Mock()
        api.get_mids = AsyncMock(return_value={"HYPE": 105.0})
        db = Mock()

        await pm.update_unrealized_pnl(api, config, db)
        assert pos.unrealized_pnl == 50.0
        assert db.save_position.called

    @pytest.mark.asyncio
    async def test_update_short(self, pm, config):
        pos = _make_pos(Side.SHORT, 100.0, 10.0)
        pos.unrealized_pnl = 0.0
        pm.add_position(pos)

        api = Mock()
        api.get_mids = AsyncMock(return_value={"HYPE": 95.0})
        db = Mock()

        await pm.update_unrealized_pnl(api, config, db)
        assert pos.unrealized_pnl == 50.0

    @pytest.mark.asyncio
    async def test_update_no_positions(self, pm, config):
        api = Mock()
        api.get_mids = AsyncMock()
        db = Mock()
        await pm.update_unrealized_pnl(api, config, db)
        api.get_mids.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_uses_cached_price(self, pm, config):
        pos = _make_pos()
        pos.unrealized_pnl = 0.0
        pm.add_position(pos)
        pm._cached_mids["HYPE"] = 110.0
        pm._mids_last_update = datetime.now()

        api = Mock()
        api.get_mids = AsyncMock()
        db = Mock()

        await pm.update_unrealized_pnl(api, config, db)
        api.get_mids.assert_not_called()
        assert pos.unrealized_pnl == (110.0 - 100.0) * 10.0

    def test_calculate_total_unrealized_pnl(self, pm):
        p1 = _make_pos()
        p1.unrealized_pnl = 30.0
        p2 = _make_pos(Side.SHORT)
        p2.unrealized_pnl = -10.0
        pm.add_position(p1)
        pm.add_position(p2)
        assert pm.calculate_total_unrealized_pnl() == 20.0


class TestPositionManagerCachedPrice:

    def test_update_cached_price(self, pm):
        pm.update_cached_price("HYPE", 42.5)
        assert pm._cached_mids["HYPE"] == 42.5
        assert pm._mids_last_update is not None


class TestPositionManagerReconciliation:

    @pytest.mark.asyncio
    async def test_maybe_reconcile_skips_paper(self, pm, config):
        config.PAPER_TRADING = True
        api = Mock()
        api.get_positions = AsyncMock()
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()
        await pm.maybe_reconcile(api, config, db, tg, close_fn)
        api.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_reconcile_respects_interval(self, pm, config):
        config.PAPER_TRADING = False
        pm._last_reconciliation = datetime.now()
        api = Mock()
        api.get_positions = AsyncMock()
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()
        await pm.maybe_reconcile(api, config, db, tg, close_fn)
        api.get_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_detects_missing(self, pm, config):
        config.PAPER_TRADING = False
        config.ASSET = "HYPE"
        api = Mock()
        api.get_positions = AsyncMock(return_value=[])
        api.get_mids = AsyncMock(return_value={"HYPE": 100.0})
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()

        pos = _make_pos()
        pm.add_position(pos)

        await pm.reconcile_positions(api, config, db, tg, close_fn)
        close_fn.assert_called_once_with(pos, 100.0, "RECONCILE_MISSING")

    @pytest.mark.asyncio
    async def test_reconcile_restores_untracked(self, pm, config):
        config.PAPER_TRADING = False
        config.ASSET = "HYPE"
        api = Mock()
        api.get_positions = AsyncMock(
            return_value=[
                {"coin": "HYPE", "direction": "Long", "szi": "5.0", "entryPx": "42.0"}
            ]
        )
        api.get_mids = AsyncMock(return_value={"HYPE": 43.0})
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()

        await pm.reconcile_positions(api, config, db, tg, close_fn)
        assert len(pm.positions) == 1
        assert pm.positions[0].side == Side.LONG
        assert pm.positions[0].quantity == 5.0
        db.save_position.assert_called()
        db.log_event.assert_called()

    @pytest.mark.asyncio
    async def test_reconcile_fixes_quantity_drift(self, pm, config):
        config.PAPER_TRADING = False
        config.ASSET = "HYPE"
        api = Mock()
        api.get_positions = AsyncMock(
            return_value=[
                {"coin": "HYPE", "direction": "Long", "szi": "8.0", "entryPx": "42.0"}
            ]
        )
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()

        pos = _make_pos(Side.LONG, 42.0, 10.0)
        pm.add_position(pos)

        await pm.reconcile_positions(api, config, db, tg, close_fn)
        assert pos.quantity == 8.0

    @pytest.mark.asyncio
    async def test_reconcile_error_handling(self, pm, config):
        config.PAPER_TRADING = False
        api = Mock()
        api.get_positions = AsyncMock(side_effect=Exception("API down"))
        db = Mock()
        tg = Mock()
        close_fn = AsyncMock()
        # Should not raise
        await pm.reconcile_positions(api, config, db, tg, close_fn)
