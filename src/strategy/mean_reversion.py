"""
Mean Reversion Strategy for HyperLiquid

Bollinger Band RSI Mean Reversion (BBRSI-MR) strategy that identifies overextended
price moves in crypto perpetual futures using the combination of Bollinger Band
width extremes, RSI oversold/overbought levels, and volume confirmation.

Strategy:
  - ENTER LONG when price closes below lower BB, RSI < oversold, volume spike, RSI turning up
  - ENTER SHORT when price closes above upper BB, RSI > overbought, volume spike, RSI turning down
  - EXIT on mean reversion (BB middle), opposite band, trailing stop, max hold, or signal invalidation
  - Risk managed with ATR-based stops, partial profit-taking, and daily loss limits
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from hyperliquid.info import Info

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class MRPositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class MRPositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class MeanReversionConfig:
    """Configuration for the Bollinger Band RSI Mean Reversion strategy."""

    # ── Mode ──────────────────────────────────────────────────
    PAPER_TRADING: bool = True
    USE_TESTNET: bool = False

    # ── Account ───────────────────────────────────────────────
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # ── Capital ───────────────────────────────────────────────
    PAPER_CAPITAL: float = 10_000.0

    # ── Bollinger Bands ───────────────────────────────────────
    BB_PERIOD: int = 20
    BB_STD_MULT_ENTRY: float = 2.5
    BB_STD_MULT_EXIT: float = 1.0
    BB_SQUEEZE_THRESHOLD: float = 0.015

    # ── RSI ───────────────────────────────────────────────────
    RSI_PERIOD: int = 14
    RSI_OVERBOUGHT: float = 75.0
    RSI_OVERSOLD: float = 25.0
    RSI_EXIT_MID: float = 50.0

    # ── VWAP ──────────────────────────────────────────────────
    VWAP_ENABLED: bool = True
    VWAP_DEVIATION_MULT: float = 2.0

    # ── Trend Filter (higher timeframe) ──────────────────────
    TREND_FILTER_ENABLED: bool = True
    HTF_EMA_FAST: int = 9
    HTF_EMA_SLOW: int = 21
    HTF_INTERVAL: str = "1h"
    COUNTER_TREND_ALLOWED: bool = False

    # ── Entry Confirmation ───────────────────────────────────
    REQUIRE_CANDLE_CLOSE: bool = True
    REQUIRE_VOLUME_SPIKE: bool = True
    VOLUME_SPIKE_MULT: float = 1.3
    REQUIRE_RSI_DIVERGENCE: bool = False

    # ── Position Sizing ──────────────────────────────────────
    POSITION_SIZE_PCT: float = 0.08
    LEVERAGE: int = 5
    COUNTER_TREND_SIZE_MULT: float = 0.5

    # ── Stop Loss / Take Profit ──────────────────────────────
    ATR_PERIOD: int = 14
    ATR_STOP_MULT: float = 1.5
    ATR_TP_MULT: float = 2.5
    USE_BB_OPPOSITE_EXIT: bool = True

    # ── Partial Profit Taking ────────────────────────────────
    PARTIAL_TP_ENABLED: bool = True
    PARTIAL_TP_PCT: float = 0.50
    PARTIAL_TP_MOVE_SL: bool = True

    # ── Trailing Stop ────────────────────────────────────────
    TRAILING_STOP_ENABLED: bool = True
    TRAILING_ACTIVATION_ATR: float = 1.0
    TRAILING_STEP_ATR: float = 0.5

    # ── Position Limits ──────────────────────────────────────
    MAX_CONCURRENT_POSITIONS: int = 4
    MAX_HOLD_CANDLES: int = 32
    MAX_LOSS_PCT: float = 0.04

    # ── Cooldown ─────────────────────────────────────────────
    COOLDOWN_CANDLES: int = 4
    MAX_TRADES_PER_COIN_PER_DAY: int = 4

    # ── Scan Interval ────────────────────────────────────────
    CHECK_INTERVAL: int = 60
    CANDLE_INTERVAL: str = "15m"

    # ── Asset Filter ─────────────────────────────────────────
    COINS: Optional[List[str]] = None

    # ── Fees ─────────────────────────────────────────────────
    TAKER_FEE_PCT: float = 0.0005

    # ── Database ─────────────────────────────────────────────
    DATABASE_PATH: str = "mean_reversion.db"

    # ── API ──────────────────────────────────────────────────
    API_URL: str = "https://api.hyperliquid.xyz"

    # ── Risk Management ──────────────────────────────────────
    MAX_DAILY_LOSS_PCT: float = 0.08
    CIRCUIT_BREAKER_LOSSES: int = 3
    CIRCUIT_BREAKER_PAUSE_SECS: float = 7200.0  # 2 hours

    def validate(self) -> bool:
        if self.POSITION_SIZE_PCT <= 0 or self.POSITION_SIZE_PCT > 0.25:
            raise ValueError("POSITION_SIZE_PCT must be in (0, 0.25]")
        if self.BB_PERIOD < 5 or self.BB_PERIOD > 100:
            raise ValueError("BB_PERIOD must be in [5, 100]")
        if self.RSI_PERIOD < 2 or self.RSI_PERIOD > 50:
            raise ValueError("RSI_PERIOD must be in [2, 50]")
        if self.RSI_OVERBOUGHT <= self.RSI_OVERSOLD:
            raise ValueError("RSI_OVERBOUGHT must be > RSI_OVERSOLD")
        if self.LEVERAGE < 1 or self.LEVERAGE > 50:
            raise ValueError("LEVERAGE must be in [1, 50]")
        if not self.PAPER_TRADING and not self.PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY required for live trading")
        return True


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------


@dataclass
class MeanReversionPosition:
    """Tracks a single mean reversion position."""

    id: str
    coin: str
    side: MRPositionSide
    entry_price: float
    quantity: float
    notional: float
    entry_time: float
    atr_at_entry: float
    stop_loss: float
    take_profit: float
    mean_target: float  # BB middle band at entry
    partial_tp_taken: bool = False
    trailing_stop: float = 0.0
    highest_price: float = 0.0  # For trailing (LONG)
    lowest_price: float = float("inf")  # For trailing (SHORT)
    status: MRPositionStatus = MRPositionStatus.OPEN
    close_reason: str = ""
    close_time: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class MeanReversionStrategy:
    """Bollinger Band RSI Mean Reversion strategy engine for HyperLiquid perpetuals."""

    def __init__(
        self,
        config: MeanReversionConfig,
        api: Any,
        db: Any,
    ) -> None:
        self.config = config
        self.api = api
        self.db = db

        self._positions: Dict[str, MeanReversionPosition] = {}
        self._running: bool = False
        self._cycle_count: int = 0
        self._paper_capital: float = config.PAPER_CAPITAL
        self._info: Optional[Info] = None

        # Cooldown tracking: {coin: last_close_time}
        self._cooldowns: Dict[str, float] = {}
        # Trade count per coin per day: {(coin, date_str): count}
        self._trade_counts: Dict[str, int] = {}
        # Circuit breaker state
        self._consecutive_losses: int = 0
        self._circuit_breaker_until: float = 0.0
        # Daily loss tracking
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: str = ""

        # Candle cache per coin
        self._candle_cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_markets(self) -> List[Dict[str, Any]]:
        """Fetch current market data and generate mean reversion signals.

        Returns:
            List of signal dicts with keys: coin, side, price, atr, confidence, ...
        """
        try:
            coins = await self._get_coins()
            signals = []

            for coin in coins:
                candles = await self._fetch_candles(coin)
                if candles is None or len(candles) < self.config.BB_PERIOD + 10:
                    continue

                signal = self._analyze_mean_reversion(coin, candles)
                if signal:
                    signals.append(signal)

            return signals

        except Exception as exc:
            logger.error("Failed to scan markets: %s", exc)
            return []

    async def open_position(
        self,
        coin: str,
        side: MRPositionSide,
        price: float,
        atr: float,
        mean_target: float,
        is_counter_trend: bool = False,
    ) -> Optional[str]:
        """Open a new mean reversion position."""
        try:
            # Check circuit breaker
            if time.time() < self._circuit_breaker_until:
                logger.debug("Circuit breaker active — skipping entry")
                return None

            # Check daily loss limit
            await self._check_daily_reset()
            if self._daily_pnl < -(self.config.PAPER_CAPITAL * self.config.MAX_DAILY_LOSS_PCT):
                logger.warning("Daily loss limit hit — skipping entry")
                return None

            # Check limits
            open_count = sum(
                1 for p in self._positions.values()
                if p.status == MRPositionStatus.OPEN
            )
            if open_count >= self.config.MAX_CONCURRENT_POSITIONS:
                return None

            # No duplicate positions for same coin
            for p in self._positions.values():
                if p.coin == coin and p.status == MRPositionStatus.OPEN:
                    return None

            # Check cooldown
            if not self._check_cooldown(coin):
                return None

            # Check max trades per coin per day
            today_key = f"{coin}_{time.strftime('%Y-%m-%d')}"
            if self._trade_counts.get(today_key, 0) >= self.config.MAX_TRADES_PER_COIN_PER_DAY:
                return None

            # Position sizing
            capital = await self._get_available_capital()
            base_notional = capital * self.config.POSITION_SIZE_PCT

            # Adjust for counter-trend
            if is_counter_trend and self.config.TREND_FILTER_ENABLED and self.config.COUNTER_TREND_ALLOWED:
                adjusted_notional = base_notional * self.config.COUNTER_TREND_SIZE_MULT
            else:
                adjusted_notional = base_notional

            # Volatility adaptation
            effective_size_mult = 1.0
            effective_stop_mult = self.config.ATR_STOP_MULT
            vol_mult = await self._get_volatility_adjustment(coin, atr)
            if vol_mult is not None:
                effective_size_mult = vol_mult["size_mult"]
                effective_stop_mult = vol_mult["stop_mult"]

            adjusted_notional *= effective_size_mult
            if adjusted_notional <= 0 or price <= 0:
                return None

            quantity = round(adjusted_notional / price, 4)
            if quantity <= 0:
                return None

            # Calculate stops with volatility adjustment
            if side == MRPositionSide.LONG:
                stop_loss = price - atr * effective_stop_mult
                take_profit = price + atr * self.config.ATR_TP_MULT
            else:
                stop_loss = price + atr * effective_stop_mult
                take_profit = price - atr * self.config.ATR_TP_MULT

            position_id = str(uuid.uuid4())[:8]

            if self.config.PAPER_TRADING:
                fee = adjusted_notional * self.config.TAKER_FEE_PCT
                self._paper_capital -= fee
                logger.info(
                    "[PAPER] OPEN %s %s %s | px=%.2f qty=%.4f sl=%.2f tp=%.2f "
                    "mean=%.2f atr=%.4f counter_trend=%s",
                    position_id, side.value, coin, price, quantity,
                    stop_loss, take_profit, mean_target, atr, is_counter_trend,
                )
            else:
                result = await self.api.place_order(
                    side=side.value,
                    price=price,
                    quantity=quantity,
                )
                if result.get("status") != "ok":
                    logger.error("Live order failed for %s: %s", coin, result)
                    return None
                logger.info(
                    "[LIVE] OPEN %s %s %s | px=%.2f qty=%.4f",
                    position_id, side.value, coin, price, quantity,
                )

            pos = MeanReversionPosition(
                id=position_id,
                coin=coin,
                side=side,
                entry_price=price,
                quantity=quantity,
                notional=adjusted_notional,
                entry_time=time.time(),
                atr_at_entry=atr,
                stop_loss=stop_loss,
                take_profit=take_profit,
                mean_target=mean_target,
                highest_price=price,
                lowest_price=price,
            )

            if self.config.TRAILING_STOP_ENABLED:
                pos.trailing_stop = stop_loss

            self._positions[position_id] = pos

            # Update trade count
            self._trade_counts[today_key] = self._trade_counts.get(today_key, 0) + 1

            if self.db:
                self.db.log_event(
                    event_type="mean_reversion_open",
                    message=f"Opened {side.value} {coin} @ {price:.2f}",
                    event_data={
                        "position_id": position_id,
                        "coin": coin,
                        "side": side.value,
                        "entry_price": price,
                        "quantity": quantity,
                        "notional": adjusted_notional,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "mean_target": mean_target,
                        "atr": atr,
                        "counter_trend": is_counter_trend,
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
        """Close an existing mean reversion position."""
        try:
            pos = self._positions.get(position_id)
            if pos is None or pos.status == MRPositionStatus.CLOSED:
                return False

            if current_price is None or current_price <= 0:
                mids = await self.api.get_mids()
                mid_str = mids.get(pos.coin)
                current_price = float(mid_str) if mid_str else pos.entry_price

            # Calculate PnL
            if pos.side == MRPositionSide.LONG:
                price_pnl = (current_price - pos.entry_price) * pos.quantity
            else:
                price_pnl = (pos.entry_price - current_price) * pos.quantity

            if self.config.PAPER_TRADING:
                fee = abs(pos.notional) * self.config.TAKER_FEE_PCT
                self._paper_capital += price_pnl - fee
                realized_pnl = price_pnl - fee
                logger.info(
                    "[PAPER] CLOSE %s %s | reason=%s pnl=%.4f",
                    position_id, pos.coin, reason, realized_pnl,
                )
            else:
                close_side = (
                    MRPositionSide.LONG
                    if pos.side == MRPositionSide.SHORT
                    else MRPositionSide.SHORT
                )
                result = await self.api.place_order(
                    side=close_side.value,
                    price=current_price,
                    quantity=pos.quantity,
                    reduce_only=True,
                )
                if result.get("status") != "ok":
                    logger.error("Live close failed: %s", result)
                    return False
                realized_pnl = price_pnl
                logger.info(
                    "[LIVE] CLOSE %s %s | reason=%s pnl=%.4f",
                    position_id, pos.coin, reason, realized_pnl,
                )

            pos.status = MRPositionStatus.CLOSED
            pos.close_reason = reason
            pos.close_time = time.time()
            pos.close_price = current_price
            pos.realized_pnl = realized_pnl

            # Update cooldown
            self._cooldowns[pos.coin] = time.time()

            # Update daily PnL
            await self._check_daily_reset()
            self._daily_pnl += realized_pnl

            # Update circuit breaker
            if realized_pnl < 0:
                self._consecutive_losses += 1
                if self._consecutive_losses >= self.config.CIRCUIT_BREAKER_LOSSES:
                    self._circuit_breaker_until = time.time() + self.config.CIRCUIT_BREAKER_PAUSE_SECS
                    logger.warning(
                        "Circuit breaker activated: %d consecutive losses. Pausing for %.0f seconds.",
                        self._consecutive_losses, self.config.CIRCUIT_BREAKER_PAUSE_SECS,
                    )
            else:
                self._consecutive_losses = 0

            if self.db:
                self.db.log_event(
                    event_type="mean_reversion_close",
                    message=f"Closed {pos.side.value} {pos.coin} reason={reason} pnl={realized_pnl:.4f}",
                    event_data={
                        "position_id": position_id,
                        "coin": pos.coin,
                        "side": pos.side.value,
                        "entry_price": pos.entry_price,
                        "close_price": current_price,
                        "quantity": pos.quantity,
                        "realized_pnl": realized_pnl,
                        "reason": reason,
                        "partial_tp_taken": pos.partial_tp_taken,
                        "hold_hours": (pos.close_time - pos.entry_time) / 3600,
                    },
                )

            return True

        except Exception as exc:
            logger.error("Failed to close position %s: %s", position_id, exc)
            return False

    async def close_partial(
        self,
        position_id: str,
        pct: float,
        reason: str,
        current_price: Optional[float] = None,
    ) -> bool:
        """Close a partial position (for mean reversion partial TP)."""
        try:
            pos = self._positions.get(position_id)
            if pos is None or pos.status == MRPositionStatus.CLOSED:
                return False

            if current_price is None or current_price <= 0:
                mids = await self.api.get_mids()
                mid_str = mids.get(pos.coin)
                current_price = float(mid_str) if mid_str else pos.entry_price

            close_quantity = round(pos.quantity * pct, 4)
            if close_quantity <= 0:
                return False

            # Calculate partial PnL
            if pos.side == MRPositionSide.LONG:
                price_pnl = (current_price - pos.entry_price) * close_quantity
            else:
                price_pnl = (pos.entry_price - current_price) * close_quantity

            if self.config.PAPER_TRADING:
                close_notional = close_quantity * current_price
                fee = close_notional * self.config.TAKER_FEE_PCT
                self._paper_capital += price_pnl - fee
                partial_pnl = price_pnl - fee
                logger.info(
                    "[PAPER] PARTIAL TP %s %s %.0f%% | px=%.2f qty=%.4f pnl=%.4f reason=%s",
                    position_id, pos.coin, pct * 100, current_price, close_quantity,
                    partial_pnl, reason,
                )
            else:
                close_side = (
                    MRPositionSide.LONG
                    if pos.side == MRPositionSide.SHORT
                    else MRPositionSide.SHORT
                )
                result = await self.api.place_order(
                    side=close_side.value,
                    price=current_price,
                    quantity=close_quantity,
                    reduce_only=True,
                )
                if result.get("status") != "ok":
                    logger.error("Live partial close failed: %s", result)
                    return False
                partial_pnl = price_pnl
                logger.info(
                    "[LIVE] PARTIAL TP %s %s %.0f%% | px=%.2f qty=%.4f",
                    position_id, pos.coin, pct * 100, current_price, close_quantity,
                )

            # Update position
            pos.quantity -= close_quantity
            pos.notional -= close_quantity * pos.entry_price
            pos.partial_tp_taken = True

            # Move SL to breakeven after partial TP
            if self.config.PARTIAL_TP_MOVE_SL:
                pos.stop_loss = pos.entry_price
                logger.info(
                    "SL moved to breakeven (%.2f) for %s after partial TP",
                    pos.stop_loss, position_id,
                )

            if self.db:
                self.db.log_event(
                    event_type="mean_reversion_partial_tp",
                    message=f"Partial TP {pct*100:.0f}% {pos.side.value} {pos.coin} @ {current_price:.2f}",
                    event_data={
                        "position_id": position_id,
                        "coin": pos.coin,
                        "side": pos.side.value,
                        "close_pct": pct,
                        "close_price": current_price,
                        "close_quantity": close_quantity,
                        "partial_pnl": partial_pnl,
                        "remaining_quantity": pos.quantity,
                        "reason": reason,
                    },
                )

            return True

        except Exception as exc:
            logger.error("Failed to close partial position %s: %s", position_id, exc)
            return False

    async def check_existing_positions(self) -> None:
        """Evaluate all open positions for exit conditions."""
        open_positions = [
            p for p in self._positions.values()
            if p.status == MRPositionStatus.OPEN
        ]
        if not open_positions:
            return

        # Get current prices
        try:
            mids = await self.api.get_mids()
        except Exception:
            return

        now = time.time()
        candle_interval_sec = self._interval_to_ms(self.config.CANDLE_INTERVAL) / 1000.0

        for pos in open_positions:
            mid_str = mids.get(pos.coin)
            if not mid_str:
                continue
            current_price = float(mid_str)
            should_close = False
            reason = ""

            candles_held = (now - pos.entry_time) / candle_interval_sec

            # ── Priority 1: Emergency Stop Loss (MAX_LOSS_PCT) ──────────
            if pos.entry_price > 0:
                if pos.side == MRPositionSide.LONG:
                    price_change = (current_price - pos.entry_price) / pos.entry_price
                else:
                    price_change = (pos.entry_price - current_price) / pos.entry_price
                if price_change < -self.config.MAX_LOSS_PCT:
                    should_close = True
                    reason = f"max_loss ({price_change * 100:.2f}%)"

            # ── Priority 2: Fixed Stop Loss ─────────────────────────────
            if not should_close:
                if pos.side == MRPositionSide.LONG and current_price <= pos.stop_loss:
                    should_close = True
                    reason = f"stop_loss ({current_price:.2f} <= {pos.stop_loss:.2f})"
                elif pos.side == MRPositionSide.SHORT and current_price >= pos.stop_loss:
                    should_close = True
                    reason = f"stop_loss ({current_price:.2f} >= {pos.stop_loss:.2f})"

            # ── Priority 3: Trailing Stop ───────────────────────────────
            if not should_close and self.config.TRAILING_STOP_ENABLED:
                # Update trailing
                if pos.side == MRPositionSide.LONG:
                    if current_price > pos.highest_price:
                        pos.highest_price = current_price
                    # Check activation
                    favor_move = pos.highest_price - pos.entry_price
                    if favor_move >= self.config.TRAILING_ACTIVATION_ATR * pos.atr_at_entry:
                        new_trail = current_price - self.config.TRAILING_STEP_ATR * pos.atr_at_entry
                        pos.trailing_stop = max(pos.trailing_stop, new_trail)
                    if pos.trailing_stop > 0 and current_price <= pos.trailing_stop:
                        should_close = True
                        reason = f"trailing_stop ({current_price:.2f} <= {pos.trailing_stop:.2f})"
                else:
                    if current_price < pos.lowest_price:
                        pos.lowest_price = current_price
                    favor_move = pos.entry_price - pos.lowest_price
                    if favor_move >= self.config.TRAILING_ACTIVATION_ATR * pos.atr_at_entry:
                        new_trail = current_price + self.config.TRAILING_STEP_ATR * pos.atr_at_entry
                        pos.trailing_stop = min(pos.trailing_stop, new_trail) if pos.trailing_stop > 0 else new_trail
                    if pos.trailing_stop > 0 and current_price >= pos.trailing_stop:
                        should_close = True
                        reason = f"trailing_stop ({current_price:.2f} >= {pos.trailing_stop:.2f})"

            # ── Priority 4: Partial Take Profit (at mean) ───────────────
            if not should_close and self.config.PARTIAL_TP_ENABLED and not pos.partial_tp_taken:
                if pos.side == MRPositionSide.LONG and current_price >= pos.mean_target:
                    await self.close_partial(position_id=pos.id, pct=self.config.PARTIAL_TP_PCT,
                                             reason="partial_tp_mean", current_price=current_price)
                elif pos.side == MRPositionSide.SHORT and current_price <= pos.mean_target:
                    await self.close_partial(position_id=pos.id, pct=self.config.PARTIAL_TP_PCT,
                                             reason="partial_tp_mean", current_price=current_price)

            # ── Priority 5: Full Take Profit ────────────────────────────
            if not should_close:
                if self.config.USE_BB_OPPOSITE_EXIT:
                    # Get current BB for opposite band exit
                    bb_data = await self._get_current_bb(pos.coin)
                    if bb_data is not None:
                        upper_band, middle_band, lower_band = bb_data
                        if pos.side == MRPositionSide.LONG and current_price >= upper_band:
                            should_close = True
                            reason = f"bb_opposite_exit ({current_price:.2f} >= {upper_band:.2f})"
                        elif pos.side == MRPositionSide.SHORT and current_price <= lower_band:
                            should_close = True
                            reason = f"bb_opposite_exit ({current_price:.2f} <= {lower_band:.2f})"
                if not should_close:
                    if pos.side == MRPositionSide.LONG and current_price >= pos.take_profit:
                        should_close = True
                        reason = f"take_profit ({current_price:.2f} >= {pos.take_profit:.2f})"
                    elif pos.side == MRPositionSide.SHORT and current_price <= pos.take_profit:
                        should_close = True
                        reason = f"take_profit ({current_price:.2f} <= {pos.take_profit:.2f})"

            # ── Priority 6: Max Hold Time ───────────────────────────────
            if not should_close and candles_held > self.config.MAX_HOLD_CANDLES:
                should_close = True
                reason = f"max_hold ({candles_held:.1f} candles)"

            # ── Priority 7: Signal Invalidation ─────────────────────────
            if not should_close:
                rsi_data = await self._get_current_rsi(pos.coin)
                if rsi_data is not None:
                    current_rsi = rsi_data
                    if self._check_signal_invalidation(pos, current_rsi, current_price):
                        should_close = True
                        reason = "signal_invalidated"

            if should_close:
                logger.info("Closing %s %s: %s", pos.id, pos.coin, reason)
                await self.close_position(pos.id, reason, current_price)

    async def run_cycle(self) -> None:
        """Execute one complete scan + manage cycle."""
        self._cycle_count += 1
        logger.info("--- Cycle #%d ---", self._cycle_count)

        # 1. Manage existing positions
        await self.check_existing_positions()

        # 2. Scan for new signals
        signals = await self.scan_markets()

        # Sort by confidence
        signals.sort(key=lambda s: s.get("confidence", 0), reverse=True)

        # 3. Open new positions
        for signal in signals:
            await self.open_position(
                coin=signal["coin"],
                side=signal["side"],
                price=signal["price"],
                atr=signal["atr"],
                mean_target=signal["mean_target"],
                is_counter_trend=signal.get("is_counter_trend", False),
            )

    async def run(self) -> None:
        """Main strategy loop."""
        self._running = True
        logger.info(
            "Mean Reversion strategy started | interval=%ds | paper=%s | coins=%s",
            self.config.CHECK_INTERVAL,
            self.config.PAPER_TRADING,
            self.config.COINS or "ALL",
        )

        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Cycle error: %s", exc)

            try:
                await asyncio.wait_for(
                    asyncio.sleep(self.config.CHECK_INTERVAL),
                    timeout=self.config.CHECK_INTERVAL + 1,
                )
            except asyncio.CancelledError:
                break

        logger.info("Strategy stopped after %d cycles", self._cycle_count)

    def stop(self) -> None:
        self._running = False

    def get_status(self) -> Dict[str, Any]:
        """Return current strategy state."""
        open_positions = [
            p for p in self._positions.values()
            if p.status == MRPositionStatus.OPEN
        ]
        closed_positions = [
            p for p in self._positions.values()
            if p.status == MRPositionStatus.CLOSED
        ]
        total_pnl = sum(p.realized_pnl for p in closed_positions)

        return {
            "cycle": self._cycle_count,
            "capital": self._paper_capital if self.config.PAPER_TRADING else None,
            "paper_trading": self.config.PAPER_TRADING,
            "positions": {
                "open": [
                    {
                        "id": p.id,
                        "coin": p.coin,
                        "side": p.side.value,
                        "entry_price": p.entry_price,
                        "quantity": p.quantity,
                        "stop_loss": p.stop_loss,
                        "take_profit": p.take_profit,
                        "mean_target": p.mean_target,
                        "trailing_stop": p.trailing_stop,
                        "partial_tp_taken": p.partial_tp_taken,
                        "hold_hours": round((time.time() - p.entry_time) / 3600, 1),
                    }
                    for p in open_positions
                ],
                "closed_count": len(closed_positions),
            },
            "summary": {
                "total_pnl": round(total_pnl, 4),
                "open_count": len(open_positions),
                "closed_count": len(closed_positions),
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_info(self) -> Info:
        if self._info is None:
            self._info = Info(self.config.API_URL, skip_ws=True)
        return self._info

    async def _get_coins(self) -> List[str]:
        """Get list of coins to monitor."""
        if self.config.COINS:
            return self.config.COINS

        try:
            info = self._get_info()
            raw = await asyncio.to_thread(info.meta_and_asset_ctxs)
            if raw and len(raw) >= 1:
                universe = raw[0].get("universe", [])
                return [u.get("name", "") for u in universe if u.get("name")]
        except Exception as exc:
            logger.error("Failed to get coin list: %s", exc)
        return []

    async def _fetch_candles(self, coin: str, interval: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Fetch candle data for a coin."""
        try:
            info = self._get_info()
            candle_interval = interval or self.config.CANDLE_INTERVAL
            end_time = int(time.time() * 1000)
            interval_ms = self._interval_to_ms(candle_interval)
            num_candles = 200
            start_time = end_time - (num_candles * interval_ms)
            candle_data = await asyncio.to_thread(
                info.candles_snapshot,
                coin,
                candle_interval,
                start_time,
                end_time,
            )

            if not candle_data:
                return None

            df = pd.DataFrame(candle_data)
            rename_map = {
                "o": "open", "h": "high", "l": "low",
                "c": "close", "v": "volume",
            }
            df = df.rename(columns=rename_map)

            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    return None
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["close"])
            return df

        except Exception as exc:
            logger.debug("Failed to fetch candles for %s: %s", coin, exc)
            return None

    def _analyze_mean_reversion(self, coin: str, df: pd.DataFrame) -> Optional[Dict]:
        """Analyze mean reversion setup and generate signal if conditions met."""
        try:
            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]

            # Calculate Bollinger Bands
            bb_result = self._calculate_bollinger_bands(df)
            if bb_result is None:
                return None
            upper_band, middle_band, lower_band = bb_result

            # Calculate RSI
            rsi = self._calculate_rsi(close, self.config.RSI_PERIOD)
            if rsi is None or len(rsi) < 3:
                return None

            # Calculate ATR
            atr = self._calculate_atr(df, self.config.ATR_PERIOD)
            if atr is None or atr.iloc[-1] <= 0:
                return None
            current_atr = atr.iloc[-1]

            # Current values
            current_price = close.iloc[-1]
            current_rsi = rsi.iloc[-1]
            prev_rsi = rsi.iloc[-2]  # RSI 1 bar ago
            rsi_2_ago = rsi.iloc[-3]  # RSI 2 bars ago
            current_upper = upper_band.iloc[-1]
            current_middle = middle_band.iloc[-1]
            current_lower = lower_band.iloc[-1]

            side = None
            is_counter_trend = False

            # ── LONG Entry Check ──────────────────────────────────
            # 1. Price closes below lower BB
            if current_price < current_lower:
                # 2. RSI < oversold
                if current_rsi < self.config.RSI_OVERSOLD:
                    # 3. RSI turning up from trough (RSI > RSI[2])
                    if current_rsi > rsi_2_ago:
                        # 4. Volume spike
                        vol_ok = True
                        if self.config.REQUIRE_VOLUME_SPIKE:
                            vol_ma = volume.rolling(20).mean()
                            if len(vol_ma) > 0 and not pd.isna(vol_ma.iloc[-1]):
                                vol_ok = volume.iloc[-1] > vol_ma.iloc[-1] * self.config.VOLUME_SPIKE_MULT

                        if vol_ok:
                            # 5. HTF trend filter
                            trend_ok = True
                            if self.config.TREND_FILTER_ENABLED:
                                htf_trend = self._get_htf_trend_sync(coin)
                                if htf_trend == "bearish":
                                    if not self.config.COUNTER_TREND_ALLOWED:
                                        trend_ok = False
                                        logger.debug(
                                            "Skipping LONG %s: strong bearish HTF trend", coin
                                        )
                                    else:
                                        is_counter_trend = True
                                        trend_ok = True

                            if trend_ok:
                                side = MRPositionSide.LONG

            # ── SHORT Entry Check ─────────────────────────────────
            # 1. Price closes above upper BB
            elif current_price > current_upper:
                # 2. RSI > overbought
                if current_rsi > self.config.RSI_OVERBOUGHT:
                    # 3. RSI turning down from peak (RSI < RSI[2])
                    if current_rsi < rsi_2_ago:
                        # 4. Volume spike
                        vol_ok = True
                        if self.config.REQUIRE_VOLUME_SPIKE:
                            vol_ma = volume.rolling(20).mean()
                            if len(vol_ma) > 0 and not pd.isna(vol_ma.iloc[-1]):
                                vol_ok = volume.iloc[-1] > vol_ma.iloc[-1] * self.config.VOLUME_SPIKE_MULT

                        if vol_ok:
                            # 5. HTF trend filter
                            trend_ok = True
                            if self.config.TREND_FILTER_ENABLED:
                                htf_trend = self._get_htf_trend_sync(coin)
                                if htf_trend == "bullish":
                                    if not self.config.COUNTER_TREND_ALLOWED:
                                        trend_ok = False
                                        logger.debug(
                                            "Skipping SHORT %s: strong bullish HTF trend", coin
                                        )
                                    else:
                                        is_counter_trend = True
                                        trend_ok = True

                            if trend_ok:
                                side = MRPositionSide.SHORT

            if side is None:
                return None

            # Optional: RSI Divergence check (stricter filter)
            if self.config.REQUIRE_RSI_DIVERGENCE:
                if not self._check_rsi_divergence(close, rsi, side):
                    return None

            # Optional: VWAP filter
            if self.config.VWAP_ENABLED:
                vwap = self._calculate_vwap(df)
                if vwap is not None and len(vwap) > 0 and not pd.isna(vwap.iloc[-1]):
                    vwap_val = vwap.iloc[-1]
                    deviation = abs(current_price - vwap_val) / current_atr
                    if deviation < self.config.VWAP_DEVIATION_MULT:
                        # Price too close to VWAP — not overextended enough
                        return None

            # Confidence score (0-100)
            bb_score = min(abs(current_price - current_middle) / (current_atr * 2), 1.0) * 30
            rsi_score = min(abs(current_rsi - 50) / 50, 1.0) * 30
            vol_ma = volume.rolling(20).mean()
            vol_score = 0
            if len(vol_ma) > 0 and not pd.isna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
                vol_score = min((volume.iloc[-1] / vol_ma.iloc[-1] - 1) / 2, 1.0) * 20
            counter_score = 0 if is_counter_trend else 20
            confidence = round(bb_score + rsi_score + vol_score + counter_score, 1)

            return {
                "coin": coin,
                "side": side,
                "price": current_price,
                "atr": current_atr,
                "mean_target": current_middle,
                "confidence": confidence,
                "rsi": current_rsi,
                "bb_upper": current_upper,
                "bb_lower": current_lower,
                "bb_middle": current_middle,
                "is_counter_trend": is_counter_trend,
            }

        except Exception as exc:
            logger.debug("Analysis failed for %s: %s", coin, exc)
            return None

    def _get_htf_trend_sync(self, coin: str) -> str:
        """Get higher timeframe trend direction (synchronous check using cached data)."""
        try:
            # Use cached candle data if available, otherwise return neutral
            if coin not in self._candle_cache:
                return "neutral"

            df = self._candle_cache[coin]
            if len(df) < self.config.HTF_EMA_SLOW + 5:
                return "neutral"

            close = df["close"]
            fast_ema = close.ewm(span=self.config.HTF_EMA_FAST).mean()
            slow_ema = close.ewm(span=self.config.HTF_EMA_SLOW).mean()

            current_fast = fast_ema.iloc[-1]
            current_slow = slow_ema.iloc[-1]
            current_price = close.iloc[-1]

            # Check neutral zone
            ema_diff_pct = abs(current_fast - current_slow) / current_price
            if ema_diff_pct < 0.005:
                return "neutral"

            if current_fast > current_slow:
                return "bullish"
            else:
                return "bearish"

        except Exception:
            return "neutral"

    def _check_rsi_divergence(
        self,
        close: pd.Series,
        rsi: pd.Series,
        side: MRPositionSide,
    ) -> bool:
        """Check for RSI divergence (optional stricter entry filter)."""
        try:
            # Look at last 10 bars for divergence
            lookback = min(10, len(close) - 1)
            if lookback < 3:
                return False

            recent_close = close.iloc[-lookback:]
            recent_rsi = rsi.iloc[-lookback:]

            if side == MRPositionSide.LONG:
                # Bullish divergence: price makes lower low, RSI makes higher low
                price_low_idx = recent_close.idxmin()
                rsi_at_price_low = recent_rsi.loc[price_low_idx]
                # Check if RSI at current low is higher than RSI at previous low
                prev_lows = recent_close.iloc[:lookback // 2]
                if len(prev_lows) > 0:
                    prev_low_idx = prev_lows.idxmin()
                    prev_rsi_at_low = recent_rsi.loc[prev_low_idx]
                    if recent_close.iloc[-1] < prev_lows.min() and rsi.iloc[-1] > prev_rsi_at_low:
                        return True
            else:
                # Bearish divergence: price makes higher high, RSI makes lower high
                price_high_idx = recent_close.idxmax()
                rsi_at_price_high = recent_rsi.loc[price_high_idx]
                prev_highs = recent_close.iloc[:lookback // 2]
                if len(prev_highs) > 0:
                    prev_high_idx = prev_highs.idxmax()
                    prev_rsi_at_high = recent_rsi.loc[prev_high_idx]
                    if recent_close.iloc[-1] > prev_highs.max() and rsi.iloc[-1] < prev_rsi_at_high:
                        return True

            return False
        except Exception:
            return False

    def _check_signal_invalidation(
        self,
        pos: MeanReversionPosition,
        current_rsi: float,
        current_price: float,
    ) -> bool:
        """Check if the mean reversion signal has been invalidated."""
        if pos.side == MRPositionSide.LONG:
            # RSI drops below 15 (extreme oversold) AND price makes new low below entry
            if current_rsi < 15 and current_price < pos.entry_price:
                return True
        else:
            # RSI rises above 85 (extreme overbought) AND price makes new high above entry
            if current_rsi > 85 and current_price > pos.entry_price:
                return True
        return False

    def _check_cooldown(self, coin: str) -> bool:
        """Check if cooldown period has passed for a coin."""
        last_close = self._cooldowns.get(coin)
        if last_close is None:
            return True
        candle_sec = self._interval_to_ms(self.config.CANDLE_INTERVAL) / 1000.0
        elapsed_candles = (time.time() - last_close) / candle_sec
        return elapsed_candles >= self.config.COOLDOWN_CANDLES

    async def _check_daily_reset(self) -> None:
        """Reset daily PnL tracking at midnight."""
        today = time.strftime("%Y-%m-%d")
        if self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today

    async def _get_volatility_adjustment(
        self, coin: str, current_atr: float
    ) -> Optional[Dict[str, float]]:
        """Get volatility-adjusted multipliers for position sizing and stops."""
        try:
            candles = await self._fetch_candles(coin)
            if candles is None or len(candles) < 55:
                return None

            atr_series = self._calculate_atr(candles, self.config.ATR_PERIOD)
            if atr_series is None or len(atr_series) < 50:
                return None

            atr_sma = atr_series.rolling(50).mean().iloc[-1]
            if atr_sma <= 0:
                return None

            ratio = current_atr / atr_sma
            if ratio > 2.0:
                # High volatility: wider stops, smaller size
                return {"stop_mult": self.config.ATR_STOP_MULT * 1.3, "size_mult": 0.7}
            elif ratio < 0.5:
                # Low volatility: tighter stops, normal size
                return {"stop_mult": self.config.ATR_STOP_MULT * 0.8, "size_mult": 1.0}
            return None
        except Exception:
            return None

    async def _get_current_bb(self, coin: str) -> Optional[Tuple[float, float, float]]:
        """Get current Bollinger Band values for a coin."""
        try:
            candles = await self._fetch_candles(coin)
            if candles is None:
                return None
            result = self._calculate_bollinger_bands(candles)
            if result is None:
                return None
            upper, middle, lower = result
            return (upper.iloc[-1], middle.iloc[-1], lower.iloc[-1])
        except Exception:
            return None

    async def _get_current_rsi(self, coin: str) -> Optional[float]:
        """Get current RSI value for a coin."""
        try:
            candles = await self._fetch_candles(coin)
            if candles is None:
                return None
            rsi = self._calculate_rsi(candles["close"], self.config.RSI_PERIOD)
            if rsi is None or len(rsi) == 0:
                return None
            return rsi.iloc[-1]
        except Exception:
            return None

    # ── Indicator Calculations ──────────────────────────────────────

    @staticmethod
    def _calculate_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std_mult: float = 2.5,
    ) -> Optional[Tuple[pd.Series, pd.Series, pd.Series]]:
        """Calculate Bollinger Bands.

        Returns: (upper_band, middle_band, lower_band)
        """
        try:
            close = df["close"]
            middle_band = close.rolling(window=period).mean()
            std = close.rolling(window=period).std(ddof=0)
            upper_band = middle_band + std_mult * std
            lower_band = middle_band - std_mult * std
            return (upper_band, middle_band, lower_band)
        except Exception:
            return None

    @staticmethod
    def _calculate_rsi(close: pd.Series, period: int = 14) -> Optional[pd.Series]:
        """Calculate RSI using Wilder's smoothing."""
        try:
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)

            # Wilder's smoothing: EMA with alpha = 1/period
            avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except Exception:
            return None

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[pd.Series]:
        """Calculate Average True Range."""
        try:
            high = df["high"]
            low = df["low"]
            close = df["close"]

            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            return tr.rolling(window=period).mean()
        except Exception:
            return None

    @staticmethod
    def _calculate_vwap(df: pd.DataFrame) -> Optional[pd.Series]:
        """Calculate rolling VWAP over last 96 candles (~24h of 15m data)."""
        try:
            typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
            tp_vol = typical_price * df["volume"]
            cum_tp_vol = tp_vol.rolling(window=96, min_periods=1).sum()
            cum_vol = df["volume"].rolling(window=96, min_periods=1).sum()

            vwap = cum_tp_vol / cum_vol
            return vwap
        except Exception:
            return None

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        """Convert candle interval string to milliseconds."""
        mapping = {
            "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
            "4h": 14_400_000, "8h": 28_800_000, "1d": 86_400_000,
            "1w": 604_800_000,
        }
        return mapping.get(interval, 900_000)  # default 15m

    async def _get_available_capital(self) -> float:
        if self.config.PAPER_TRADING:
            return self._paper_capital
        try:
            balance = await self.api.get_balance()
            return float(balance.get("account_value", 0))
        except Exception:
            return 0.0
