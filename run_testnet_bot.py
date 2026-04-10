#!/usr/bin/env python3
"""
HYPE/USDC Testnet Trading Bot

Testnet trading uses real orders but testnet funds - no real money at risk!

Usage:
    python3 run_testnet_bot.py

Requirements:
    - .env file with PRIVATE_KEY and ADDRESS
    - Testnet funds (get from https://testnet.hyperliquid.xyz/)
"""

import asyncio
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
        logging.FileHandler("hype_testnet_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def create_testnet_config():
    """Create testnet trading configuration"""
    config = BotConfig()

    # Use testnet
    config.USE_TESTNET = True
    config.PAPER_TRADING = False

    # Trading parameters
    config.ASSET = "HYPE"
    config.TIMEFRAME = "15m"
    config.LEVERAGE = 5

    # Strategy Parameters
    config.ROC_SHORT = 1
    config.ROC_LONG = 5
    config.MOMENTUM_THRESHOLD = 0.08
    config.CONFIDENCE_THRESHOLD = 45
    config.EMA_TREND_FILTER = 20

    # Risk Management
    config.RISK_PER_TRADE_PCT = 0.08
    config.TP_ATR_MULTIPLIER = 2.0
    config.SL_ATR_MULTIPLIER = 0.4
    config.MAX_POSITIONS = 2
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


async def run_testnet_bot():
    """Run the testnet trading bot"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         HYPE/USDC TESTNET TRADING BOT                         ║
║         Modular Architecture Edition                             ║
╠══════════════════════════════════════════════════════════════════╣
║  MODE: TESTNET TRADING                                        ║
║  - Testnet exchange (real orders, testnet funds)              ║
║  - NO real money at risk                                      ║
║  - Perfect for testing strategies before mainnet               ║
║                                                                ║
║  STRATEGY:                                                     ║
║  - Ultra-Optimized Momentum                                    ║
║  - 8% risk per trade, 5x leverage, max 2 positions            ║
║                                                                ║
║  REQUIREMENTS:                                                 ║
║  - .env file with PRIVATE_KEY and ADDRESS                      ║
║  - Testnet funds from https://testnet.hyperliquid.xyz/         ║
║  - Testnet markets may be illiquid                             ║
║                                                                ║
║  WEB DASHBOARD:                                                ║
║  1. Start the bot (this script)                               ║
║  2. In another terminal: make dashboard                        ║
║  3. Open http://localhost:8501 in your browser                ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Load config from environment
    config = BotConfig.from_env()
    config.USE_TESTNET = True
    config.PAPER_TRADING = False

    # Validate credentials
    if not config.PRIVATE_KEY or not config.ADDRESS:
        logger.error("❌ Error: PRIVATE_KEY and ADDRESS must be set in .env file")
        logger.error("   Copy .env.example to .env and fill in your credentials")
        sys.exit(1)

    # Override with testnet defaults
    testnet_config = create_testnet_config()
    config.ASSET = testnet_config.ASSET
    config.TIMEFRAME = testnet_config.TIMEFRAME
    config.LEVERAGE = testnet_config.LEVERAGE
    config.ROC_SHORT = testnet_config.ROC_SHORT
    config.ROC_LONG = testnet_config.ROC_LONG
    config.MOMENTUM_THRESHOLD = testnet_config.MOMENTUM_THRESHOLD
    config.CONFIDENCE_THRESHOLD = testnet_config.CONFIDENCE_THRESHOLD
    config.EMA_TREND_FILTER = testnet_config.EMA_TREND_FILTER
    config.RISK_PER_TRADE_PCT = testnet_config.RISK_PER_TRADE_PCT
    config.TP_ATR_MULTIPLIER = testnet_config.TP_ATR_MULTIPLIER
    config.SL_ATR_MULTIPLIER = testnet_config.SL_ATR_MULTIPLIER
    config.MAX_POSITIONS = testnet_config.MAX_POSITIONS
    config.MAX_DAILY_TRADES = testnet_config.MAX_DAILY_TRADES
    config.ORDER_TYPE = testnet_config.ORDER_TYPE
    config.MIN_ORDER_SIZE = testnet_config.MIN_ORDER_SIZE
    config.MAX_DAILY_LOSS_PCT = testnet_config.MAX_DAILY_LOSS_PCT

    logger.info("=" * 60)
    logger.info("TESTNET TRADING BOT STARTING")
    logger.info("=" * 60)
    logger.info(f"Exchange: Testnet")
    logger.info(f"Mode: Real orders with testnet funds")
    logger.info(f"Asset: {config.ASSET}")
    logger.info(f"Timeframe: {config.TIMEFRAME}")
    logger.info(f"Leverage: {config.LEVERAGE}x")
    logger.info(f"Risk per trade: {config.RISK_PER_TRADE_PCT * 100}%")
    logger.info("✓ NO REAL MONEY - Testnet trading only")
    logger.info("⚠️  Testnet markets may be illiquid")
    logger.info("")
    logger.info("CONTROL COMMANDS:")
    logger.info("  Force close all positions: touch .force_close_positions")
    logger.info("  Or send signal: kill -USR1 $(pgrep -f run_testnet_bot.py)")
    logger.info("  Reset circuit breaker: touch .reset_circuit_breaker")
    logger.info("  Or send signal: kill -USR2 $(pgrep -f run_testnet_bot.py)")

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
    asyncio.run(run_testnet_bot())