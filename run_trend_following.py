#!/usr/bin/env python3
"""
Trend Following Bot Runner

Multi-asset trend following strategy using EMA crossoovers + ADX trend strength.

Usage:
    # Paper mode (default):
    python3 run_trend_following.py
    python3 run_trend_following.py --paper --capital 10000
    python3 run_trend_following.py --paper --coins BTC,ETH,SOL,HYPE

    # Live mode:
    python3 run_trend_following.py --live --capital 5000
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.core.config import BotConfig
from src.exchange.connector import HyperliquidAPI
from src.storage.database import DatabaseManager
from src.strategy.trend_following import (
    TrendFollowingConfig,
    TrendFollowingStrategy,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("trend_following.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("trend_following_runner")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trend Following Bot for HyperLiquid"
    )
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trading mode (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Live trading mode (requires .env)")
    parser.add_argument("--capital", type=float, default=10000,
                        help="Starting capital in USD (default: 10000)")
    parser.add_argument("--coins", type=str, default=None,
                        help="Comma-separated list of coins (default: all)")
    parser.add_argument("--interval", type=int, default=300,
                        help="Check interval in seconds (default: 300)")
    parser.add_argument("--leverage", type=int, default=3,
                        help="Leverage (default: 3)")
    parser.add_argument("--fast-ema", type=int, default=9,
                        help="Fast EMA period (default: 9)")
    parser.add_argument("--slow-ema", type=int, default=21,
                        help="Slow EMA period (default: 21)")
    parser.add_argument("--candle-tf", type=str, default="1h",
                        help="Candle timeframe (default: 1h)")
    parser.add_argument("--max-positions", type=int, default=3,
                        help="Max concurrent positions (default: 3)")
    parser.add_argument("--atr-stop", type=float, default=2.0,
                        help="ATR stop loss multiplier (default: 2.0)")
    parser.add_argument("--atr-tp", type=float, default=4.0,
                        help="ATR take profit multiplier (default: 4.0)")
    parser.add_argument("--trailing", type=float, default=2.5,
                        help="ATR trailing stop multiplier (default: 2.5)")
    parser.add_argument("--no-trailing", action="store_true",
                        help="Disable trailing stop")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def display_status(strategy: TrendFollowingStrategy) -> None:
    """Render rich status table."""
    status = strategy.get_status()

    # Header
    mode = "📄 PAPER" if status["paper_trading"] else "🔴 LIVE"
    capital = status.get("capital", 0)
    console.print(Panel(
        f"[bold]{mode}[/bold] | Capital: [green]${capital:,.2f}[/green] | "
        f"Cycle: {status['cycle']} | "
        f"PnL: {'[green]' if status['summary']['total_pnl'] >= 0 else '[red]'}"
        f"${status['summary']['total_pnl']:,.4f}[/]",
        title="📈 Trend Following Bot",
    ))

    # Open positions
    open_pos = status["positions"]["open"]
    if open_pos:
        table = Table(title="Open Positions")
        table.add_column("ID", style="cyan")
        table.add_column("Coin", style="bold")
        table.add_column("Side", style="green")
        table.add_column("Entry", justify="right")
        table.add_column("SL", justify="right", style="red")
        table.add_column("TP", justify="right", style="green")
        table.add_column("Trail", justify="right", style="yellow")
        table.add_column("Hold (h)", justify="right")

        for p in open_pos:
            table.add_row(
                p["id"],
                p["coin"],
                p["side"],
                f"${p['entry_price']:.2f}",
                f"${p['stop_loss']:.2f}",
                f"${p['take_profit']:.2f}",
                f"${p['trailing_stop']:.2f}",
                f"{p['hold_hours']:.1f}",
            )
        console.print(table)
    else:
        console.print("[dim]No open positions — waiting for signals…[/dim]")

    # Summary
    console.print(
        f"Closed: {status['summary']['closed_count']} | "
        f"Open: {status['summary']['open_count']} | "
        f"Total PnL: ${status['summary']['total_pnl']:,.4f}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    load_dotenv()
    args = parse_args()

    paper_mode = not args.live
    coins = [c.strip().upper() for c in args.coins.split(",")] if args.coins else None

    config = TrendFollowingConfig(
        PAPER_TRADING=paper_mode,
        PRIVATE_KEY=os.getenv("PRIVATE_KEY", ""),
        ADDRESS=os.getenv("ADDRESS", ""),
        ACCOUNT_ADDRESS=os.getenv("ACCOUNT_ADDRESS"),
        PAPER_CAPITAL=args.capital,
        COINS=coins,
        CHECK_INTERVAL=args.interval,
        LEVERAGE=args.leverage,
        FAST_EMA_PERIOD=args.fast_ema,
        SLOW_EMA_PERIOD=args.slow_ema,
        CANDLE_INTERVAL=args.candle_tf,
        MAX_CONCURRENT_POSITIONS=args.max_positions,
        ATR_STOP_MULT=args.atr_stop,
        ATR_TP_MULT=args.atr_tp,
        TRAILING_STOP_MULT=args.trailing,
        USE_TRAILING_STOP=not args.no_trailing,
        DATABASE_PATH="trend_following.db",
        API_URL="https://api.hyperliquid.xyz",
    )

    if not paper_mode:
        if not config.PRIVATE_KEY:
            console.print("[red]PRIVATE_KEY required for live mode![/red]")
            sys.exit(1)
        console.print("[bold red]⚠️  LIVE TRADING MODE ⚠️[/bold red]")

    config.validate()

    # Setup components
    bot_config = BotConfig()
    bot_config.PRIVATE_KEY = config.PRIVATE_KEY or (
        "0x0000000000000000000000000000000000000000000000000000000000000001"
    )
    bot_config.ADDRESS = config.ADDRESS or "0x0000000000000000000000000000000000000001"
    bot_config.USE_TESTNET = config.USE_TESTNET

    api = HyperliquidAPI(bot_config)
    db = DatabaseManager(config.DATABASE_PATH)

    strategy = TrendFollowingStrategy(config, api, db)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Received signal %s — shutting down…", sig)
        strategy.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    console.print(Panel(
        f"[bold green]Trend Following Bot Started[/bold green]\n"
        f"Mode: {'PAPER' if paper_mode else 'LIVE'}\n"
        f"Capital: ${args.capital:,.2f}\n"
        f"Coins: {', '.join(coins) if coins else 'ALL'}\n"
        f"Interval: {args.interval}s\n"
        f"EMA: {args.fast_ema}/{args.slow_ema} | TF: {args.candle_tf}\n"
        f"SL: {args.atr_stop}x ATR | TP: {args.atr_tp}x ATR | Trail: {args.trailing}x ATR",
        title="🚀 Config",
    ))

    # Run strategy in background
    strategy_task = asyncio.create_task(strategy.run())

    # Periodic status display
    try:
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            console.clear()
            display_status(strategy)
    except asyncio.CancelledError:
        pass

    await strategy_task
    db.close()
    console.print("[bold]Bot stopped.[/bold]")


if __name__ == "__main__":
    asyncio.run(main())
