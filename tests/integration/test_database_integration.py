"""
Integration tests for Database persistence
"""

import pytest
import tempfile
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from src.core.config import BotConfig, Side, Position, Trade, OrderStatus
from src.storage.database import DatabaseManager


@pytest.mark.integration
class TestDatabaseIntegration:
    """Integration tests for database operations"""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database for testing"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            yield f.name
        # Cleanup
        if Path(f.name).exists():
            Path(f.name).unlink()
    
    @pytest.fixture
    def db(self, temp_db_path):
        """Create database manager with temporary file"""
        db = DatabaseManager(temp_db_path)
        yield db
        db.close()
    
    @pytest.fixture
    def sample_config(self):
        """Create a sample configuration"""
        config = BotConfig()
        config.PAPER_TRADING = True
        config.ASSET = "HYPE"
        config.TIMEFRAME = "15m"
        config.LEVERAGE = 5
        config.RISK_PER_TRADE_PCT = 0.08
        config.TP_ATR_MULTIPLIER = 2.0
        config.SL_ATR_MULTIPLIER = 0.4
        config.MAX_POSITIONS = 2
        config.MAX_DAILY_TRADES = 20
        config.MAX_CONSECUTIVE_LOSSES = 3
        config.CONFIDENCE_THRESHOLD = 45
        return config
    
    def test_database_initialization(self, temp_db_path):
        """Test database initialization"""
        db = DatabaseManager(temp_db_path)
        
        assert db.conn is not None
        assert db.db_path == Path(temp_db_path)
        
        # Check tables were created
        cursor = db.conn.cursor()
        
        # Check trades table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        assert cursor.fetchone() is not None
        
        # Check positions table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'")
        assert cursor.fetchone() is not None
        
        # Check daily_summaries table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_summaries'")
        assert cursor.fetchone() is not None
        
        # Check events table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        assert cursor.fetchone() is not None
        
        db.close()
    
    def test_save_position(self, db):
        """Test saving positions"""
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=12345,
            cloid="test_cloid",
            status=OrderStatus.OPEN,
            unrealized_pnl=50.0
        )
        
        position_id = db.save_position(position)
        
        assert position_id > 0
        
        # Verify position was saved
        positions = db.get_active_positions()
        assert len(positions) == 1
        assert positions[0]["entry_price"] == 100.0
        assert positions[0]["status"] == "open"
        assert positions[0]["unrealized_pnl"] == 50.0
    
    def test_update_position(self, db):
        """Test updating existing positions"""
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=12345
        )
        
        # Save position
        position_id = db.save_position(position)
        
        # Update unrealized PnL
        position.unrealized_pnl = 75.0
        updated_id = db.save_position(position)
        
        assert updated_id == position_id
        
        # Verify update
        positions = db.get_active_positions()
        assert positions[0]["unrealized_pnl"] == 75.0
    
    def test_close_position(self, db):
        """Test closing positions"""
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=12345
        )
        
        position_id = db.save_position(position)
        
        # Close position
        closed = db.close_position(position_id)
        
        assert closed is True
        
        # Verify position is closed
        positions = db.get_active_positions()
        assert len(positions) == 0
    
    def test_save_trade(self, db):
        """Test saving completed trades"""
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now(),
            pnl=50.0,
            fees=2.0,
            notes="Good trade setup"
        )
        
        trade_id = db.save_trade(trade)
        
        assert trade_id > 0
    
    def test_get_trades_with_filters(self, db):
        """Test retrieving trades with date filters"""
        now = datetime.now()
        
        # Create trades with different timestamps
        old_trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=now - timedelta(days=10),
            exit_time=now - timedelta(days=9),
            pnl=50.0,
            fees=2.0
        )
        
        recent_trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=now - timedelta(hours=1),
            exit_time=now - timedelta(minutes=30),
            pnl=75.0,
            fees=2.5
        )
        
        db.save_trade(old_trade)
        db.save_trade(recent_trade)
        
        # Get all trades
        all_trades = db.get_trades()
        assert len(all_trades) == 2
        
        # Get trades after a date
        recent_trades = db.get_trades(start_date=now - timedelta(hours=2))
        assert len(recent_trades) == 1
        assert recent_trades[0]["pnl"] == 75.0
        
        # Get trades before a date
        old_trades = db.get_trades(end_date=now - timedelta(days=8))
        assert len(old_trades) == 1
        assert old_trades[0]["pnl"] == 50.0
    
    def test_trade_statistics(self, db):
        """Test trade statistics calculation"""
        # Add winning trade
        db.save_trade(Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now() - timedelta(hours=2),
            pnl=50.0,
            fees=2.0
        ))
        
        # Add losing trade
        db.save_trade(Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=98.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=3),
            exit_time=datetime.now() - timedelta(hours=4),
            pnl=-20.0,
            fees=2.0
        ))
        
        stats = db.get_trade_stats()
        
        assert stats["total_trades"] == 2
        assert stats["winning_trades"] == 1
        assert stats["losing_trades"] == 1
        assert stats["win_rate"] == 50.0
        assert stats["total_pnl"] == 30.0  # 50 - 20
        assert stats["total_fees"] == 4.0
        assert stats["avg_win"] == 50.0
        assert stats["avg_loss"] == -20.0
    
    def test_save_daily_summary(self, db):
        """Test saving daily summaries"""
        date_str = "2024-01-15"
        summary = {
            "date": date_str,
            "total_trades": 10,
            "winning_trades": 6,
            "losing_trades": 4,
            "total_pnl": 500.0,
            "total_fees": 20.0,
            "win_rate": 60.0,
            "max_drawdown_pct": 5.0,
            "starting_capital": 10000.0,
            "ending_capital": 10500.0
        }
        
        summary_id = db.save_daily_summary(date_str, summary)
        
        assert summary_id > 0
    
    def test_get_daily_summaries(self, db):
        """Test retrieving daily summaries"""
        # Create summaries for different days
        for i in range(5):
            date = f"2024-01-{10 + i:02d}"
            summary = {
                "date": date,
                "total_trades": 10 + i,
                "winning_trades": 6 + i,
                "losing_trades": 4,
                "total_pnl": 500.0 + (i * 100),
                "total_fees": 20.0,
                "win_rate": 60.0 + i,
                "max_drawdown_pct": 5.0,
                "starting_capital": 10000.0 + (i * 500),
                "ending_capital": 10500.0 + (i * 500)
            }
            db.save_daily_summary(date, summary)
        
        summaries = db.get_daily_summaries()
        assert len(summaries) == 5
        
        # Test limit
        limited_summaries = db.get_daily_summaries(limit=3)
        assert len(limited_summaries) == 3
    
    def test_log_event(self, db):
        """Test logging events"""
        db.log_event("test_event", "This is a test event", {"key": "value"})
        
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "test_event"
        assert events[0]["message"] == "This is a test event"
        assert events[0]["event_data"] is not None
    
    def test_log_event_filtering(self, db):
        """Test filtering events by type"""
        db.log_event("trade_entry", "Entry signal")
        db.log_event("trade_exit", "Exit signal")
        db.log_event("error", "Error occurred")
        db.log_event("trade_entry", "Another entry")
        
        # Get all events
        all_events = db.get_events()
        assert len(all_events) == 4
        
        # Filter by type
        trade_entry_events = db.get_events(event_type="trade_entry")
        assert len(trade_entry_events) == 2
        
        error_events = db.get_events(event_type="error")
        assert len(error_events) == 1
    
    def test_profit_factor_calculation(self, db):
        """Test profit factor calculation"""
        # Add profitable trades
        for _ in range(3):
            db.save_trade(Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=50.0,
                fees=2.0
            ))
        
        # Add losing trade
        db.save_trade(Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=98.0,
            quantity=10.0,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            pnl=-20.0,
            fees=2.0
        ))
        
        stats = db.get_trade_stats()
        
        # Profit factor = gross wins / gross losses
        # Gross wins = 3 * 50 = 150
        # Gross losses = 1 * 20 = 20
        # Profit factor = 150 / 20 = 7.5
        assert abs(stats["profit_factor"] - 7.5) < 0.1
    
    def test_database_context_manager(self, temp_db_path):
        """Test database as context manager"""
        with DatabaseManager(temp_db_path) as db:
            # Perform operations
            db.save_trade(Trade(
                side=Side.LONG,
                entry_price=100.0,
                exit_price=105.0,
                quantity=10.0,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                pnl=50.0,
                fees=2.0
            ))
            
            stats = db.get_trade_stats()
            assert stats["total_trades"] == 1
        
        # Connection should be closed after context
        # Can't check this directly, but database should handle cleanup
    
    def test_multiple_positions_same_oid(self, db):
        """Test updating position with same oid"""
        position = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=datetime.now(),
            leverage=5,
            oid=12345
        )
        
        # Save position
        db.save_position(position)
        
        # Update position
        position.unrealized_pnl = 100.0
        db.save_position(position)
        
        # Should update existing position, not create new one
        positions = db.get_active_positions()
        assert len(positions) == 1
        assert positions[0]["unrealized_pnl"] == 100.0