"""
Real-time market data feed via WebSocket
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable, List, Optional, Dict

import websockets
import pandas as pd

from ..core.config import BotConfig

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """
    Real-time market data via WebSocket with auto-reconnect.

    Provides candle data updates via registered callbacks.
    Handles connection failures and automatic reconnection with exponential backoff.
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.ws_url = config.WS_URL
        self.callbacks: List[Callable] = []
        self.candles: List[pd.DataFrame] = []
        self.current_candle: Optional[Dict] = None
        self.connected = False
        self._ws = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._base_reconnect_delay = 2  # seconds

    def on_candle_update(self, callback: Callable):
        """Register callback for candle updates"""
        self.callbacks.append(callback)

    def remove_callback(self, callback: Callable):
        """Remove a registered callback"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    async def connect(self):
        """
        Connect to WebSocket with auto-reconnect

        Will automatically reconnect on connection failure with exponential backoff.
        """
        while self._reconnect_attempts < self._max_reconnect_attempts:
            try:
                logger.info(f"Connecting to {self.ws_url}...")

                self._ws = await websockets.connect(self.ws_url)
                self.connected = True
                self._reconnect_attempts = 0  # Reset on successful connect

                logger.info("WebSocket connected")

                # Subscribe to market data
                await self._subscribe(self._ws)

                # Listen for messages
                await self._listen()

            except Exception as e:
                self.connected = False
                self._reconnect_attempts += 1

                if self._reconnect_attempts < self._max_reconnect_attempts:
                    delay = self._base_reconnect_delay * (
                        2 ** (self._reconnect_attempts - 1)
                    )
                    delay = min(delay, 60)  # Cap at 60 seconds

                    logger.warning(
                        f"WebSocket connection failed: {e}. "
                        f"Reconnecting in {delay}s (attempt {self._reconnect_attempts}/{self._max_reconnect_attempts})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Max reconnection attempts reached. Giving up."
                    )
                    raise

    async def disconnect(self):
        """Disconnect from WebSocket"""
        self.connected = False
        if self._ws:
            await self._ws.close()
            logger.info("WebSocket disconnected")

    async def _subscribe(self, ws):
        """Subscribe to market data channel"""
        subscribe_msg = {
            "method": "subscribe",
            "subscription": {
                "type": "candle",
                "coin": self.config.ASSET,
                "interval": self.config.TIMEFRAME,
            },
        }

        await ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to {self.config.ASSET} {self.config.TIMEFRAME} candles")

    async def _listen(self):
        """Listen for incoming WebSocket messages"""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if not self.connected:
                    break

                await self._handle_message(message)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self.connected = False
        except Exception as e:
            logger.error(f"Error in listen loop: {e}")
            self.connected = False

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            data = json.loads(message)

            if data.get("channel") == "candle":
                candle = data["data"]
                await self._process_candle(candle)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _process_candle(self, candle: dict):
        """
        Process candle data and notify callbacks

        Args:
            candle: Raw candle data from WebSocket
        """
        try:
            candle_data = {
                "timestamp": datetime.fromtimestamp(candle["t"] / 1000),
                "open": float(candle["o"]),
                "high": float(candle["h"]),
                "low": float(candle["l"]),
                "close": float(candle["c"]),
                "volume": float(candle["v"]),
            }

            self.current_candle = candle_data

            # Notify all registered callbacks
            for callback in self.callbacks:
                try:
                    result = callback(candle_data)
                    # Check if result is a coroutine and await it
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Error in callback: {e}")

        except Exception as e:
            logger.error(f"Error processing candle: {e}")

    async def get_historical_candles(
        self, interval: str = None, limit: int = 100
    ) -> pd.DataFrame:
        """
        Fetch historical candle data from REST API

        Args:
            interval: Candle interval (defaults to config.TIMEFRAME)
            limit: Number of candles to fetch

        Returns:
            DataFrame with historical candle data
        """
        # This would use the info API to fetch historical data
        # Implementation depends on available Hyperliquid API endpoints
        # For now, return empty DataFrame
        logger.warning("Historical candle fetch not implemented")
        return pd.DataFrame()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self.connected

    @property
    def reconnect_attempts(self) -> int:
        """Get current reconnection attempt count"""
        return self._reconnect_attempts
