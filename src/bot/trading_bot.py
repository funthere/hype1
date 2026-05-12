"""
Main Trading Bot - Orchestrates all components
"""

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from ..core.config import BotConfig, Side, Position, Trade, OrderStatus
from ..core.strategy import StrategyEngine, RiskManager
from ..core.survival_risk import SurvivalRiskManager
from ..exchange.connector import HyperliquidAPI
from ..exchange.market_data import MarketDataFeed
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
        """Setup signal handlers for external control"""
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

                # Check exits on open positions (always check)
                await self._check_position_exits()

                # Periodic position reconciliation with exchange (live mode only)
                await self._maybe_reconcile_positions()

                # Process new signals (only if not paused)
                if not self._is_paused and self.market_data.current_candle:
                    signal = self.strategy.generate_signal(self.current_capital)
                    if signal:
                        await self._process_signal(signal)

                # Sleep before next iteration
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                if self.telegram:
                    await self.telegram.notify_error(str(e), "main_loop")
                await asyncio.sleep(5)

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
        """Place entry order based on signal"""
        side = signal["action"]
        entry_price = signal["entry_price"]
        quantity = signal["quantity"]

        logger.info(f"Placing {side.value} entry order @ ${entry_price:.4f}")

        # Create position object
        position = Position(
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            tp_price=signal["tp_price"],
            sl_price=signal["sl_price"],
            entry_time=datetime.now(),
            leverage=self.config.LEVERAGE,
        )

        # Place order on exchange (skip for paper trading)
        if not self.config.PAPER_TRADING:
            result = await self.api.place_order(
                side=side, price=entry_price, quantity=quantity
            )

            if result.get("status") != "ok":
                logger.error(f"Entry order failed: {result.get('msg')}")
                return

            # Store order ID
            position.oid = result.get("response", {}).get("oid")

        # Add to positions
        self.positions.append(position)

        # Save to database
        self.db.save_position(position)
        self.db.log_event("trade_entry", f"{side.value} entry", signal)

        # Send notification
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
            return

        now = datetime.now()
        if (
            self._last_reconciliation is not None
            and now - self._last_reconciliation < self._reconciliation_interval
        ):
            return

        self._last_reconciliation = now
        await self._reconcile_positions()

    async def _reconcile_positions(self):
        """Compare local positions against exchange and fix drift.

        Cases handled:
        1. Position exists on exchange but NOT locally → restore from exchange
        2. Position exists locally but NOT on exchange → mark closed
        3. Quantity mismatch → update local quantity
        """
        try:
            exchange_positions = await self.api.get_positions()
            asset = self.config.ASSET

            # Build a lookup of exchange positions for our asset
            exchange_map: Dict[str, Dict] = {}
            for ep in exchange_positions:
                coin = ep.get("coin", "")
                if coin == asset:
                    exchange_map[ep.get("direction", "")] = ep

            # Track which exchange positions were matched
            matched_directions: List[str] = []

            # --- Check local positions against exchange ---
            stale_locals: List[Position] = []
            for local_pos in list(self.positions):
                direction = "Long" if local_pos.side == Side.LONG else "Short"
                ep = exchange_map.get(direction)

                if ep is None:
                    # Position closed on exchange without us knowing
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
                mids = await self.api.get_mids()
                exit_price = float(mids.get(asset, pos.entry_price))
                await self._close_position(pos, exit_price, "RECONCILE_MISSING")

            # --- Check for exchange positions not in local ---
            for direction, ep in exchange_map.items():
                if direction in matched_directions:
                    continue

                # Found a position on exchange we don't track
                side = Side.LONG if direction == "Long" else Side.SHORT
                entry_px = float(ep.get("entryPx", 0))
                qty = abs(float(ep.get("szi", 0)))

                if qty <= 0:
                    continue

                logger.warning(
                    f"⚠️ Reconciliation: restoring untracked {direction} "
                    f"qty={qty} @ ${entry_px:.4f} from exchange"
                )
                mids = await self.api.get_mids()
                current_px = float(mids.get(asset, entry_px))
                tp_mult = 1.03 if side == Side.LONG else 0.97
                sl_mult = 0.97 if side == Side.LONG else 1.03

                restored = Position(
                    side=side,
                    entry_price=entry_px,
                    quantity=qty,
                    tp_price=round(current_px * tp_mult, 4),
                    sl_price=round(current_px * sl_mult, 4),
                    entry_time=datetime.now(),  # best effort
                    leverage=self.config.LEVERAGE,
                    status=OrderStatus.OPEN,
                )
                self.positions.append(restored)
                self.db.save_position(restored)
                self.db.log_event(
                    "reconciliation",
                    f"Restored {direction} position from exchange",
                    {"entry_price": entry_px, "quantity": qty},
                )

                if self.telegram:
                    await self.telegram.notify_info(
                        f"🔄 Reconciliation: restored {direction} {qty} {asset} @ ${entry_px:.4f}"
                    )

            if stale_locals or len(matched_directions) != len(exchange_map):
                logger.info("Reconciliation complete — state synced with exchange")

        except Exception as exc:
            logger.error(f"Position reconciliation failed: {exc}")

    async def _close_position(self, position: Position, exit_price: float, reason: str):
        """Close a position"""
        logger.info(
            f"Closing {position.side.value} position @ ${exit_price:.4f} ({reason})"
        )

        # Calculate P&L
        if position.side == Side.LONG:
            pnl_gross = (exit_price - position.entry_price) * position.quantity
        else:
            pnl_gross = (position.entry_price - exit_price) * position.quantity

        # PnL = (exit - entry) * qty; leverage determines margin, NOT PnL
        pnl = pnl_gross

        # Calculate fees - entry + exit, using taker fee for conservative estimate
        notional = position.entry_price * position.quantity
        fees = notional * self.config.TAKER_FEE_PCT * 2  # Entry + exit

        # Net P&L
        net_pnl = pnl - fees

        # Create trade record
        trade = Trade(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.entry_time,
            exit_time=datetime.now(),
            pnl=net_pnl,
            fees=fees,
        )

        # Save trade to database
        self.db.save_trade(trade)
        self.db.log_event(
            "trade_exit", f"{position.side.value} exit ({reason})", {"pnl": net_pnl}
        )

        # Update tracking
        self.trades.append(trade)
        self.daily_trades_list.append(trade)
        self.daily_trades += 1
        self.daily_pnl += net_pnl
        self.current_capital += net_pnl

        # Update survival risk manager
        self.survival_risk.update_after_trade(
            trade, self.current_capital, datetime.now()
        )
        self.survival_risk.tiered_risk.update(
            trade, self.daily_pnl, self.consecutive_losses
        )

        # Record trade for adaptive parameters and performance analysis
        self.adaptive_params.record_trade(trade)
        self.performance_analyzer.add_trade(trade)

        # Update consecutive losses tracking
        if net_pnl < 0:
            self.consecutive_losses += 1
            if (
                self.config.CIRCUIT_BREAKER_ENABLED
                and self.consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES
            ):
                await self._trigger_circuit_breaker()
        else:
            self.consecutive_losses = 0

        # Update drawdown tracking
        self.current_capital = max(self.current_capital, 100)  # Floor at minimum
        if self.current_capital > self.peak_equity:
            self.peak_equity = self.current_capital
        drawdown = (self.peak_equity - self.current_capital) / self.peak_equity
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)

        # Remove from positions
        if position in self.positions:
            self.positions.remove(position)

        # Close on exchange
        if not self.config.PAPER_TRADING and position.oid:
            await self.api.cancel_order(position.oid)

        # Send notification
        if self.telegram:
            await self.telegram.notify_trade_exit(trade)

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

        positions_to_close = self.positions[:]

        for position in positions_to_close:
            await self._close_position(position, current_price, reason)

        closed_count = len(positions_to_close)
        logger.info(
            f"[{reason}] Closed {closed_count} position(s). P&L: ${self.daily_pnl:.2f}"
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

        # Close all positions
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
