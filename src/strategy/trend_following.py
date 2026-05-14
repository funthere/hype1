"""
Trend Following Strategy for HyperLiquid

Multi-asset trend following bot that captures sustained directional moves
using a combination of EMA crossovers, ADX trend strength, and ATR-based
position management.

Strategy:
  - ENTER LONG when fast EMA > slow EMA, ADX > threshold, and pullback confirmed
  - ENTER SHORT when fast EMA < slow EMA, ADX > threshold, and pullback confirmed
  - EXIT on trend reversal (EMA cross), trailing stop, or max hold time
  - Risk managed with ATR-based stops and position sizing
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hyperliquid.info import Info

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TrendPositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TrendPositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class TrendFollowingConfig:
    """Configuration for the Trend Following strategy."""

    # Mode
    PAPER_TRADING: bool = True
    USE_TESTNET: bool = False

    # Account
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # Capital
    PAPER_CAPITAL: float = 10_000.0

    # Trend detection
    FAST_EMA_PERIOD: int = 9
    SLOW_EMA_PERIOD: int = 21
    TREND_EMA_PERIOD: int = 50      # Higher timeframe trend filter
    ADX_PERIOD: int = 14
    ADX_THRESHOLD: float = 20.0     # Minimum trend strength
    ATR_PERIOD: int = 14

    # Entry filters
    REQUIRE_PULLBACK: bool = True    # Wait for pullback to EMA before entry
    PULLBACK_ATR_MULT: float = 0.5  # Max distance from fast EMA (in ATR)
    VOLUME_FILTER: bool = True       # Require above-average volume
    VOLUME_MULT: float = 1.2         # Volume must be > MA * this

    # Position management
    POSITION_SIZE_PCT: float = 0.10  # 10% of capital per trade
    LEVERAGE: int = 3
    ATR_STOP_MULT: float = 2.0       # Stop loss = ATR * this
    ATR_TP_MULT: float = 4.0         # Take profit = ATR * this
    TRAILING_STOP_MULT: float = 2.5  # Trailing stop = ATR * this
    USE_TRAILING_STOP: bool = True

    # Limits
    MAX_CONCURRENT_POSITIONS: int = 3
    MAX_HOLD_HOURS: float = 168.0    # 7 days
    MAX_LOSS_PCT: float = 0.05       # 5% emergency stop

    # Scan interval
    CHECK_INTERVAL: int = 300        # 5 minutes
    CANDLE_INTERVAL: str = "1h"      # Candle timeframe

    # Asset filter
    COINS: Optional[List[str]] = None

    # Fees
    TAKER_FEE_PCT: float = 0.0005

    # Database
    DATABASE_PATH: str = "trend_following.db"

    # API URLs
    API_URL: str = "https://api.hyperliquid.xyz"

    def validate(self) -> bool:
        if self.POSITION_SIZE_PCT <= 0 or self.POSITION_SIZE_PCT > 1:
            raise ValueError("POSITION_SIZE_PCT must be in (0, 1]")
        if self.FAST_EMA_PERIOD >= self.SLOW_EMA_PERIOD:
            raise ValueError("FAST_EMA_PERIOD must be < SLOW_EMA_PERIOD")
        if self.LEVERAGE < 1 or self.LEVERAGE > 100:
            raise ValueError("LEVERAGE must be in [1, 100]")
        if not self.PAPER_TRADING and not self.PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY required for live trading")
        return True


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------


@dataclass
class TrendPosition:
    """Tracks a single trend following position."""

    id: str
    coin: str
    side: TrendPositionSide
    entry_price: float
    quantity: float
    notional: float
    entry_time: float
    atr_at_entry: float
    stop_loss: float
    take_profit: float
    trailing_stop: float = 0.0
    highest_profit_price: float = 0.0
    lowest_profit_price: float = float("inf")
    status: TrendPositionStatus = TrendPositionStatus.OPEN
    close_reason: str = ""
    close_time: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class TrendFollowingStrategy:
    """Trend Following strategy engine for HyperLiquid perpetuals."""

    def __init__(
        self,
        config: TrendFollowingConfig,
        api: Any,
        db: Any,
    ) -> None:
        self.config = config
        self.api = api
        self.db = db

        self._positions: Dict[str, TrendPosition] = {}
        self._running: bool = False
        self._cycle_count: int = 0
        self._paper_capital: float = config.PAPER_CAPITAL
        self._info: Optional[Info] = None

        # Cache candle data per coin
        self._candle_cache: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan_markets(self) -> List[Dict[str, Any]]:
        """Fetch current market data and generate trend signals.

        Returns:
            List of signal dicts with keys: coin, side, price, atr, confidence
        """
        try:
            coins = await self._get_coins()
            signals = []

            for coin in coins:
                candles = await self._fetch_candles(coin)
                if candles is None or len(candles) < self.config.TREND_EMA_PERIOD + 10:
                    continue

                signal = self._analyze_trend(coin, candles)
                if signal:
                    signals.append(signal)

            return signals

        except Exception as exc:
            logger.error("Failed to scan markets: %s", exc)
            return []

    async def open_position(
        self,
        coin: str,
        side: TrendPositionSide,
        price: float,
        atr: float,
    ) -> Optional[str]:
        """Open a new trend following position."""
        try:
            # Check limits
            open_count = sum(
                1 for p in self._positions.values()
                if p.status == TrendPositionStatus.OPEN
            )
            if open_count >= self.config.MAX_CONCURRENT_POSITIONS:
                return None

            # No duplicate positions for same coin
            for p in self._positions.values():
                if p.coin == coin and p.status == TrendPositionStatus.OPEN:
                    return None

            # Position sizing
            capital = await self._get_available_capital()
            notional = capital * self.config.POSITION_SIZE_PCT
            if notional <= 0 or price <= 0:
                return None

            quantity = round(notional / price, 4)
            if quantity <= 0:
                return None

            # Calculate stops
            if side == TrendPositionSide.LONG:
                stop_loss = price - atr * self.config.ATR_STOP_MULT
                take_profit = price + atr * self.config.ATR_TP_MULT
            else:
                stop_loss = price + atr * self.config.ATR_STOP_MULT
                take_profit = price - atr * self.config.ATR_TP_MULT

            position_id = str(uuid.uuid4())[:8]

            if self.config.PAPER_TRADING:
                fee = notional * self.config.TAKER_FEE_PCT
                self._paper_capital -= fee
                logger.info(
                    "[PAPER] OPEN %s %s %s | px=%.2f qty=%.4f sl=%.2f tp=%.2f atr=%.4f",
                    position_id, side.value, coin, price, quantity,
                    stop_loss, take_profit, atr,
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

            pos = TrendPosition(
                id=position_id,
                coin=coin,
                side=side,
                entry_price=price,
                quantity=quantity,
                notional=notional,
                entry_time=time.time(),
                atr_at_entry=atr,
                stop_loss=stop_loss,
                take_profit=take_profit,
                highest_profit_price=price if side == TrendPositionSide.LONG else price,
                lowest_profit_price=price if side == TrendPositionSide.SHORT else price,
            )

            if self.config.USE_TRAILING_STOP:
                pos.trailing_stop = stop_loss

            self._positions[position_id] = pos

            if self.db:
                self.db.log_event(
                    event_type="trend_open",
                    message=f"Opened {side.value} {coin} @ {price:.2f}",
                    event_data={
                        "position_id": position_id,
                        "coin": coin,
                        "side": side.value,
                        "entry_price": price,
                        "quantity": quantity,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "atr": atr,
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
        """Close an existing trend following position."""
        try:
            pos = self._positions.get(position_id)
            if pos is None or pos.status == TrendPositionStatus.CLOSED:
                return False

            if current_price is None or current_price <= 0:
                mids = await self.api.get_mids()
                mid_str = mids.get(pos.coin)
                current_price = float(mid_str) if mid_str else pos.entry_price

            # Calculate PnL
            if pos.side == TrendPositionSide.LONG:
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
                    TrendPositionSide.LONG
                    if pos.side == TrendPositionSide.SHORT
                    else TrendPositionSide.SHORT
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

            pos.status = TrendPositionStatus.CLOSED
            pos.close_reason = reason
            pos.close_time = time.time()
            pos.close_price = current_price
            pos.realized_pnl = realized_pnl

            if self.db:
                self.db.log_event(
                    event_type="trend_close",
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
                        "hold_hours": (pos.close_time - pos.entry_time) / 3600,
                    },
                )

            return True

        except Exception as exc:
            logger.error("Failed to close position %s: %s", position_id, exc)
            return False

    async def check_existing_positions(self) -> None:
        """Evaluate all open positions for exit conditions."""
        open_positions = [
            p for p in self._positions.values()
            if p.status == TrendPositionStatus.OPEN
        ]
        if not open_positions:
            return

        # Get current prices
        try:
            mids = await self.api.get_mids()
        except Exception:
            return

        now = time.time()

        for pos in open_positions:
            mid_str = mids.get(pos.coin)
            if not mid_str:
                continue
            current_price = float(mid_str)
            should_close = False
            reason = ""

            hold_hours = (now - pos.entry_time) / 3600

            # Update trailing stop
            if self.config.USE_TRAILING_STOP:
                if pos.side == TrendPositionSide.LONG:
                    if current_price > pos.highest_profit_price:
                        pos.highest_profit_price = current_price
                        new_trail = current_price - pos.atr_at_entry * self.config.TRAILING_STOP_MULT
                        pos.trailing_stop = max(pos.trailing_stop, new_trail)
                else:
                    if current_price < pos.lowest_profit_price:
                        pos.lowest_profit_price = current_price
                        new_trail = current_price + pos.atr_at_entry * self.config.TRAILING_STOP_MULT
                        pos.trailing_stop = min(pos.trailing_stop, new_trail) if pos.trailing_stop != 0 else new_trail

            # Check exits
            if pos.side == TrendPositionSide.LONG:
                # Trailing stop hit
                if self.config.USE_TRAILING_STOP and current_price <= pos.trailing_stop:
                    should_close = True
                    reason = f"trailing_stop ({current_price:.2f} <= {pos.trailing_stop:.2f})"
                # Fixed stop loss
                elif current_price <= pos.stop_loss:
                    should_close = True
                    reason = f"stop_loss ({current_price:.2f} <= {pos.stop_loss:.2f})"
                # Take profit
                elif current_price >= pos.take_profit:
                    should_close = True
                    reason = f"take_profit ({current_price:.2f} >= {pos.take_profit:.2f})"
                # Trend reversal: check if fast EMA crossed below slow EMA
                elif await self._check_trend_reversal(pos.coin, TrendPositionSide.LONG):
                    should_close = True
                    reason = "trend_reversal"
            else:
                # SHORT exits
                if self.config.USE_TRAILING_STOP and current_price >= pos.trailing_stop and pos.trailing_stop != 0:
                    should_close = True
                    reason = f"trailing_stop ({current_price:.2f} >= {pos.trailing_stop:.2f})"
                elif current_price >= pos.stop_loss:
                    should_close = True
                    reason = f"stop_loss ({current_price:.2f} >= {pos.stop_loss:.2f})"
                elif current_price <= pos.take_profit:
                    should_close = True
                    reason = f"take_profit ({current_price:.2f} <= {pos.take_profit:.2f})"
                elif await self._check_trend_reversal(pos.coin, TrendPositionSide.SHORT):
                    should_close = True
                    reason = "trend_reversal"

            # Max hold time
            if hold_hours > self.config.MAX_HOLD_HOURS:
                should_close = True
                reason = f"max_hold ({hold_hours:.1f}h)"

            # Emergency loss stop
            if pos.entry_price > 0:
                if pos.side == TrendPositionSide.LONG:
                    price_change = (current_price - pos.entry_price) / pos.entry_price
                else:
                    price_change = (pos.entry_price - current_price) / pos.entry_price
                if price_change < -self.config.MAX_LOSS_PCT:
                    should_close = True
                    reason = f"max_loss ({price_change * 100:.2f}%)"

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
            )

    async def run(self) -> None:
        """Main strategy loop."""
        self._running = True
        logger.info(
            "Trend Following strategy started | interval=%ds | paper=%s | coins=%s",
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
            if p.status == TrendPositionStatus.OPEN
        ]
        closed_positions = [
            p for p in self._positions.values()
            if p.status == TrendPositionStatus.CLOSED
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
                        "trailing_stop": p.trailing_stop,
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

    async def _fetch_candles(self, coin: str) -> Optional[pd.DataFrame]:
        """Fetch candle data for a coin."""
        try:
            info = self._get_info()
            # Fetch last 200 candles (startTime/endTime in ms)
            end_time = int(time.time() * 1000)
            # ~200 candles * interval; approximate 1h=3600000ms each
            interval_ms = self._interval_to_ms(self.config.CANDLE_INTERVAL)
            start_time = end_time - (200 * interval_ms)
            candle_data = await asyncio.to_thread(
                info.candles_snapshot,
                coin,
                self.config.CANDLE_INTERVAL,
                start_time,
                end_time,
            )

            if not candle_data:
                return None

            # HL returns keys: t, T, s, i, o, c, h, l, v, n
            df = pd.DataFrame(candle_data)
            rename_map = {
                "o": "open", "h": "high", "l": "low",
                "c": "close", "v": "volume",
            }
            df = df.rename(columns=rename_map)

            # Ensure required columns
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    return None
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["close"])
            return df

        except Exception as exc:
            logger.debug("Failed to fetch candles for %s: %s", coin, exc)
            return None

    def _analyze_trend(self, coin: str, df: pd.DataFrame) -> Optional[Dict]:
        """Analyze trend and generate signal if conditions met."""
        try:
            close = df["close"]
            volume = df["volume"]

            # Calculate EMAs
            fast_ema = close.ewm(span=self.config.FAST_EMA_PERIOD).mean()
            slow_ema = close.ewm(span=self.config.SLOW_EMA_PERIOD).mean()
            trend_ema = close.ewm(span=self.config.TREND_EMA_PERIOD).mean()

            # Calculate ATR
            atr = self._calculate_atr(df, self.config.ATR_PERIOD)
            if atr is None or atr.iloc[-1] <= 0:
                return None
            current_atr = atr.iloc[-1]

            # Calculate ADX
            adx = self._calculate_adx(df, self.config.ADX_PERIOD)
            if adx is None or adx.iloc[-1] < self.config.ADX_THRESHOLD:
                return None  # Trend too weak

            current_price = close.iloc[-1]
            current_fast = fast_ema.iloc[-1]
            current_slow = slow_ema.iloc[-1]
            current_trend = trend_ema.iloc[-1]
            prev_fast = fast_ema.iloc[-2]
            prev_slow = slow_ema.iloc[-2]

            side = None

            # --- LONG: fast EMA crosses above slow EMA, price above trend EMA ---
            if (current_fast > current_slow and
                prev_fast <= prev_slow and  # Crossover just happened
                current_price > current_trend):  # Higher timeframe trend up

                side = TrendPositionSide.LONG

            # --- SHORT: fast EMA crosses below slow EMA, price below trend EMA ---
            elif (current_fast < current_slow and
                  prev_fast >= prev_slow and  # Crossover just happened
                  current_price < current_trend):  # Higher timeframe trend down

                side = TrendPositionSide.SHORT

            if side is None:
                return None

            # Pullback filter: price should be close to fast EMA
            if self.config.REQUIRE_PULLBACK:
                distance = abs(current_price - current_fast) / current_atr
                if distance > self.config.PULLBACK_ATR_MULT:
                    return None  # Too far from EMA, not a good entry

            # Volume filter
            if self.config.VOLUME_FILTER:
                vol_ma = volume.rolling(20).mean()
                if volume.iloc[-1] < vol_ma.iloc[-1] * self.config.VOLUME_MULT:
                    return None  # Low volume, skip

            # Confidence score (0-100)
            adx_score = min(adx.iloc[-1] / 50.0, 1.0) * 40  # Up to 40 pts
            trend_score = 30 if (side == TrendPositionSide.LONG and current_price > current_trend) or \
                                (side == TrendPositionSide.SHORT and current_price < current_trend) else 0
            vol_score = min((volume.iloc[-1] / volume.rolling(20).mean().iloc[-1] - 1), 1.0) * 30  # Up to 30 pts
            confidence = round(adx_score + trend_score + vol_score, 1)

            return {
                "coin": coin,
                "side": side,
                "price": current_price,
                "atr": current_atr,
                "confidence": confidence,
                "fast_ema": current_fast,
                "slow_ema": current_slow,
                "trend_ema": current_trend,
                "adx": adx.iloc[-1],
            }

        except Exception as exc:
            logger.debug("Analysis failed for %s: %s", coin, exc)
            return None

    async def _check_trend_reversal(self, coin: str, original_side: TrendPositionSide) -> bool:
        """Check if the trend has reversed for an existing position."""
        try:
            candles = await self._fetch_candles(coin)
            if candles is None or len(candles) < self.config.SLOW_EMA_PERIOD:
                return False

            close = candles["close"]
            fast_ema = close.ewm(span=self.config.FAST_EMA_PERIOD).mean()
            slow_ema = close.ewm(span=self.config.SLOW_EMA_PERIOD).mean()

            current_fast = fast_ema.iloc[-1]
            current_slow = slow_ema.iloc[-1]

            if original_side == TrendPositionSide.LONG:
                return current_fast < current_slow  # Reversed to downtrend
            else:
                return current_fast > current_slow  # Reversed to uptrend

        except Exception:
            return False

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
    def _calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[pd.Series]:
        """Calculate Average Directional Index (ADX)."""
        try:
            high = df["high"]
            low = df["low"]
            close = df["close"]

            # True Range
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            # +DM and -DM
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low

            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

            # Smoothed averages
            atr = tr.rolling(window=period).mean()
            plus_di = 100 * pd.Series(plus_dm).rolling(window=period).mean() / atr
            minus_di = 100 * pd.Series(minus_dm).rolling(window=period).mean() / atr

            # DX and ADX
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = dx.rolling(window=period).mean()

            return adx
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
        return mapping.get(interval, 3_600_000)  # default 1h

    async def _get_available_capital(self) -> float:
        if self.config.PAPER_TRADING:
            return self._paper_capital
        try:
            balance = await self.api.get_balance()
            return float(balance.get("account_value", 0))
        except Exception:
            return 0.0
