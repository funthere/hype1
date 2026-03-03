#!/usr/bin/env python3
"""
Modular HYPE Trading Bot - New Entry Point

Usage:
    # Paper trading (default)
    python run_modular_bot.py

    # Testnet trading
    python run_modular_bot.py --mode testnet

    # Mainnet trading
    python run_modular_bot.py --mode mainnet

    # With custom config file
    python run_modular_bot.py --config config.yaml
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.config import BotConfig
from src.bot.trading_bot import TradingBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("hype_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="HYPE Trading Bot")
    parser.add_argument(
        "--mode",
        choices=["paper", "testnet", "mainnet"],
        default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--asset",
        default="HYPE",
        help="Asset to trade (default: HYPE)",
    )
    parser.add_argument(
        "--timeframe",
        default="15m",
        help="Trading timeframe (default: 15m)",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=5,
        help="Leverage multiplier (default: 5)",
    )
    parser.add_argument(
        "--risk",
        type=float,
        default=0.08,
        help="Risk per trade as decimal (default: 0.08 = 8%%)",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable web UI",
    )
    parser.add_argument(
        "--config",
        help="Path to YAML config file",
    )
    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_args()

    # Load config
    if args.config:
        # TODO: Implement YAML config loading
        logger.warning("YAML config not implemented yet, using environment + CLI args")

    config = BotConfig.from_env()

    # Override with CLI args
    config.PAPER_TRADING = args.mode == "paper"
    config.USE_TESTNET = args.mode == "testnet"

    if args.no_ui:
        config.WEB_UI_ENABLED = False

    config.ASSET = args.asset
    config.TIMEFRAME = args.timeframe
    config.LEVERAGE = args.leverage
    config.RISK_PER_TRADE_PCT = args.risk

    # Validate config
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Create and start bot
    bot = TradingBot(config)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal...")
        await bot.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await bot.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
