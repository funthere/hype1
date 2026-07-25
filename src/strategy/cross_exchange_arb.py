"""
Cross-Exchange Funding Rate Arbitrage Strategy
HyperLiquid vs Binance Futures  —  BTC, ETH, SOL

Strategy:
  Monitor hourly funding rates on both exchanges.
  When the spread exceeds ENTRY_THRESHOLD:
    - If HL rate > Binance rate → SHORT HL, LONG Binance (collect HL funding, pay Binance)
    - If Binance rate > HL rate → LONG HL, SHORT Binance
  Close when spread narrows below EXIT_THRESHOLD.

Delta-neutral: Both legs are opened simultaneously so price risk cancels.
Risk management: max position size, max exposure per coin, max hold time,
                 emergency loss stop.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import yaml
from hyperliquid.info import Info

from ..core.base_config import BaseStrategyConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ArbSide(Enum):
    """Which exchange gets the SHORT leg when HL has higher rate."""

    SHORT_HL_LONG_BINANCE = "SHORT_HL_LONG_BINANCE"
    LONG_HL_SHORT_BINANCE = "LONG_HL_SHORT_BINANCE"


class PairStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class CrossExchangeArbConfig(BaseStrategyConfig):
    """Configuration loaded from ``cross_exchange_arb_config.yaml``."""

    # Override defaults
    LEVERAGE: int = 3
    PAPER_TRADING: bool = True
    DATABASE_PATH: str = "cross_exchange_arb.db"

    # --- Strategy thresholds (per-hour) ---
    ENTRY_THRESHOLD: float = 0.0001  # 0.01 %/hr
    EXIT_THRESHOLD: float = 0.00003  # 0.003 %/hr

    # --- Coins ---
    COINS: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])

    # --- Position sizing ---
    POSITION_SIZE_PCT: float = 0.10
    MAX_POSITION_SIZE_USD: float = 5000.0

    # --- Limits ---
    MAX_CONCURRENT_POSITIONS: int = 3
    MAX_HOLD_HOURS: float = 72.0
    MAX_EXPOSURE_PER_COIN_PCT: float = 0.25

    # --- Scan interval ---
    SCAN_INTERVAL: int = 60  # seconds

    # --- API URLs ---
    HL_BASE_URL: str = "https://api.hyperliquid.xyz"
    BINANCE_BASE_URL: str = "https://fapi.binance.com"
    BINANCE_MARKETS: Dict[str, str] = field(
        default_factory=lambda: {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            "SOL": "SOLUSDT",
        }
    )

    # --- Fees ---
    HL_MAKER_FEE: float = -0.0002
    HL_TAKER_FEE: float = 0.0005
    BINANCE_MAKER_FEE: float = -0.0002
    BINANCE_TAKER_FEE: float = 0.0004

    # --- Logging ---
    LOG_FILE: str = "cross_exchange_arb.log"

    # --- YAML loader ---
    _ENV_VAR_KEYS: ClassVar[Tuple[str, ...]] = (
        "USE_TESTNET",
        "PAPER_TRADING",
        "PRIVATE_KEY",
        "ADDRESS",
        "ACCOUNT_ADDRESS",
        "PAPER_CAPITAL",
        "LEVERAGE",
        "RISK_PER_TRADE_PCT",
        "POSITION_SIZE_PCT",
        "MAX_POSITIONS",
        "MAX_DAILY_TRADES",
        "MAX_DAILY_LOSS_PCT",
        "MAX_LOSS_PCT",
        "EMERGENCY_SHUTDOWN",
        "MAKER_FEE_PCT",
        "TAKER_FEE_PCT",
        "DATABASE_PATH",
    )

    @classmethod
    def from_yaml(cls, path: str) -> "CrossExchangeArbConfig":
        """Load config from a YAML file, falling back to defaults."""
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        # Mapping from YAML keys → dataclass fields
        key_map = {
            "mode": None,  # handled specially
            "paper_capital": "PAPER_CAPITAL",
            "coins": "COINS",
            "entry_threshold": "ENTRY_THRESHOLD",
            "exit_threshold": "EXIT_THRESHOLD",
            "position_size_pct": "POSITION_SIZE_PCT",
            "max_position_size_usd": "MAX_POSITION_SIZE_USD",
            "leverage": "LEVERAGE",
            "max_concurrent_positions": "MAX_CONCURRENT_POSITIONS",
            "max_hold_hours": "MAX_HOLD_HOURS",
            "max_loss_pct": "MAX_LOSS_PCT",
            "max_exposure_per_coin_pct": "MAX_EXPOSURE_PER_COIN_PCT",
            "scan_interval": "SCAN_INTERVAL",
            "binance_base_url": "BINANCE_BASE_URL",
            "binance_markets": "BINANCE_MARKETS",
            "hl_base_url": "HL_BASE_URL",
            "hl_maker_fee": "HL_MAKER_FEE",
            "hl_taker_fee": "HL_TAKER_FEE",
            "binance_maker_fee": "BINANCE_MAKER_FEE",
            "binance_taker_fee": "BINANCE_TAKER_FEE",
            "log_file": "LOG_FILE",
            "database_path": "DATABASE_PATH",
        }

        kwargs: Dict[str, Any] = {}
        for yaml_key, field_name in key_map.items():
            if yaml_key in raw and field_name is not None:
                kwargs[field_name] = raw[yaml_key]

        # Mode → PAPER_TRADING
        mode = raw.get("mode", "paper")
        kwargs["PAPER_TRADING"] = mode.lower() != "mainnet"

        config = cls(**kwargs)

        # Log level
        log_level = raw.get("log_level", "INFO")
        logging.getLogger().setLevel(log_level.upper())

        return config

    def validate(self) -> bool:
        super().validate()
        if self.ENTRY_THRESHOLD <= 0:
            raise ValueError("ENTRY_THRESHOLD must be > 0")
        if self.EXIT_THRESHOLD < 0:
            raise ValueError("EXIT_THRESHOLD must be >= 0")
        if not self.COINS:
            raise ValueError("COINS must not be empty")
        return True


# ---------------------------------------------------------------------------
# Position tracker (pair of legs)
# ---------------------------------------------------------------------------


@dataclass
class ArbPosition:
    """Tracks a matched pair (HL leg + dYdX leg)."""

    id: str
    coin: str
    arb_side: ArbSide

    # HL leg
    hl_side: str  # "SHORT" or "LONG"
    hl_quantity: float
    hl_notional: float
    hl_entry_price: float
    hl_funding_rate: float  # rate at entry (hourly)

    # dYdX leg
    dydx_side: str
    dydx_quantity: float
    dydx_notional: float
    dydx_entry_price: float
    dydx_funding_rate: float  # rate at entry (hourly)

    # Spread at entry
    entry_spread: float  # HL_rate - dYdX_rate (per hour)

    # Timing
    entry_time: float
    last_funding_time: float

    # PnL tracking
    total_funding_collected: float = 0.0
    fees_paid: float = 0.0
    status: PairStatus = PairStatus.OPEN
    close_reason: str = ""
    close_time: Optional[float] = None
    hl_close_price: Optional[float] = None
    dydx_close_price: Optional[float] = None
    realized_pnl: float = 0.0


# ---------------------------------------------------------------------------
# Strategy engine
# ---------------------------------------------------------------------------


class CrossExchangeArbStrategy:
    """Cross-exchange funding-rate arbitrage engine.

    Monitors funding rates on HyperLiquid and dYdX v4, opens
    delta-neutral pairs when the spread exceeds thresholds.
    """

    def __init__(
        self,
        config: CrossExchangeArbConfig,
        hl_info: Info,
        db: Any,
    ) -> None:
        self.config = config
        self.hl_info = hl_info
        self.db = db

        self._positions: Dict[str, ArbPosition] = {}
        self._running: bool = False
        self._cycle_count: int = 0
        self._paper_capital: float = config.PAPER_CAPITAL

        # Reusable caches
        self._hl_funding_cache: Dict[str, float] = {}
        self._hl_price_cache: Dict[str, float] = {}

        # Binance client is set externally (to allow test injection)
        self._binance_client: Any = None

    # ------------------------------------------------------------------
    # Dependency setter
    # ------------------------------------------------------------------

    def set_binance_client(self, client: Any) -> None:
        """Set the Binance API client (BinanceClient)."""
        self._binance_client = client

    # ------------------------------------------------------------------
    # Public API – data fetching
    # ------------------------------------------------------------------

    async def fetch_hl_funding_rates(self) -> Dict[str, Dict[str, Any]]:
        """Fetch funding rates from HyperLiquid for configured coins.

        Returns dict: ``{COIN: {rate_hourly, mark_px}}``
        HL funding rate is per-8h; we convert to per-hour.
        """
        try:
            raw: tuple = await asyncio.to_thread(self.hl_info.meta_and_asset_ctxs)
            if not raw or len(raw) < 2:
                return {}

            meta, ctxs = raw[0], raw[1]
            universe = meta.get("universe", [])
            coin_set = {c.upper() for c in self.config.COINS}

            out: Dict[str, Dict[str, Any]] = {}
            for idx, ctx in enumerate(ctxs):
                if idx >= len(universe):
                    break
                coin = universe[idx].get("name", "")
                if coin.upper() not in coin_set:
                    continue

                funding_str = ctx.get("funding", "0")
                mark_px_str = ctx.get("markPx", "0")

                try:
                    rate_8h = float(funding_str) if funding_str else 0.0
                    mark_px = float(mark_px_str) if mark_px_str else 0.0
                except (ValueError, TypeError):
                    continue

                if mark_px <= 0:
                    continue

                rate_hourly = rate_8h / 8.0
                out[coin] = {"rate_hourly": rate_hourly, "mark_px": mark_px}

            self._hl_funding_cache = {c: v["rate_hourly"] for c, v in out.items()}
            self._hl_price_cache = {c: v["mark_px"] for c, v in out.items()}
            return out

        except Exception as exc:
            logger.error("Failed to fetch HL funding rates: %s", exc)
            return {}

    async def fetch_binance_funding_rates(self) -> Dict[str, Dict[str, Any]]:
        """Fetch funding rates from Binance Futures.

        Returns dict: ``{COIN: {rate_hourly, mark_px}}``
        Binance rate is per 8h; we convert to per-hour (done in client).
        """
        if self._binance_client is None:
            logger.warning("Binance client not set – returning empty rates")
            return {}

        results = await self._binance_client.get_all_funding_rates(self.config.COINS)

        out: Dict[str, Dict[str, Any]] = {}
        for coin, data in results.items():
            out[coin] = {
                "rate_hourly": data["rate_hourly"],
                "mark_px": data["oracle_px"],
            }

        return out

    # ------------------------------------------------------------------
    # Public API – position management
    # ------------------------------------------------------------------

    async def open_position(
        self,
        coin: str,
        arb_side: ArbSide,
        hl_rate: float,
        dydx_rate: float,
        hl_price: float,
        dydx_price: float,
    ) -> Optional[str]:
        """Open a delta-neutral pair on both exchanges.

        Returns position_id on success, None on failure.
        """
        try:
            # --- Check limits ---
            open_count = sum(
                1 for p in self._positions.values() if p.status == PairStatus.OPEN
            )
            if open_count >= self.config.MAX_CONCURRENT_POSITIONS:
                logger.info(
                    "Max concurrent positions (%d) reached – skipping %s",
                    self.config.MAX_CONCURRENT_POSITIONS,
                    coin,
                )
                return None

            # No duplicate open for same coin
            for p in self._positions.values():
                if p.coin == coin and p.status == PairStatus.OPEN:
                    logger.debug("Already have open pair for %s", coin)
                    return None

            # Exposure check
            coin_exposure = sum(
                p.hl_notional
                for p in self._positions.values()
                if p.coin == coin and p.status == PairStatus.OPEN
            )
            capital = await self._get_available_capital()
            max_coin_notional = capital * self.config.MAX_EXPOSURE_PER_COIN_PCT
            if coin_exposure >= max_coin_notional:
                logger.info("Max exposure for %s reached – skipping", coin)
                return None

            # --- Position sizing ---
            notional = capital * self.config.POSITION_SIZE_PCT
            notional = min(notional, self.config.MAX_POSITION_SIZE_USD)
            if notional <= 0 or hl_price <= 0 or dydx_price <= 0:
                return None

            hl_qty = round(notional / hl_price, 6)
            dydx_qty = round(notional / dydx_price, 6)
            if hl_qty <= 0 or dydx_qty <= 0:
                return None

            position_id = str(uuid.uuid4())[:8]

            spread = hl_rate - dydx_rate

            if arb_side == ArbSide.SHORT_HL_LONG_BINANCE:
                hl_side = "SHORT"
                dydx_side = "LONG"
            else:
                hl_side = "LONG"
                dydx_side = "SHORT"

            if self.config.PAPER_TRADING:
                # Paper: simulate fees
                hl_fee = notional * self.config.HL_TAKER_FEE
                dydx_fee = notional * self.config.BINANCE_TAKER_FEE
                total_fees = abs(hl_fee) + abs(dydx_fee)
                self._paper_capital -= total_fees

                logger.info(
                    "[PAPER] OPEN %s %s | %s HL:%s dYdX:%s | "
                    "hl_qty=%.6f dydx_qty=%.6f | hl_px=%.2f dydx_px=%.2f | "
                    "spread=%.6f fees=%.4f",
                    position_id,
                    coin,
                    arb_side.value,
                    hl_side,
                    dydx_side,
                    hl_qty,
                    dydx_qty,
                    hl_price,
                    dydx_price,
                    spread,
                    total_fees,
                )
            else:
                # Live: place HL order via HyperliquidAPI
                # (dYdX order placement requires Cosmos SDK signing – not implemented)
                logger.warning(
                    "LIVE mode: only HL leg will be placed; dYdX leg is paper-tracked"
                )
                # We would call self.hl_api.place_order(...) here

            pos = ArbPosition(
                id=position_id,
                coin=coin,
                arb_side=arb_side,
                hl_side=hl_side,
                hl_quantity=hl_qty,
                hl_notional=notional,
                hl_entry_price=hl_price,
                hl_funding_rate=hl_rate,
                dydx_side=dydx_side,
                dydx_quantity=dydx_qty,
                dydx_notional=notional,
                dydx_entry_price=dydx_price,
                dydx_funding_rate=dydx_rate,
                entry_spread=spread,
                entry_time=time.time(),
                last_funding_time=time.time(),
                fees_paid=abs(self.config.HL_TAKER_FEE * notional)
                + abs(self.config.BINANCE_TAKER_FEE * notional)
                if self.config.PAPER_TRADING
                else 0.0,
            )

            self._positions[position_id] = pos

            if self.db:
                self.db.log_event(
                    event_type="cross_arb_open",
                    message=(
                        f"Opened {arb_side.value} {coin} | "
                        f"HL {hl_side} dYdX {dydx_side} | "
                        f"spread={spread:.6f}"
                    ),
                    event_data={
                        "position_id": position_id,
                        "coin": coin,
                        "arb_side": arb_side.value,
                        "hl_side": hl_side,
                        "dydx_side": dydx_side,
                        "hl_quantity": hl_qty,
                        "dydx_quantity": dydx_qty,
                        "hl_entry_price": hl_price,
                        "dydx_entry_price": dydx_price,
                        "hl_notional": notional,
                        "dydx_notional": notional,
                        "hl_rate": hl_rate,
                        "dydx_rate": dydx_rate,
                        "spread": spread,
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
        hl_price: Optional[float] = None,
        dydx_price: Optional[float] = None,
    ) -> bool:
        """Close a cross-exchange arb pair."""
        try:
            pos = self._positions.get(position_id)
            if pos is None or pos.status == PairStatus.CLOSED:
                return False

            if hl_price is None:
                hl_price = self._hl_price_cache.get(pos.coin, pos.hl_entry_price)
            if dydx_price is None:
                # Use HL price as proxy for dYdX (they track closely for majors)
                dydx_price = hl_price

            # --- Calculate PnL for each leg ---
            if pos.hl_side == "SHORT":
                hl_pnl = (pos.hl_entry_price - hl_price) * pos.hl_quantity
            else:
                hl_pnl = (hl_price - pos.hl_entry_price) * pos.hl_quantity

            if pos.dydx_side == "SHORT":
                dydx_pnl = (pos.dydx_entry_price - dydx_price) * pos.dydx_quantity
            else:
                dydx_pnl = (dydx_price - pos.dydx_entry_price) * pos.dydx_quantity

            # Close fees
            close_fees = abs(pos.hl_notional * self.config.HL_TAKER_FEE) + abs(
                pos.dydx_notional * self.config.BINANCE_TAKER_FEE
            )

            # Net PnL = HL leg + dYdX leg + funding collected - close fees
            realized_pnl = hl_pnl + dydx_pnl + pos.total_funding_collected - close_fees

            if self.config.PAPER_TRADING:
                self._paper_capital += hl_pnl + dydx_pnl - close_fees
                logger.info(
                    "[PAPER] CLOSE %s %s | reason=%s | "
                    "hl_pnl=%.4f dydx_pnl=%.4f funding=%.4f fees=%.4f net=%.4f",
                    position_id,
                    pos.coin,
                    reason,
                    hl_pnl,
                    dydx_pnl,
                    pos.total_funding_collected,
                    close_fees + pos.fees_paid,
                    realized_pnl,
                )

            pos.status = PairStatus.CLOSED
            pos.close_reason = reason
            pos.close_time = time.time()
            pos.hl_close_price = hl_price
            pos.dydx_close_price = dydx_price
            pos.realized_pnl = realized_pnl

            if self.db:
                self.db.log_event(
                    event_type="cross_arb_close",
                    message=(
                        f"Closed {pos.arb_side.value} {pos.coin} | "
                        f"reason={reason} pnl={realized_pnl:.4f} "
                        f"funding={pos.total_funding_collected:.4f}"
                    ),
                    event_data={
                        "position_id": position_id,
                        "coin": pos.coin,
                        "hl_close_price": hl_price,
                        "dydx_close_price": dydx_price,
                        "hl_pnl": hl_pnl,
                        "dydx_pnl": dydx_pnl,
                        "funding_collected": pos.total_funding_collected,
                        "close_fees": close_fees,
                        "realized_pnl": realized_pnl,
                        "reason": reason,
                        "hold_hours": (pos.close_time - pos.entry_time) / 3600,
                    },
                )

            return True

        except Exception as exc:
            logger.error("Failed to close position %s: %s", position_id, exc)
            return False

    # ------------------------------------------------------------------
    # Cycle management
    # ------------------------------------------------------------------

    async def run_cycle(self) -> None:
        """One complete scan + manage cycle."""
        self._cycle_count += 1
        logger.info("--- Cross-Exchange Arb Cycle #%d ---", self._cycle_count)

        # 1. Manage existing positions
        await self._check_existing_positions()

        # 2. Fetch rates from both exchanges
        hl_rates = await self.fetch_hl_funding_rates()
        dydx_rates = await self.fetch_binance_funding_rates()

        if not hl_rates or not dydx_rates:
            logger.warning(
                "Missing rates – HL: %d coins, dYdX: %d coins",
                len(hl_rates),
                len(dydx_rates),
            )
            return

        # 3. Log current rate comparison
        for coin in self.config.COINS:
            hl = hl_rates.get(coin)
            dydx = dydx_rates.get(coin)
            if hl and dydx:
                spread = hl["rate_hourly"] - dydx["rate_hourly"]
                logger.info(
                    "  %s  HL=%.6f%%/hr  dYdX=%.6f%%/hr  spread=%.6f%%/hr  "
                    "hl_px=%.2f  dydx_px=%.2f",
                    coin,
                    hl["rate_hourly"] * 100,
                    dydx["rate_hourly"] * 100,
                    spread * 100,
                    hl["mark_px"],
                    dydx["mark_px"],
                )

        # 4. Check for entry signals
        for coin in self.config.COINS:
            hl = hl_rates.get(coin)
            dydx = dydx_rates.get(coin)
            if not hl or not dydx:
                continue

            spread = hl["rate_hourly"] - dydx["rate_hourly"]

            if abs(spread) >= self.config.ENTRY_THRESHOLD - 1e-9:
                if spread > 0:
                    # HL rate > dYdX → SHORT HL, LONG dYdX
                    await self.open_position(
                        coin,
                        ArbSide.SHORT_HL_LONG_BINANCE,
                        hl["rate_hourly"],
                        dydx["rate_hourly"],
                        hl["mark_px"],
                        dydx["mark_px"],
                    )
                else:
                    # dYdX rate > HL → LONG HL, SHORT dYdX
                    await self.open_position(
                        coin,
                        ArbSide.LONG_HL_SHORT_BINANCE,
                        hl["rate_hourly"],
                        dydx["rate_hourly"],
                        hl["mark_px"],
                        dydx["mark_px"],
                    )

    async def _check_existing_positions(self) -> None:
        """Evaluate open pairs for exit conditions."""
        open_positions = [
            p for p in self._positions.values() if p.status == PairStatus.OPEN
        ]
        if not open_positions:
            return

        hl_rates = await self.fetch_hl_funding_rates()
        dydx_rates = await self.fetch_binance_funding_rates()

        now = time.time()

        for pos in open_positions:
            hl = hl_rates.get(pos.coin, {})
            dydx = dydx_rates.get(pos.coin, {})

            hl_rate = hl.get("rate_hourly", 0.0)
            dydx_rate = dydx.get("rate_hourly", 0.0)
            hl_price = hl.get("mark_px", pos.hl_entry_price)
            dydx_price = dydx.get("mark_px", pos.dydx_entry_price)

            hold_hours = (now - pos.entry_time) / 3600
            current_spread = hl_rate - dydx_rate

            # --- Accumulate funding ---
            await self._accumulate_funding(pos, hl_rate, dydx_rate, now)

            # --- Exit checks ---
            should_close = False
            reason = ""

            # 1. Spread narrowed below exit threshold
            if abs(current_spread) < self.config.EXIT_THRESHOLD:
                should_close = True
                reason = f"spread_narrowed ({current_spread:.6f})"

            # 2. Spread reversed (sign changed)
            original_sign = 1 if pos.entry_spread > 0 else -1
            current_sign = 1 if current_spread > 0 else -1
            if original_sign != current_sign:
                should_close = True
                reason = f"spread_reversed ({current_spread:.6f})"

            # 3. Max hold time
            if hold_hours > self.config.MAX_HOLD_HOURS:
                should_close = True
                reason = f"max_hold ({hold_hours:.1f}h > {self.config.MAX_HOLD_HOURS}h)"

            # 4. Emergency loss stop (based on price divergence)
            if hl_price > 0 and pos.hl_entry_price > 0:
                price_divergence = (
                    abs(hl_price - pos.hl_entry_price) / pos.hl_entry_price
                )
                if price_divergence > self.config.MAX_LOSS_PCT:
                    should_close = True
                    reason = (
                        f"max_loss (divergence {price_divergence * 100:.2f}% > "
                        f"{self.config.MAX_LOSS_PCT * 100:.0f}%)"
                    )

            if should_close:
                logger.info("Closing %s %s: %s", pos.id, pos.coin, reason)
                await self.close_position(pos.id, reason, hl_price, dydx_price)

    async def _accumulate_funding(
        self,
        pos: ArbPosition,
        hl_rate: float,
        dydx_rate: float,
        now: float,
    ) -> None:
        """Estimate pro-rata funding accumulated since last check.

        SHORT on HL with positive rate → we receive funding from HL.
        LONG on dYdX with positive rate → we pay funding on dYdX.
        Net = HL received - dYdX paid.
        """
        elapsed_hours = (now - pos.last_funding_time) / 3600.0
        if elapsed_hours < 0.01:
            return

        # HL leg funding
        if pos.hl_side == "SHORT":
            hl_funding = pos.hl_notional * hl_rate * elapsed_hours
        else:
            hl_funding = pos.hl_notional * (-hl_rate) * elapsed_hours

        # dYdX leg funding
        if pos.dydx_side == "SHORT":
            dydx_funding = pos.dydx_notional * dydx_rate * elapsed_hours
        else:
            dydx_funding = pos.dydx_notional * (-dydx_rate) * elapsed_hours

        # Net: what we receive (positive = good for us)
        net_funding = hl_funding - dydx_funding
        pos.total_funding_collected += net_funding
        pos.last_funding_time = now

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main strategy loop."""
        self._running = True
        logger.info(
            "Cross-Exchange Arb started | interval=%ds | paper=%s | coins=%s",
            self.config.SCAN_INTERVAL,
            self.config.PAPER_TRADING,
            ", ".join(self.config.COINS),
        )

        while self._running:
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Cycle error: %s", exc)

            try:
                await asyncio.wait_for(
                    asyncio.sleep(self.config.SCAN_INTERVAL),
                    timeout=self.config.SCAN_INTERVAL + 1,
                )
            except asyncio.CancelledError:
                logger.info("Strategy loop cancelled")
                break

        logger.info("Strategy stopped after %d cycles", self._cycle_count)

    def stop(self) -> None:
        """Signal the strategy loop to stop."""
        self._running = False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return current strategy state."""
        open_positions = [
            p for p in self._positions.values() if p.status == PairStatus.OPEN
        ]
        closed_positions = [
            p for p in self._positions.values() if p.status == PairStatus.CLOSED
        ]

        total_pnl = sum(p.realized_pnl for p in closed_positions)
        total_funding = sum(p.total_funding_collected for p in self._positions.values())
        total_fees = sum(p.fees_paid for p in closed_positions)

        capital = self._paper_capital if self.config.PAPER_TRADING else None

        return {
            "cycle": self._cycle_count,
            "paper_trading": self.config.PAPER_TRADING,
            "capital": capital,
            "positions": {
                "open": [
                    {
                        "id": p.id,
                        "coin": p.coin,
                        "arb_side": p.arb_side.value,
                        "hl_side": p.hl_side,
                        "dydx_side": p.dydx_side,
                        "hl_quantity": p.hl_quantity,
                        "dydx_quantity": p.dydx_quantity,
                        "hl_entry_price": p.hl_entry_price,
                        "dydx_entry_price": p.dydx_entry_price,
                        "entry_spread": p.entry_spread,
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
                "total_fees": round(total_fees, 4),
                "open_count": len(open_positions),
                "closed_count": len(closed_positions),
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_available_capital(self) -> float:
        if self.config.PAPER_TRADING:
            return self._paper_capital
        # Live: would query exchange balance
        return self._paper_capital
