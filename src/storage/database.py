"""
SQLite database for trade and position persistence

Uses aiosqlite for async-safe database operations with WAL mode
for concurrent read/write performance.
"""

import json
import logging
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Dict
from enum import Enum

from ..core.config import Position, Trade, Side

logger = logging.getLogger(__name__)


def _serialize_enum(obj) -> Any:
    """Convert enum values to their string values for JSON serialization"""
    if isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, dict):
        return {k: _serialize_enum(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_enum(item) for item in obj]
    return obj


def _json_dumps_safe(data: Any) -> Optional[str]:
    """JSON encode with enum handling"""
    if data is None:
        return None
    return json.dumps(_serialize_enum(data))


class DatabaseManager:
    """
    Async SQLite database manager for trading bot data.

    Uses aiosqlite for thread-safe async operations with WAL mode
    for concurrent read/write performance. Each operation gets its
    own connection from aiosqlite's connection pool.

    Provides persistent storage for:
    - Trades (completed positions)
    - Active positions
    - Performance metrics
    - Daily summaries
    """

    def __init__(self, db_path: str = "trading_bot.db"):
        self.db_path = Path(db_path)
        self._initialized = False
        self._init_lock = None  # Created lazily in async context

    async def initialize(self):
        """Initialize database tables and WAL mode. Must be called before use."""
        import asyncio
        self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._initialized:
                return

            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            # Perform table creation in a single connection
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await self._create_tables(conn)
                await conn.commit()

            self._initialized = True
            logger.info(f"Database initialized at {self.db_path}")

    @staticmethod
    async def _create_tables(conn: aiosqlite.Connection):
        """Create all database tables and indexes."""

        # Trades table
        await conn.execute(
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
        await conn.execute(
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
        await conn.execute(
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
        await conn.execute(
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

        # Bot state table (single-row persistence for graceful shutdown/restore)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                current_capital REAL DEFAULT 0,
                peak_equity REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                daily_pnl REAL DEFAULT 0,
                daily_trades INTEGER DEFAULT 0,
                consecutive_losses INTEGER DEFAULT 0,
                circuit_breaker_triggered INTEGER DEFAULT 0,
                circuit_breaker_until TEXT,
                last_trade_date TEXT,
                last_signal_time TEXT,
                emergency_stop INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                CHECK (id = 1)
            )
            """
        )

        # Create indexes
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_entry_time ON positions(entry_time)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_summaries_date ON daily_summaries(date)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)"
        )

    # Ensure initialization before any operation

    async def _ensure_initialized(self):
        """Ensure database is initialized before use."""
        if not self._initialized:
            await self.initialize()

    # Health check

    async def health_check(self) -> bool:
        """Check database connection health."""
        try:
            await self._ensure_initialized()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute("SELECT 1")
                await cursor.fetchone()
                return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    # Trade operations

    async def save_trade(self, trade: Trade) -> int:
        """
        Save a completed trade to database

        Returns:
            The ID of the inserted trade
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            cursor = await conn.execute(
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
            await conn.commit()
            trade_id = cursor.lastrowid
            logger.info(
                f"Saved trade #{trade_id}: {trade.side.value} P&L=${trade.pnl:.2f}"
            )
            return trade_id

    async def get_trades(
        self,
        limit: int = 100,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Dict]:
        """Get trades from database with optional filters"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

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

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

            trades = []
            for row in rows:
                trades.append(dict(row))

            return trades

    async def get_trade_stats(self) -> Dict:
        """Get overall trade statistics"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

            cursor = await conn.execute(
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

            row = await cursor.fetchone()

            total_trades = row["total_trades"] or 0
            winning_trades = row["winning_trades"] or 0
            losing_trades = row["losing_trades"] or 0
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

            profit_factor = await self._calculate_profit_factor(conn)

            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(win_rate, 2),
                "total_pnl": round(row["total_pnl"] or 0, 2),
                "total_fees": round(row["total_fees"] or 0, 2),
                "avg_win": round(row["avg_win"] or 0, 2),
                "avg_loss": round(row["avg_loss"] or 0, 2),
                "profit_factor": profit_factor,
            }

    @staticmethod
    async def _calculate_profit_factor(conn: aiosqlite.Connection) -> float:
        """Calculate profit factor (gross wins / gross losses)"""
        conn.row_factory = aiosqlite.Row

        cursor = await conn.execute(
            """
            SELECT
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_wins,
                SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as gross_losses
            FROM trades
        """
        )

        row = await cursor.fetchone()
        gross_wins = row["gross_wins"] or 0
        gross_losses = row["gross_losses"] or 0

        if gross_losses == 0:
            return 0.0

        return round(gross_wins / gross_losses, 2)

    # Position operations

    async def save_position(self, position: Position) -> int:
        """
        Save or update an active position

        Returns:
            The ID of the inserted/updated position
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            # Check if position already exists (by oid or cloid)
            if position.oid:
                await cursor.execute(
                    "SELECT id FROM positions WHERE oid = ?", (position.oid,)
                )
            elif position.cloid:
                await cursor.execute(
                    "SELECT id FROM positions WHERE cloid = ?",
                    (position.cloid,),
                )
            else:
                await cursor.execute(
                    "SELECT id FROM positions WHERE entry_time = ? AND side = ?",
                    (position.entry_time.isoformat(), position.side.value),
                )

            existing = await cursor.fetchone()

            if existing:
                # Update existing position
                await cursor.execute(
                    """
                    UPDATE positions SET
                        unrealized_pnl = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        position.unrealized_pnl,
                        position.status.value,
                        existing["id"],
                    ),
                )
                await conn.commit()
                return existing["id"]
            else:
                # Insert new position
                await cursor.execute(
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
                await conn.commit()
                return cursor.lastrowid

    async def get_active_positions(self) -> List[Dict]:
        """Get all active positions"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

            cursor = await conn.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time DESC"
            )
            rows = await cursor.fetchall()

            return [dict(row) for row in rows]

    async def close_position(self, position_id: int) -> bool:
        """Mark a position as closed"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            cursor = await conn.execute(
                "UPDATE positions SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (position_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    # Bot state persistence for graceful shutdown/restore

    async def save_bot_state(
        self,
        current_capital: float,
        peak_equity: float,
        max_drawdown_pct: float,
        daily_pnl: float,
        daily_trades: int,
        consecutive_losses: int,
        circuit_breaker_triggered: bool,
        circuit_breaker_until: Optional[str] = None,
        last_trade_date: Optional[str] = None,
        last_signal_time: Optional[str] = None,
        emergency_stop: bool = False,
    ) -> int:
        """Persist bot runtime state so it can be restored after a restart.

        Uses a single-row upsert (key = id = 1).
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT id FROM bot_state WHERE id = 1")
            exists = await cursor.fetchone()

            row = (
                current_capital,
                peak_equity,
                max_drawdown_pct,
                daily_pnl,
                daily_trades,
                consecutive_losses,
                int(circuit_breaker_triggered),
                circuit_breaker_until,
                last_trade_date,
                last_signal_time,
                int(emergency_stop),
            )

            if exists:
                await conn.execute(
                    """
                    UPDATE bot_state SET
                        current_capital=?, peak_equity=?, max_drawdown_pct=?,
                        daily_pnl=?, daily_trades=?, consecutive_losses=?,
                        circuit_breaker_triggered=?, circuit_breaker_until=?,
                        last_trade_date=?, last_signal_time=?, emergency_stop=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=1
                    """,
                    row,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO bot_state (
                        id, current_capital, peak_equity, max_drawdown_pct,
                        daily_pnl, daily_trades, consecutive_losses,
                        circuit_breaker_triggered, circuit_breaker_until,
                        last_trade_date, last_signal_time, emergency_stop
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            await conn.commit()
            return 1

    async def load_bot_state(self) -> Optional[Dict]:
        """Load the last saved bot state. Returns None if no state exists."""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

            cursor = await conn.execute("SELECT * FROM bot_state WHERE id = 1")
            row = await cursor.fetchone()
            if row is None:
                return None
            d = dict(row)
            # Normalise booleans
            d["circuit_breaker_triggered"] = bool(d.get("circuit_breaker_triggered", 0))
            d["emergency_stop"] = bool(d.get("emergency_stop", 0))
            return d

    # Daily summary operations

    async def save_daily_summary(self, date: str, summary: Dict) -> int:
        """
        Save or update daily summary

        Args:
            date: Date string in ISO format (YYYY-MM-DD)
            summary: Dict with daily stats
        """
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            cursor = await conn.execute(
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
            await conn.commit()
            return cursor.lastrowid

    async def get_daily_summaries(self, limit: int = 30) -> List[Dict]:
        """Get recent daily summaries"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

            cursor = await conn.execute(
                "SELECT * FROM daily_summaries ORDER BY date DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()

            return [dict(row) for row in rows]

    # Event logging

    async def log_event(
        self, event_type: str, message: str, event_data: Optional[Dict] = None
    ):
        """Log an important event to the database"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute(
                """
                INSERT INTO events (event_type, message, event_data)
                VALUES (?, ?, ?)
                """,
                (event_type, message, _json_dumps_safe(event_data)),
            )
            await conn.commit()

    async def get_events(
        self, event_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        """Get recent events, optionally filtered by type"""
        await self._ensure_initialized()

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row

            if event_type:
                cursor = await conn.execute(
                    "SELECT * FROM events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
                )

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # Lifecycle methods

    async def close(self):
        """Close database connections and clean up WAL.

        With aiosqlite, connections are per-operation so there's no
        persistent connection to close. We just mark as uninitialized.
        """
        self._initialized = False
        logger.info("Database manager closed")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # Legacy sync context manager support (calls async init in __init__)
    # For backward compat with `with DatabaseManager(...) as db:` pattern,
    # callers should switch to `async with DatabaseManager(...) as db:`

    def __enter__(self):
        # Legacy sync context manager — best-effort sync init
        import sqlite3
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_conn = sqlite3.connect(self.db_path)
        self._sync_conn.row_factory = sqlite3.Row
        self._initialized = True
        logger.warning(
            "Using sync context manager for DatabaseManager — "
            "switch to 'async with' for async safety"
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "_sync_conn") and self._sync_conn:
            self._sync_conn.close()
            self._sync_conn = None
        self._initialized = False


class CSVMigration:
    """Helper to migrate existing CSV data to SQLite"""

    @staticmethod
    async def migrate_trades_from_csv(csv_path: str, db: DatabaseManager) -> int:
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
                await db.save_trade(trade)
                count += 1
            except Exception as e:
                logger.error(f"Failed to migrate trade: {e}")

        logger.info(f"Migrated {count} trades from {csv_path}")
        return count
