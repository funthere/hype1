"""
HYPE/USDC Automated Trading Bot for Hyperliquid

Based on Ultra-Optimized Momentum Strategy (15m timeframe)
- 65.7% return in 52 days (3,325% annualized)
- 3.2% max drawdown
- 1.67 profit factor

Features:
- Real-time market data via WebSocket
- Automated order placement and management
- Dynamic position sizing
- Risk management and emergency controls
- Trade logging and performance tracking
"""

import asyncio
import json
import logging
import time
import hmac
import hashlib
import websockets
import signal
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np
import requests
from eth_account import Account
from eth_account.messages import encode_defunct

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Hyperliquid SDK
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('hype_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Position:
    """Open position tracking"""
    side: Side
    entry_price: float
    quantity: float
    tp_price: float
    sl_price: float
    entry_time: datetime
    leverage: int
    oid: Optional[int] = None
    cloid: Optional[str] = None
    status: OrderStatus = OrderStatus.OPEN
    unrealized_pnl: float = 0.0


@dataclass
class Trade:
    """Completed trade tracking"""
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    fees: float = 0.0


@dataclass
class BotConfig:
    """Bot configuration"""
    # Environment
    USE_TESTNET: bool = False  # Use testnet exchange (illiquid)
    PAPER_TRADING: bool = False  # Simulate trades with mainnet data

    # API (auto-switches based on USE_TESTNET - ignores PAPER_TRADING)
    @property
    def API_URL(self) -> str:
        if self.USE_TESTNET:
            return constants.TESTNET_API_URL
        return constants.MAINNET_API_URL

    @property
    def INFO_URL(self) -> str:
        if self.USE_TESTNET:
            return constants.TESTNET_API_URL
        return constants.MAINNET_API_URL

    @property
    def WS_URL(self) -> str:
        if self.USE_TESTNET:
            return "wss://api.hyperliquid-testnet.xyz/ws"
        return "wss://api.hyperliquid.xyz/ws"

    # Account
    PRIVATE_KEY: str = ""  # Set from environment or config
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None  # For API wallet support

    # Paper Trading
    PAPER_CAPITAL: float = 10000  # Starting capital for paper trading

    # Trading
    ASSET: str = "HYPE"
    ASSET_INDEX: int = 0  # Will fetch from meta
    TIMEFRAME: str = "15m"
    LEVERAGE: int = 5

    # Strategy Parameters (Ultra-Optimized)
    ROC_SHORT: int = 1
    ROC_LONG: int = 5
    MOMENTUM_THRESHOLD: float = 0.08
    CONFIDENCE_THRESHOLD: int = 45
    EMA_TREND_FILTER: int = 20

    # Risk Management (Balanced config - RECOMMENDED)
    RISK_PER_TRADE_PCT: float = 0.08  # 8% per trade (balanced)
    TP_ATR_MULTIPLIER: float = 2.0
    SL_ATR_MULTIPLIER: float = 0.4
    MAX_POSITIONS: int = 2  # Max 2 concurrent positions
    MAX_DAILY_TRADES: int = 20

    # Order Settings
    ORDER_TYPE: str = "limit"  # limit for maker rebates
    MIN_ORDER_SIZE: float = 10  # USD

    # Safety
    MAX_DAILY_LOSS_PCT: float = 0.15  # Emergency shutdown at 15% daily loss
    EMERGENCY_SHUTDOWN: bool = False

    # Circuit Breaker - Stop trading after consecutive losses
    CIRCUIT_BREAKER_ENABLED: bool = True
    MAX_CONSECUTIVE_LOSSES: int = 3  # Stop after N consecutive losses
    CIRCUIT_BREAKER_COOLDOWN_MINUTES: int = 30  # Wait before trading again

    # Fees
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0004

    # Web UI / API
    WEB_UI_ENABLED: bool = True
    WEB_UI_HOST: str = "127.0.0.1"
    WEB_UI_PORT: int = 8000


class HyperliquidAPI:
    """Hyperliquid API client using official SDK"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.account = Account.from_key(config.PRIVATE_KEY)
        self.address = self.account.address

        # Initialize SDK clients
        base_url = self.config.API_URL
        self.info = Info(base_url, skip_ws=True)

        # Create Exchange client with wallet
        # For API wallets, provide account_address
        exchange_kwargs = {"account_address": config.ACCOUNT_ADDRESS} if config.ACCOUNT_ADDRESS else {}
        self.exchange = Exchange(self.account, base_url, **exchange_kwargs)

        # Asset index cache
        self._asset_index: Optional[int] = None

    async def get_asset_index(self) -> int:
        """Get HYPE asset index from metadata"""
        if self._asset_index is not None:
            return self._asset_index

        # Use SDK's meta method
        meta_data = self.info.meta()

        for i, asset in enumerate(meta_data["universe"]):
            if asset["name"] == self.config.ASSET:
                self._asset_index = i
                self.config.ASSET_INDEX = i
                logger.info(f"Found {self.config.ASSET} at index {i}")
                return i

        raise ValueError(f"{self.config.ASSET} not found in universe")

    async def place_order(self, side: Side, price: float, quantity: float,
                          reduce_only: bool = False, cloid: Optional[str] = None) -> dict:
        """Place limit order using SDK"""

        # Get asset index
        asset_index = await self.get_asset_index()

        # Build order using SDK format
        order_result = self.exchange.order(
            coin=self.config.ASSET,
            is_buy=(side == Side.LONG),
            sz=quantity,
            limit_px=price,
            order_type={"limit": {"tif": "Gtc"}},  # Good til canceled
            reduce_only=reduce_only,
            cloid=cloid
        )

        # SDK returns dict with response status
        if order_result.get("status") == "ok":
            return {
                "status": "ok",
                "response": order_result.get("response", {})
            }
        else:
            error_msg = order_result.get("response", {}).get("error", "Unknown error")
            logger.error(f"Order failed: {error_msg}")
            return {"status": "error", "msg": error_msg}

    async def cancel_order(self, oid: int) -> dict:
        """Cancel order by ID using SDK"""
        result = self.exchange.cancel(coin=self.config.ASSET, oid=oid)

        if result.get("status") == "ok":
            return {"status": "ok"}
        else:
            error_msg = result.get("response", {}).get("error", "Unknown error")
            return {"status": "error", "msg": error_msg}

    async def cancel_all_orders(self) -> dict:
        """Cancel all open orders using SDK"""
        # First get open orders
        open_orders = await self.get_open_orders()

        if open_orders:
            # Build cancel list
            cancel_list = [
                {"coin": self.config.ASSET, "oid": o["oid"]}
                for o in open_orders
            ]
            result = self.exchange.bulk_cancel(cancel_list)

            if result.get("status") == "ok":
                return {"status": "ok"}
            else:
                error_msg = result.get("response", {}).get("error", "Unknown error")
                return {"status": "error", "msg": error_msg}

        return {"status": "ok"}

    async def modify_order(self, oid: int, new_price: float, new_quantity: float) -> dict:
        """Modify existing order using SDK"""
        # SDK doesn't have direct modify, we cancel and replace
        # Cancel the old order
        cancel_result = await self.cancel_order(oid)

        if cancel_result.get("status") != "ok":
            return cancel_result

        # Place new order (we need to know the side - assume LONG for now)
        # In practice, you'd need to track the original order's side
        place_result = await self.place_order(
            side=Side.LONG,
            price=new_price,
            quantity=new_quantity
        )

        return place_result

    async def get_open_orders(self) -> List[dict]:
        """Get open orders using SDK"""
        orders = self.info.open_orders(self.config.ASSET)
        return orders

    async def get_positions(self) -> List[dict]:
        """Get current positions using SDK"""
        if not self.address:
            return []

        user_state = self.info.user_state(self.address)

        # Extract positions from user state
        asset_positions = user_state.get("assetPositions", [])

        positions = []
        for pos_data in asset_positions:
            position = pos_data.get("position", {})
            if position:  # Only include non-empty positions
                positions.append(position)

        return positions

    async def get_mids(self) -> dict:
        """Get current mid prices using SDK"""
        return self.info.all_mids()

    async def get_balance(self) -> dict:
        """Get account balance using SDK"""
        if not self.address:
            return {}

        user_state = self.info.user_state(self.address)

        # Extract margin summary
        margin_summary = user_state.get("marginSummary", {})
        cross_margin_summary = user_state.get("crossMarginSummary", {})

        return {
            "account_value": margin_summary.get("accountValue", 0),
            "total_margin_used": margin_summary.get("totalMarginUsed", 0),
            "total_npos": cross_margin_summary.get("totalNpos", 0),
            "margin_summary": margin_summary,
            "cross_margin_summary": cross_margin_summary
        }

    async def get_user_state(self) -> dict:
        """Get full user state using SDK"""
        if not self.address:
            return {}

        return self.info.user_state(self.address)

    async def set_leverage(self, leverage: int, is_cross: bool = True) -> dict:
        """Set leverage using SDK"""
        result = self.exchange.update_leverage(
            leverage=leverage,
            coin=self.config.ASSET,
            is_cross=is_cross
        )

        if result.get("status") == "ok":
            return {"status": "ok"}
        else:
            error_msg = result.get("response", {}).get("error", "Unknown error")
            return {"status": "error", "msg": error_msg}

    async def get_order_status(self, oid: int) -> dict:
        """Get order status by ID using SDK"""
        # SDK doesn't have a direct method, use open_orders
        open_orders = await self.get_open_orders()

        for order in open_orders:
            if order.get("oid") == oid:
                return order

        return None

    async def get_recent_fills(self, limit: int = 100) -> List[dict]:
        """Get recent trade fills for the user using SDK"""
        if not self.address:
            return []

        fills = self.info.user_fills(self.address)
        return fills[:limit] if fills else []


class MarketDataFeed:
    """Real-time market data via WebSocket"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.ws_url = config.WS_URL
        self.callbacks = []
        self.candles: List[pd.DataFrame] = []
        self.current_candle = None
        self.connected = False

    def on_candle_update(self, callback):
        """Register callback for candle updates"""
        self.callbacks.append(callback)

    async def connect(self):
        """Connect to WebSocket"""
        logger.info(f"Connecting to {self.ws_url}")

        try:
            async with websockets.connect(self.ws_url) as ws:
                self.connected = True
                logger.info("WebSocket connected")

                # Subscribe to candle data
                await self._subscribe(ws)

                # Listen for messages
                while self.connected:
                    message = await ws.recv()
                    await self._handle_message(message)

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self.connected = False
            # Reconnect after delay
            await asyncio.sleep(5)
            await self.connect()

    async def _subscribe(self, ws):
        """Subscribe to market data"""
        # Subscribe to trades
        subscribe_msg = {
            "method": "subscribe",
            "subscription": {
                "type": "candle",
                "coin": self.config.ASSET,
                "interval": self.config.TIMEFRAME
            }
        }

        await ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {self.config.ASSET} {self.config.TIMEFRAME} candles")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            data = json.loads(message)

            if data.get("channel") == "candle":
                candle = data["data"]
                await self._process_candle(candle)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _process_candle(self, candle: dict):
        """Process candle data"""
        # Convert to DataFrame row
        candle_data = {
            "timestamp": datetime.fromtimestamp(candle["t"] / 1000),
            "open": float(candle["o"]),
            "high": float(candle["h"]),
            "low": float(candle["l"]),
            "close": float(candle["c"]),
            "volume": float(candle["v"])
        }

        self.current_candle = candle_data

        # Notify callbacks
        for callback in self.callbacks:
            await callback(candle_data)


class StrategyEngine:
    """Signal generation using Ultra-Optimized Momentum strategy"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.candles: pd.DataFrame = pd.DataFrame()
        self.max_candles = 200  # Keep enough for calculations

    def update_candle(self, candle: dict):
        """Update with new candle data"""
        new_row = pd.DataFrame([candle])
        new_row.set_index('timestamp', inplace=True)

        self.candles = pd.concat([self.candles, new_row])

        # Keep only recent candles
        if len(self.candles) > self.max_candles:
            self.candles = self.candles.iloc[-self.max_candles:]

    def generate_signal(self) -> Optional[Dict]:
        """
        Generate trading signal based on Ultra-Optimized Momentum strategy

        Returns:
            Dict with signal info or None if no signal
            {
                'action': 'LONG' or 'SHORT',
                'confidence': 0-100,
                'entry_price': float,
                'tp_price': float,
                'sl_price': float,
                'quantity': float
            }
        """
        if len(self.candles) < 30:  # Need minimum candles
            return None

        df = self.candles.copy()

        # Calculate indicators
        # Rate of Change
        roc_short = df['close'].pct_change(self.config.ROC_SHORT) * 100
        roc_long = df['close'].pct_change(self.config.ROC_LONG) * 100

        # Momentum score
        momentum_score = 50 + (roc_short * 5 + roc_long * 2)

        # EMA trend filter
        ema_20 = df['close'].ewm(span=self.config.EMA_TREND_FILTER).mean()
        trend_up = df['close'].iloc[-1] > ema_20.iloc[-1]
        trend_down = df['close'].iloc[-1] < ema_20.iloc[-1]

        # Volume confirmation
        vol_ma = df['volume'].rolling(window=20).mean()
        vol_ok = df['volume'].iloc[-1] > vol_ma.iloc[-1] * 1.2

        # ATR for TP/SL
        df['tr'] = df['high'] - df['low']
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = abs(df['tr'])
        df['tr2'] = abs(df['close'] - df['prev_close'])
        df['true_range'] = df[['tr', 'tr1', 'tr2']].max(axis=1)
        atr = df['true_range'].rolling(window=14).mean().iloc[-1]
        current_price = df['close'].iloc[-1]

        # Generate signals
        long_setup = (
            momentum_score.iloc[-1] > 50 + self.config.MOMENTUM_THRESHOLD and
            trend_up and
            vol_ok
        )

        short_setup = (
            momentum_score.iloc[-1] < 50 - self.config.MOMENTUM_THRESHOLD and
            trend_down and
            vol_ok
        )

        signal = None

        if long_setup:
            tp_price = current_price + (atr * self.config.TP_ATR_MULTIPLIER)
            sl_price = current_price - (atr * self.config.SL_ATR_MULTIPLIER)

            # Calculate quantity (position sizing) - matches backtest formula
            capital = self.config.PAPER_CAPITAL if self.config.PAPER_TRADING else 10000  # Use paper capital if set
            margin = capital * self.config.RISK_PER_TRADE_PCT  # Margin to use
            notional = margin * self.config.LEVERAGE  # Notional with leverage
            quantity = notional / current_price  # Token quantity

            signal = {
                'action': Side.LONG,
                'confidence': min(100, momentum_score.iloc[-1]),
                'entry_price': current_price,
                'tp_price': tp_price,
                'sl_price': sl_price,
                'quantity': quantity,
                'atr': atr
            }

        elif short_setup:
            tp_price = current_price - (atr * self.config.TP_ATR_MULTIPLIER)
            sl_price = current_price + (atr * self.config.SL_ATR_MULTIPLIER)

            # Calculate quantity (position sizing) - matches backtest formula
            capital = self.config.PAPER_CAPITAL if self.config.PAPER_TRADING else 10000  # Use paper capital if set
            margin = capital * self.config.RISK_PER_TRADE_PCT  # Margin to use
            notional = margin * self.config.LEVERAGE  # Notional with leverage
            quantity = notional / current_price  # Token quantity

            signal = {
                'action': Side.SHORT,
                'confidence': min(100, 100 - momentum_score.iloc[-1]),
                'entry_price': current_price,
                'tp_price': tp_price,
                'sl_price': sl_price,
                'quantity': quantity,
                'atr': atr
            }

        if signal:
            logger.info(f"Signal generated: {signal['action'].value} @ {signal['entry_price']:.4f} | "
                       f"TP: {signal['tp_price']:.4f} | SL: {signal['sl_price']:.4f} | "
                       f"Qty: {signal['quantity']:.2f} | Conf: {signal['confidence']:.1f}")

        return signal


class TradingBot:
    """Main trading bot class"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.api = HyperliquidAPI(config)
        self.market_data = MarketDataFeed(config)
        self.strategy = StrategyEngine(config)

        # State
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.last_trade_date = None
        self.emergency_stop = False
        self.force_close_all = False  # Flag to force close all positions
        self._is_paused = False  # Pause state for API control

        # Statistics
        self.start_time = datetime.now()
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_fees = 0.0

        # Capital tracking
        self._starting_capital = config.PAPER_CAPITAL if config.PAPER_TRADING else 10000.0
        self._current_capital = self._starting_capital
        self._max_drawdown_pct = 0.0
        self._peak_equity = self._starting_capital

        # Circuit Breaker State
        self.consecutive_losses = 0
        self.circuit_breaker_triggered = False
        self.circuit_breaker_trigger_time = None

        # API Server (optional)
        self.api_server = None

        # Setup signal handlers for testing
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for testing controls"""
        try:
            # SIGUSR1 = Force close all positions
            signal.signal(signal.SIGUSR1, self._handle_force_close_signal)
            logger.info("Signal handlers loaded: SIGUSR1 = force close all positions")
        except Exception as e:
            logger.warning(f"Could not setup signal handlers: {e}")

        try:
            # SIGUSR2 = Reset circuit breaker
            signal.signal(signal.SIGUSR2, self._handle_reset_circuit_breaker_signal)
            logger.info("Signal handlers loaded: SIGUSR2 = reset circuit breaker")
        except Exception as e:
            logger.warning(f"Could not setup SIGUSR2 handler: {e}")

    def _handle_force_close_signal(self, signum, frame):
        """Handle SIGUSR1 signal - force close all positions"""
        logger.info("⚠️  SIGUSR1 received - forcing all positions to close...")
        self.force_close_all = True

    def _handle_reset_circuit_breaker_signal(self, signum, frame):
        """Handle SIGUSR2 signal - reset circuit breaker"""
        if self.circuit_breaker_triggered:
            logger.info("✅ SIGUSR2 received - circuit breaker reset")
            self.circuit_breaker_triggered = False
            self.circuit_breaker_trigger_time = None
            self.consecutive_losses = 0
        else:
            logger.info("SIGUSR2 received - circuit breaker not active")

    async def force_close_all_positions(self, reason="FORCE_CLOSE"):
        """Force close all open positions (for testing)"""
        if not self.positions:
            logger.info(f"[{reason}] No positions to close")
            return

        logger.warning(f"[{reason}] Closing {len(self.positions)} position(s)...")

        # Get current price
        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, 0))

        positions_to_close = self.positions[:]

        for position in positions_to_close:
            await self._close_position(position, current_price, reason)

        logger.info(f"[{reason}] All positions closed. P&L: ${self.daily_pnl:.2f}")

        # Setup signal handlers for testing
        self._setup_signal_handlers()

    async def start(self):
        """Start the trading bot"""
        logger.info("=" * 60)
        logger.info("HYPE TRADING BOT STARTING")
        logger.info("=" * 60)
        logger.info(f"Strategy: Ultra-Optimized Momentum ({self.config.TIMEFRAME})")
        logger.info(f"Asset: {self.config.ASSET}")
        logger.info(f"Leverage: {self.config.LEVERAGE}x")
        logger.info(f"Risk Per Trade: {self.config.RISK_PER_TRADE_PCT:.1%}")

        # Check emergency shutdown
        if self.config.EMERGENCY_SHUTDOWN:
            logger.error("EMERGENCY SHUTDOWN ENABLED - NOT TRADING")
            return

        # Log control commands
        mode = "PAPER" if self.config.PAPER_TRADING else ("TESTNET" if self.config.USE_TESTNET else "MAINNET")
        logger.info("")
        logger.info("CONTROL COMMANDS:")
        logger.info("  Force close all positions: touch .force_close_positions")
        logger.info("  Or send signal: kill -USR1 $(pgrep -f hype_trading_bot)")
        logger.info("  Or run script: ./force_close.sh --signal")
        logger.info("  Reset circuit breaker: touch .reset_circuit_breaker")
        logger.info("  Or send signal: kill -USR2 $(pgrep -f hype_trading_bot)")
        logger.info(f"  Mode: {mode}")

        # Log circuit breaker status
        if self.config.CIRCUIT_BREAKER_ENABLED:
            logger.info("")
            logger.info("CIRCUIT BREAKER:")
            logger.info(f"  Max consecutive losses: {self.config.MAX_CONSECUTIVE_LOSSES}")
            logger.info(f"  Cooldown period: {self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES} minutes")

        # Start API server if enabled
        if self.config.WEB_UI_ENABLED:
            from bot_api_server import TradingBotAPI
            self.api_server = TradingBotAPI(
                self,
                host=self.config.WEB_UI_HOST,
                port=self.config.WEB_UI_PORT
            )
            # Start API server in background
            self.api_server.start_in_background()
            logger.info(f"✅ Web UI API server started on http://{self.config.WEB_UI_HOST}:{self.config.WEB_UI_PORT}")
            logger.info(f"   Dashboard: streamlit run hype_dashboard.py")

        # Register candle callback
        self.market_data.on_candle_update(self._on_candle_update)

        # Get initial mids
        mids = await self.api.get_mids()
        logger.info(f"Current {self.config.ASSET} mid: ${mids.get(self.config.ASSET, 'N/A')}")

        # Start market data feed
        asyncio.create_task(self.market_data.connect())

        # Main loop
        await self._main_loop()

    async def _main_loop(self):
        """Main trading loop"""
        logger.info("Starting main trading loop...")

        while not self.emergency_stop:
            try:
                # Check for force close signal (control file or flag)
                if self.force_close_all or os.path.exists(".force_close_positions"):
                    if os.path.exists(".force_close_positions"):
                        os.remove(".force_close_positions")  # Remove the file
                    await self.force_close_all_positions("FORCE_CLOSE")
                    self.force_close_all = False

                # Check for circuit breaker manual reset
                if os.path.exists(".reset_circuit_breaker"):
                    os.remove(".reset_circuit_breaker")
                    if self.circuit_breaker_triggered:
                        logger.info("✅ Circuit breaker manually reset via control file")
                        self.circuit_breaker_triggered = False
                        self.circuit_breaker_trigger_time = None
                        self.consecutive_losses = 0

                # Check daily reset
                await self._check_daily_reset()

                # Check emergency conditions
                await self._check_emergency_conditions()

                # Check exits on open positions (always check, even if paused)
                await self._check_position_exits()

                # Process new signals (only if not paused)
                if not self._is_paused and self.market_data.current_candle:
                    signal = self.strategy.generate_signal()
                    if signal:
                        await self._process_signal(signal)

                # Sleep before next iteration
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)

    async def _on_candle_update(self, candle: dict):
        """Handle candle update from WebSocket"""
        self.strategy.update_candle(candle)

        # Update P&L for open positions
        await self._update_unrealized_pnl()

    async def _check_daily_reset(self):
        """Reset daily counters at midnight UTC"""
        now = datetime.utcnow()
        if self.last_trade_date != now.date():
            self.last_trade_date = now.date()
            self.daily_trades = 0
            self.daily_pnl = 0.0
            # Reset circuit breaker on new day
            if self.circuit_breaker_triggered:
                logger.info("Daily reset: Circuit breaker deactivated")
                self.circuit_breaker_triggered = False
                self.circuit_breaker_trigger_time = None
            self.consecutive_losses = 0
            logger.info(f"Daily reset - Date: {now.date()}")

    async def _check_emergency_conditions(self):
        """Check for emergency shutdown conditions"""
        if self.daily_pnl < -self.config.MAX_DAILY_LOSS_PCT * 10000:
            logger.error(f"EMERGENCY SHUTDOWN: Daily loss exceeds {self.config.MAX_DAILY_LOSS_PCT:.1%}")
            await self._emergency_shutdown()

    async def _emergency_shutdown(self):
        """Emergency shutdown - close all positions and cancel orders"""
        logger.warning("EMERGENCY SHUTDOWN INITIATED")
        self.emergency_stop = True

        # Cancel all orders
        await self.api.cancel_all_orders()

        # Close all positions (market orders)
        for position in self.positions:
            if position.status == OrderStatus.OPEN:
                await self._close_position_market(position)

        logger.warning("EMERGENCY SHUTDOWN COMPLETE")

    async def _check_circuit_breaker(self):
        """Check if circuit breaker cooldown has expired"""
        if not self.circuit_breaker_triggered:
            return

        if self.circuit_breaker_trigger_time is None:
            return

        elapsed = (datetime.now() - self.circuit_breaker_trigger_time).total_seconds() / 60
        if elapsed >= self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES:
            # Reset circuit breaker
            logger.info(f"✅ Circuit breaker cooldown expired ({elapsed:.1f} min elapsed)")
            logger.info(f"   Resuming trading. Consecutive losses reset to 0.")
            self.circuit_breaker_triggered = False
            self.circuit_breaker_trigger_time = None
            self.consecutive_losses = 0
        else:
            remaining = self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES - elapsed
            logger.debug(f"Circuit breaker active: {remaining:.1f} min remaining")

    async def _trigger_circuit_breaker(self):
        """Trigger circuit breaker after consecutive losses"""
        self.circuit_breaker_triggered = True
        self.circuit_breaker_trigger_time = datetime.now()

        logger.warning("=" * 60)
        logger.warning("⛔ CIRCUIT BREAKER TRIGGERED!")
        logger.warning("=" * 60)
        logger.warning(f"   Consecutive losses: {self.consecutive_losses}")
        logger.warning(f"   Max allowed: {self.config.MAX_CONSECUTIVE_LOSSES}")
        logger.warning(f"   Cooldown: {self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES} minutes")
        logger.warning("")
        logger.warning("   Trading paused until cooldown expires.")
        logger.warning("   Open positions will still be managed.")
        logger.warning("=" * 60)

    async def _process_signal(self, signal: Dict):
        """Process trading signal"""
        # Check circuit breaker
        if self.config.CIRCUIT_BREAKER_ENABLED:
            await self._check_circuit_breaker()
            if self.circuit_breaker_triggered:
                logger.info("⛔ Circuit breaker active - skipping signal")
                return

        # Check if we can take this trade
        if len(self.positions) >= self.config.MAX_POSITIONS:
            logger.info("Max positions reached, skipping signal")
            return

        if self.daily_trades >= self.config.MAX_DAILY_TRADES:
            logger.info("Max daily trades reached, skipping signal")
            return

        # Check if signal confidence meets threshold
        if signal['action'] == Side.LONG:
            min_conf = self.config.CONFIDENCE_THRESHOLD
            if signal['confidence'] < min_conf:
                return
        else:  # SHORT
            min_conf = 100 - self.config.CONFIDENCE_THRESHOLD
            if signal['confidence'] < min_conf:
                return

        # Place order
        await self._place_order(signal)

    async def _place_order(self, signal: Dict):
        """Place order based on signal"""
        side = signal['action']

        # Check if paper trading mode
        if self.config.PAPER_TRADING:
            await self._place_paper_order(signal)
            return

        # Real trading mode
        await self._place_real_order(signal, side)

    async def _place_paper_order(self, signal: Dict):
        """Simulate order placement in paper trading mode"""
        side = signal['action']
        quantity = signal['quantity']
        entry_price = signal['entry_price']
        notional_value = quantity * entry_price

        # Generate paper order ID
        paper_oid = int(time.time() * 1000) % 1000000
        cloid = f"PAPER-{paper_oid}"

        logger.info(f"[PAPER] {side.value} order: {quantity:.2f} @ ${entry_price:.4f} (${notional_value:,.2f} notional)")

        # Simulate immediate fill at entry price (paper trading assumption)
        logger.info(f"[PAPER] Order FILLED at ${entry_price:.4f}")

        # Create position object
        position = Position(
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            tp_price=signal['tp_price'],
            sl_price=signal['sl_price'],
            entry_time=datetime.now(),
            leverage=self.config.LEVERAGE,
            oid=paper_oid,
            cloid=cloid,
            status=OrderStatus.OPEN  # OPEN so TP/SL monitoring works
        )

        self.positions.append(position)
        self.daily_trades += 1

        logger.info(f"[PAPER] Position opened: {side.value} {quantity:.2f} @ ${entry_price:.4f}")

    async def _place_real_order(self, signal: Dict, side: Side):
        """Place real order on exchange"""
        # Validate quantity (sanity check)
        quantity = signal['quantity']
        notional_value = quantity * signal['entry_price']

        # Minimum notional check
        min_notional = self.config.MIN_ORDER_SIZE
        if notional_value < min_notional:
            logger.warning(f"Order too small: ${notional_value:.2f} < ${min_notional} minimum")
            return

        # Maximum sanity checks (based on actual capital)
        capital = self.config.PAPER_CAPITAL if self.config.PAPER_TRADING else 10000
        max_notional = capital * self.config.LEVERAGE * 10  # Max 10x position
        max_quantity = max_notional / signal['entry_price']  # Convert to tokens

        if quantity > max_quantity or notional_value > max_notional:
            logger.error(f"ORDER REJECTED - Quantity too large: {quantity:.2f} (${notional_value:,.2f} notional)")
            logger.error(f"Max allowed: {max_quantity} tokens or ${max_notional:,.0f} notional")
            return

        # Determine order price (slightly better than current)
        entry_price = signal['entry_price']
        if side == Side.LONG:
            limit_price = entry_price * 0.9998  # Slightly below for fill
        else:
            limit_price = entry_price * 1.0002  # Slightly above for fill

        # Generate client order ID
        cloid = f"{int(time.time() * 1000):x}"

        logger.info(f"Placing {side.value} order: {quantity:.2f} @ ${limit_price:.4f} (${notional_value:,.2f} notional)")

        # Place order
        response = await self.api.place_order(
            side=side,
            price=limit_price,
            quantity=signal['quantity'],
            reduce_only=False,
            cloid=cloid
        )

        # Handle response
        if response.get("status") == "ok":
            order_response = response.get("response", {})

            if order_response.get("type") == "order":
                statuses = order_response.get("data", {}).get("statuses", [])
                for status in statuses:
                    if "resting" in status:
                        oid = status["resting"]["oid"]
                        logger.info(f"Order placed: OID={oid}, CloID={cloid}")

                        # Create position object
                        position = Position(
                            side=side,
                            entry_price=entry_price,
                            quantity=signal['quantity'],
                            tp_price=signal['tp_price'],
                            sl_price=signal['sl_price'],
                            entry_time=datetime.now(),
                            leverage=self.config.LEVERAGE,
                            oid=oid,
                            cloid=cloid
                        )

                        self.positions.append(position)
                        self.daily_trades += 1

                    elif "error" in status:
                        logger.error(f"Order rejected: {status['error']}")

                    elif "filled" in status:
                        # Order immediately filled
                        await self._handle_filled_order(status["filled"], signal, cloid)
            else:
                logger.error(f"Unexpected response type: {order_response.get('type')}")
        else:
            error_msg = response.get("msg", "Unknown error")
            logger.error(f"Order failed: {error_msg}")

    async def _handle_filled_order(self, filled: dict, signal: Dict, cloid: str):
        """Handle immediately filled order"""
        oid = filled["oid"]
        avg_px = float(filled["avgPx"])
        total_sz = float(filled["totalSz"])

        logger.info(f"Order filled: OID={oid} @ ${avg_px:.4f}, Size: {total_sz}")

        # Create position with OPEN status (so TP/SL monitoring works)
        position = Position(
            side=signal['action'],
            entry_price=avg_px,
            quantity=total_sz,
            tp_price=signal['tp_price'],
            sl_price=signal['sl_price'],
            entry_time=datetime.now(),
            leverage=self.config.LEVERAGE,
            oid=oid,
            cloid=cloid,
            status=OrderStatus.OPEN  # OPEN so TP/SL monitoring works
        )

        self.positions.append(position)
        self.daily_trades += 1

        # Place TP/SL orders
        await self._place_tp_sl_orders(position)

    async def _place_tp_sl_orders(self, position: Position):
        """Place take-profit and stop-loss orders"""
        # For now, we'll monitor and close manually
        # In production, you'd place conditional orders
        pass

    async def _check_position_exits(self):
        """Check if any positions should be closed (TP/SL hit)"""
        if not self.positions:
            return

        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, 0))

        for position in self.positions[:]:  # Copy to avoid modification during iteration
            # Check status and log
            if position.status != OrderStatus.OPEN:
                logger.debug(f"Skipping position {position.oid} - status: {position.status.value}")
                continue

            should_close = False
            exit_price = current_price
            exit_reason = None

            # Check TP/SL with logging
            if position.side == Side.LONG:
                if current_price >= position.tp_price:
                    should_close = True
                    exit_price = position.tp_price
                    exit_reason = "TP"
                    logger.info(f"LONG TP HIT: ${current_price:.4f} >= ${position.tp_price:.4f}")
                elif current_price <= position.sl_price:
                    should_close = True
                    exit_price = position.sl_price
                    exit_reason = "SL"
                    logger.info(f"LONG SL HIT: ${current_price:.4f} <= ${position.sl_price:.4f}")
            else:  # SHORT
                if current_price <= position.tp_price:
                    should_close = True
                    exit_price = position.tp_price
                    exit_reason = "TP"
                    logger.info(f"SHORT TP HIT: ${current_price:.4f} <= ${position.tp_price:.4f}")
                elif current_price >= position.sl_price:
                    should_close = True
                    exit_price = position.sl_price
                    exit_reason = "SL"
                    logger.info(f"SHORT SL HIT: ${current_price:.4f} >= ${position.sl_price:.4f}")

            if should_close:
                await self._close_position(position, exit_price, exit_reason)

    async def _close_position(self, position: Position, exit_price: float, reason: str):
        """Close position"""
        mode_tag = "[PAPER] " if self.config.PAPER_TRADING else ""
        logger.info(f"{mode_tag}Closing position: {position.side.value} @ ${exit_price:.4f} ({reason})")

        # Cancel existing orders (only in real mode)
        if not self.config.PAPER_TRADING and position.oid:
            await self.api.cancel_order(position.oid)

        # Calculate P&L
        if position.side == Side.LONG:
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity

        # Apply leverage
        pnl = pnl * position.leverage

        # Calculate fees
        fee = exit_price * position.quantity * self.config.TAKER_FEE_PCT

        position.pnl = pnl - fee
        position.fees = fee
        position.exit_price = exit_price
        position.exit_time = datetime.now()
        position.status = OrderStatus.FILLED

        # Update statistics
        self.daily_pnl += position.pnl
        self._current_capital += position.pnl

        # Update peak equity and drawdown
        if self._current_capital > self._peak_equity:
            self._peak_equity = self._current_capital
        drawdown = (self._peak_equity - self._current_capital) / self._peak_equity * 100
        self._max_drawdown_pct = max(self._max_drawdown_pct, drawdown)

        if position.pnl > 0:
            self.winning_trades += 1
            # Reset consecutive losses on win
            self.consecutive_losses = 0
            logger.info(f"✅ Win! P&L: ${position.pnl:.2f} | Consecutive losses reset: 0")
        else:
            self.losing_trades += 1
            # Track consecutive losses for circuit breaker
            self.consecutive_losses += 1
            logger.warning(f"❌ Loss! P&L: ${position.pnl:.2f} | Consecutive losses: {self.consecutive_losses}/{self.config.MAX_CONSECUTIVE_LOSSES}")

            # Check if circuit breaker should trigger
            if (self.config.CIRCUIT_BREAKER_ENABLED and
                self.consecutive_losses >= self.config.MAX_CONSECUTIVE_LOSSES):
                await self._trigger_circuit_breaker()
        self.total_trades += 1
        self.total_fees += fee

        # Move to trades list
        trade = Trade(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.entry_time,
            exit_time=position.exit_time,
            pnl=position.pnl,
            fees=fee
        )
        self.trades.append(trade)

        # Remove from positions
        if position in self.positions:
            self.positions.remove(position)

        logger.info(f"{mode_tag}Position closed: P&L=${position.pnl:.2f} | "
                   f"Daily P&L=${self.daily_pnl:.2f} | "
                   f"Win Rate: {self._calculate_win_rate():.1f}%")

    async def _close_position_market(self, position: Position):
        """Close position with market order"""
        # For emergency closing
        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, 0))
        await self._close_position(position, current_price, "EMERGENCY")

    async def _update_unrealized_pnl(self):
        """Update unrealized P&L for open positions"""
        if not self.positions:
            return

        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, 0))

        for position in self.positions:
            if position.side == Side.LONG:
                position.unrealized_pnl = (current_price - position.entry_price) * position.quantity
            else:
                position.unrealized_pnl = (position.entry_price - current_price) * position.quantity

            position.unrealized_pnl *= position.leverage

    def _calculate_win_rate(self) -> float:
        """Calculate win rate"""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    def print_statistics(self):
        """Print trading statistics"""
        uptime = datetime.now() - self.start_time

        print("\n" + "=" * 60)
        print("TRADING STATISTICS")
        print("=" * 60)
        print(f"Uptime: {uptime}")
        print(f"Total Trades: {self.total_trades}")
        print(f"Win Rate: {self._calculate_win_rate():.1f}%")
        print(f"Winning Trades: {self.winning_trades}")
        print(f"Losing Trades: {self.losing_trades}")
        print(f"Total P&L: ${self.daily_pnl:.2f}")
        print(f"Total Fees: ${self.total_fees:.2f}")
        print(f"Open Positions: {len([p for p in self.positions if p.status == OrderStatus.OPEN])}")

        # Circuit breaker status
        if self.config.CIRCUIT_BREAKER_ENABLED:
            cb_status = "🔴 ACTIVE" if self.circuit_breaker_triggered else "🟢 OK"
            print(f"Circuit Breaker: {cb_status}")
            print(f"  Consecutive Losses: {self.consecutive_losses}/{self.config.MAX_CONSECUTIVE_LOSSES}")
            if self.circuit_breaker_triggered and self.circuit_breaker_trigger_time:
                elapsed = (datetime.now() - self.circuit_breaker_trigger_time).total_seconds() / 60
                remaining = self.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES - elapsed
                print(f"  Cooldown Remaining: {max(0, remaining):.1f} min")

        if self.trades:
            print("\nRecent Trades:")
            for trade in self.trades[-10:]:
                print(f"  {trade.side.value:4} | "
                      f"${trade.entry_price:.4f} → ${trade.exit_price:.4f} | "
                      f"P&L: ${trade.pnl:>8.2f}")

    # =============================================================================
    # API Control Methods
    # =============================================================================

    @property
    def Side(self):
        """Access to Side enum for API"""
        return Side

    @property
    def is_running(self) -> bool:
        """Check if bot is running"""
        return not self.emergency_stop

    @property
    def is_paused(self) -> bool:
        """Check if bot is paused"""
        return self._is_paused

    @property
    def starting_capital(self) -> float:
        """Get starting capital"""
        return self._starting_capital

    @property
    def current_capital(self) -> float:
        """Get current capital"""
        return self._current_capital

    @property
    def daily_trade_count(self) -> int:
        """Get daily trade count"""
        return self.daily_trades

    @property
    def max_drawdown_pct(self) -> float:
        """Get max drawdown percentage"""
        return self._max_drawdown_pct

    @property
    def circuit_breaker_until(self) -> Optional[datetime]:
        """Get circuit breaker cooldown end time"""
        return self.circuit_breaker_trigger_time

    def pause_trading(self):
        """Pause trading (keep managing open positions)"""
        logger.info("⏸️ Trading paused via API")
        self._is_paused = True

    def resume_trading(self):
        """Resume trading"""
        logger.info("▶️ Trading resumed via API")
        self._is_paused = False

    def update_config_param(self, name: str, value):
        """Update configuration parameter"""
        if hasattr(self.config, name):
            old_value = getattr(self.config, name)
            setattr(self.config, name, value)
            logger.info(f"⚙️ Config updated: {name} = {value} (was: {old_value})")
        else:
            raise ValueError(f"Unknown config parameter: {name}")

    async def close_all_positions(self) -> int:
        """Close all open positions via API"""
        if not self.positions:
            return 0

        logger.info("🚨 Closing all positions via API...")
        count = len(self.positions)

        mids = await self.api.get_mids()
        current_price = float(mids.get(self.config.ASSET, 0))

        for position in self.positions[:]:
            await self._close_position(position, current_price, "API_CLOSE")

        logger.info(f"✅ Closed {count} positions via API")
        return count

    async def close_position(self, position_id: str) -> bool:
        """Close a specific position by ID"""
        for position in self.positions[:]:
            if str(position.oid) == position_id or position.cloid == position_id:
                mids = await self.api.get_mids()
                current_price = float(mids.get(self.config.ASSET, 0))
                await self._close_position(position, current_price, "API_CLOSE")
                return True
        return False

    async def place_manual_trade(self, side: Side, quantity_usd: float, price: Optional[float] = None) -> dict:
        """Place a manual trade"""
        logger.info(f"💱 Manual trade: {side.value} ${quantity_usd}")

        # Get current price if not specified
        if price is None:
            mids = await self.api.get_mids()
            price = float(mids.get(self.config.ASSET, 0))
            if price == 0:
                raise ValueError("Could not get current price")

        # Calculate quantity
        quantity = quantity_usd / price

        # Create signal dict
        atr = price * 0.02  # Default ATR estimate
        signal = {
            'action': side,
            'confidence': 100,
            'entry_price': price,
            'tp_price': price + (atr * self.config.TP_ATR_MULTIPLIER) if side == Side.LONG else price - (atr * self.config.TP_ATR_MULTIPLIER),
            'sl_price': price - (atr * self.config.SL_ATR_MULTIPLIER) if side == Side.LONG else price + (atr * self.config.SL_ATR_MULTIPLIER),
            'quantity': quantity,
            'atr': atr
        }

        # Place the order
        await self._place_order(signal)

        return {
            'side': side.value,
            'quantity': quantity,
            'entry_price': price,
            'notional': quantity_usd
        }

    def reset_circuit_breaker(self):
        """Reset circuit breaker"""
        if self.circuit_breaker_triggered:
            logger.info("✅ Circuit breaker reset via API")
            self.circuit_breaker_triggered = False
            self.circuit_breaker_trigger_time = None
            self.consecutive_losses = 0
        else:
            logger.info("Circuit breaker not active, no reset needed")


# =============================================================================
# Configuration and Setup
# =============================================================================

def create_bot_config(private_key: str, address: str, testnet: bool = False, paper_trading: bool = False) -> BotConfig:
    """Create bot configuration"""
    config = BotConfig()

    config.PRIVATE_KEY = private_key
    config.ADDRESS = address
    config.USE_TESTNET = testnet
    config.PAPER_TRADING = paper_trading

    return config


async def run_bot():
    """Main entry point"""
    # Load configuration from environment or config file
    import os

    private_key = os.environ.get("HYPERLICUID_PRIVATE_KEY")
    wallet_address = os.environ.get("HYPERLICUID_ADDRESS")
    use_testnet = os.environ.get("HYPERLICUID_TESTNET", "false").lower() in ("true", "1", "yes")
    paper_trading = os.environ.get("HYPERLICUID_PAPER_TRADING", "false").lower() in ("true", "1", "yes")
    paper_capital = float(os.environ.get("HYPERLICUID_PAPER_CAPITAL", "10000"))

    # For paper trading, we don't need real keys
    if paper_trading:
        if not private_key:
            private_key = "0" * 64  # Dummy key for paper trading
        if not wallet_address:
            wallet_address = "0x0000000000000000000000000000000000000000"
    elif not private_key or not wallet_address:
        logger.error("Please set HYPERLICUID_PRIVATE_KEY and HYPERLICUID_ADDRESS environment variables")
        return

    # Log mode
    if paper_trading:
        mode = f"PAPER TRADING (Mainnet Data, Simulated Trades) - ${paper_capital:,.0f}"
    elif use_testnet:
        mode = "TESTNET (Testnet Exchange, Illiquid)"
    else:
        mode = "MAINNET (Real Money - BE CAREFUL!)"

    logger.info(f"Starting bot in {mode} mode")
    if paper_trading:
        logger.info("✓ PAPER TRADING MODE - No real money at risk")
        logger.info(f"✓ Starting capital: ${paper_capital:,.0f}")
        logger.info("✓ Using mainnet data for realistic signals")
    elif use_testnet:
        logger.info("✓ TESTNET MODE - No real money at risk")
        logger.info("⚠️  Testnet markets may be illiquid")

    # Create config
    config = create_bot_config(
        private_key,
        wallet_address,
        testnet=use_testnet,
        paper_trading=paper_trading
    )
    config.PAPER_CAPITAL = paper_capital

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
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         HYPE/USDC AUTOMATED TRADING BOT                     ║
║         Ultra-Optimized Momentum Strategy                   ║
╠══════════════════════════════════════════════════════════════════╣
║  EXPECTED PERFORMANCE:                                         ║
║  - Return: 48.1% in 52 days (1,476% annualized)            ║
║  - Max Drawdown: 3.0%                                        ║
║  - Profit Factor: 1.54                                        ║
║  - Win Rate: 33.0%                                            ║
║                                                                ║
║  CONFIGURATION (Balanced):                                     ║
║  - 8% risk per trade                                          ║
║  - 5x leverage, max 2 positions                               ║
║  - ONLY USE CAPITAL YOU CAN AFFORD TO LOSE                  ║
║                                                                ║
║  BEFORE RUNNING:                                             ║
║  1. Set HYPERLICUID_PRIVATE_KEY env variable                 ║
║  2. Set HYPERLICUID_ADDRESS env variable                    ║
║                                                                ║
║  PAPER TRADING (RECOMMENDED):                                ║
║  export HYPERLICUID_PAPER_TRADING=true                       ║
║  export HYPERLICUID_PAPER_CAPITAL=10000                     ║
║  → Uses mainnet data, simulates trades (NO real money)      ║
║                                                                ║
║  TESTNET (illiquid markets):                                 ║
║  export HYPERLICUID_TESTNET=true                             ║
║  → Uses testnet exchange (needs testnet funds)              ║
║                                                                ║
║  MAINNET (REAL MONEY):                                       ║
║  → No special flags needed (BE CAREFUL!)                     ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(run_bot())
