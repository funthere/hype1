"""
HYPE/USDC Testnet Trading Bot for Hyperliquid

This is a simplified version specifically for TESTNET trading.
Testnet allows you to test with real orders but no real money.

GET TESTNET FUNDS: https://app.hyperliquid-testnet.xyz/drip

Based on Ultra-Optimized Momentum Strategy (15m timeframe)
"""

import asyncio
import os
import sys
import logging

# Import from main bot
from hype_trading_bot import (
    BotConfig, Side, OrderStatus,
    HyperliquidAPI, MarketDataFeed, StrategyEngine, TradingBot,
    create_bot_config
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('hype_testnet_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def create_testnet_config(private_key: str, address: str) -> BotConfig:
    """Create testnet-specific configuration"""
    config = BotConfig()

    # Testnet endpoints
    config.USE_TESTNET = True
    config.PAPER_TRADING = False  # Real orders on testnet

    # Account
    config.PRIVATE_KEY = private_key
    config.ADDRESS = address

    # Strategy - same as mainnet
    config.ASSET = "HYPE"
    config.TIMEFRAME = "15m"
    config.LEVERAGE = 5

    # Ultra-Optimized Parameters
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


async def run_testnet_bot():
    """Run the testnet bot"""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         HYPE/USDC TESTNET TRADING BOT                         ║
║         Ultra-Optimized Momentum Strategy                      ║
╠══════════════════════════════════════════════════════════════════╣
║  MODE: TESTNET                                                 ║
║  - Real orders on testnet exchange                             ║
║  - NO real money at risk                                       ║
║  - Uses simulated testnet funds                                ║
║                                                                ║
║  GET FUNDS: https://app.hyperliquid-testnet.xyz/drip           ║
║                                                                ║
║  STRATEGY:                                                     ║
║  - Expected: 48.1% in 52 days (1,476% annualized)            ║
║  - Max Drawdown: 3.0%                                         ║
║  - 8% risk per trade, 5x leverage, max 2 positions            ║
║                                                                ║
║  SETUP:                                                        ║
║  1. Get testnet funds from the faucet                         ║
║  2. Set HYPERLICUID_PRIVATE_KEY env variable                  ║
║  3. Set HYPERLICUID_ADDRESS env variable                      ║
║  4. Run: python3 hype_testnet_bot.py                          ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    # Get credentials
    private_key = os.environ.get("HYPERLICUID_PRIVATE_KEY")
    address = os.environ.get("HYPERLICUID_ADDRESS")

    if not private_key or not address:
        logger.error("❌ Please set HYPERLICUID_PRIVATE_KEY and HYPERLICUID_ADDRESS")
        logger.info("💡 Get testnet funds: https://app.hyperliquid-testnet.xyz/drip")
        return

    # Create testnet config
    config = create_testnet_config(private_key, address)

    logger.info("=" * 60)
    logger.info("TESTNET BOT STARTING")
    logger.info("=" * 60)
    logger.info(f"Exchange: Testnet (api.hyperliquid-testnet.xyz)")
    logger.info(f"Asset: {config.ASSET}")
    logger.info(f"Timeframe: {config.TIMEFRAME}")
    logger.info(f"Leverage: {config.LEVERAGE}x")
    logger.info(f"Risk per trade: {config.RISK_PER_TRADE_PCT * 100}%")
    logger.info("✓ NO REAL MONEY - Using testnet")
    logger.info("⚠️  Testnet markets may be illiquid")
    logger.info("")
    logger.info("CONTROL COMMANDS:")
    logger.info("  Force close all positions: touch .force_close_positions")
    logger.info("  Or send signal: kill -USR1 $(pgrep -f hype_testnet_bot)")
    logger.info("  Or run script: ./force_close.sh")
    logger.info("  Reset circuit breaker: touch .reset_circuit_breaker")
    logger.info("  Or send signal: kill -USR2 $(pgrep -f hype_testnet_bot)")

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
