#!/usr/bin/env python3
"""
Multi-Asset HYPE Trading Bot

Trade multiple assets with correlation filtering and position allocation.

Usage:
    # Trade HYPE and related assets
    python run_multi_asset_bot.py --assets HYPE ETH BTC

    # Use custom weights
    python run_multi_asset_bot.py --assets HYPE ETH --weights 0.6 0.4

    # Use risk parity allocation
    python run_multi_asset_bot.py --allocation risk_parity
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.config import BotConfig, Side, Position
from src.core.strategy import StrategyEngine
from src.core.multi_asset import (
    AssetConfig,
    MultiAssetSignal,
    AssetAllocationMethod,
    MultiAssetStrategy,
    create_default_multi_asset_config,
)
from src.exchange.connector import HyperliquidAPI
from src.exchange.market_data import MarketDataFeed
from src.storage.database import DatabaseManager
from src.notifications.telegram import TelegramNotifier

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("hype_multi_asset_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class MultiAssetTradingBot:
    """
    Multi-asset trading bot with correlation filtering.

    Manages multiple assets simultaneously with:
    - Per-asset signal generation
    - Correlation-based position filtering
    - Dynamic position allocation
    """

    def __init__(
        self,
        config: BotConfig,
        assets: List[AssetConfig],
        allocation_method: AssetAllocationMethod = AssetAllocationMethod.EQUAL_WEIGHT,
        max_correlation: float = 0.7,
    ):
        self.config = config

        # Initialize multi-asset strategy
        self.multi_asset_strategy = MultiAssetStrategy(
            base_config=config,
            assets=assets,
            allocation_method=allocation_method,
            max_correlation=max_correlation,
        )

        # Per-asset components
        self.apis: Dict[str, HyperliquidAPI] = {}
        self.market_feeds: Dict[str, MarketDataFeed] = {}
        self.strategies: Dict[str, StrategyEngine] = {}

        # Initialize for each asset
        for asset_config in assets:
            if not asset_config.enabled:
                continue

            # Create asset-specific config
            asset_config_obj = BotConfig()
            asset_config_obj.PAPER_TRADING = config.PAPER_TRADING
            asset_config_obj.USE_TESTNET = config.USE_TESTNET
            asset_config_obj.ASSET = asset_config.symbol
            asset_config_obj.PRIVATE_KEY = config.PRIVATE_KEY
            asset_config_obj.ADDRESS = config.ADDRESS
            asset_config_obj.LEVERAGE = (
                asset_config.leverage or config.LEVERAGE
            )
            asset_config_obj.RISK_PER_TRADE_PCT = (
                asset_config.risk_per_trade or config.RISK_PER_TRADE_PCT
            )

            # Initialize components
            self.apis[asset_config.symbol] = HyperliquidAPI(asset_config_obj)
            self.market_feeds[asset_config.symbol] = MarketDataFeed(asset_config_obj)
            self.strategies[asset_config.symbol] = StrategyEngine(asset_config_obj)

        # Storage and notifications
        self.db = DatabaseManager(config.DATABASE_PATH)
        self.telegram = TelegramNotifier.from_config(config)

        # State
        self.positions: Dict[str, List[Position]] = {
            asset.symbol: [] for asset in assets
        }
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.is_running = False

        # Statistics
        self.start_time = None
        self.starting_capital = config.PAPER_CAPITAL if config.PAPER_TRADING else 10000.0
        self.current_capital = self.starting_capital

    async def start(self):
        """Start the multi-asset trading bot"""
        self.is_running = True
        self.start_time = asyncio.get_event_loop().time()

        logger.info("=" * 60)
        logger.info("MULTI-ASSET TRADING BOT STARTING")
        logger.info("=" * 60)

        # Log asset configuration
        asset_summary = self.multi_asset_strategy.get_asset_summary()
        logger.info(f"\nAsset Configuration:\n{asset_summary.to_string()}")

        # Send startup notification
        if self.telegram:
            await self.telegram.notify_start(self.config)

        # Start market data feeds for all assets
        for asset, feed in self.market_feeds.items():
            feed.on_candle_update(
                lambda c, a=asset: self._on_candle_update(a, c)
            )
            asyncio.create_task(feed.connect())

        # Main loop
        await self._main_loop()

    async def _main_loop(self):
        """Main trading loop"""
        logger.info("Starting multi-asset main loop...")

        while self.is_running:
            try:
                # Check exits on all positions
                await self._check_all_position_exits()

                # Process signals for each asset
                for asset in self.strategies.keys():
                    strategy = self.strategies[asset]

                    if self.market_feeds[asset].current_candle:
                        signal = strategy.generate_signal(self.current_capital)

                        if signal:
                            # Convert to multi-asset signal
                            ma_signal = MultiAssetSignal(
                                asset=asset,
                                action=signal["action"],
                                confidence=signal["confidence"],
                                entry_price=signal["entry_price"],
                                tp_price=signal["tp_price"],
                                sl_price=signal["sl_price"],
                                quantity=signal["quantity"],
                                atr=signal["atr"],
                            )

                            await self._process_signal(asset, ma_signal)

                # Update portfolio exposure
                await self._update_portfolio_metrics()

                # Sleep before next iteration
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)

    async def _on_candle_update(self, asset: str, candle: dict):
        """Handle candle update for an asset"""
        # Update strategy
        self.strategies[asset].update_candle(candle)

        # Update correlation filter
        price = candle.get("close", 0)
        if price > 0:
            self.multi_asset_strategy.update_asset_price(asset, price)

    async def _process_signal(self, asset: str, signal: MultiAssetSignal):
        """Process trading signal for an asset"""
        # Get all current positions
        all_positions = self.multi_asset_strategy.get_all_positions()

        # Check if we can trade this asset
        can_trade, reason = self.multi_asset_strategy.can_trade_asset(
            asset, signal, all_positions
        )

        if not can_trade:
            logger.debug(f"Skipping {asset} signal: {reason}")
            return

        # Calculate position size
        quantity = self.multi_asset_strategy.calculate_position_size(
            asset, signal, self.current_capital
        )

        # Create position
        position = Position(
            side=signal.action,
            entry_price=signal.entry_price,
            quantity=quantity,
            tp_price=signal.tp_price,
            sl_price=signal.sl_price,
            entry_time=signal.timestamp,
            leverage=self.config.LEVERAGE,
        )

        # Place order (skip for paper trading)
        if not self.config.PAPER_TRADING:
            api = self.apis[asset]
            result = await api.place_order(
                side=signal.action,
                price=signal.entry_price,
                quantity=quantity,
            )

            if result.get("status") != "ok":
                logger.error(f"Entry order failed for {asset}: {result.get('msg')}")
                return

            position.oid = result.get("response", {}).get("oid")

        # Add to strategy
        self.multi_asset_strategy.add_position(asset, position)
        self.positions[asset].append(position)

        # Save to database
        self.db.save_position(position)
        self.db.log_event("trade_entry", f"{asset} {signal.action.value} entry")

        logger.info(
            f"Opened {asset} {signal.action.value} position @ ${signal.entry_price:.4f}, "
            f"Qty: {quantity:.2f}"
        )

        # Send notification
        if self.telegram:
            await self.telegram.notify_trade_entry(signal.__dict__)

    async def _check_all_position_exits(self):
        """Check exit conditions for all positions"""
        for asset, positions in list(self.positions.items()):
            if not positions:
                continue

            # Get current price
            api = self.apis[asset]
            mids = await api.get_mids()
            current_price = float(mids.get(asset, 0))

            if current_price == 0:
                continue

            positions_to_close = []

            for position in positions:
                should_close = False
                exit_reason = ""

                if position.side == Side.LONG:
                    if current_price >= position.tp_price:
                        should_close = True
                        exit_reason = "TP"
                    elif current_price <= position.sl_price:
                        should_close = True
                        exit_reason = "SL"
                else:  # SHORT
                    if current_price <= position.tp_price:
                        should_close = True
                        exit_reason = "TP"
                    elif current_price >= position.sl_price:
                        should_close = True
                        exit_reason = "SL"

                if should_close:
                    positions_to_close.append((position, exit_reason))

            # Close positions
            for position, reason in positions_to_close:
                await self._close_position(asset, position, current_price, reason)

    async def _close_position(
        self, asset: str, position: Position, exit_price: float, reason: str
    ):
        """Close a position"""
        logger.info(f"Closing {asset} {position.side.value} @ ${exit_price:.4f} ({reason})")

        # Calculate P&L
        if position.side == Side.LONG:
            pnl_gross = (exit_price - position.entry_price) * position.quantity
        else:
            pnl_gross = (position.entry_price - exit_price) * position.quantity

        pnl = pnl_gross * position.leverage
        fees = abs(position.entry_price * position.quantity * self.config.MAKER_FEE_PCT * 2)
        net_pnl = pnl - fees

        # Create trade record
        trade = Trade(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.entry_time,
            exit_time=datetime.now(),
            pnl=net_pnl,
            fees=fees,
        )

        # Save trade
        self.db.save_trade(trade)

        # Update tracking
        self.daily_trades += 1
        self.daily_pnl += net_pnl
        self.current_capital += net_pnl

        # Remove from positions
        self.multi_asset_strategy.remove_position(asset, position)
        if position in self.positions[asset]:
            self.positions[asset].remove(position)

        # Close on exchange
        if not self.config.PAPER_TRADING and position.oid:
            await self.apis[asset].cancel_order(position.oid)

        logger.info(f"Closed {asset} position: P&L=${net_pnl:.2f}")

        # Send notification
        if self.telegram:
            await self.telegram.notify_trade_exit(trade)

    async def _update_portfolio_metrics(self):
        """Update portfolio exposure metrics"""
        current_prices = {}

        for asset, api in self.apis.items():
            mids = await api.get_mids()
            current_prices[asset] = float(mids.get(asset, 0))

        exposure = self.multi_asset_strategy.get_total_exposure(current_prices)

        logger.debug(
            f"Portfolio: ${exposure['total_exposure']:,.0f} exposure | "
            f"{exposure['long_exposure']:,.0f} long | "
            f"{exposure['short_exposure']:,.0f} short | "
            f"{exposure['num_positions']} positions"
        )

    async def stop(self):
        """Stop the bot"""
        logger.info("Stopping multi-asset bot...")

        # Close all positions
        for asset, positions in list(self.positions.items()):
            for position in positions:
                api = self.apis[asset]
                mids = await api.get_mids()
                current_price = float(mids.get(asset, 0))
                if current_price > 0:
                    await self._close_position(asset, position, current_price, "SHUTDOWN")

        # Close database
        self.db.close()

        # Close telegram
        if self.telegram:
            await self.telegram.close()

        self.is_running = False


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Multi-Asset HYPE Trading Bot")
    parser.add_argument(
        "--assets",
        nargs="+",
        default=["HYPE"],
        help="Assets to trade (default: HYPE)",
    )
    parser.add_argument(
        "--weights",
        nargs="+",
        type=float,
        help="Asset weights (must match number of assets)",
    )
    parser.add_argument(
        "--allocation",
        choices=["equal_weight", "risk_parity", "signal_strength", "volatility_target"],
        default="equal_weight",
        help="Capital allocation method (default: equal_weight)",
    )
    parser.add_argument(
        "--max-correlation",
        type=float,
        default=0.7,
        help="Maximum correlation filter (0-1, default: 0.7)",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "testnet", "mainnet"],
        default="paper",
        help="Trading mode",
    )
    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_args()

    # Validate weights
    if args.weights and len(args.weights) != len(args.assets):
        logger.error("Number of weights must match number of assets")
        sys.exit(1)

    # Create asset configurations
    assets = []
    for i, asset in enumerate(args.assets):
        weight = args.weights[i] if args.weights else 1.0
        assets.append(AssetConfig(
            symbol=asset,
            weight=weight,
            max_positions=1,
            min_signal_confidence=45,
            enabled=True
        ))

    # Load config
    config = BotConfig.from_env()
    config.PAPER_TRADING = args.mode == "paper"
    config.USE_TESTNET = args.mode == "testnet"

    # Validate
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Create and start bot
    bot = MultiAssetTradingBot(
        config=config,
        assets=assets,
        allocation_method=AssetAllocationMethod(args.allocation),
        max_correlation=args.max_correlation,
    )

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal...")
        await bot.stop()


if __name__ == "__main__":
    from datetime import datetime
    asyncio.run(main())
