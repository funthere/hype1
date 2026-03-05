"""
SQLite database for trade and position persistence
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from enum import Enum

from ..core.config import Position, Trade, Side, OrderStatus

logger = logging.getLogger(__name__)


def _serialize_enum(obj):
    """Convert enum values to their string values for JSON serialization"""
    if isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, dict):
        return {k: _serialize_enum(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_enum(item) for item in obj]
    return obj


def _json_dumps_safe(data):
    """JSON encode with enum handling"""
    if data is None:
        return None
    return json.dumps(_serialize_enum(data))


class DatabaseManager:
    """
    SQLite database manager for trading bot data.

    Provides persistent storage for:
    - Trades (completed positions)
    - Active positions
    - Performance metrics
    - Daily summaries
    """

    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize_db()

    def _initialize_db(self):
        """Create database tables if they don't exist"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None
        )
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()

        # Trades table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                pnl REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Positions table (for active positions)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                tp_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                leverage INTEGER NOT NULL,
                oid INTEGER,
                cloid TEXT,
                status TEXT DEFAULT 'open',
                unrealized_pnl REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Daily summaries table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                total_fees REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                starting_capital REAL DEFAULT 0,
                ending_capital REAL DEFAULT 0
            )
        """
        )

        # Events table (for logging important events)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                event_data TEXT,
                message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Create indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_entry_time ON positions(entry_time)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_summaries_date ON daily_summaries(date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)"
        )

        self.conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    # Trade operations

    def save_trade(self, trade: Trade) -> int:
        """
        Save a completed trade to database

        Returns:
            The ID of the inserted trade
        """
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT INTO trades (
                side, entry_price, exit_price, quantity, entry_time, exit_time,
                pnl, fees, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                trade.side.value,
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.pnl,
                trade.fees,
                trade.notes,
            ),
        )

        self.conn.commit()
        trade_id = cursor.lastrowid
        logger.info(f"Saved trade #{trade_id}: {trade.side.value} P&L=${trade.pnl:.2f}")

        return trade_id

    def get_trades(
        self,
        limit: int = 100,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get trades from database with optional filters"""
        cursor = self.conn.cursor()

        query = "SELECT * FROM trades"
        params = []

        conditions = []
        if start_date:
            conditions.append("entry_time >= ?")
            params.append(start_date.isoformat())
        if end_date:
            conditions.append("entry_time <= ?")
            params.append(end_date.isoformat())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        trades = []
        for row in rows:
            trades.append(self._row_to_dict(row))

        return trades

    def get_trade_stats(self) -> Dict:
        """Get overall trade statistics"""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(pnl) as total_pnl,
                SUM(fees) as total_fees,
                AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win,
                AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss
            FROM trades
        """
        )

        row = cursor.fetchone()

        total_trades = row["total_trades"] or 0
        winning_trades = row["winning_trades"] or 0
        losing_trades = row["losing_trades"] or 0
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(row["total_pnl"] or 0, 2),
            "total_fees": round(row["total_fees"] or 0, 2),
            "avg_win": round(row["avg_win"] or 0, 2),
            "avg_loss": round(row["avg_loss"] or 0, 2),
            "profit_factor": self._calculate_profit_factor(),
        }

    def _calculate_profit_factor(self) -> float:
        """Calculate profit factor (gross wins / gross losses)"""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_wins,
                SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as gross_losses
            FROM trades
        """
        )

        row = cursor.fetchone()
        gross_wins = row["gross_wins"] or 0
        gross_losses = row["gross_losses"] or 0

        if gross_losses == 0:
            return 0.0

        return round(gross_wins / gross_losses, 2)

    # Position operations

    def save_position(self, position: Position) -> int:
        """
        Save or update an active position

        Returns:
            The ID of the inserted/updated position
        """
        cursor = self.conn.cursor()

        # Check if position already exists (by oid or cloid)
        if position.oid:
            cursor.execute("SELECT id FROM positions WHERE oid = ?", (position.oid,))
        elif position.cloid:
            cursor.execute("SELECT id FROM positions WHERE cloid = ?", (position.cloid,))
        else:
            cursor.execute(
                "SELECT id FROM positions WHERE entry_time = ? AND side = ?",
                (position.entry_time.isoformat(), position.side.value),
            )

        existing = cursor.fetchone()

        if existing:
            # Update existing position
            cursor.execute(
                """
                UPDATE positions SET
                    unrealized_pnl = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (position.unrealized_pnl, position.status.value, existing["id"]),
            )
            self.conn.commit()
            return existing["id"]
        else:
            # Insert new position
            cursor.execute(
                """
                INSERT INTO positions (
                    side, entry_price, quantity, tp_price, sl_price, entry_time,
                    leverage, oid, cloid, status, unrealized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.side.value,
                    position.entry_price,
                    position.quantity,
                    position.tp_price,
                    position.sl_price,
                    position.entry_time.isoformat(),
                    position.leverage,
                    position.oid,
                    position.cloid,
                    position.status.value,
                    position.unrealized_pnl,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid

    def get_active_positions(self) -> List[Dict]:
        """Get all active positions"""
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time DESC"
        )
        rows = cursor.fetchall()

        return [self._row_to_dict(row) for row in rows]

    def close_position(self, position_id: int) -> bool:
        """Mark a position as closed"""
        cursor = self.conn.cursor()

        cursor.execute(
            "UPDATE positions SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (position_id,),
        )

        self.conn.commit()
        return cursor.rowcount > 0

    # Daily summary operations

    def save_daily_summary(self, date: str, summary: Dict) -> int:
        """
        Save or update daily summary

        Args:
            date: Date string in ISO format (YYYY-MM-DD)
            summary: Dict with daily stats
        """
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO daily_summaries (
                date, total_trades, winning_trades, losing_trades,
                total_pnl, total_fees, win_rate, max_drawdown_pct,
                starting_capital, ending_capital
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                summary.get("total_trades", 0),
                summary.get("winning_trades", 0),
                summary.get("losing_trades", 0),
                summary.get("total_pnl", 0),
                summary.get("total_fees", 0),
                summary.get("win_rate", 0),
                summary.get("max_drawdown_pct", 0),
                summary.get("starting_capital", 0),
                summary.get("ending_capital", 0),
            ),
        )

        self.conn.commit()
        return cursor.lastrowid

    def get_daily_summaries(self, limit: int = 30) -> List[Dict]:
        """Get recent daily summaries"""
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()

        return [self._row_to_dict(row) for row in rows]

    # Event logging

    def log_event(
        self, event_type: str, message: str, event_data: Optional[Dict] = None
    ):
        """Log an important event to the database"""
        cursor = self.conn.cursor()

        cursor.execute(
            """
            INSERT INTO events (event_type, message, event_data)
            VALUES (?, ?, ?)
            """,
            (event_type, message, _json_dumps_safe(event_data)),
        )

        self.conn.commit()

    def get_events(
        self, event_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """Get recent events, optionally filtered by type"""
        cursor = self.conn.cursor()

        if event_type:
            cursor.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            )

        rows = cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    # Utility methods

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        """Convert a database row to dictionary"""
        return dict(row)

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class CSVMigration:
    """Helper to migrate existing CSV data to SQLite"""

    @staticmethod
    def migrate_trades_from_csv(csv_path: str, db: DatabaseManager) -> int:
        """
        Migrate trades from CSV file to database

        Returns:
            Number of trades migrated
        """
        import pandas as pd

        if not Path(csv_path).exists():
            logger.warning(f"CSV file not found: {csv_path}")
            return 0

        df = pd.read_csv(csv_path)
        count = 0

        for _, row in df.iterrows():
            try:
                trade = Trade(
                    side=Side.LONG if row.get("side") == "LONG" else Side.SHORT,
                    entry_price=row.get("entry_price", 0),
                    exit_price=row.get("exit_price", 0),
                    quantity=row.get("quantity", 0),
                    entry_time=pd.to_datetime(row["entry_time"]),
                    exit_time=pd.to_datetime(row["exit_time"])
                    if "exit_time" in row and pd.notna(row["exit_time"])
                    else None,
                    pnl=row.get("pnl", 0),
                    fees=row.get("fees", 0),
                )
                db.save_trade(trade)
                count += 1
            except Exception as e:
                logger.error(f"Failed to migrate trade: {e}")

        logger.info(f"Migrated {count} trades from {csv_path}")
        return count
