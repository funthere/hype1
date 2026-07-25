"""
Main Trading Bot - Orchestrates all components
"""

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from uuid import uuid4

from ..core.config import BotConfig, Side, Position, Trade, OrderStatus
from ..core.strategy import StrategyEngine, RiskManager
from ..core.survival_risk import SurvivalRiskManager
from ..exchange.connector import HyperliquidAPI
from ..exchange.market_data import MarketDataFeed
from ..execution import PositionRead
from ..storage.database import DatabaseManager
from ..notifications.telegram import TelegramNotifier
from ..analytics.adaptive import AdaptiveParameterManager
from ..analytics.health import HealthMonitor
from ..analytics.performance import PerformanceAnalyzer

logger = logging.getLogger(__name__)


class TradingBot:
    """
    Main trading bot that orchestrates all components.

    Features:
    - Modular architecture
    - SQLite persistence
    - Telegram notifications
    - Circuit breaker protection
    - Risk management
    - Paper trading support
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.config.validate()

        # Initialize components
        self.api = HyperliquidAPI(config)
        self.market_data = MarketDataFeed(config)
        self.strategy = StrategyEngine(config)
        self.risk_manager = RiskManager(config)
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.telegram = TelegramNotifier.from_config(config)

        # State
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.daily_trades_list: List[Trade] = []  # Today's trades only
        self.last_trade_date = None
        self.emergency_stop = False
        self.force_close_all = False
        self._is_paused = False
        self.is_running = False

        # Statistics
        self.start_time: Optional[datetime] = None
        self.starting_capital = (
            config.PAPER_CAPITAL if config.PAPER_TRADING else 10000.0
        )
        self.current_capital = self.starting_capital
        self.peak_equity = self.starting_capital
        self.max_drawdown_pct = 0.0

        # Signal cooldown
        self._last_signal_time: Optional[datetime] = None
        self._signal_cooldown_seconds: int = 900  # 15 min (one 15m candle)

        # Advanced modules (Phase 2 integration)
        self.survival_risk = SurvivalRiskManager(config)
        self.adaptive_params = AdaptiveParameterManager(config)
        self.health_monitor = HealthMonitor()
        self.performance_analyzer = PerformanceAnalyzer(self.starting_capital)

        # Cached market data
        self._cached_mids: Dict[str, float] = {}
        self._mids_last_update: Optional[datetime] = None

        # Position reconciliation (live mode)
        self._last_reconciliation: Optional[datetime] = None
        self._reconciliation_interval = timedelta(minutes=2)

        # Circuit breaker
        self.consecutive_losses = 0
        self.circuit_breaker_triggered = False
        self.circuit_breaker_until: Optional[datetime] = None

        # API server (optional)
        self.api_server = None

        # Setup signal handlers
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for external control and graceful shutdown"""
        try:
            signal.signal(signal.SIGUSR1, self._handle_force_close_signal)
            logger.info("Signal handler: SIGUSR1 = force close all positions")
        except Exception as e:
            logger.warning(f"Could not setup SIGUSR1 handler: {e}")

        try:
            signal.signal(signal.SIGUSR2, self._handle_reset_circuit_breaker_signal)
            logger.info("Signal handler: SIGUSR2 = reset circuit breaker")
        except Exception as e:
            logger.warning(f"Could not setup SIGUSR2 handler: {e}")

        # Graceful shutdown on SIGTERM / SIGINT
        try:
            signal.signal(signal.SIGTERM, self._handle_graceful_shutdown)
            logger.info("Signal handler: SIGTERM = graceful shutdown")
        except Exception as e:
            logger.warning(f"Could not setup SIGTERM handler: {e}")

        try:
            signal.signal(signal.SIGINT, self._handle_graceful_shutdown)
            logger.info("Signal handler: SIGINT = graceful shutdown")
        except Exception as e:
            logger.warning(f"Could not setup SIGINT handler: {e}")

    def _handle_graceful_shutdown(self, signum, frame):
        """Handle SIGTERM/SIGINT — set flag for graceful shutdown in main loop."""
        sig_name = signal.Signals(signum).name
        logger.info(f"🛑 {sig_name} received — initiating graceful shutdown...")
        self.emergency_stop = True

    def _handle_force_close_signal(self, signum, frame):
        """Handle SIGUSR1 - force close all positions"""
        logger.info("⚠️  SIGUSR1 received - forcing all positions to close...")
        self.force_close_all = True

    def _handle_reset_circuit_breaker_signal(self, signum, frame):
        """Handle SIGUSR2 - reset circuit breaker"""
        if self.circuit_breaker_triggered:
            logger.info("✅ SIGUSR2 received - circuit breaker reset")
            self.circuit_breaker_triggered = False
            self.circuit_breaker_until = None
            self.consecutive_losses = 0
        else:
            logger.info("SIGUSR2 received - circuit breaker not active")

    # Public control methods

    def pause_trading(self) -> None:
        """Pause trading (positions still managed)"""
        self._is_paused = True
        logger.info("Trading paused")

    def resume_trading(self) -> None:
        """Resume trading"""
        self._is_paused = False
        logger.info("Trading resumed")

    def reset_circuit_breaker(self) -> None:
        """Manually reset circuit breaker"""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_until = None
        self.consecutive_losses = 0
        logger.info("Circuit breaker manually reset")

    def update_config_param(self, name: str, value):
        """Update a configuration parameter"""
        if hasattr(self.config, name):
            setattr(self.config, name, value)
            logger.info(f"Config updated: {name} = {value}")
        else:
            logger.warning(f"Unknown config parameter: {name}")

    # Main execution

    async def start(self):
        """Start the trading bot"""
        self.is_running = True
        self.start_time = datetime.now()

        logger.info("=" * 60)
        logger.info("HYPE TRADING BOT STARTING")
        logger.info("=" * 60)

        # Restore previous state from database
        self._restore_state()

        # Send startup notification
        if self.telegram:
            await self.telegram.notify_start(self.config)

        # Log bot info
        self._log_bot_info()

        # Check emergency shutdown
        if self.config.EMERGENCY_SHUTDOWN:
            logger.error("EMERGENCY SHUTDOWN ENABLED - NOT TRADING")
            return

        # Start API server if enabled
        if self.config.WEB_UI_ENABLED:
            self._start_api_server()

        # Register candle callback
        self.market_data.on_candle_update(self._on_candle_update)

        # Check connection
        if not await self.api.check_connection():
            logger.error("Failed to connect to exchange")
            await self._shutdown("Connection failed")
            return

        # Live recovery must establish exchange state before strategy management.
        if not self.config.PAPER_TRADING:
            recovered = await self._reconcile_positions()
            if not recovered:
                logger.error(
                    "No authoritative position snapshot; refusing to start live loop"
                )
                await self._shutdown("Initial reconciliation unavailable")
                return

        # Recovered local state is only a candidate. A live bot must establish
        # authoritative exchange state before it evaluates signals or exits.
        if not self.config.PAPER_TRADING:
            if not await self._reconcile_positions():
                logger.error(
                    "Initial reconciliation unavailable; refusing live startup"
                )
                await self._shutdown("Initial reconciliation unavailable")
                return

        # Get initial price
        mids = await self.api.get_mids()
        logger.info(
            f"Current {self.config.ASSET} mid: ${mids.get(self.config.ASSET, 'N/A')}"
        )

        # Start market data feed
        asyncio.create_task(self.market_data.connect())

        # Main loop
        await self._main_loop()

    async def _main_loop(self):
        """Main trading loop"""
        logger.info("Starting main trading loop...")
        _last_persist = datetime.now()
        _persist_interval = timedelta(minutes=5)

        while not self.emergency_stop:
            try:
                # Check for force close signal
                if self.force_close_all or os.path.exists(".force_close_positions"):
                    if os.path.exists(".force_close_positions"):
                        os.remove(".force_close_positions")
                    await self.force_close_all_positions("FORCE_CLOSE")
                    self.force_close_all = False

                # Check for circuit breaker manual reset
                if os.path.exists(".reset_circuit_breaker"):
                    os.remove(".reset_circuit_breaker")
                    self.reset_circuit_breaker()

                # Check daily reset
                await self._check_daily_reset()

                # Check circuit breaker cooldown
                await self._check_circuit_breaker_cooldown()

                # Periodic position reconciliation with exchange (live mode only)
                await self._maybe_reconcile_positions()

                # Check exits on managed, confirmed positions only.
                await self._check_position_exits()

                # Process new signals (only if not paused)
                if not self._is_paused and self.market_data.current_candle:
                    signal = self.strategy.generate_signal(self.current_capital)
                    if signal:
                        await self._process_signal(signal)

                # Periodic state persistence (every 5 minutes)
                now = datetime.now()
                if now - _last_persist >= _persist_interval:
                    self._persist_state()
                    _last_persist = now

                # Sleep before next iteration
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                if self.telegram:
                    await self.telegram.notify_error(str(e), "main_loop")
                await asyncio.sleep(5)

        # Main loop exited — perform graceful shutdown
        await self._shutdown("Main loop exited")

    async def _on_candle_update(self, candle: Dict):
        """Handle candle update from WebSocket"""
        self.strategy.update_candle(candle)
        # Update adaptive parameters with new price
        current_price = float(candle.get("close", 0))
        if current_price > 0:
            self.adaptive_params.update_market_data(current_price)
            self._cached_mids[self.config.ASSET] = current_price
            self._mids_last_update = datetime.now()
        await self._update_unrealized_pnl()

    # Trading logic

    async def _process_signal(self, signal: Dict):
        """Process trading signal"""
        # Signal cooldown - prevent duplicate signals
        if self._last_signal_time:
            elapsed = (datetime.now() - self._last_signal_time).total_seconds()
            if elapsed < self._signal_cooldown_seconds:
                return

        # Check if we can take this trade
        can_open, reason = self.risk_manager.can_open_position(
            open_positions=len(self.positions),
            daily_trades=self.daily_trades,
            daily_pnl=self.daily_pnl,
            circuit_breaker_active=self.circuit_breaker_triggered,
        )

        if not can_open:
            logger.info(f"Skipping signal: {reason}")
            return

        # Check confidence threshold
        min_conf = (
            self.config.CONFIDENCE_THRESHOLD
            if signal["action"] == Side.LONG
            else 100 - self.config.CONFIDENCE_THRESHOLD
        )
        if signal["confidence"] < min_conf:
            return

        # Survival risk check (Phase 2)
        if not self.config.PAPER_TRADING or True:  # Always check
            mids = await self.api.get_mids()
            current_price = float(mids.get(self.config.ASSET, 0))
            if current_price > 0:
                test_position = Position(
                    side=signal["action"],
                    entry_price=signal["entry_price"],
                    quantity=signal["quantity"],
                    tp_price=signal["tp_price"],
                    sl_price=signal["sl_price"],
                    entry_time=datetime.now(),
                    leverage=self.config.LEVERAGE,
                )
                can_open_survival, survival_reason = (
                    self.survival_risk.can_open_position(
                        position=test_position,
                        existing_positions=self.positions,
                        capital=self.current_capital,
                        current_price=current_price,
                        daily_pnl=self.daily_pnl,
                        consecutive_losses=self.consecutive_losses,
                    )
                )
                if not can_open_survival:
                    logger.info(f"Survival risk blocked: {survival_reason}")
                    return

        # Place entry order
        await self._place_entry_order(signal)

        self._last_signal_time = datetime.now()

    async def _place_entry_order(self, signal: Dict):
        """Create local exposure only after a paper fill or confirmed live fill.

        A live post-only acknowledgement remains ``PENDING_ENTRY`` in SQLite
        and is deliberately excluded from strategy management until the next
        authoritative exchange reconciliation verifies a filled position.
        """
        side = signal["action"]
        entry_price = signal["entry_price"]
        quantity = signal["quantity"]
        logger.info("Placing %s entry order @ $%.4f", side.value, entry_price)

        position = Position(
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            tp_price=signal["tp_price"],
            sl_price=signal["sl_price"],
            entry_time=datetime.now(),
            leverage=self.config.LEVERAGE,
            asset=self.config.ASSET,
            execution_state="pending_entry",
            confirmed_quantity=0.0,
            remaining_quantity=0.0,
        )

        if not self.config.PAPER_TRADING:
            position.cloid = f"entry-{position.id[:20]}"
            self.db.save_position(position)
            result = await self.api.place_order(
                side=side,
                price=entry_price,
                quantity=quantity,
                cloid=position.cloid,
                order_type="post_only",
                coin=position.asset,
            )
            if result.get("status") != "ok":
                position.execution_state = (
                    "state_unknown"
                    if result.get("status") == "unknown"
                    else "entry_rejected"
                )
                self.db.save_position(position)
                logger.error("Entry order was not accepted: %s", result.get("msg"))
                return

            position.oid = result.get("response", {}).get("oid")
            # Keep the acknowledged entry in memory so reconciliation can turn
            # it into managed exposure only after an authoritative fill.
            self.positions.append(position)
            self.db.save_position(position)
            self.db.log_event(
                "entry_submitted",
                f"{side.value} entry acknowledged; awaiting exchange fill",
                {"position_id": position.id, "cloid": position.cloid},
            )
            return

        position.execution_state = "open"
        position.confirmed_quantity = quantity
        position.remaining_quantity = quantity
        self.positions.append(position)
        self.db.save_position(position)
        self.db.log_event("trade_entry", f"{side.value} paper entry", signal)
        if self.telegram:
            await self.telegram.notify_trade_entry(signal)

    async def _check_position_exits(self):
        """Check if any positions should be closed"""
        if not self.positions:
            return

        # Use cached mids from WebSocket if recent (< 30s), otherwise fetch
        if (
            self._mids_last_update
            and (datetime.now() - self._mids_last_update).total_seconds() < 30
            and self.config.ASSET in self._cached_mids
        ):
            current_price = self._cached_mids[self.config.ASSET]
        else:
            mids = await self.api.get_mids()
            current_price = float(mids.get(self.config.ASSET, 0))

        if current_price == 0:
            return

        positions_to_close = []

        for position in self.positions:
            if position.execution_state != "open":
                continue
            should_close = False
            exit_reason = ""

            # Check take profit
            if position.side == Side.LONG:
                if current_price >= position.tp_price:
                    should_close = True
                    exit_reason = "TP"
                elif current_price <= position.sl_price:
                    should_close = True
                    exit_reason = "SL"
            else:  # SHORT
                if current_price <= position.tp_price:
                    should_close = True
                    exit_reason = "TP"
                elif current_price >= position.sl_price:
                    should_close = True
                    exit_reason = "SL"

            if should_close:
                positions_to_close.append((position, exit_reason))

        # Close positions
        for position, reason in positions_to_close:
            await self._close_position(position, current_price, reason)

    # ------------------------------------------------------------------
    # Position reconciliation (sync local state with exchange)
    # ------------------------------------------------------------------

    async def _maybe_reconcile_positions(self):
        """Periodically reconcile local positions with exchange (live mode only)."""
        if self.config.PAPER_TRADING:
            return True

        now = datetime.now()
        if (
            self._last_reconciliation is not None
            and now - self._last_reconciliation < self._reconciliation_interval
        ):
            return not self._is_paused

        self._last_reconciliation = now
        return await self._reconcile_positions()

    async def _reconcile_positions(self):
        """Synchronize managed exposure only from a successful exchange snapshot."""
        try:
            snapshot = await self.api.get_positions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            snapshot = PositionRead.unavailable(str(exc))
        if not isinstance(snapshot, PositionRead):
            snapshot = PositionRead.success(list(snapshot))
        if not snapshot.available:
            logger.error("Position reconciliation unavailable: %s", snapshot.error)
            self._is_paused = True
            self.db.log_event(
                "reconciliation_unavailable", snapshot.error or "unknown", {}
            )
            if self.telegram:
                await self.telegram.notify_error(
                    snapshot.error or "unknown", "reconciliation"
                )
            return False

        asset = self.config.ASSET
        exchange_map: Dict[str, Dict] = {}
        for exchange_position in snapshot:
            if exchange_position.get("coin") == asset:
                exchange_map[exchange_position.get("direction", "")] = exchange_position

        matched_directions: List[str] = []
        for local_position in list(self.positions):
            direction = "Long" if local_position.side == Side.LONG else "Short"
            exchange_position = exchange_map.get(direction)
            local_position.last_exchange_observation = snapshot.observed_at
            if exchange_position is None:
                if local_position.execution_state == "pending_entry":
                    # A resting post-only entry is not exposure. Retain its
                    # durable intent until order cancellation/fill resolution;
                    # do not manufacture an external-close event.
                    self.db.save_position(local_position)
                    continue
                if local_position.execution_state == "exit_requested":
                    # Resolve the known exit intent from fills before deciding
                    # whether the account is flat. This path never resubmits.
                    await self._confirm_live_exit(local_position)
                    continue
                # A confirmed flat exchange account is not permission to submit
                # another exit. The exact external fill is unknown, so preserve
                # an auditable unresolved state and halt entry activity.
                local_position.execution_state = "externally_closed"
                local_position.remaining_quantity = 0.0
                self.db.save_position(local_position)
                self.db.log_event(
                    "external_close_unresolved",
                    "Exchange position is flat; no synthetic P&L was recorded",
                    {"position_id": local_position.id, "asset": asset},
                )
                self.positions.remove(local_position)
                self._is_paused = True
                logger.warning(
                    "Exchange reports %s flat; position %s requires fill review",
                    asset,
                    local_position.id,
                )
                continue

            matched_directions.append(direction)
            exchange_quantity = abs(float(exchange_position.get("szi", 0)))
            if exchange_quantity <= 0:
                continue
            local_position.quantity = exchange_quantity
            local_position.confirmed_quantity = exchange_quantity
            local_position.remaining_quantity = exchange_quantity
            local_position.execution_state = "open"
            exchange_entry = float(exchange_position.get("entryPx", 0))
            if exchange_entry > 0:
                local_position.entry_price = exchange_entry
            self.db.save_position(local_position)

        for direction, exchange_position in exchange_map.items():
            if direction in matched_directions:
                continue
            quantity = abs(float(exchange_position.get("szi", 0)))
            if quantity <= 0:
                continue
            side = Side.LONG if direction == "Long" else Side.SHORT
            recovered = Position(
                side=side,
                entry_price=float(exchange_position.get("entryPx", 0)),
                quantity=quantity,
                tp_price=0.0,
                sl_price=0.0,
                entry_time=datetime.now(),
                leverage=self.config.LEVERAGE,
                asset=asset,
                execution_state="recovered_unmanaged",
                confirmed_quantity=quantity,
                remaining_quantity=quantity,
                last_exchange_observation=snapshot.observed_at,
            )
            self.positions.append(recovered)
            self.db.save_position(recovered)
            self.db.log_event(
                "recovered_unmanaged_position",
                "Exchange-only exposure quarantined; no TP/SL was invented",
                {"position_id": recovered.id, "asset": asset, "quantity": quantity},
            )
            self._is_paused = True
            logger.error("Recovered unmanaged %s exposure; new entries paused", asset)

        return True

    async def _close_position(self, position: Position, exit_price: float, reason: str):
        """Request an exit and settle it only after authoritative evidence.

        Paper mode receives an immediate deterministic fill. In live mode, an
        accepted IOC is merely an exit request; its final P&L is not recorded
        until the exchange is flat *and* reports the corresponding fill.
        """
        if exit_price <= 0:
            logger.error("Refusing %s exit without a valid market price", reason)
            return False
        if position.is_exit_pending:
            logger.info("Exit already pending for %s", position.id)
            return False

        if self.config.PAPER_TRADING:
            simulated_exit_fee = (
                position.entry_price * position.quantity * self.config.TAKER_FEE_PCT
            )
            await self._finalize_closed_position(
                position,
                exit_price,
                position.quantity,
                simulated_exit_fee,
                reason,
            )
            return True

        position.execution_state = "exit_requested"
        position.exit_reason = reason
        position.exit_cloid = position.exit_cloid or f"exit-{position.id[:20]}"
        self.db.save_position(position)
        close_side = Side.SHORT if position.side == Side.LONG else Side.LONG
        result = await self.api.place_order(
            side=close_side,
            price=exit_price,
            quantity=position.remaining_quantity or position.quantity,
            reduce_only=True,
            cloid=position.exit_cloid,
            order_type="ioc",
            coin=position.asset or self.config.ASSET,
        )
        if result.get("status") != "ok":
            position.execution_state = (
                "state_unknown" if result.get("status") == "unknown" else "open"
            )
            self.db.save_position(position)
            logger.error(
                "Exit submission did not complete for %s: %s",
                position.id,
                result.get("msg"),
            )
            return False

        position.exit_oid = result.get("response", {}).get("oid")
        self.db.save_position(position)
        return await self._confirm_live_exit(position)

    async def _confirm_live_exit(self, position: Position) -> bool:
        """Finalize only when a fill and a flat authoritative snapshot agree."""
        try:
            snapshot = await self.api.get_positions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            snapshot = PositionRead.unavailable(str(exc))
        if not isinstance(snapshot, PositionRead):
            snapshot = PositionRead.success(list(snapshot))
        if not snapshot.available:
            self.db.log_event(
                "exit_confirmation_unavailable",
                snapshot.error or "unknown",
                {"position_id": position.id},
            )
            return False

        direction = "Long" if position.side == Side.LONG else "Short"
        matching_exchange_positions = [
            exchange_position
            for exchange_position in snapshot
            if exchange_position.get("coin") == (position.asset or self.config.ASSET)
            and exchange_position.get("direction") == direction
            and abs(float(exchange_position.get("szi", 0))) > 1e-9
        ]
        fills = await self.api.get_recent_fills()
        matching_fills = [
            fill
            for fill in fills
            if position.exit_oid is not None
            and str(fill.get("oid")) == str(position.exit_oid)
        ]
        filled_quantity = sum(abs(float(fill.get("sz", 0))) for fill in matching_fills)

        for index, fill in enumerate(matching_fills):
            fill_id = str(
                fill.get("tid") or fill.get("hash") or f"{position.exit_oid}:{index}"
            )
            if not self.db.record_execution_fill(
                position_id=position.id,
                exchange_order_id=position.exit_oid,
                fill_id=fill_id,
                quantity=abs(float(fill.get("sz", 0))),
                price=float(fill.get("px", 0)),
                fee=float(fill.get("fee", 0) or 0),
            ):
                self.db.log_event(
                    "duplicate_exit_fill",
                    "Duplicate fill ignored during reconciliation",
                    {"position_id": position.id, "fill_id": fill_id},
                )
                return False

        if matching_exchange_positions:
            if filled_quantity > 0:
                remaining = sum(
                    abs(float(exchange_position.get("szi", 0)))
                    for exchange_position in matching_exchange_positions
                )
                position.quantity = remaining
                position.confirmed_quantity = remaining
                position.remaining_quantity = remaining
                position.execution_state = "partially_closed"
                self.db.save_position(position)
                self.db.log_event(
                    "exit_partially_filled",
                    "Authoritative snapshot reports residual exposure",
                    {"position_id": position.id, "remaining_quantity": remaining},
                )
            else:
                self.db.log_event(
                    "exit_unfilled",
                    "Exchange still reports full exposure",
                    {"position_id": position.id},
                )
            return False

        if not matching_fills:
            position.execution_state = "state_unknown"
            self.db.save_position(position)
            self.db.log_event(
                "exit_fill_unresolved",
                "Account is flat but matching fill data is unavailable",
                {"position_id": position.id},
            )
            logger.error(
                "Refusing synthetic P&L: exit fill for %s is unavailable", position.id
            )
            return False

        expected_quantity = position.remaining_quantity or position.quantity
        if filled_quantity + 1e-9 < expected_quantity:
            position.quantity = max(0.0, expected_quantity - filled_quantity)
            position.remaining_quantity = position.quantity
            position.execution_state = "partially_closed"
            self.db.save_position(position)
            return False

        exit_notional = sum(
            abs(float(fill.get("sz", 0))) * float(fill.get("px", 0))
            for fill in matching_fills
        )
        fill_price = exit_notional / filled_quantity if filled_quantity else 0.0
        fill_fee = sum(float(fill.get("fee", 0) or 0) for fill in matching_fills)
        return await self._finalize_closed_position(
            position,
            fill_price,
            filled_quantity,
            fill_fee,
            position.exit_reason or "EXIT",
        )

    async def _finalize_closed_position(
        self,
        position: Position,
        exit_price: float,
        quantity: float,
        exit_fee: float,
        reason: str,
    ) -> bool:
        """Perform the single terminal accounting path for a confirmed exit."""
        if exit_price <= 0 or quantity <= 0:
            return False
        if position.side == Side.LONG:
            pnl_gross = (exit_price - position.entry_price) * quantity
        else:
            pnl_gross = (position.entry_price - exit_price) * quantity
        entry_fee = position.entry_price * quantity * self.config.MAKER_FEE_PCT
        fees = entry_fee + exit_fee
        net_pnl = pnl_gross - fees
        trade = Trade(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=quantity,
            entry_time=position.entry_time,
            exit_time=datetime.now(),
            pnl=net_pnl,
            fees=fees,
            notes=reason,
        )
        self.db.save_trade(trade)
        self.db.close_position_by_uid(position.id)
        self.db.log_event(
            "trade_exit",
            f"{position.side.value} exit ({reason})",
            {"pnl": net_pnl, "position_id": position.id},
        )
        self.trades.append(trade)
        self.daily_trades_list.append(trade)
        self.daily_trades += 1
        self.daily_pnl += net_pnl
        self.current_capital += net_pnl
        self.survival_risk.update_after_trade(
            trade, self.current_capital, datetime.now()
        )
        self.survival_risk.tiered_risk.update(
            trade,
            self.daily_pnl,
            self.consecutive_losses,
            self.starting_capital,
        )
        self.adaptive_params.record_trade(trade)
        self.performance_analyzer.add_trade(trade)
        if net_pnl < 0:
            self.consecutive_losses += 1
            if (
                self.config.CIRCUIT_BREAKER_ENABLED
                and self.consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES
            ):
                await self._trigger_circuit_breaker()
        else:
            self.consecutive_losses = 0
        self.current_capital = max(self.current_capital, 100)
        if self.current_capital > self.peak_equity:
            self.peak_equity = self.current_capital
        self.max_drawdown_pct = max(
            self.max_drawdown_pct,
            (self.peak_equity - self.current_capital) / self.peak_equity,
        )
        position.execution_state = "closed_confirmed"
        position.remaining_quantity = 0.0
        if position in self.positions:
            self.positions.remove(position)
        if self.telegram:
            await self.telegram.notify_trade_exit(trade)
        return True

    # Risk management

    async def _check_daily_reset(self):
        """Reset daily counters at midnight"""
        now = datetime.utcnow()
        if self.last_trade_date != now.date():
            self.last_trade_date = now.date()

            # Save daily summary before reset
            if self.daily_trades > 0:
                daily_wins = len([t for t in self.daily_trades_list if t.pnl > 0])
                daily_losses = len([t for t in self.daily_trades_list if t.pnl < 0])
                daily_fees = sum(t.fees for t in self.daily_trades_list)
                summary = {
                    "date": self.last_trade_date.isoformat(),
                    "total_trades": self.daily_trades,
                    "winning_trades": daily_wins,
                    "losing_trades": daily_losses,
                    "total_pnl": self.daily_pnl,
                    "total_fees": daily_fees,
                    "win_rate": (
                        daily_wins / self.daily_trades * 100
                        if self.daily_trades > 0
                        else 0
                    ),
                    "max_drawdown_pct": self.max_drawdown_pct,
                    "starting_capital": self.starting_capital,
                    "ending_capital": self.current_capital,
                }
                self.db.save_daily_summary(self.last_trade_date.isoformat(), summary)

                if self.telegram:
                    await self.telegram.notify_daily_summary(summary)

            # Reset counters
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.daily_trades_list = []
            self.consecutive_losses = 0

            # Reset survival risk
            self.survival_risk.tiered_risk.reset()

            logger.info(f"Daily reset - Date: {now.date()}")

    async def _check_circuit_breaker_cooldown(self):
        """Check if circuit breaker cooldown has expired"""
        if not self.circuit_breaker_triggered or self.circuit_breaker_until is None:
            return

        if datetime.now() >= self.circuit_breaker_until:
            logger.info("✅ Circuit breaker cooldown expired - resuming trading")
            self.circuit_breaker_triggered = False
            self.circuit_breaker_until = None
            self.consecutive_losses = 0

            if self.telegram:
                await self.telegram.notify_circuit_breaker(
                    False, 0, self.config.MAX_CONSECUTIVE_LOSSES
                )

    async def _trigger_circuit_breaker(self):
        """Trigger circuit breaker after consecutive losses"""
        self.circuit_breaker_triggered = True
        cooldown = timedelta(minutes=self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES)
        self.circuit_breaker_until = datetime.now() + cooldown

        logger.warning("=" * 60)
        logger.warning("⛔ CIRCUIT BREAKER TRIGGERED!")
        logger.warning(f"   Consecutive losses: {self.consecutive_losses}")
        logger.warning(
            f"   Cooldown: {self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES} minutes"
        )
        logger.warning("=" * 60)

        if self.telegram:
            await self.telegram.notify_circuit_breaker(
                True,
                self.consecutive_losses,
                self.config.MAX_CONSECUTIVE_LOSSES,
                self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES,
            )

    async def _update_unrealized_pnl(self):
        """Update unrealized P&L for open positions"""
        if not self.positions:
            return

        # Use cached mids from WebSocket if recent (< 30s), otherwise fetch
        if (
            self._mids_last_update
            and (datetime.now() - self._mids_last_update).total_seconds() < 30
            and self.config.ASSET in self._cached_mids
        ):
            current_price = self._cached_mids[self.config.ASSET]
        else:
            mids = await self.api.get_mids()
            current_price = float(mids.get(self.config.ASSET, 0))

        if current_price == 0:
            return

        for position in self.positions:
            if position.execution_state != "open":
                continue
            if position.side == Side.LONG:
                pnl = (current_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - current_price) * position.quantity

            position.unrealized_pnl = pnl
            self.db.save_position(position)

    # Emergency controls

    async def force_close_all_positions(self, reason="FORCE_CLOSE"):
        """Force close all open positions"""
        if not self.positions:
            logger.info(f"[{reason}] No positions to close")
            return 0

        logger.warning(f"[{reason}] Closing {len(self.positions)} position(s)...")

        # Use cached mids from WebSocket if recent (< 30s), otherwise fetch
        if (
            self._mids_last_update
            and (datetime.now() - self._mids_last_update).total_seconds() < 30
            and self.config.ASSET in self._cached_mids
        ):
            current_price = self._cached_mids[self.config.ASSET]
        else:
            mids = await self.api.get_mids()
            current_price = float(mids.get(self.config.ASSET, 0))

        if current_price <= 0:
            logger.error(
                "[%s] Cannot close positions without a valid mid price", reason
            )
            return 0

        positions_to_close = [
            position
            for position in self.positions
            if position.execution_state == "open"
        ]

        closed_count = 0
        for position in positions_to_close:
            if await self._close_position(position, current_price, reason):
                closed_count += 1

        logger.info(
            "[%s] Confirmed %s position(s) closed. P&L: $%.2f",
            reason,
            closed_count,
            self.daily_pnl,
        )
        return closed_count

    async def close_all_positions(self):
        """Public method to close all positions"""
        return await self.force_close_all_positions("MANUAL_CLOSE")

    async def place_manual_trade(
        self, side: Side, quantity: float, price: Optional[float] = None
    ) -> Dict:
        """Place a manual trade"""
        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, price if price else 0))

        if current_price == 0:
            raise ValueError("Cannot get current price")

        signal = {
            "action": side,
            "confidence": 100,
            "entry_price": current_price,
            "tp_price": current_price * 1.02,  # Default 2% TP
            "sl_price": current_price * 0.98,  # Default 2% SL
            "quantity": quantity,
        }

        await self._place_entry_order(signal)

        return {"status": "ok", "signal": signal}

    # Utility methods

    def _log_bot_info(self):
        """Log bot configuration info"""
        mode = (
            "PAPER"
            if self.config.PAPER_TRADING
            else ("TESTNET" if self.config.USE_TESTNET else "MAINNET")
        )

        logger.info(f"Strategy: Ultra-Optimized Momentum ({self.config.TIMEFRAME})")
        logger.info(f"Asset: {self.config.ASSET}")
        logger.info(f"Leverage: {self.config.LEVERAGE}x")
        logger.info(f"Risk Per Trade: {self.config.RISK_PER_TRADE_PCT:.1%}")
        logger.info(f"Mode: {mode}")

        if self.config.CIRCUIT_BREAKER_ENABLED:
            logger.info(
                f"Circuit Breaker: {self.config.MAX_CONSECUTIVE_LOSSES} losses -> {self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES}min cooldown"
            )

    def print_statistics(self) -> None:
        """Print trading statistics summary"""
        total_trades = len(self.trades)
        winning_trades = len([t for t in self.trades if t.pnl > 0])
        losing_trades = len([t for t in self.trades if t.pnl < 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        total_pnl = sum(t.pnl for t in self.trades)
        total_fees = sum(t.fees for t in self.trades)
        pnl_pct = (
            (self.current_capital - self.starting_capital) / self.starting_capital * 100
            if self.starting_capital > 0
            else 0
        )

        # Calculate average win/loss
        avg_win = (
            (sum(t.pnl for t in self.trades if t.pnl > 0) / winning_trades)
            if winning_trades > 0
            else 0
        )
        avg_loss = (
            (sum(t.pnl for t in self.trades if t.pnl < 0) / losing_trades)
            if losing_trades > 0
            else 0
        )

        # Best/worst trade
        best_trade = max((t.pnl for t in self.trades), default=0)
        worst_trade = min((t.pnl for t in self.trades), default=0)

        # Runtime
        runtime = ""
        if self.start_time:
            delta = datetime.now() - self.start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            runtime = f"{hours}h {minutes}m"

        print("\n" + "=" * 60)
        print("TRADING BOT STATISTICS")
        print("=" * 60)
        print(f"Runtime:              {runtime}")
        print(f"Starting Capital:     ${self.starting_capital:,.2f}")
        print(f"Current Capital:      ${self.current_capital:,.2f}")
        print(f"Total P&L:            ${total_pnl:,.2f} ({pnl_pct:+.2f}%)")
        print(f"Total Fees:           ${total_fees:,.2f}")
        print("-" * 60)
        print(f"Total Trades:         {total_trades}")
        print(f"Winning Trades:       {winning_trades}")
        print(f"Losing Trades:        {losing_trades}")
        print(f"Win Rate:             {win_rate:.1f}%")
        print(f"Avg Win:              ${avg_win:,.2f}")
        print(f"Avg Loss:             ${avg_loss:,.2f}")
        print(f"Best Trade:           ${best_trade:,.2f}")
        print(f"Worst Trade:          ${worst_trade:,.2f}")
        print("-" * 60)
        print(f"Max Drawdown:         {self.max_drawdown_pct:.2%}")
        print(f"Open Positions:       {len(self.positions)}")
        print(f"Consecutive Losses:   {self.consecutive_losses}")
        print(
            f"Circuit Breaker:      {'ACTIVE' if self.circuit_breaker_triggered else 'Off'}"
        )

        # Performance analytics (Phase 2)
        if self.trades:
            try:
                metrics = self.performance_analyzer.calculate_metrics()
                print("-" * 60)
                print("PERFORMANCE ANALYTICS")
                print("-" * 60)
                print(f"Sharpe Ratio:          {metrics.sharpe_ratio:.2f}")
                print(f"Sortino Ratio:         {metrics.sortino_ratio:.2f}")
                print(f"Calmar Ratio:          {metrics.calmar_ratio:.2f}")
                print(f"Profit Factor:         {metrics.profit_factor:.2f}")
                print(f"Max Winning Streak:    {metrics.max_winning_streak}")
                print(f"Max Losing Streak:     {metrics.max_losing_streak}")
            except Exception:
                pass

        # Adaptive parameters
        try:
            params = self.adaptive_params.get_parameters()
            print("-" * 60)
            print("ADAPTIVE PARAMETERS")
            print("-" * 60)
            print(f"Volatility Regime:     {params.volatility_regime.value}")
            print(f"Market Phase:          {params.market_phase.value}")
            print(f"Adjusted Leverage:     {params.leverage}x")
            print(f"Adjusted Risk:         {params.risk_per_trade:.1%}")
        except Exception:
            pass

        print("=" * 60 + "\n")

    def _start_api_server(self) -> None:
        """Start the API server if enabled"""
        try:
            from bot_api_server import TradingBotAPI

            self.api_server = TradingBotAPI(
                self, host=self.config.WEB_UI_HOST, port=self.config.WEB_UI_PORT
            )
            self.api_server.start_in_background()

            logger.info(
                f"✅ API server on http://{self.config.WEB_UI_HOST}:{self.config.WEB_UI_PORT}"
            )
        except ImportError:
            logger.warning("Could not import API server - bot_api_server.py not found")

    async def _shutdown(self, reason: str = ""):
        """Shutdown the bot"""
        logger.info(f"Shutting down: {reason}")

        # Persist state before closing anything
        self._persist_state()

        # Close all positions (paper: force close, live: cancel + close)
        await self.force_close_all_positions("SHUTDOWN")

        # Cancel all orders
        if not self.config.PAPER_TRADING:
            await self.api.cancel_all_orders()

        # Send shutdown notification
        if self.telegram:
            await self.telegram.notify_shutdown(reason)

        # Close database
        self.db.close()

        # Close telegram client
        if self.telegram:
            await self.telegram.close()

        self.is_running = False

    # ------------------------------------------------------------------
    # State persistence (graceful shutdown / restart recovery)
    # ------------------------------------------------------------------

    def _persist_state(self):
        """Save current bot state to database for recovery after restart."""
        try:
            self.db.save_bot_state(
                current_capital=self.current_capital,
                peak_equity=self.peak_equity,
                max_drawdown_pct=self.max_drawdown_pct,
                daily_pnl=self.daily_pnl,
                daily_trades=self.daily_trades,
                consecutive_losses=self.consecutive_losses,
                circuit_breaker_triggered=self.circuit_breaker_triggered,
                circuit_breaker_until=(
                    self.circuit_breaker_until.isoformat()
                    if self.circuit_breaker_until
                    else None
                ),
                last_trade_date=(
                    self.last_trade_date.isoformat()
                    if isinstance(self.last_trade_date, datetime)
                    else self.last_trade_date
                ),
                last_signal_time=(
                    self._last_signal_time.isoformat()
                    if self._last_signal_time
                    else None
                ),
                emergency_stop=self.emergency_stop,
            )
            logger.info("Bot state persisted to database")
        except Exception as exc:
            logger.error(f"Failed to persist bot state: {exc}")

    def _restore_state(self):
        """Restore bot state from database after restart."""
        try:
            state = self.db.load_bot_state()
            if state is None:
                logger.info("No previous bot state found — starting fresh")
                return

            self.current_capital = state.get("current_capital", self.current_capital)
            self.peak_equity = state.get("peak_equity", self.peak_equity)
            self.max_drawdown_pct = state.get("max_drawdown_pct", self.max_drawdown_pct)
            self.daily_pnl = state.get("daily_pnl", self.daily_pnl)
            self.daily_trades = state.get("daily_trades", self.daily_trades)
            self.consecutive_losses = state.get(
                "consecutive_losses", self.consecutive_losses
            )
            self.circuit_breaker_triggered = state.get(
                "circuit_breaker_triggered", False
            )

            cb_until = state.get("circuit_breaker_until")
            if cb_until:
                self.circuit_breaker_until = datetime.fromisoformat(cb_until)

            last_td = state.get("last_trade_date")
            if last_td:
                self.last_trade_date = datetime.fromisoformat(last_td)

            last_st = state.get("last_signal_time")
            if last_st:
                self._last_signal_time = datetime.fromisoformat(last_st)

            # Restore open positions
            saved_positions = self.db.get_active_positions()
            for pdict in saved_positions:
                try:
                    pos = Position(
                        side=Side[pdict["side"]],
                        entry_price=pdict["entry_price"],
                        quantity=pdict["quantity"],
                        tp_price=pdict.get("tp_price", 0),
                        sl_price=pdict.get("sl_price", 0),
                        entry_time=datetime.fromisoformat(pdict["entry_time"]),
                        leverage=pdict.get("leverage", self.config.LEVERAGE),
                        oid=pdict.get("oid"),
                        cloid=pdict.get("cloid"),
                        status=OrderStatus.OPEN,
                        unrealized_pnl=pdict.get("unrealized_pnl", 0),
                        id=pdict.get("position_uid") or uuid4().hex,
                        asset=pdict.get("asset") or self.config.ASSET,
                        execution_state=pdict.get("execution_state") or "state_unknown",
                        exit_oid=pdict.get("exit_oid"),
                        exit_cloid=pdict.get("exit_cloid"),
                        exit_reason=pdict.get("exit_reason"),
                        confirmed_quantity=pdict.get("confirmed_quantity") or 0.0,
                        remaining_quantity=pdict.get("remaining_quantity"),
                        last_exchange_observation=(
                            datetime.fromisoformat(pdict["last_exchange_observation"])
                            if pdict.get("last_exchange_observation")
                            else None
                        ),
                    )
                    self.positions.append(pos)
                except Exception as exc:
                    logger.warning(f"Could not restore position: {exc}")

            logger.info(
                f"Restored state: capital=${self.current_capital:.2f}, "
                f"positions={len(self.positions)}, "
                f"daily_pnl=${self.daily_pnl:.2f}, "
                f"consecutive_losses={self.consecutive_losses}"
            )

            # Reset daily counters if a new day
            if self.last_trade_date:
                today = datetime.now().date()
                last_date = (
                    self.last_trade_date.date()
                    if isinstance(self.last_trade_date, datetime)
                    else self.last_trade_date
                )
                if today > last_date:
                    logger.info("New day detected — resetting daily counters")
                    self.daily_trades = 0
                    self.daily_pnl = 0.0
                    self.daily_trades_list = []

        except Exception as exc:
            logger.error(f"Failed to restore bot state: {exc}")
            logger.info("Starting with fresh state")

    async def stop(self):
        """Stop the trading bot"""
        self.emergency_stop = True
        await self._shutdown("Manual stop")

    # Properties for API server

    @property
    def daily_trade_count(self) -> int:
        return self.daily_trades

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def circuit_breaker_status(self) -> Dict:
        return {
            "enabled": self.config.CIRCUIT_BREAKER_ENABLED,
            "is_triggered": self.circuit_breaker_triggered,
            "consecutive_losses": self.consecutive_losses,
            "max_consecutive_losses": self.config.MAX_CONSECUTIVE_LOSSES,
            "cooldown_until": self.circuit_breaker_until.isoformat()
            if self.circuit_breaker_until
            else None,
            "cooldown_minutes": self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES,
        }
