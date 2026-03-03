"""
Telegram notification system for trading bot alerts
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List

import httpx

from ..core.config import BotConfig, Trade, Position, Side

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Telegram notification system for trading bot events.

    Supports notifications for:
    - Trade entries and exits
    - Circuit breaker triggers
    - Errors and warnings
    - Daily summaries
    """

    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram notifier

        Args:
            bot_token: Telegram bot token from @BotFather
            chat_id: Chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self._enabled = True
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def from_config(cls, config: BotConfig) -> Optional["TelegramNotifier"]:
        """Create notifier from config if enabled"""
        if not config.TELEGRAM_ENABLED:
            return None

        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            logger.warning("Telegram enabled but token or chat_id missing")
            return None

        return cls(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

    async def _send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """
        Send message to Telegram

        Args:
            message: Message text
            parse_mode: Markdown or HTML

        Returns:
            True if successful, False otherwise
        """
        if not self._enabled:
            return False

        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=10.0)

            response = await self._client.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode},
            )

            if response.status_code == 200:
                return True
            else:
                logger.error(f"Telegram API error: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def format_trade_entry(self, signal: Dict) -> str:
        """Format trade entry signal into message"""
        emoji = "🟢" if signal["action"] == Side.LONG else "🔴"
        side = signal["action"].value

        return (
            f"{emoji} *Trade Entry*\n"
            f"Side: `{side}`\n"
            f"Price: `${signal['entry_price']:.4f}`\n"
            f"Quantity: `{signal['quantity']:.2f}`\n"
            f"TP: `${signal['tp_price']:.4f}`\n"
            f"SL: `${signal['sl_price']:.4f}`\n"
            f"Confidence: `{signal['confidence']:.1f}%`\n"
            f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
        )

    def format_trade_exit(self, trade: Trade) -> str:
        """Format completed trade into message"""
        emoji = "✅" if trade.pnl > 0 else "❌"
        side = trade.side.value
        pnl_emoji = "📈" if trade.pnl > 0 else "📉"

        return (
            f"{emoji} *Trade Exit*\n"
            f"Side: `{side}`\n"
            f"Entry: `${trade.entry_price:.4f}`\n"
            f"Exit: `${trade.exit_price:.4f}`\n"
            f"P&L: {pnl_emoji} `${trade.pnl:.2f}`\n"
            f"Fees: `${trade.fees:.2f}`\n"
            f"Net: `${trade.pnl - trade.fees:.2f}`\n"
            f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
        )

    def format_circuit_breaker(
        self, triggered: bool, consecutive_losses: int, max_losses: int, cooldown_minutes: int = 30
    ) -> str:
        """Format circuit breaker notification"""
        if triggered:
            return (
                f"⛔ *Circuit Breaker TRIGGERED*\n"
                f"Consecutive losses: `{consecutive_losses}/{max_losses}`\n"
                f"Cooldown: `{cooldown_minutes}` minutes\n"
                f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
            )
        else:
            return (
                f"✅ *Circuit Breaker Reset*\n"
                f"Trading can resume\n"
                f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
            )

    def format_daily_summary(self, summary: Dict) -> str:
        """Format daily summary into message"""
        win_rate = summary.get("win_rate", 0)
        pnl = summary.get("total_pnl", 0)
        emoji = "📈" if pnl >= 0 else "📉"

        return (
            f"📊 *Daily Summary*\n"
            f"Date: `{summary.get('date', 'N/A')}`\n"
            f"Trades: `{summary.get('total_trades', 0)}`\n"
            f"Win Rate: `{win_rate:.1f}%`\n"
            f"P&L: {emoji} `${pnl:.2f}`\n"
            f"Fees: `${summary.get('total_fees', 0):.2f}`\n"
            f"Return: `{summary.get('total_return_pct', 0):.2f}%`"
        )

    def format_error(self, error_message: str, context: str = "") -> str:
        """Format error notification"""
        msg = f"🚨 *Error*\n{error_message}"
        if context:
            msg += f"\n\nContext: `{context}`"
        return msg

    def format_warning(self, warning_message: str) -> str:
        """Format warning notification"""
        return f"⚠️ *Warning*\n{warning_message}"

    def format_start_notification(self, config: BotConfig) -> str:
        """Format bot startup notification"""
        mode = (
            "PAPER"
            if config.PAPER_TRADING
            else ("TESTNET" if config.USE_TESTNET else "MAINNET")
        )

        return (
            f"🚀 *Trading Bot Started*\n"
            f"Mode: `{mode}`\n"
            f"Asset: `{config.ASSET}`\n"
            f"Leverage: `{config.LEVERAGE}x`\n"
            f"Timeframe: `{config.TIMEFRAME}`\n"
            f"Risk: `{config.RISK_PER_TRADE_PCT:.1%}`\n"
            f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        )

    def format_shutdown_notification(self, reason: str = "") -> str:
        """Format bot shutdown notification"""
        msg = f"🛑 *Trading Bot Stopped*\n"
        if reason:
            msg += f"Reason: `{reason}`\n"
        msg += f"Time: `{datetime.now().strftime('%H:%M:%S')}`"
        return msg

    # Public notification methods

    async def notify_trade_entry(self, signal: Dict) -> bool:
        """Send trade entry notification"""
        message = self.format_trade_entry(signal)
        return await self._send_message(message)

    async def notify_trade_exit(self, trade: Trade) -> bool:
        """Send trade exit notification"""
        message = self.format_trade_exit(trade)
        return await self._send_message(message)

    async def notify_circuit_breaker(
        self, triggered: bool, consecutive_losses: int, max_losses: int, cooldown_minutes: int = 30
    ) -> bool:
        """Send circuit breaker notification"""
        message = self.format_circuit_breaker(
            triggered, consecutive_losses, max_losses, cooldown_minutes
        )
        return await self._send_message(message)

    async def notify_daily_summary(self, summary: Dict) -> bool:
        """Send daily summary notification"""
        message = self.format_daily_summary(summary)
        return await self._send_message(message)

    async def notify_error(self, error_message: str, context: str = "") -> bool:
        """Send error notification"""
        message = self.format_error(error_message, context)
        return await self._send_message(message)

    async def notify_warning(self, warning_message: str) -> bool:
        """Send warning notification"""
        message = self.format_warning(warning_message)
        return await self._send_message(message)

    async def notify_start(self, config: BotConfig) -> bool:
        """Send bot startup notification"""
        message = self.format_start_notification(config)
        return await self._send_message(message)

    async def notify_shutdown(self, reason: str = "") -> bool:
        """Send bot shutdown notification"""
        message = self.format_shutdown_notification(reason)
        return await self._send_message(message)

    async def notify_position_update(self, position: Position) -> bool:
        """Send position update notification (for P&L changes)"""
        emoji = "🟢" if position.unrealized_pnl >= 0 else "🔴"
        side = position.side.value

        message = (
            f"{emoji} *Position Update*\n"
            f"Side: `{side}`\n"
            f"Entry: `${position.entry_price:.4f}`\n"
            f"Unrealized P&L: `${position.unrealized_pnl:.2f}`"
        )

        return await self._send_message(message)

    async def test_connection(self) -> bool:
        """Test Telegram connection by sending a test message"""
        return await self._send_message("✅ *Test Message*\nTrading bot notifications are working!")

    def disable(self):
        """Disable notifications (for testing/maintenance)"""
        self._enabled = False

    def enable(self):
        """Enable notifications"""
        self._enabled = True

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
