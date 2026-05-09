#!/usr/bin/env python3
"""
Funding Rate Arbitrage Runner

Starts the FundingRateArbStrategy in paper or live mode.

Usage:
    # Paper mode (default, no API keys needed):
    python3 run_funding_arb.py
    python3 run_funding_arb.py --paper --capital 50000
    python3 run_funding_arb.py --paper --coins BTC,ETH,SOL

    # Live mode (requires .env with PRIVATE_KEY / ADDRESS):
    python3 run_funding_arb.py --live
    python3 run_funding_arb.py --live --capital 50000
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.core.config import BotConfig
from src.exchange.connector import HyperliquidAPI
from src.storage.database import DatabaseManager
from src.strategy.funding_rate_arb import (
    FundingArbConfig,
    FundingRateArbStrategy,
    PositionSide,
    PositionStatus,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("funding_arb.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("funding_arb_runner")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Funding Rate Arbitrage Bot for HyperLiquid"
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Paper trading mode (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Live trading mode (requires .env credentials)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10_000,
        help="Starting capital for paper mode (default: 10000)",
    )
    parser.add_argument(
        "--coins",
        type=str,
        default=None,
        help="Comma-separated list of coins to monitor (default: ALL)",
    )
    parser.add_argument(
        "--entry-threshold",
        type=float,
        default=0.0003,
        help="Entry threshold per 8h (default: 0.0003 = 0.03%%)",
    )
    parser.add_argument(
        "--exit-threshold",
        type=float,
        default=0.0001,
        help="Exit threshold per 8h (default: 0.0001 = 0.01%%)",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=3,
        help="Leverage for positions (default: 3)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=3,
        help="Max concurrent positions (default: 3)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Scan interval in seconds (default: 300)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


def build_config(args: argparse.Namespace) -> FundingArbConfig:
    """Build a :class:`FundingArbConfig` from CLI args and environment."""
    load_dotenv()

    paper_mode = not args.live
    if os.environ.get("PAPER_TRADING", "").lower() in ("true", "1", "yes"):
        paper_mode = True

    use_testnet = os.environ.get("USE_TESTNET", "").lower() in ("true", "1", "yes")

    # Determine API URL
    if use_testnet:
        api_url = "https://api.hyperliquid-testnet.xyz"
    else:
        api_url = "https://api.hyperliquid.xyz"

    coins: Optional[list] = None
    if args.coins:
        coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]

    config = FundingArbConfig(
        PAPER_TRADING=paper_mode,
        USE_TESTNET=use_testnet,
        PRIVATE_KEY=os.environ.get("PRIVATE_KEY", ""),
        ADDRESS=os.environ.get("ADDRESS", ""),
        ACCOUNT_ADDRESS=os.environ.get("ACCOUNT_ADDRESS"),
        PAPER_CAPITAL=args.capital,
        ENTRY_THRESHOLD=args.entry_threshold,
        EXIT_THRESHOLD=args.exit_threshold,
        LEVERAGE=args.leverage,
        MAX_CONCURRENT_POSITIONS=args.max_positions,
        CHECK_INTERVAL=args.interval,
        COINS=coins,
        API_URL=api_url,
    )

    # Live mode needs a valid key
    if not paper_mode:
        if not config.PRIVATE_KEY:
            logger.error(
                "Live mode requires PRIVATE_KEY in .env or environment"
            )
            sys.exit(1)
    else:
        # Dummy key for paper mode SDK init
        config.PRIVATE_KEY = (
            "0x0000000000000000000000000000000000000000000000000000000000000001"
        )
        config.ADDRESS = "0x0000000000000000000000000000000000000001"

    config.validate()
    return config


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def build_status_table(
    strategy: FundingRateArbStrategy,
    latest_rates: list,
) -> Table:
    """Build a Rich table summarising strategy state."""
    status = strategy.get_status()
    summary = status["summary"]

    # --- Main status table ---
    table = Table(
        title="⚡ Funding Rate Arbitrage",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("Coin", style="bold")
    table.add_column("Funding Rate", justify="right")
    table.add_column("Position", justify="center")
    table.add_column("Side", justify="center")
    table.add_column("Entry Px", justify="right")
    table.add_column("Funding Collected", justify="right", style="green")
    table.add_column("Hold (h)", justify="right")
    table.add_column("PnL", justify="right")

    # Build lookup for open positions
    open_map = {}
    for p in status["positions"]["open"]:
        open_map[p["coin"]] = p

    # Show open positions first (sorted by coin)
    shown_coins = set()
    for p in status["positions"]["open"]:
        shown_coins.add(p["coin"])
        rate_display = f"{p['entry_rate'] * 100:.4f}%"
        pnl_display = f"+{p['funding_collected']:.6f}" if p["funding_collected"] >= 0 else f"{p['funding_collected']:.6f}"
        table.add_row(
            p["coin"],
            rate_display,
            "[green]OPEN[/green]",
            p["side"],
            f"${p['entry_price']:.2f}",
            f"${p['funding_collected']:.6f}",
            f"{p['hold_hours']:.1f}",
            pnl_display,
        )

    # Show top funding rate opportunities (that don't have open positions)
    for r in latest_rates[:10]:
        if r["coin"] in shown_coins:
            continue
        rate = r["funding_rate"]
        rate_display = f"{rate * 100:.4f}%"
        rate_style = "red" if rate > 0 else "green"

        # Indicate if it's above threshold
        if abs(rate) >= strategy.config.ENTRY_THRESHOLD:
            signal = "→ SHORT" if rate > 0 else "→ LONG"
            pos_display = f"[yellow]{signal}[/yellow]"
        else:
            pos_display = "—"

        table.add_row(
            r["coin"],
            f"[{rate_style}]{rate_display}[/{rate_style}]",
            pos_display,
            "—",
            f"${r['mark_px']:.2f}",
            "—",
            "—",
            "—",
        )

    return table


def build_summary_panel(
    strategy: FundingRateArbStrategy,
    config: FundingArbConfig,
) -> Panel:
    """Build a Rich panel with summary statistics."""
    status = strategy.get_status()
    summary = status["summary"]

    mode_label = "[yellow]PAPER[/yellow]" if config.PAPER_TRADING else "[red]LIVE[/red]"
    capital_str = (
        f"${status['capital']:,.2f}"
        if status["capital"] is not None
        else "N/A (live)"
    )

    text = Text()
    text.append(f"  Mode: ", style="bold")
    text.append(f"{mode_label}\n")
    text.append(f"  Cycle: ")
    text.append(f"{status['cycle']}\n")
    text.append(f"  Capital: ")
    text.append(f"{capital_str}\n")
    text.append(f"  Open Positions: ")
    text.append(f"{summary['open_count']}\n", style="bold yellow")
    text.append(f"  Closed Positions: ")
    text.append(f"{summary['closed_count']}\n")
    text.append(f"  Total PnL: ")
    pnl_style = "green" if summary["total_pnl"] >= 0 else "red"
    text.append(f"${summary['total_pnl']:.4f}\n", style=pnl_style)
    text.append(f"  Total Funding Collected: ")
    text.append(f"${summary['total_funding_collected']:.6f}\n", style="green")
    if config.COINS:
        text.append(f"  Coins: ")
        text.append(f"{', '.join(config.COINS)}\n")
    else:
        text.append(f"  Coins: ALL\n")
    text.append(f"  Entry Threshold: ")
    text.append(f"{config.ENTRY_THRESHOLD * 100:.4f}%\n")
    text.append(f"  Exit Threshold: ")
    text.append(f"{config.EXIT_THRESHOLD * 100:.4f}%\n")
    text.append(f"  Leverage: ")
    text.append(f"{config.LEVERAGE}x\n")
    text.append(f"  Scan Interval: ")
    text.append(f"{config.CHECK_INTERVAL}s\n")

    return Panel(text, title="📊 Strategy Summary", border_style="cyan")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_strategy(config: FundingArbConfig) -> None:
    """Initialise dependencies and start the strategy loop."""
    console.print(
        Panel(
            "[bold cyan]⚡ Funding Rate Arbitrage Bot[/bold cyan]\n\n"
            f"  Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'}\n"
            f"  Capital: ${config.PAPER_CAPITAL:,.2f}\n"
            f"  Entry Threshold: {config.ENTRY_THRESHOLD * 100:.4f}%\n"
            f"  Exit Threshold: {config.EXIT_THRESHOLD * 100:.4f}%\n"
            f"  Leverage: {config.LEVERAGE}x\n"
            f"  Max Positions: {config.MAX_CONCURRENT_POSITIONS}\n"
            f"  Coins: {', '.join(config.COINS) if config.COINS else 'ALL'}\n"
            f"  Scan Interval: {config.CHECK_INTERVAL}s\n\n"
            "[dim]Press Ctrl+C to stop gracefully.[/dim]",
            title="Starting",
            border_style="green",
        )
    )

    # --- Initialise API connector ---
    bot_config = BotConfig(
        PAPER_TRADING=config.PAPER_TRADING,
        USE_TESTNET=config.USE_TESTNET,
        PRIVATE_KEY=config.PRIVATE_KEY,
        ADDRESS=config.ADDRESS,
        ACCOUNT_ADDRESS=config.ACCOUNT_ADDRESS,
        PAPER_CAPITAL=config.PAPER_CAPITAL,
        LEVERAGE=config.LEVERAGE,
    )
    api = HyperliquidAPI(bot_config)

    # --- Initialise database ---
    db = DatabaseManager(config.DATABASE_PATH)

    # --- Create strategy ---
    strategy = FundingRateArbStrategy(config, api, db)

    # --- Graceful shutdown handler ---
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, frame: object) -> None:
        logger.info("Received signal %d – shutting down", sig)
        strategy.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # --- Run with live display ---
    latest_rates: list = []

    async def _display_loop() -> None:
        """Periodically refresh the Rich console display."""
        nonlocal latest_rates
        while not shutdown_event.is_set():
            try:
                # Fetch latest rates for display
                rates = await strategy.scan_funding_rates()
                if rates:
                    latest_rates = rates
                    latest_rates.sort(
                        key=lambda r: abs(r["funding_rate"]), reverse=True
                    )

                console.clear()
                console.print(build_summary_panel(strategy, config))
                console.print(build_status_table(strategy, latest_rates))
                console.print(
                    f"\n[dim]Last update: "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
                    f"  |  Next scan in {config.CHECK_INTERVAL}s[/dim]"
                )
            except Exception as exc:
                logger.error("Display refresh error: %s", exc)

            await asyncio.sleep(min(config.CHECK_INTERVAL, 30))

    # Start strategy and display concurrently
    strategy_task = asyncio.create_task(strategy.run())
    display_task = asyncio.create_task(_display_loop())

    try:
        await shutdown_event.wait()
    finally:
        strategy.stop()
        strategy_task.cancel()
        display_task.cancel()
        try:
            await strategy_task
        except asyncio.CancelledError:
            pass
        try:
            await display_task
        except asyncio.CancelledError:
            pass

        # Final status
        console.print("\n")
        console.print(
            Panel(
                "[bold]Strategy stopped.[/bold]\n"
                f"  Total cycles: {strategy.get_status()['cycle']}\n"
                f"  Total PnL: ${strategy.get_status()['summary']['total_pnl']:.4f}\n"
                f"  Total Funding: ${strategy.get_status()['summary']['total_funding_collected']:.6f}",
                title="Shutdown Complete",
                border_style="yellow",
            )
        )

        db.close()
        logger.info("Funding arb runner exited cleanly")


def main() -> None:
    """Entry point."""
    args = parse_args()
    # --live overrides --paper
    if args.live:
        args.paper = False

    config = build_config(args)

    try:
        asyncio.run(run_strategy(config))
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")


if __name__ == "__main__":
    main()
