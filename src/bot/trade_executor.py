"""
Trade execution component.

Handles placing entry orders, closing positions, and signal handlers.
"""

import logging
import signal
from datetime import datetime
from typing import Optional, Dict

from ..core.config import BotConfig, Side, Position, Trade, OrderStatus

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Handles order placement (entry and exit) on the exchange or in paper mode."""

    def __init__(self, api, config: BotConfig):
        self.api = api
        self.config = config

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def place_entry_order(
        self,
        signal: Dict,
        db,
        telegram,
    ) -> Optional[Position]:
        """Place an entry order based on signal. Returns the Position or None."""
        side = signal["action"]
        entry_price = signal["entry_price"]
        quantity = signal["quantity"]

        logger.info(f"Placing {side.value} entry order @ ${entry_price:.4f}")

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
                side=side,
                price=entry_price,
                quantity=quantity,
                order_type="post_only",
            )

            if result.get("status") != "ok":
                logger.error(f"Entry order failed: {result.get('msg')}")
                return None

            position.oid = result.get("response", {}).get("oid")

        # Save to database
        db.save_position(position)
        db.log_event("trade_entry", f"{side.value} entry", signal)

        # Send notification
        if telegram:
            await telegram.notify_trade_entry(signal)

        return position

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close_position_on_exchange(self, position: Position, exit_price: float) -> bool:
        """Close a position on the exchange. Returns True on success."""
        if self.config.PAPER_TRADING:
            return True

        # Cancel any remaining open entry order
        if position.oid:
            await self.api.cancel_order(position.oid)

        # Place closing order (IOC — ensure execution)
        close_side = Side.SHORT if position.side == Side.LONG else Side.LONG
        close_result = await self.api.place_order(
            side=close_side,
            price=exit_price,
            quantity=position.quantity,
            reduce_only=True,
            order_type="ioc",
        )
        if close_result.get("status") != "ok":
            logger.error(f"Close order failed: {close_result.get('msg')}")
            return False
        return True

    # ------------------------------------------------------------------
    # Signal handler helpers
    # ------------------------------------------------------------------

    @staticmethod
    def setup_signal_handlers(bot) -> None:
        """Register OS signal handlers for external control and graceful shutdown.

        *bot* must expose: ``emergency_stop``, ``force_close_all``,
        ``circuit_breaker_triggered``, ``circuit_breaker_until``,
        ``consecutive_losses``.
        """
        try:
            signal.signal(signal.SIGUSR1, TradeExecutor._make_force_close_handler(bot))
            logger.info("Signal handler: SIGUSR1 = force close all positions")
        except Exception as e:
            logger.warning(f"Could not setup SIGUSR1 handler: {e}")

        try:
            signal.signal(signal.SIGUSR2, TradeExecutor._make_reset_cb_handler(bot))
            logger.info("Signal handler: SIGUSR2 = reset circuit breaker")
        except Exception as e:
            logger.warning(f"Could not setup SIGUSR2 handler: {e}")

        try:
            handler = TradeExecutor._make_graceful_shutdown_handler(bot)
            signal.signal(signal.SIGTERM, handler)
            logger.info("Signal handler: SIGTERM = graceful shutdown")
        except Exception as e:
            logger.warning(f"Could not setup SIGTERM handler: {e}")

        try:
            handler = TradeExecutor._make_graceful_shutdown_handler(bot)
            signal.signal(signal.SIGINT, handler)
            logger.info("Signal handler: SIGINT = graceful shutdown")
        except Exception as e:
            logger.warning(f"Could not setup SIGINT handler: {e}")

    # -- factory helpers (closures that reference bot) --

    @staticmethod
    def _make_graceful_shutdown_handler(bot):
        def handler(signum, frame):
            sig_name = signal.Signals(signum).name
            logger.info(f"🛑 {sig_name} received — initiating graceful shutdown...")
            bot.emergency_stop = True
        return handler

    @staticmethod
    def _make_force_close_handler(bot):
        def handler(signum, frame):
            logger.info("⚠️  SIGUSR1 received - forcing all positions to close...")
            bot.force_close_all = True
        return handler

    @staticmethod
    def _make_reset_cb_handler(bot):
        def handler(signum, frame):
            if bot.circuit_breaker_triggered:
                logger.info("✅ SIGUSR2 received - circuit breaker reset")
                bot.circuit_breaker_triggered = False
                bot.circuit_breaker_until = None
                bot.consecutive_losses = 0
            else:
                logger.info("SIGUSR2 received - circuit breaker not active")
        return handler
