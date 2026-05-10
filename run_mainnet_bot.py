#!/usr/bin/env python3
"""
HYPE/USDC Mainnet Trading Bot

⚠️  WARNING: This uses REAL MONEY! Use with extreme caution!

Usage:
    python3 run_mainnet_bot.py

Requirements:
    - .env file with PRIVATE_KEY and ADDRESS
    - Mainnet funds on Hyperliquid
    - EXTREME CAUTION - real money is at risk!
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
        logging.FileHandler("hype_mainnet_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def create_mainnet_config():
    """Create mainnet trading configuration"""
    config = BotConfig()

    # Use mainnet
    config.USE_TESTNET = False
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


async def run_mainnet_bot():
    """Run the mainnet trading bot"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         ⚠️  HYPE/USDC MAINNET TRADING BOT ⚠️                   ║
║         Modular Architecture Edition                             ║
╠══════════════════════════════════════════════════════════════════╣
║  ⚠️  WARNING: THIS USES REAL MONEY!                          ║
║  ⚠️  EXTREME CAUTION REQUIRED!                                ║
║                                                                ║
║  MODE: MAINNET TRADING                                        ║
║  - Real orders on mainnet exchange                             ║
║  - REAL MONEY IS AT RISK!                                     ║
║  - Past performance does not guarantee future results           ║
║                                                                ║
║  STRATEGY:                                                     ║
║  - Ultra-Optimized Momentum                                    ║
║  - 8% risk per trade, 5x leverage, max 2 positions            ║
║                                                                ║
║  SAFETY MEASURES:                                             ║
║  - Start with paper trading to validate strategy                ║
║  - Use testnet before mainnet                                  ║
║  - Monitor closely for first few days                           ║
║  - Set appropriate daily loss limits                           ║
║                                                                ║
║  REQUIREMENTS:                                                 ║
║  - .env file with PRIVATE_KEY and ADDRESS                      ║
║  - Mainnet funds on Hyperliquid                               ║
║  - Accept full responsibility for losses                        ║
║                                                                ║
║  WEB DASHBOARD:                                                ║
║  1. Start the bot (this script)                               ║
║  2. In another terminal: make dashboard                        ║
║  3. Open http://localhost:8501 in your browser                ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Safety check - require user to type "I AGREE"
    response = input('Type "I AGREE" to continue with mainnet trading: ')
    if response.strip().upper() != "I AGREE":
        logger.info("Mainnet trading cancelled")
        sys.exit(0)

    # Load config from environment
    config = BotConfig.from_env()
    config.USE_TESTNET = False
    config.PAPER_TRADING = False

    # Validate credentials
    if not config.PRIVATE_KEY or not config.ADDRESS:
        logger.error("❌ Error: PRIVATE_KEY and ADDRESS must be set in .env file")
        logger.error("   Copy .env.example to .env and fill in your credentials")
        sys.exit(1)

    # Override with mainnet defaults
    mainnet_config = create_mainnet_config()
    config.ASSET = mainnet_config.ASSET
    config.TIMEFRAME = mainnet_config.TIMEFRAME
    config.LEVERAGE = mainnet_config.LEVERAGE
    config.ROC_SHORT = mainnet_config.ROC_SHORT
    config.ROC_LONG = mainnet_config.ROC_LONG
    config.MOMENTUM_THRESHOLD = mainnet_config.MOMENTUM_THRESHOLD
    config.CONFIDENCE_THRESHOLD = mainnet_config.CONFIDENCE_THRESHOLD
    config.EMA_TREND_FILTER = mainnet_config.EMA_TREND_FILTER
    config.RISK_PER_TRADE_PCT = mainnet_config.RISK_PER_TRADE_PCT
    config.TP_ATR_MULTIPLIER = mainnet_config.TP_ATR_MULTIPLIER
    config.SL_ATR_MULTIPLIER = mainnet_config.SL_ATR_MULTIPLIER
    config.MAX_POSITIONS = mainnet_config.MAX_POSITIONS
    config.MAX_DAILY_TRADES = mainnet_config.MAX_DAILY_TRADES
    config.ORDER_TYPE = mainnet_config.ORDER_TYPE
    config.MIN_ORDER_SIZE = mainnet_config.MIN_ORDER_SIZE
    config.MAX_DAILY_LOSS_PCT = mainnet_config.MAX_DAILY_LOSS_PCT

    logger.info("=" * 60)
    logger.info("⚠️  MAINNET TRADING BOT STARTING ⚠️")
    logger.info("=" * 60)
    logger.info("Exchange: Mainnet")
    logger.info("Mode: REAL MONEY TRADING")
    logger.info(f"Asset: {config.ASSET}")
    logger.info(f"Timeframe: {config.TIMEFRAME}")
    logger.info(f"Leverage: {config.LEVERAGE}x")
    logger.info(f"Risk per trade: {config.RISK_PER_TRADE_PCT * 100}%")
    logger.info(f"Max daily loss: {config.MAX_DAILY_LOSS_PCT * 100}%")
    logger.info("")
    logger.info("CONTROL COMMANDS:")
    logger.info("  Force close all positions: touch .force_close_positions")
    logger.info("  Or send signal: kill -USR1 $(pgrep -f run_mainnet_bot.py)")
    logger.info("  Reset circuit breaker: touch .reset_circuit_breaker")
    logger.info("  Or send signal: kill -USR2 $(pgrep -f run_mainnet_bot.py)")

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
    asyncio.run(run_mainnet_bot())
