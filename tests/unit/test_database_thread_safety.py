"""
Thread safety tests for async DatabaseManager.

Validates that the aiosqlite-backed DatabaseManager handles concurrent
operations correctly, uses WAL mode, and maintains transaction integrity.
"""

import asyncio
import pytest
import pytest_asyncio
import aiosqlite
from datetime import datetime, timedelta

from src.core.config import Side, OrderStatus, Position, Trade
from src.storage.database import DatabaseManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(index: int, side: Side = Side.LONG) -> Trade:
    """Build a Trade object with unique data for *index*."""
    now = datetime.now()
    return Trade(
        side=side,
        entry_price=100.0 + index,
        exit_price=105.0 + index,
        quantity=10.0,
        entry_time=now - timedelta(hours=1),
        exit_time=now,
        pnl=50.0 + index,
        fees=1.0,
        notes=f"concurrent-trade-{index}",
    )


def _make_position(index: int) -> Position:
    """Build a Position object with unique data for *index*."""
    return Position(
        side=Side.LONG if index % 2 == 0 else Side.SHORT,
        entry_price=100.0 + index,
        quantity=5.0 + index,
        tp_price=110.0 + index,
        sl_price=95.0 + index,
        entry_time=datetime.now(),
        leverage=5,
        oid=1000 + index,          # unique oid so positions are distinct
        status=OrderStatus.OPEN,
        unrealized_pnl=float(index),
    )


# ---------------------------------------------------------------------------
# Fixture  – uses pytest_asyncio.fixture for async setup/teardown
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a DatabaseManager backed by a temp file (auto-cleaned by pytest)."""
    db_file = tmp_path / "test_thread_safety.db"
    database = DatabaseManager(str(db_file))
    await database.initialize()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# 1. Concurrent writes — 10 coroutines writing trades simultaneously
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_writes_all_succeed(db: DatabaseManager):
    """10 coroutines write trades at the same time; all must succeed without corruption."""
    trades = [_make_trade(i) for i in range(10)]

    # Fire all writes concurrently
    results = await asyncio.gather(*[db.save_trade(t) for t in trades])

    # Every write should return a positive row id
    assert len(results) == 10
    assert all(isinstance(r, int) and r > 0 for r in results)

    # Verify all 10 rows are present and not corrupted
    saved = await db.get_trades(limit=20)
    assert len(saved) == 10

    notes_in_db = {row["notes"] for row in saved}
    expected_notes = {f"concurrent-trade-{i}" for i in range(10)}
    assert notes_in_db == expected_notes

    # Verify no cross-contamination of numeric fields
    for row in saved:
        idx = int(row["notes"].split("-")[-1])
        assert row["entry_price"] == pytest.approx(100.0 + idx)
        assert row["exit_price"] == pytest.approx(105.0 + idx)
        assert row["pnl"] == pytest.approx(50.0 + idx)


# ---------------------------------------------------------------------------
# 2. WAL mode verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wal_mode_enabled(db: DatabaseManager):
    """Database should be configured with WAL journal mode."""
    async with aiosqlite.connect(db.db_path) as conn:
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        mode = row[0].lower()
        # File-backed DBs should be 'wal'; in-memory DBs report 'memory'
        assert mode in ("wal", "memory"), f"Expected WAL or memory mode, got {mode}"


# ---------------------------------------------------------------------------
# 3. Connection lifecycle — initialize → operations → close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connection_lifecycle(tmp_path):
    """Full lifecycle: initialize(), do operations, close() — then confirm closed state."""
    db_file = tmp_path / "lifecycle_test.db"
    database = DatabaseManager(str(db_file))

    # Before init
    assert database._initialized is False

    # Initialize
    await database.initialize()
    assert database._initialized is True

    # Perform operations
    trade = _make_trade(0)
    trade_id = await database.save_trade(trade)
    assert trade_id > 0

    fetched = await database.get_trades(limit=10)
    assert len(fetched) == 1
    assert fetched[0]["id"] == trade_id

    # Health check should pass
    assert await database.health_check() is True

    # Close
    await database.close()
    assert database._initialized is False


# ---------------------------------------------------------------------------
# 4. Health check — returns True on a healthy DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_healthy(db: DatabaseManager):
    """health_check() must return True when the database is responsive."""
    result = await db.health_check()
    assert result is True


# ---------------------------------------------------------------------------
# 5. Concurrent read/write — no errors when one writes and another reads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_read_write_no_errors(db: DatabaseManager):
    """One coroutine writes while another reads — neither should raise."""
    async def writer():
        for i in range(20):
            await db.save_trade(_make_trade(i))

    async def reader():
        for _ in range(20):
            trades = await db.get_trades(limit=100)
            assert isinstance(trades, list)
            await asyncio.sleep(0)  # yield to event loop

    # Run both concurrently; any exception propagates and fails the test
    await asyncio.gather(writer(), reader())

    # Confirm writer inserted all rows
    final = await db.get_trades(limit=100)
    assert len(final) == 20


# ---------------------------------------------------------------------------
# 6. Transaction integrity — save position then close it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transaction_integrity(db: DatabaseManager):
    """Multi-step: save a position, then close it. Both steps must succeed together."""
    position = _make_position(0)
    pos_id = await db.save_position(position)
    assert pos_id > 0

    # Verify it's stored as open
    active = await db.get_active_positions()
    assert len(active) == 1
    assert active[0]["id"] == pos_id
    assert active[0]["status"] == "open"

    # Close it
    closed = await db.close_position(pos_id)
    assert closed is True

    # Verify it's no longer in active positions
    active_after = await db.get_active_positions()
    assert len(active_after) == 0


@pytest.mark.asyncio
async def test_transaction_integrity_rollback_on_missing(db: DatabaseManager):
    """Closing a non-existent position should return False (graceful failure, no crash)."""
    result = await db.close_position(99999)
    assert result is False
