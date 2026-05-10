"""
Funding Rate Arbitrage Strategy for HyperLiquid

Monitors funding rates across all perpetual assets and opens delta-neutral
positions to collect funding payments when rates exceed configurable thresholds.

Strategy:
  - SHORT when funding rate > ENTRY_THRESHOLD (longs pay shorts)
  - LONG  when funding rate < -ENTRY_THRESHOLD (shorts pay longs)
  - Close positions when rates revert past EXIT_THRESHOLD
  - Emergency close on max loss or max hold time exceeded
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from hyperliquid.info import Info

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class PositionSide(Enum):
    """Position direction for funding-rate arbitrage."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(Enum):
    """Lifecycle status of a funding arb position."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass
class FundingArbConfig:
    """Standalone configuration for the Funding Rate Arbitrage strategy.

    All thresholds are expressed as **absolute decimal values per 8-hour
    funding period**.  For example ``0.0003`` means 0.03 % per 8 h.
    """

    # Mode
    PAPER_TRADING: bool = True
    USE_TESTNET: bool = False

    # Account (only needed for live trading)
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # Capital (paper mode)
    PAPER_CAPITAL: float = 10_000.0

    # Strategy thresholds (per 8h funding period)
    ENTRY_THRESHOLD: float = 0.0003  # 0.03 %
    EXIT_THRESHOLD: float = 0.0001  # 0.01 %

    # Position sizing
    POSITION_SIZE_PCT: float = 0.10  # 10 % of account per trade
    LEVERAGE: int = 3

    # Limits
    MAX_CONCURRENT_POSITIONS: int = 3
    MAX_HOLD_HOURS: float = 72.0
    MAX_LOSS_PCT: float = 0.05  # 5 % emergency stop

    # Scan interval
    CHECK_INTERVAL: int = 300  # seconds (5 min)

    # Asset filter (empty list ⇒ scan ALL perps)
    COINS: Optional[List[str]] = None

    # Fees (for PnL estimation)
    TAKER_FEE_PCT: float = 0.0005

    # Database
    DATABASE_PATH: str = "trading_bot.db"

    # API URLs (set at runtime)
    API_URL: str = "https://api.hyperliquid.xyz"

    def validate(self) -> bool:
        """Validate configuration values."""
        if self.POSITION_SIZE_PCT <= 0 or self.POSITION_SIZE_PCT > 1:
            raise ValueError("POSITION_SIZE_PCT must be in (0, 1]")
        if self.ENTRY_THRESHOLD <= 0:
            raise ValueError("ENTRY_THRESHOLD must be > 0")
        if self.EXIT_THRESHOLD < 0:
            raise ValueError("EXIT_THRESHOLD must be >= 0")
        if self.LEVERAGE < 1 or self.LEVERAGE > 100:
            raise ValueError("LEVERAGE must be in [1, 100]")
        if not self.PAPER_TRADING and not self.PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY required for live trading")
        return True


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------


@dataclass
class FundingPosition:
    """Tracks a single funding-rate arbitrage position."""

    id: str
    coin: str
    side: PositionSide
    entry_rate: float  # funding rate at entry
    entry_price: float  # mark price at entry
    quantity: float  # position size in base asset
    notional: float  # entry_price * quantity
    entry_time: float  # unix timestamp
    total_funding_collected: float = 0.0
    last_funding_time: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    close_reason: str = ""
    close_time: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class FundingRateArbStrategy:
    """Funding Rate Arbitrage strategy engine.

    Coordinates scanning, position management, and PnL tracking for a
    delta-neutral funding-rate capture approach on HyperLiquid perpetuals.
    """

    def __init__(
        self,
        config: FundingArbConfig,
        api: Any,  # HyperliquidAPI (or paper shim) – duck-typed
        db: Any,  # DatabaseManager – duck-typed
    ) -> None:
        self.config = config
        self.api = api
        self.db = db

        # Internal state
        self._positions: Dict[str, FundingPosition] = {}
        self._running: bool = False
        self._cycle_count: int = 0
        self._last_scan_time: float = 0.0

        # For paper mode capital tracking
        self._paper_capital: float = config.PAPER_CAPITAL

        # Cache for SDK Info object (used for meta_and_asset_ctxs)
        self._info: Optional[Info] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_funding_rates(self) -> List[Dict[str, Any]]:
        """Fetch current funding rates for all (or configured) perpetual assets.

        Returns:
            A list of dicts with keys:
              coin, funding_rate, mark_px, mid_px, open_interest
        """
        try:
            info = self._get_info()
            raw: tuple = await asyncio.to_thread(info.meta_and_asset_ctxs)

            if not raw or len(raw) < 2:
                logger.warning("meta_and_asset_ctxs returned unexpected format")
                return []

            meta, ctxs = raw[0], raw[1]
            universe = meta.get("universe", [])

            opportunities: List[Dict[str, Any]] = []
            for idx, ctx in enumerate(ctxs):
                if idx >= len(universe):
                    break

                coin = universe[idx].get("name", f"UNKNOWN_{idx}")

                # Apply coin filter (case-insensitive, supports prefix match e.g. kPEPE)
                if self.config.COINS:
                    coin_upper = coin.upper()
                    coins_upper = [c.upper() for c in self.config.COINS]
                    # Match exact or suffix (kPEPE matches PEPE)
                    if not any(
                        coin_upper == cu or coin_upper.endswith(cu)
                        for cu in coins_upper
                        if cu
                    ):
                        continue

                funding_str = ctx.get("funding", "0")
                mark_px_str = ctx.get("markPx")
                mid_px_str = ctx.get("midPx")
                oi_str = ctx.get("openInterest", "0")

                try:
                    funding_rate = float(funding_str) if funding_str else 0.0
                    mark_px = float(mark_px_str) if mark_px_str else 0.0
                    mid_px = float(mid_px_str) if mid_px_str else 0.0
                    open_interest = float(oi_str) if oi_str else 0.0
                except (ValueError, TypeError):
                    continue

                # Skip assets with no price data
                if mark_px <= 0:
                    continue

                opportunities.append(
                    {
                        "coin": coin,
                        "funding_rate": funding_rate,
                        "mark_px": mark_px,
                        "mid_px": mid_px,
                        "open_interest": open_interest,
                    }
                )

            self._last_scan_time = time.time()
            logger.debug("Scanned funding rates for %d assets", len(opportunities))
            return opportunities

        except Exception as exc:
            logger.error("Failed to scan funding rates: %s", exc)
            return []

    async def open_position(
        self,
        coin: str,
        side: PositionSide,
        rate: float,
        mark_px: float,
    ) -> Optional[str]:
        """Open a new funding-rate arbitrage position.

        Args:
            coin: Asset symbol (e.g. ``"BTC"``).
            side: ``LONG`` or ``SHORT``.
            rate: Current funding rate that triggered entry.
            mark_px: Current mark price.

        Returns:
            Position ID string on success, ``None`` on failure.
        """
        try:
            # Check max concurrent positions
            open_count = sum(
                1 for p in self._positions.values() if p.status == PositionStatus.OPEN
            )
            if open_count >= self.config.MAX_CONCURRENT_POSITIONS:
                logger.info(
                    "Max concurrent positions (%d) reached – skipping %s",
                    self.config.MAX_CONCURRENT_POSITIONS,
                    coin,
                )
                return None

            # Check if we already have an open position for this coin
            for p in self._positions.values():
                if p.coin == coin and p.status == PositionStatus.OPEN:
                    logger.debug("Already have open position for %s", coin)
                    return None

            # Position sizing
            capital = await self._get_available_capital()
            notional = capital * self.config.POSITION_SIZE_PCT
            if notional <= 0 or mark_px <= 0:
                logger.warning(
                    "Invalid sizing: notional=%.2f mark_px=%.2f", notional, mark_px
                )
                return None

            quantity = notional / mark_px
            # Round quantity to 4 decimal places (most perps)
            quantity = round(quantity, 4)
            if quantity <= 0:
                return None

            position_id = str(uuid.uuid4())[:8]

            if self.config.PAPER_TRADING:
                # Simulate entry (apply taker fee)
                fee = notional * self.config.TAKER_FEE_PCT
                self._paper_capital -= fee
                logger.info(
                    "[PAPER] OPEN %s %s | side=%s qty=%.4f px=%.2f rate=%.6f fee=%.4f",
                    position_id,
                    coin,
                    side.value,
                    quantity,
                    mark_px,
                    rate,
                    fee,
                )
            else:
                # Live order via API
                result = await self.api.place_order(
                    side=side,  # type: ignore[arg-type]
                    price=mark_px,
                    quantity=quantity,
                )
                if result.get("status") != "ok":
                    logger.error("Live order failed for %s: %s", coin, result)
                    return None
                logger.info(
                    "[LIVE] OPEN %s %s | side=%s qty=%.4f px=%.2f rate=%.6f",
                    position_id,
                    coin,
                    side.value,
                    quantity,
                    mark_px,
                    rate,
                )

            pos = FundingPosition(
                id=position_id,
                coin=coin,
                side=side,
                entry_rate=rate,
                entry_price=mark_px,
                quantity=quantity,
                notional=notional,
                entry_time=time.time(),
                last_funding_time=time.time(),
            )
            self._positions[position_id] = pos

            # Log to database
            if self.db:
                self.db.log_event(
                    event_type="funding_arb_open",
                    message=f"Opened {side.value} {coin} @ {mark_px:.2f} rate={rate:.6f}",
                    event_data={
                        "position_id": position_id,
                        "coin": coin,
                        "side": side.value,
                        "entry_price": mark_px,
                        "quantity": quantity,
                        "notional": notional,
                        "funding_rate": rate,
                    },
                )

            return position_id

        except Exception as exc:
            logger.error("Failed to open position for %s: %s", coin, exc)
            return None

    async def close_position(
        self,
        position_id: str,
        reason: str,
        current_price: Optional[float] = None,
    ) -> bool:
        """Close an existing funding-rate arbitrage position.

        Args:
            position_id: Unique position identifier.
            reason: Human-readable close reason (e.g. ``"rate_reverted"``).
            current_price: Price to use for close; fetched if not provided.

        Returns:
            ``True`` on success.
        """
        try:
            pos = self._positions.get(position_id)
            if pos is None or pos.status == PositionStatus.CLOSED:
                logger.warning("Position %s not found or already closed", position_id)
                return False

            # Resolve close price
            if current_price is None or current_price <= 0:
                mids = await self.api.get_mids()
                mid_str = mids.get(pos.coin)
                current_price = float(mid_str) if mid_str else pos.entry_price

            # Calculate PnL
            if pos.side == PositionSide.LONG:
                price_pnl = (current_price - pos.entry_price) * pos.quantity
            else:
                price_pnl = (pos.entry_price - current_price) * pos.quantity

            realized_pnl = price_pnl + pos.total_funding_collected

            # Close on exchange or paper
            if self.config.PAPER_TRADING:
                fee = abs(pos.notional) * self.config.TAKER_FEE_PCT
                self._paper_capital += price_pnl - fee
                realized_pnl -= fee
                logger.info(
                    "[PAPER] CLOSE %s %s | reason=%s pnl=%.4f funding=%.4f net=%.4f",
                    position_id,
                    pos.coin,
                    reason,
                    price_pnl,
                    pos.total_funding_collected,
                    realized_pnl,
                )
            else:
                close_side = (
                    PositionSide.LONG
                    if pos.side == PositionSide.SHORT
                    else PositionSide.SHORT
                )
                result = await self.api.place_order(
                    side=close_side,  # type: ignore[arg-type]
                    price=current_price,
                    quantity=pos.quantity,
                    reduce_only=True,
                )
                if result.get("status") != "ok":
                    logger.error("Live close failed for %s: %s", position_id, result)
                    return False
                logger.info(
                    "[LIVE] CLOSE %s %s | reason=%s pnl=%.4f",
                    position_id,
                    pos.coin,
                    reason,
                    realized_pnl,
                )

            # Update position record
            pos.status = PositionStatus.CLOSED
            pos.close_reason = reason
            pos.close_time = time.time()
            pos.close_price = current_price
            pos.realized_pnl = realized_pnl

            # Log to database
            if self.db:
                self.db.log_event(
                    event_type="funding_arb_close",
                    message=(
                        f"Closed {pos.side.value} {pos.coin} "
                        f"reason={reason} pnl={realized_pnl:.4f} "
                        f"funding={pos.total_funding_collected:.4f}"
                    ),
                    event_data={
                        "position_id": position_id,
                        "coin": pos.coin,
                        "side": pos.side.value,
                        "entry_price": pos.entry_price,
                        "close_price": current_price,
                        "quantity": pos.quantity,
                        "realized_pnl": realized_pnl,
                        "funding_collected": pos.total_funding_collected,
                        "reason": reason,
                        "hold_hours": (pos.close_time - pos.entry_time) / 3600,
                    },
                )

            return True

        except Exception as exc:
            logger.error("Failed to close position %s: %s", position_id, exc)
            return False

    async def check_existing_positions(self) -> None:
        """Evaluate all open positions for exit conditions.

        Checks:
          1. Funding rate reversal (exit threshold).
          2. Maximum hold time exceeded.
          3. Emergency max-loss stop.
        """
        open_positions = [
            p for p in self._positions.values() if p.status == PositionStatus.OPEN
        ]
        if not open_positions:
            return

        # Get latest funding rates
        rates = await self.scan_funding_rates()
        rate_map: Dict[str, float] = {r["coin"]: r["funding_rate"] for r in rates}
        price_map: Dict[str, float] = {r["coin"]: r["mark_px"] for r in rates}

        now = time.time()

        for pos in open_positions:
            current_rate = rate_map.get(pos.coin, 0.0)
            current_price = price_map.get(pos.coin, pos.entry_price)
            hold_hours = (now - pos.entry_time) / 3600

            # --- 1. Funding rate reversal ---
            should_close = False
            reason = ""

            if pos.side == PositionSide.SHORT:
                # Close short when rate drops below negative exit threshold
                if current_rate < -self.config.EXIT_THRESHOLD:
                    should_close = True
                    reason = f"rate_reverted ({current_rate:.6f})"
            else:
                # Close long when rate rises above positive exit threshold
                if current_rate > self.config.EXIT_THRESHOLD:
                    should_close = True
                    reason = f"rate_reverted ({current_rate:.6f})"

            # --- 2. Max hold time ---
            if hold_hours > self.config.MAX_HOLD_HOURS:
                should_close = True
                reason = f"max_hold ({hold_hours:.1f}h > {self.config.MAX_HOLD_HOURS}h)"

            # --- 3. Emergency loss stop ---
            if current_price > 0 and pos.entry_price > 0:
                if pos.side == PositionSide.LONG:
                    price_change = (current_price - pos.entry_price) / pos.entry_price
                else:
                    price_change = (pos.entry_price - current_price) / pos.entry_price
                if price_change < -self.config.MAX_LOSS_PCT:
                    should_close = True
                    reason = (
                        f"max_loss ({price_change * 100:.2f}% < "
                        f"-{self.config.MAX_LOSS_PCT * 100:.0f}%)"
                    )

            # --- 4. Accumulate funding for open positions ---
            await self._accumulate_funding(pos, current_rate, now)

            if should_close:
                logger.info("Closing %s %s: %s", pos.id, pos.coin, reason)
                await self.close_position(pos.id, reason, current_price)

    async def run_cycle(self) -> None:
        """Execute one complete scan + manage cycle."""
        self._cycle_count += 1
        logger.info("--- Cycle #%d ---", self._cycle_count)

        # 1. Manage existing positions first
        await self.check_existing_positions()

        # 2. Scan for new opportunities
        opportunities = await self.scan_funding_rates()

        # Sort by absolute funding rate (best opportunities first)
        opportunities.sort(key=lambda o: abs(o["funding_rate"]), reverse=True)

        # 3. Open new positions where thresholds are met
        for opp in opportunities:
            coin: str = opp["coin"]
            rate: float = opp["funding_rate"]
            mark_px: float = opp["mark_px"]

            if rate > self.config.ENTRY_THRESHOLD:
                # High positive rate → SHORT (longs pay shorts)
                await self.open_position(coin, PositionSide.SHORT, rate, mark_px)
            elif rate < -self.config.ENTRY_THRESHOLD:
                # High negative rate → LONG (shorts pay longs)
                await self.open_position(coin, PositionSide.LONG, rate, mark_px)

    async def run(self) -> None:
        """Main strategy loop – runs until :meth:`stop` is called."""
        self._running = True
        logger.info(
            "Funding Rate Arb strategy started | interval=%ds | paper=%s",
            self.config.CHECK_INTERVAL,
            self.config.PAPER_TRADING,
        )

        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Cycle error: %s", exc)

            # Wait for next cycle
            try:
                await asyncio.wait_for(
                    asyncio.sleep(self.config.CHECK_INTERVAL),
                    timeout=self.config.CHECK_INTERVAL + 1,
                )
            except asyncio.CancelledError:
                logger.info("Strategy loop cancelled")
                break

        logger.info("Strategy stopped after %d cycles", self._cycle_count)

    def stop(self) -> None:
        """Signal the strategy loop to stop."""
        self._running = False

    def get_status(self) -> Dict[str, Any]:
        """Return current strategy state for display / monitoring.

        Returns:
            Dict with keys: ``cycle``, ``capital``, ``positions``, ``summary``.
        """
        open_positions = [
            p for p in self._positions.values() if p.status == PositionStatus.OPEN
        ]
        closed_positions = [
            p for p in self._positions.values() if p.status == PositionStatus.CLOSED
        ]

        total_pnl = sum(p.realized_pnl for p in closed_positions)
        total_funding = sum(p.total_funding_collected for p in self._positions.values())

        capital = self._paper_capital if self.config.PAPER_TRADING else None

        return {
            "cycle": self._cycle_count,
            "last_scan": datetime.fromtimestamp(
                self._last_scan_time, tz=timezone.utc
            ).isoformat()
            if self._last_scan_time
            else None,
            "capital": capital,
            "paper_trading": self.config.PAPER_TRADING,
            "positions": {
                "open": [
                    {
                        "id": p.id,
                        "coin": p.coin,
                        "side": p.side.value,
                        "entry_price": p.entry_price,
                        "entry_rate": p.entry_rate,
                        "quantity": p.quantity,
                        "notional": p.notional,
                        "funding_collected": round(p.total_funding_collected, 6),
                        "hold_hours": round((time.time() - p.entry_time) / 3600, 1),
                    }
                    for p in open_positions
                ],
                "closed_count": len(closed_positions),
            },
            "summary": {
                "total_pnl": round(total_pnl, 4),
                "total_funding_collected": round(total_funding, 6),
                "open_count": len(open_positions),
                "closed_count": len(closed_positions),
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_info(self) -> Info:
        """Return a reusable SDK ``Info`` object."""
        if self._info is None:
            self._info = Info(self.config.API_URL, skip_ws=True)
        return self._info

    async def _get_available_capital(self) -> float:
        """Return capital available for new positions."""
        if self.config.PAPER_TRADING:
            return self._paper_capital
        try:
            balance = await self.api.get_balance()
            return float(balance.get("account_value", 0))
        except Exception as exc:
            logger.error("Failed to get balance: %s", exc)
            return 0.0

    async def _accumulate_funding(
        self,
        pos: FundingPosition,
        current_rate: float,
        now: float,
    ) -> None:
        """Estimate funding accumulated since last settlement.

        Funding is paid every 8 hours.  We approximate the pro-rata
        accumulation based on elapsed time since ``last_funding_time``.

        For SHORT positions: positive rate ⇒ we *receive* funding.
        For LONG  positions: negative rate ⇒ we *receive* funding.
        """
        elapsed_hours = (now - pos.last_funding_time) / 3600.0
        if elapsed_hours < 0.01:
            return

        # Pro-rata of the 8h period
        fraction = min(elapsed_hours / 8.0, 1.0)

        if pos.side == PositionSide.SHORT:
            # Positive funding: longs pay shorts
            funding_amount = pos.notional * current_rate * fraction
        else:
            # Negative funding: shorts pay longs (flip sign)
            funding_amount = pos.notional * (-current_rate) * fraction

        # Only credit if it's in the right direction for our position
        if pos.side == PositionSide.SHORT and current_rate > 0:
            pos.total_funding_collected += funding_amount
        elif pos.side == PositionSide.LONG and current_rate < 0:
            pos.total_funding_collected += funding_amount

        # Track last accumulation time (reset every 8h equivalent)
        if elapsed_hours >= 8.0:
            pos.last_funding_time = now
