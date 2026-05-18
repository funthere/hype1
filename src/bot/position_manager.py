"""
Position tracking and management component.

Handles position CRUD, unrealized P&L updates, and reconciliation
with the exchange.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable, Awaitable

from ..core.config import BotConfig, Side, Position, OrderStatus

logger = logging.getLogger(__name__)


class PositionManager:
    """Manages open positions, unrealized P&L, and reconciliation with exchange."""

    def __init__(self):
        self.positions: List[Position] = []
        self._cached_mids: Dict[str, float] = {}
        self._mids_last_update: Optional[datetime] = None
        self._last_reconciliation: Optional[datetime] = None
        self._reconciliation_interval = timedelta(minutes=2)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def add_position(self, position: Position) -> None:
        """Add a new position to tracking."""
        self.positions.append(position)

    def remove_position(self, position: Position) -> bool:
        """Remove a position. Returns True if found and removed."""
        if position in self.positions:
            self.positions.remove(position)
            return True
        return False

    def get_position(self, side: Side = None) -> Optional[Position]:
        """Get first position, or first position matching *side*."""
        if not self.positions:
            return None
        if side is None:
            return self.positions[0]
        for p in self.positions:
            if p.side == side:
                return p
        return None

    def get_all_positions(self) -> List[Position]:
        """Return a shallow copy of all positions."""
        return list(self.positions)

    # ------------------------------------------------------------------
    # Unrealized P&L
    # ------------------------------------------------------------------

    def calculate_total_unrealized_pnl(self) -> float:
        """Sum of unrealized P&L across all open positions."""
        return sum(p.unrealized_pnl for p in self.positions)

    async def update_unrealized_pnl(self, api, config: BotConfig, db) -> None:
        """Update unrealized P&L for every open position."""
        if not self.positions:
            return

        current_price = await self._get_current_price(api, config)
        if current_price == 0:
            return

        for position in self.positions:
            if position.side == Side.LONG:
                pnl = (current_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - current_price) * position.quantity
            position.unrealized_pnl = pnl
            db.save_position(position)

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    async def maybe_reconcile(
        self,
        api,
        config: BotConfig,
        db,
        telegram,
        close_fn: Callable[..., Awaitable],
    ) -> None:
        """Periodically reconcile local positions with exchange (live mode only)."""
        if config.PAPER_TRADING:
            return

        now = datetime.now()
        if (
            self._last_reconciliation is not None
            and now - self._last_reconciliation < self._reconciliation_interval
        ):
            return

        self._last_reconciliation = now
        await self.reconcile_positions(api, config, db, telegram, close_fn)

    async def reconcile_positions(
        self,
        api,
        config: BotConfig,
        db,
        telegram,
        close_fn: Callable[..., Awaitable],
    ) -> None:
        """Compare local positions against exchange and fix drift.

        Cases handled:
        1. Position exists on exchange but NOT locally → restore from exchange
        2. Position exists locally but NOT on exchange → mark closed
        3. Quantity mismatch → update local quantity
        """
        try:
            exchange_positions = await api.get_positions()
            asset = config.ASSET

            # Build a lookup of exchange positions for our asset
            exchange_map: Dict[str, Dict] = {}
            for ep in exchange_positions:
                coin = ep.get("coin", "")
                if coin == asset:
                    exchange_map[ep.get("direction", "")] = ep

            matched_directions: List[str] = []

            # --- Check local positions against exchange ---
            stale_locals: List[Position] = []
            for local_pos in list(self.positions):
                direction = "Long" if local_pos.side == Side.LONG else "Short"
                ep = exchange_map.get(direction)

                if ep is None:
                    logger.warning(
                        f"⚠️ Reconciliation: {direction} position MISSING on exchange — closing locally"
                    )
                    stale_locals.append(local_pos)
                    continue

                matched_directions.append(direction)

                # Check quantity mismatch
                ex_qty = abs(float(ep.get("szi", 0)))
                if ex_qty > 0 and abs(ex_qty - local_pos.quantity) > 1e-6:
                    logger.warning(
                        f"⚠️ Reconciliation: qty drift local={local_pos.quantity} vs exchange={ex_qty}"
                    )
                    local_pos.quantity = ex_qty

                # Update entry price if available
                ex_entry = float(ep.get("entryPx", 0))
                if ex_entry > 0 and abs(ex_entry - local_pos.entry_price) > 1e-6:
                    logger.warning(
                        f"⚠️ Reconciliation: entry price drift local={local_pos.entry_price} vs exchange={ex_entry}"
                    )
                    local_pos.entry_price = ex_entry

            # Close stale local positions
            for pos in stale_locals:
                mids = await api.get_mids()
                exit_price = float(mids.get(asset, pos.entry_price))
                await close_fn(pos, exit_price, "RECONCILE_MISSING")

            # --- Check for exchange positions not in local ---
            for direction, ep in exchange_map.items():
                if direction in matched_directions:
                    continue

                side = Side.LONG if direction == "Long" else Side.SHORT
                entry_px = float(ep.get("entryPx", 0))
                qty = abs(float(ep.get("szi", 0)))

                if qty <= 0:
                    continue

                logger.warning(
                    f"⚠️ Reconciliation: restoring untracked {direction} "
                    f"qty={qty} @ ${entry_px:.4f} from exchange"
                )
                mids = await api.get_mids()
                current_px = float(mids.get(asset, entry_px))
                tp_mult = 1.03 if side == Side.LONG else 0.97
                sl_mult = 0.97 if side == Side.LONG else 1.03

                restored = Position(
                    side=side,
                    entry_price=entry_px,
                    quantity=qty,
                    tp_price=round(current_px * tp_mult, 4),
                    sl_price=round(current_px * sl_mult, 4),
                    entry_time=datetime.now(),
                    leverage=config.LEVERAGE,
                    status=OrderStatus.OPEN,
                )
                self.positions.append(restored)
                db.save_position(restored)
                db.log_event(
                    "reconciliation",
                    f"Restored {direction} position from exchange",
                    {"entry_price": entry_px, "quantity": qty},
                )

                if telegram:
                    await telegram.notify_info(
                        f"🔄 Reconciliation: restored {direction} {qty} {asset} @ ${entry_px:.4f}"
                    )

            if stale_locals or len(matched_directions) != len(exchange_map):
                logger.info("Reconciliation complete — state synced with exchange")

        except Exception as exc:
            logger.error(f"Position reconciliation failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_current_price(self, api, config: BotConfig) -> float:
        """Return current mid price, using cache when fresh (< 30 s)."""
        if (
            self._mids_last_update
            and (datetime.now() - self._mids_last_update).total_seconds() < 30
            and config.ASSET in self._cached_mids
        ):
            return self._cached_mids[config.ASSET]
        mids = await api.get_mids()
        return float(mids.get(config.ASSET, 0))

    def update_cached_price(self, asset: str, price: float) -> None:
        """Update the cached mid price from WebSocket data."""
        self._cached_mids[asset] = price
        self._mids_last_update = datetime.now()
