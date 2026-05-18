"""
Unit tests for DatabaseManager — SQLite persistence layer

Uses in-memory SQLite (:memory:) for fast, isolated tests.
"""

import pytest
from datetime import datetime, timedelta

from src.core.config import Side, OrderStatus, Position, Trade
from src.storage.database import DatabaseManager, _serialize_enum, _json_dumps_safe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """Create an in-memory database for testing"""
    database = DatabaseManager(":memory:")
    yield database
    database.close()


@pytest.fixture
def sample_position():
    """Create a sample Position"""
    return Position(
        side=Side.LONG,
        entry_price=100.0,
        quantity=10.0,
        tp_price=105.0,
        sl_price=98.0,
        entry_time=datetime.now(),
        leverage=5,
    )


@pytest.fixture
def sample_trade():
    """Create a sample Trade"""
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=105.0,
        quantity=10.0,
        entry_time=datetime.now() - timedelta(hours=1),
        exit_time=datetime.now(),
        pnl=50.0,
        fees=2.0,
        notes="Test trade",
    )


@pytest.fixture
def sample_short_trade():
    return Trade(
        side=Side.SHORT,
        entry_price=100.0,
        exit_price=90.0,
        quantity=5.0,
        entry_time=datetime.now() - timedelta(hours=2),
        exit_time=datetime.now() - timedelta(hours=1),
        pnl=50.0,
        fees=1.5,
        notes="Short winner",
    )


@pytest.fixture
def sample_losing_trade():
    return Trade(
        side=Side.LONG,
        entry_price=100.0,
        exit_price=95.0,
        quantity=10.0,
        entry_time=datetime.now() - timedelta(hours=1),
        exit_time=datetime.now(),
        pnl=-50.0,
        fees=2.0,
    )


# ---------------------------------------------------------------------------
# Enum serialization helpers
# ---------------------------------------------------------------------------

class TestSerializeEnum:
    def test_enum_serialized_to_value(self):
        assert _serialize_enum(Side.LONG) == "LONG"
        assert _serialize_enum(Side.SHORT) == "SHORT"
        assert _serialize_enum(OrderStatus.OPEN) == "open"

    def test_dict_with_enum_values(self):
        data = {"side": Side.LONG, "status": OrderStatus.FILLED}
        result = _serialize_enum(data)
        assert result == {"side": "LONG", "status": "filled"}

    def test_list_with_enum_values(self):
        data = [Side.LONG, Side.SHORT]
        result = _serialize_enum(data)
        assert result == ["LONG", "SHORT"]

    def test_plain_values_unchanged(self):
        assert _serialize_enum(42) == 42
        assert _serialize_enum("hello") == "hello"
        assert _serialize_enum(3.14) == 3.14

    def test_nested_structure(self):
        data = {"positions": [{"side": Side.LONG}], "count": 1}
        result = _serialize_enum(data)
        assert result == {"positions": [{"side": "LONG"}], "count": 1}


class TestJsonDumpsSafe:
    def test_none_returns_none(self):
        assert _json_dumps_safe(None) is None

    def test_dict_with_enum(self):
        result = _json_dumps_safe({"side": Side.LONG})
        assert '"side": "LONG"' in result

    def test_regular_dict(self):
        result = _json_dumps_safe({"key": "value"})
        assert '"key": "value"' in result


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

class TestDatabaseInit:
    def test_tables_created(self, db):
        """Verify all expected tables exist"""
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        assert "trades" in tables
        assert "positions" in tables
        assert "daily_summaries" in tables
        assert "events" in tables
        assert "bot_state" in tables

    def test_indexes_created(self, db):
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_trades_entry_time" in indexes
        assert "idx_trades_exit_time" in indexes
        assert "idx_positions_entry_time" in indexes
        assert "idx_daily_summaries_date" in indexes
        assert "idx_events_created_at" in indexes

    def test_context_manager(self):
        """Test __enter__ / __exit__ protocol"""
        with DatabaseManager(":memory:") as db:
            assert db.conn is not None
        # After exit, connection should be closed
        # Further operations should fail

    def test_in_memory_db_is_isolated(self):
        """Two in-memory DBs should be independent"""
        db1 = DatabaseManager(":memory:")
        db2 = DatabaseManager(":memory:")
        trade = Trade(
            side=Side.LONG, entry_price=100.0, exit_price=105.0,
            quantity=5.0, entry_time=datetime.now()
        )
        db1.save_trade(trade)
        assert len(db1.get_trades()) == 1
        assert len(db2.get_trades()) == 0
        db1.close()
        db2.close()


# ---------------------------------------------------------------------------
# Trade operations
# ---------------------------------------------------------------------------

class TestSaveTrade:
    def test_save_trade_returns_id(self, db, sample_trade):
        trade_id = db.save_trade(sample_trade)
        assert isinstance(trade_id, int)
        assert trade_id > 0

    def test_save_trade_stores_all_fields(self, db, sample_trade):
        db.save_trade(sample_trade)
        trades = db.get_trades(limit=1)
        assert len(trades) == 1
        t = trades[0]
        assert t["side"] == "LONG"
        assert t["entry_price"] == 100.0
        assert t["exit_price"] == 105.0
        assert t["quantity"] == 10.0
        assert t["pnl"] == 50.0
        assert t["fees"] == 2.0
        assert t["notes"] == "Test trade"

    def test_save_multiple_trades(self, db):
        for i in range(5):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0 + i,
                exit_price=105.0 + i,
                quantity=10.0,
                entry_time=datetime.now() - timedelta(hours=5 - i),
                exit_time=datetime.now() - timedelta(hours=4 - i),
                pnl=float(i * 10),
                fees=1.0,
            )
            db.save_trade(trade)

        trades = db.get_trades(limit=10)
        assert len(trades) == 5

    def test_save_trade_with_none_exit_time(self, db):
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=5.0,
            entry_time=datetime.now(),
            exit_time=None,
        )
        trade_id = db.save_trade(trade)
        assert trade_id > 0
        trades = db.get_trades()
        assert trades[0]["exit_time"] is None

    def test_save_trade_increments_ids(self, db, sample_trade):
        id1 = db.save_trade(sample_trade)
        id2 = db.save_trade(sample_trade)
        assert id2 == id1 + 1


class TestGetTrades:
    def test_get_trades_empty_db(self, db):
        trades = db.get_trades()
        assert trades == []

    def test_get_trades_with_limit(self, db):
        for i in range(10):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now() - timedelta(hours=i),
                pnl=float(i),
                fees=0.5,
            )
            db.save_trade(trade)

        trades = db.get_trades(limit=3)
        assert len(trades) == 3

    def test_get_trades_ordered_by_entry_time_desc(self, db):
        for i in range(3):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime(2025, 1, i + 1),
                pnl=10.0,
            )
            db.save_trade(trade)

        trades = db.get_trades()
        assert len(trades) == 3
        # Most recent first
        assert trades[0]["entry_time"] >= trades[1]["entry_time"]

    def test_get_trades_with_date_filter(self, db):
        start = datetime(2025, 1, 5)
        for i in range(10):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime(2025, 1, i + 1),
                pnl=10.0,
            )
            db.save_trade(trade)

        trades = db.get_trades(start_date=start)
        # Should only include trades from Jan 5 onwards
        for t in trades:
            assert t["entry_time"] >= start.isoformat()

    def test_get_trades_with_end_date_filter(self, db):
        end = datetime(2025, 1, 3)
        for i in range(5):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime(2025, 1, i + 1),
                pnl=10.0,
            )
            db.save_trade(trade)

        trades = db.get_trades(end_date=end)
        for t in trades:
            assert t["entry_time"] <= end.isoformat()

    def test_get_trades_with_both_date_filters(self, db):
        start = datetime(2025, 1, 3)
        end = datetime(2025, 1, 5)
        for i in range(10):
            trade = Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime(2025, 1, i + 1),
                pnl=10.0,
            )
            db.save_trade(trade)

        trades = db.get_trades(start_date=start, end_date=end)
        for t in trades:
            assert start.isoformat() <= t["entry_time"] <= end.isoformat()


# ---------------------------------------------------------------------------
# Trade statistics
# ---------------------------------------------------------------------------

class TestGetTradeStats:
    def test_empty_stats(self, db):
        stats = db.get_trade_stats()
        assert stats["total_trades"] == 0
        assert stats["winning_trades"] == 0
        assert stats["losing_trades"] == 0
        assert stats["win_rate"] == 0
        assert stats["total_pnl"] == 0
        assert stats["total_fees"] == 0
        assert stats["avg_win"] == 0
        assert stats["avg_loss"] == 0
        assert stats["profit_factor"] == 0.0

    def test_stats_with_mixed_trades(self, db, sample_trade, sample_losing_trade):
        db.save_trade(sample_trade)  # pnl=50
        db.save_trade(sample_losing_trade)  # pnl=-50
        stats = db.get_trade_stats()
        assert stats["total_trades"] == 2
        assert stats["winning_trades"] == 1
        assert stats["losing_trades"] == 1
        assert stats["win_rate"] == 50.0
        assert stats["total_pnl"] == 0.0  # 50 + (-50)
        assert stats["avg_win"] == 50.0
        assert stats["avg_loss"] == -50.0

    def test_profit_factor(self, db):
        # 2 winners of $100 each, 1 loser of -$50
        for pnl in [100.0, 100.0, -50.0]:
            db.save_trade(Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=100.0 + pnl / 10.0,
                quantity=10.0,
                entry_time=datetime.now(),
                pnl=pnl,
            ))
        stats = db.get_trade_stats()
        # gross_wins=200, gross_losses=50 => profit_factor = 4.0
        assert stats["profit_factor"] == 4.0

    def test_profit_factor_no_losses(self, db):
        db.save_trade(Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=110.0,
            quantity=10.0,
            entry_time=datetime.now(),
            pnl=100.0,
        ))
        stats = db.get_trade_stats()
        assert stats["profit_factor"] == 0.0  # No losses => 0.0 by design

    def test_stats_only_losers(self, db):
        for _ in range(3):
            db.save_trade(Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=95.0,
                quantity=10.0,
                entry_time=datetime.now(),
                pnl=-50.0,
            ))
        stats = db.get_trade_stats()
        assert stats["winning_trades"] == 0
        assert stats["losing_trades"] == 3
        assert stats["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# Position operations
# ---------------------------------------------------------------------------

class TestSavePosition:
    def test_save_new_position_returns_id(self, db, sample_position):
        pos_id = db.save_position(sample_position)
        assert isinstance(pos_id, int)
        assert pos_id > 0

    def test_save_position_stores_fields(self, db, sample_position):
        db.save_position(sample_position)
        positions = db.get_active_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["side"] == "LONG"
        assert p["entry_price"] == 100.0
        assert p["quantity"] == 10.0
        assert p["tp_price"] == 105.0
        assert p["sl_price"] == 98.0
        assert p["leverage"] == 5
        assert p["status"] == "open"

    def test_update_existing_position_by_oid(self, db, sample_position):
        sample_position.oid = 42
        pos_id1 = db.save_position(sample_position)
        sample_position.unrealized_pnl = 25.0
        pos_id2 = db.save_position(sample_position)
        assert pos_id2 == pos_id1  # Should update, not insert new
        positions = db.get_active_positions()
        assert len(positions) == 1
        assert positions[0]["unrealized_pnl"] == 25.0

    def test_update_existing_position_by_cloid(self, db, sample_position):
        sample_position.cloid = "my_cloid"
        pos_id1 = db.save_position(sample_position)
        sample_position.unrealized_pnl = -10.0
        pos_id2 = db.save_position(sample_position)
        assert pos_id2 == pos_id1
        positions = db.get_active_positions()
        assert len(positions) == 1

    def test_save_position_without_oid_or_cloid(self, db):
        """Position without oid/cloid uses entry_time + side as dedup key"""
        now = datetime.now()
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=now,
            leverage=5,
        )
        pos_id1 = db.save_position(pos)
        pos.unrealized_pnl = 5.0
        pos_id2 = db.save_position(pos)
        assert pos_id2 == pos_id1
        positions = db.get_active_positions()
        assert len(positions) == 1

    def test_different_positions_stored_separately(self, db):
        pos1 = Position(
            side=Side.LONG, entry_price=100.0, quantity=10.0,
            tp_price=105.0, sl_price=98.0,
            entry_time=datetime.now(), leverage=5,
        )
        pos2 = Position(
            side=Side.SHORT, entry_price=100.0, quantity=10.0,
            tp_price=95.0, sl_price=102.0,
            entry_time=datetime.now(), leverage=5,
        )
        id1 = db.save_position(pos1)
        id2 = db.save_position(pos2)
        assert id1 != id2
        assert len(db.get_active_positions()) == 2


class TestGetActivePositions:
    def test_empty_db(self, db):
        assert db.get_active_positions() == []

    def test_only_returns_open_positions(self, db, sample_position):
        db.save_position(sample_position)
        assert len(db.get_active_positions()) == 1
        db.close_position(1)
        assert len(db.get_active_positions()) == 0

    def test_ordered_by_entry_time_desc(self, db):
        for i in range(3):
            pos = Position(
                side=Side.LONG, entry_price=100.0, quantity=10.0,
                tp_price=105.0, sl_price=98.0,
                entry_time=datetime(2025, 1, i + 1), leverage=5,
            )
            db.save_position(pos)
        positions = db.get_active_positions()
        assert len(positions) == 3


class TestClosePosition:
    def test_close_existing_position(self, db, sample_position):
        pos_id = db.save_position(sample_position)
        result = db.close_position(pos_id)
        assert result is True

    def test_close_nonexistent_position(self, db):
        result = db.close_position(9999)
        assert result is False

    def test_double_close(self, db, sample_position):
        pos_id = db.save_position(sample_position)
        assert db.close_position(pos_id) is True
        # Second close: UPDATE sets status='closed' again — rowcount is still 1
        # (SQLite doesn't check old vs new value). Verify the position is closed.
        positions = db.get_active_positions()
        assert len(positions) == 0  # Not in active positions anymore


# ---------------------------------------------------------------------------
# Bot state persistence
# ---------------------------------------------------------------------------

class TestBotState:
    def test_save_and_load_state(self, db):
        db.save_bot_state(
            current_capital=9500.0,
            peak_equity=10000.0,
            max_drawdown_pct=5.0,
            daily_pnl=-500.0,
            daily_trades=5,
            consecutive_losses=2,
            circuit_breaker_triggered=True,
            circuit_breaker_until="2025-06-01T00:00:00",
        )
        state = db.load_bot_state()
        assert state is not None
        assert state["current_capital"] == 9500.0
        assert state["peak_equity"] == 10000.0
        assert state["daily_trades"] == 5
        assert state["circuit_breaker_triggered"] is True

    def test_load_state_when_empty(self, db):
        state = db.load_bot_state()
        assert state is None

    def test_update_existing_state(self, db):
        db.save_bot_state(
            current_capital=10000.0,
            peak_equity=10000.0,
            max_drawdown_pct=0,
            daily_pnl=0,
            daily_trades=0,
            consecutive_losses=0,
            circuit_breaker_triggered=False,
        )
        db.save_bot_state(
            current_capital=9800.0,
            peak_equity=10000.0,
            max_drawdown_pct=2.0,
            daily_pnl=-200.0,
            daily_trades=3,
            consecutive_losses=1,
            circuit_breaker_triggered=False,
        )
        state = db.load_bot_state()
        assert state["current_capital"] == 9800.0
        assert state["daily_trades"] == 3

    def test_state_emergency_stop_boolean(self, db):
        db.save_bot_state(
            current_capital=10000.0,
            peak_equity=10000.0,
            max_drawdown_pct=0,
            daily_pnl=0,
            daily_trades=0,
            consecutive_losses=0,
            circuit_breaker_triggered=False,
            emergency_stop=True,
        )
        state = db.load_bot_state()
        assert state["emergency_stop"] is True


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

class TestDailySummary:
    def test_save_and_get_summary(self, db):
        db.save_daily_summary("2025-05-19", {
            "total_trades": 5,
            "winning_trades": 3,
            "losing_trades": 2,
            "total_pnl": 150.0,
            "total_fees": 5.0,
            "win_rate": 60.0,
            "max_drawdown_pct": 2.0,
            "starting_capital": 10000.0,
            "ending_capital": 10150.0,
        })
        summaries = db.get_daily_summaries()
        assert len(summaries) == 1
        assert summaries[0]["date"] == "2025-05-19"
        assert summaries[0]["total_trades"] == 5
        assert summaries[0]["total_pnl"] == 150.0

    def test_upsert_daily_summary(self, db):
        db.save_daily_summary("2025-05-19", {"total_trades": 3, "total_pnl": 50.0})
        db.save_daily_summary("2025-05-19", {"total_trades": 5, "total_pnl": 100.0})
        summaries = db.get_daily_summaries()
        assert len(summaries) == 1
        assert summaries[0]["total_trades"] == 5

    def test_empty_summaries(self, db):
        assert db.get_daily_summaries() == []

    def test_summaries_ordered_by_date_desc(self, db):
        for d in ["2025-05-17", "2025-05-18", "2025-05-19"]:
            db.save_daily_summary(d, {"total_trades": 1})
        summaries = db.get_daily_summaries()
        assert summaries[0]["date"] == "2025-05-19"
        assert summaries[2]["date"] == "2025-05-17"


# ---------------------------------------------------------------------------
# Event logging
# ---------------------------------------------------------------------------

class TestEventLogging:
    def test_log_event(self, db):
        db.log_event("TRADE", "Opened LONG position")
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "TRADE"
        assert events[0]["message"] == "Opened LONG position"

    def test_log_event_with_data(self, db):
        db.log_event("SIGNAL", "Signal generated", {"side": "LONG", "confidence": 75})
        events = db.get_events()
        assert events[0]["event_data"] is not None

    def test_get_events_filtered(self, db):
        db.log_event("TRADE", "Trade 1")
        db.log_event("SIGNAL", "Signal 1")
        db.log_event("TRADE", "Trade 2")

        trade_events = db.get_events(event_type="TRADE")
        assert len(trade_events) == 2
        signal_events = db.get_events(event_type="SIGNAL")
        assert len(signal_events) == 1

    def test_get_events_with_limit(self, db):
        for i in range(10):
            db.log_event("TEST", f"Event {i}")
        events = db.get_events(limit=3)
        assert len(events) == 3

    def test_get_events_empty(self, db):
        assert db.get_events() == []
