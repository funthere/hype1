#!/usr/bin/env python3
"""
Cross-Exchange Funding Rate Arbitrage Runner
HyperLiquid vs Binance Futures  —  BTC, ETH, SOL

Starts the CrossExchangeArbStrategy in paper or mainnet mode.

Usage:
    # Paper mode (default):
    python3 run_cross_exchange_arb.py
    python3 run_cross_exchange_arb.py --config cross_exchange_arb_config.yaml

    # Mainnet mode (requires .env with PRIVATE_KEY):
    python3 run_cross_exchange_arb.py --live

    # Custom config:
    python3 run_cross_exchange_arb.py --config my_config.yaml --live
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from hyperliquid.info import Info

from src.core.safety import require_mainnet_release_approval
from src.exchange.binance_client import BinanceClient
from src.storage.database import DatabaseManager
from src.strategy.cross_exchange_arb import (
    CrossExchangeArbConfig,
    CrossExchangeArbStrategy,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

console = Console()

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(log_file: str, log_level: str = "INFO") -> None:
    """Configure logging to both file and console."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format=DEFAULT_LOG_FORMAT,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )


logger = logging.getLogger("cross_arb_runner")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-Exchange Funding Rate Arbitrage (HyperLiquid vs dYdX)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="cross_exchange_arb_config.yaml",
        help="Path to YAML config file (default: cross_exchange_arb_config.yaml)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Mainnet mode (requires .env credentials)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=False,
        help="Force paper mode",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def build_rate_table(
    hl_rates: dict,
    dydx_rates: dict,
    open_coins: set,
    config: CrossExchangeArbConfig,
) -> Table:
    """Build a Rich table showing rate comparison."""
    table = Table(
        title="📊 Funding Rate Comparison (per hour)",
        show_lines=True,
        title_style="bold cyan",
    )
    table.add_column("Coin", style="bold")
    table.add_column("HL Rate", justify="right")
    table.add_column("dYdX Rate", justify="right")
    table.add_column("Spread", justify="right")
    table.add_column("Signal", justify="center")
    table.add_column("Status", justify="center")

    for coin in config.COINS:
        hl = hl_rates.get(coin, {})
        dydx = dydx_rates.get(coin, {})

        hl_rate = hl.get("rate_hourly", 0.0)
        dydx_rate = dydx.get("rate_hourly", 0.0)
        spread = hl_rate - dydx_rate

        hl_style = "red" if hl_rate > 0 else "green"
        dydx_style = "red" if dydx_rate > 0 else "green"
        spread_style = "yellow" if abs(spread) >= config.ENTRY_THRESHOLD else ""

        # Signal
        if abs(spread) >= config.ENTRY_THRESHOLD:
            if spread > 0:
                signal = "SHORT HL / LONG dYdX"
            else:
                signal = "LONG HL / SHORT dYdX"
            signal_style = "bold yellow"
        else:
            signal = "—"
            signal_style = ""

        # Status
        status = "🟢 ACTIVE" if coin in open_coins else ""
        status_style = "green" if coin in open_coins else ""

        table.add_row(
            coin,
            f"[{hl_style}]{hl_rate * 100:.4f}%[/{hl_style}]",
            f"[{dydx_style}]{dydx_rate * 100:.4f}%[/{dydx_style}]",
            f"[{spread_style}]{spread * 100:.4f}%[/{spread_style}]",
            f"[{signal_style}]{signal}[/{signal_style}]" if signal_style else signal,
            f"[{status_style}]{status}[/{status_style}]" if status_style else status,
        )

    return table


def build_positions_table(status: dict) -> Table:
    """Build a table showing open arb positions."""
    table = Table(
        title="🔀 Open Arb Positions",
        show_lines=True,
        title_style="bold green",
    )
    table.add_column("ID", style="dim")
    table.add_column("Coin", style="bold")
    table.add_column("Direction", justify="center")
    table.add_column("HL Qty", justify="right")
    table.add_column("dYdX Qty", justify="right")
    table.add_column("Entry Spread", justify="right")
    table.add_column("Funding $", justify="right", style="green")
    table.add_column("Hold (h)", justify="right")

    for p in status["positions"]["open"]:
        spread_display = f"{p['entry_spread'] * 100:.4f}%"
        funding_display = (
            f"+${p['funding_collected']:.4f}"
            if p["funding_collected"] >= 0
            else f"-${abs(p['funding_collected']):.4f}"
        )
        table.add_row(
            p["id"],
            p["coin"],
            f"HL:{p['hl_side']} / dYdX:{p['dydx_side']}",
            f"{p['hl_quantity']:.6f}",
            f"{p['dydx_quantity']:.6f}",
            spread_display,
            funding_display,
            f"{p['hold_hours']:.1f}",
        )

    if not status["positions"]["open"]:
        table.add_row("—", "No open positions", "", "", "", "", "", "")

    return table


def build_summary_panel(
    strategy: CrossExchangeArbStrategy,
    config: CrossExchangeArbConfig,
) -> Panel:
    """Build a summary panel."""
    status = strategy.get_status()
    summary = status["summary"]

    mode_label = "[yellow]PAPER[/yellow]" if config.PAPER_TRADING else "[red]LIVE[/red]"
    capital_str = (
        f"${status['capital']:,.2f}" if status["capital"] is not None else "N/A (live)"
    )

    text = Text()
    text.append("  Mode: ", style="bold")
    text.append(f"{mode_label}\n")
    text.append("  Cycle: ")
    text.append(f"{status['cycle']}\n")
    text.append("  Capital: ")
    text.append(f"{capital_str}\n")
    text.append("  Open Positions: ")
    text.append(f"{summary['open_count']}\n", style="bold yellow")
    text.append("  Closed Positions: ")
    text.append(f"{summary['closed_count']}\n")
    text.append("  Total PnL: ")
    pnl_style = "green" if summary["total_pnl"] >= 0 else "red"
    text.append(f"${summary['total_pnl']:.4f}\n", style=pnl_style)
    text.append("  Total Funding Collected: ")
    text.append(f"${summary['total_funding_collected']:.6f}\n", style="green")
    text.append("  Total Fees: ")
    text.append(f"${summary['total_fees']:.4f}\n", style="red")
    text.append("  Coins: ")
    text.append(f"{', '.join(config.COINS)}\n")
    text.append("  Entry Threshold: ")
    text.append(f"{config.ENTRY_THRESHOLD * 100:.4f}%/hr\n")
    text.append("  Exit Threshold: ")
    text.append(f"{config.EXIT_THRESHOLD * 100:.4f}%/hr\n")
    text.append("  Scan Interval: ")
    text.append(f"{config.SCAN_INTERVAL}s\n")

    return Panel(text, title="⚡ Cross-Exchange Arb Summary", border_style="cyan")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_strategy(config: CrossExchangeArbConfig) -> None:
    """Initialise and run the cross-exchange arb strategy."""
    console.print(
        Panel(
            "[bold cyan]⚡ Cross-Exchange Funding Rate Arbitrage[/bold cyan]\n"
            "[dim]HyperLiquid vs Binance Futures[/dim]\n\n"
            f"  Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'}\n"
            f"  Capital: ${config.PAPER_CAPITAL:,.2f}\n"
            f"  Entry Threshold: {config.ENTRY_THRESHOLD * 100:.4f}%/hr\n"
            f"  Exit Threshold: {config.EXIT_THRESHOLD * 100:.4f}%/hr\n"
            f"  Leverage: {config.LEVERAGE}x\n"
            f"  Max Positions: {config.MAX_CONCURRENT_POSITIONS}\n"
            f"  Coins: {', '.join(config.COINS)}\n"
            f"  Scan Interval: {config.SCAN_INTERVAL}s\n\n"
            "[dim]Press Ctrl+C to stop gracefully.[/dim]",
            title="Starting",
            border_style="green",
        )
    )

    # --- Initialise HyperLiquid Info (read-only for data) ---
    hl_info = Info(config.HL_BASE_URL, skip_ws=True)

    # --- Initialise Binance client ---
    binance = BinanceClient(base_url=config.BINANCE_BASE_URL)

    # Health check
    binance_ok = await binance.healthcheck()
    if binance_ok:
        logger.info("Binance Futures API reachable at %s", config.BINANCE_BASE_URL)
    else:
        logger.warning("Binance Futures API NOT reachable – continuing anyway")

    # --- Initialise database ---
    db = DatabaseManager(config.DATABASE_PATH)

    # --- Create strategy ---
    strategy = CrossExchangeArbStrategy(config, hl_info, db)
    strategy.set_binance_client(binance)

    # --- Graceful shutdown ---
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, frame: object) -> None:
        logger.info("Received signal %d – shutting down", sig)
        strategy.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # --- Display loop ---
    latest_hl_rates: dict = {}
    latest_dydx_rates: dict = {}

    async def _display_loop() -> None:
        nonlocal latest_hl_rates, latest_dydx_rates
        while not shutdown_event.is_set():
            try:
                latest_hl_rates = await strategy.fetch_hl_funding_rates()
                latest_dydx_rates = await strategy.fetch_binance_funding_rates()

                open_coins = {
                    p["coin"] for p in strategy.get_status()["positions"]["open"]
                }

                console.clear()
                console.print(build_summary_panel(strategy, config))
                console.print(
                    build_rate_table(
                        latest_hl_rates, latest_dydx_rates, open_coins, config
                    )
                )
                console.print(build_positions_table(strategy.get_status()))
                console.print(
                    f"\n[dim]Last update: "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
                    f"  |  Next scan in {config.SCAN_INTERVAL}s[/dim]"
                )
            except Exception as exc:
                logger.error("Display refresh error: %s", exc)

            await asyncio.sleep(min(config.SCAN_INTERVAL, 30))

    # --- Run ---
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

        # Cleanup
        await binance.close()
        db.close()

        # Final status
        final = strategy.get_status()
        console.print("\n")
        console.print(
            Panel(
                "[bold]Strategy stopped.[/bold]\n"
                f"  Total cycles: {final['cycle']}\n"
                f"  Total PnL: ${final['summary']['total_pnl']:.4f}\n"
                f"  Total Funding: ${final['summary']['total_funding_collected']:.6f}\n"
                f"  Total Fees: ${final['summary']['total_fees']:.4f}",
                title="Shutdown Complete",
                border_style="yellow",
            )
        )

        logger.info("Cross-exchange arb runner exited cleanly")


def main() -> None:
    args = parse_args()

    if args.live:
        try:
            require_mainnet_release_approval("Cross-exchange arbitrage strategy")
        except RuntimeError as exc:
            console.print(f"[bold red]{exc}[/bold red]")
            return

    # Locate config file
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).parent / config_path

    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        console.print("Create one from cross_exchange_arb_config.yaml.example")
        sys.exit(1)

    # Load YAML config
    try:
        config = CrossExchangeArbConfig.from_yaml(str(config_path))
    except Exception as exc:
        console.print(f"[red]Failed to load config: {exc}[/red]")
        sys.exit(1)

    # CLI overrides
    if args.live:
        config.PAPER_TRADING = False
    elif args.paper:
        config.PAPER_TRADING = True

    # Load .env for credentials (mainnet mode)
    load_dotenv()
    if not config.PAPER_TRADING:
        private_key = os.environ.get("PRIVATE_KEY", "")
        if not private_key:
            console.print("[red]Live mode requires PRIVATE_KEY in .env[/red]")
            sys.exit(1)
        config.PRIVATE_KEY = private_key
        config.ADDRESS = os.environ.get("ADDRESS", "")

    # Validate
    try:
        config.validate()
    except ValueError as exc:
        console.print(f"[red]Config validation error: {exc}[/red]")
        sys.exit(1)

    # Setup logging (after config loaded so we have log_file)
    setup_logging(config.LOG_FILE)

    logger.info("Config loaded from %s", config_path)
    logger.info(
        "Mode: %s | Coins: %s",
        "PAPER" if config.PAPER_TRADING else "LIVE",
        ", ".join(config.COINS),
    )

    try:
        asyncio.run(run_strategy(config))
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")


if __name__ == "__main__":
    main()
