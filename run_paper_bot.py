#!/usr/bin/env python3
"""
HYPE/USDC Paper Trading Bot

This uses MAINNET market data but SIMULATES trades.
No real orders, no real money - perfect for strategy validation!

Usage:
    python3 run_paper_bot.py                    # Start with default $10k
    HYPERLICUID_PAPER_CAPITAL=50000 python3 run_paper_bot.py  # Custom capital
"""

import asyncio
import os
import sys
import logging
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
        logging.FileHandler("hype_paper_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def create_paper_config(capital: float = 10000):
    """Create paper trading configuration"""
    config = BotConfig()

    # Use mainnet data (not testnet)
    config.USE_TESTNET = False
    config.PAPER_TRADING = True  # Simulate trades
    config.PAPER_CAPITAL = capital

    # Account (not needed for paper, but set dummy values)
    # Valid dummy key for eth_account
    config.PRIVATE_KEY = (
        "0x0000000000000000000000000000000000000000000000000000000000000001"
    )
    config.ADDRESS = "0x0000000000000000000000000000000000000001"

    # Strategy - same as mainnet
    config.ASSET = "HYPE"
    config.TIMEFRAME = "15m"
    config.LEVERAGE = 5

    # Strategy Parameters
    config.ROC_SHORT = 1
    config.ROC_LONG = 5
    config.MOMENTUM_THRESHOLD = 0.08
    config.CONFIDENCE_THRESHOLD = 45
    config.EMA_TREND_FILTER = 20

    # Risk Management (Balanced config - RECOMMENDED)
    config.RISK_PER_TRADE_PCT = 0.08  # 8% per trade (balanced)
    config.TP_ATR_MULTIPLIER = 2.0
    config.SL_ATR_MULTIPLIER = 0.4
    config.MAX_POSITIONS = 2  # Max 2 concurrent positions
    config.MAX_DAILY_TRADES = 20

    # Order Settings
    config.ORDER_TYPE = "limit"
    config.MIN_ORDER_SIZE = 10

    # Safety
    config.MAX_DAILY_LOSS_PCT = 0.15

    # Fees
    config.MAKER_FEE_PCT = -0.0002
    config.TAKER_FEE_PCT = 0.0004

    return config


async def run_paper_bot(capital: float = 10000):
    """Run the paper trading bot"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         HYPE/USDC PAPER TRADING BOT                           ║
║         Modular Architecture Edition                             ║
╠══════════════════════════════════════════════════════════════════╣
║  MODE: PAPER TRADING                                           ║
║  - Mainnet market data (real prices)                           ║
║  - Simulated trades (no real orders)                           ║
║  - NO real money at risk                                       ║
║                                                                ║
║  STRATEGY:                                                     ║
║  - Ultra-Optimized Momentum                                    ║
║  - Expected: 48.1% in 52 days                                 ║
║  - Max Drawdown: 3.0%                                         ║
║  - 8% risk per trade, 5x leverage, max 2 positions            ║
║                                                                ║
║  WEB DASHBOARD:                                                ║
║  The bot includes a built-in web dashboard!                   ║
║  1. Start the bot (this script)                               ║
║  2. In another terminal: make dashboard                        ║
║  3. Open http://localhost:8501 in your browser                ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Get capital from env or use default
    capital_env = os.environ.get("HYPERLICUID_PAPER_CAPITAL")
    if capital_env:
        capital = float(capital_env)

    # Create paper config
    config = create_paper_config(capital)

    logger.info("=" * 60)
    logger.info("PAPER TRADING BOT STARTING")
    logger.info("=" * 60)
    logger.info("Exchange: Mainnet (real market data)")
    logger.info("Mode: Paper Trading (simulated orders)")
    logger.info(f"Starting Capital: ${capital:,.2f}")
    logger.info(f"Asset: {config.ASSET}")
    logger.info(f"Timeframe: {config.TIMEFRAME}")
    logger.info(f"Leverage: {config.LEVERAGE}x")
    logger.info(f"Risk per trade: {config.RISK_PER_TRADE_PCT * 100}%")
    logger.info("✓ NO REAL MONEY - Paper trading only")
    logger.info("✓ Using mainnet data for realistic signals")
    logger.info("")
    logger.info("CONTROL COMMANDS:")
    logger.info("  Force close all positions: touch .force_close_positions")
    logger.info("  Or send signal: kill -USR1 $(pgrep -f run_paper_bot.py)")
    logger.info("  Reset circuit breaker: touch .reset_circuit_breaker")
    logger.info("  Or send signal: kill -USR2 $(pgrep -f run_paper_bot.py)")

    # Create and start bot
    bot = TradingBot(config)

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        bot.print_statistics()
    except Exception as e:
        logger.error(f"Bot error: {e}")
        bot.print_statistics()


if __name__ == "__main__":
    asyncio.run(run_paper_bot())
