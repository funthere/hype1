#!/usr/bin/env python3
"""
Mean Reversion Bot Runner

Bollinger Band RSI Mean Reversion strategy for HyperLiquid perpetuals.

Usage:
    # Paper mode (default):
    python3 run_mean_reversion.py
    python3 run_mean_reversion.py --paper --capital 10000
    python3 run_mean_reversion.py --paper --coins BTC,ETH,SOL,HYPE

    # Live mode:
    python3 run_mean_reversion.py --live --capital 5000
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
from src.strategy.mean_reversion import (
    MeanReversionConfig,
    MeanReversionStrategy,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("mean_reversion.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("mean_reversion_runner")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mean Reversion Bot for HyperLiquid"
    )
    # Mode
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trading mode (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Live trading mode (requires .env)")
    # Capital & assets
    parser.add_argument("--capital", type=float, default=10000,
                        help="Starting capital in USD (default: 10000)")
    parser.add_argument("--coins", type=str, default=None,
                        help="Comma-separated list of coins (default: all)")
    # Intervals
    parser.add_argument("--interval", type=int, default=60,
                        help="Check interval in seconds (default: 60)")
    parser.add_argument("--candle-tf", type=str, default="15m",
                        help="Candle timeframe (default: 15m)")
    parser.add_argument("--htf-interval", type=str, default="1h",
                        help="Higher timeframe for trend filter (default: 1h)")
    # Bollinger Bands
    parser.add_argument("--bb-period", type=int, default=20,
                        help="Bollinger Band SMA period (default: 20)")
    parser.add_argument("--bb-std", type=float, default=2.5,
                        help="BB std dev multiplier for entry (default: 2.5)")
    # RSI
    parser.add_argument("--rsi-period", type=int, default=14,
                        help="RSI period (default: 14)")
    parser.add_argument("--rsi-overbought", type=float, default=75.0,
                        help="RSI overbought level (default: 75)")
    parser.add_argument("--rsi-oversold", type=float, default=25.0,
                        help="RSI oversold level (default: 25)")
    # Position sizing
    parser.add_argument("--leverage", type=int, default=5,
                        help="Leverage (default: 5)")
    parser.add_argument("--position-size", type=float, default=0.08,
                        help="Position size as fraction of capital (default: 0.08)")
    parser.add_argument("--max-positions", type=int, default=4,
                        help="Max concurrent positions (default: 4)")
    # Stops
    parser.add_argument("--atr-stop", type=float, default=1.5,
                        help="ATR stop loss multiplier (default: 1.5)")
    parser.add_argument("--atr-tp", type=float, default=2.5,
                        help="ATR take profit multiplier (default: 2.5)")
    # Partial TP
    parser.add_argument("--no-partial-tp", action="store_true",
                        help="Disable partial take profit at mean")
    parser.add_argument("--partial-tp-pct", type=float, default=0.50,
                        help="Partial TP percentage (default: 0.50)")
    # Trailing
    parser.add_argument("--no-trailing", action="store_true",
                        help="Disable trailing stop")
    parser.add_argument("--trailing-activation", type=float, default=1.0,
                        help="ATR multiplier to activate trailing (default: 1.0)")
    parser.add_argument("--trailing-step", type=float, default=0.5,
                        help="ATR multiplier for trailing step (default: 0.5)")
    # Trend filter
    parser.add_argument("--no-trend-filter", action="store_true",
                        help="Disable HTF trend filter")
    parser.add_argument("--htf-fast-ema", type=int, default=9,
                        help="HTF fast EMA period (default: 9)")
    parser.add_argument("--htf-slow-ema", type=int, default=21,
                        help="HTF slow EMA period (default: 21)")
    # VWAP
    parser.add_argument("--no-vwap", action="store_true",
                        help="Disable VWAP filter")
    # Volume
    parser.add_argument("--no-volume-filter", action="store_true",
                        help="Disable volume spike requirement")
    parser.add_argument("--volume-mult", type=float, default=1.3,
                        help="Volume spike multiplier (default: 1.3)")
    # Limits
    parser.add_argument("--max-hold", type=int, default=32,
                        help="Max hold in candles (default: 32)")
    parser.add_argument("--max-loss", type=float, default=0.04,
                        help="Max loss per position as fraction (default: 0.04)")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def display_status(strategy: MeanReversionStrategy) -> None:
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
        title="📊 Mean Reversion Bot",
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
        table.add_column("Mean", justify="right", style="yellow")
        table.add_column("Trail", justify="right", style="yellow")
        table.add_column("Partial", justify="right")
        table.add_column("Hold (h)", justify="right")

        for p in open_pos:
            table.add_row(
                p["id"],
                p["coin"],
                p["side"],
                f"${p['entry_price']:.2f}",
                f"${p['stop_loss']:.2f}",
                f"${p['take_profit']:.2f}",
                f"${p['mean_target']:.2f}",
                f"${p['trailing_stop']:.2f}",
                "✅" if p["partial_tp_taken"] else "—",
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

    config = MeanReversionConfig(
        PAPER_TRADING=paper_mode,
        PRIVATE_KEY=os.getenv("PRIVATE_KEY", ""),
        ADDRESS=os.getenv("ADDRESS", ""),
        ACCOUNT_ADDRESS=os.getenv("ACCOUNT_ADDRESS"),
        PAPER_CAPITAL=args.capital,
        COINS=coins,
        CHECK_INTERVAL=args.interval,
        CANDLE_INTERVAL=args.candle_tf,
        HTF_INTERVAL=args.htf_interval,
        # BB
        BB_PERIOD=args.bb_period,
        BB_STD_MULT_ENTRY=args.bb_std,
        # RSI
        RSI_PERIOD=args.rsi_period,
        RSI_OVERBOUGHT=args.rsi_overbought,
        RSI_OVERSOLD=args.rsi_oversold,
        # Position sizing
        LEVERAGE=args.leverage,
        POSITION_SIZE_PCT=args.position_size,
        MAX_CONCURRENT_POSITIONS=args.max_positions,
        # Stops
        ATR_STOP_MULT=args.atr_stop,
        ATR_TP_MULT=args.atr_tp,
        # Partial TP
        PARTIAL_TP_ENABLED=not args.no_partial_tp,
        PARTIAL_TP_PCT=args.partial_tp_pct,
        # Trailing
        TRAILING_STOP_ENABLED=not args.no_trailing,
        TRAILING_ACTIVATION_ATR=args.trailing_activation,
        TRAILING_STEP_ATR=args.trailing_step,
        # Trend filter
        TREND_FILTER_ENABLED=not args.no_trend_filter,
        HTF_EMA_FAST=args.htf_fast_ema,
        HTF_EMA_SLOW=args.htf_slow_ema,
        # VWAP
        VWAP_ENABLED=not args.no_vwap,
        # Volume
        REQUIRE_VOLUME_SPIKE=not args.no_volume_filter,
        VOLUME_SPIKE_MULT=args.volume_mult,
        # Limits
        MAX_HOLD_CANDLES=args.max_hold,
        MAX_LOSS_PCT=args.max_loss,
        # DB & API
        DATABASE_PATH="mean_reversion.db",
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

    strategy = MeanReversionStrategy(config, api, db)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Received signal %s — shutting down…", sig)
        strategy.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    console.print(Panel(
        f"[bold green]Mean Reversion Bot Started[/bold green]\n"
        f"Mode: {'PAPER' if paper_mode else 'LIVE'}\n"
        f"Capital: ${args.capital:,.2f}\n"
        f"Coins: {', '.join(coins) if coins else 'ALL'}\n"
        f"Interval: {args.interval}s | TF: {args.candle_tf}\n"
        f"BB: {args.bb_period}/{args.bb_std}σ | RSI: {args.rsi_period} ({args.rsi_oversold}/{args.rsi_overbought})\n"
        f"SL: {args.atr_stop}x ATR | TP: {args.atr_tp}x ATR | Trail: {args.trailing_activation}/{args.trailing_step}x ATR\n"
        f"Partial TP: {'ON' if not args.no_partial_tp else 'OFF'} ({args.partial_tp_pct*100:.0f}%)\n"
        f"Trend Filter: {'ON' if not args.no_trend_filter else 'OFF'} | VWAP: {'ON' if not args.no_vwap else 'OFF'}",
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
