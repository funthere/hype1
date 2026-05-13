"""
Hyperliquid Exchange API connector using official SDK
"""

import asyncio
import logging
from typing import List, Optional, Dict

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

from ..core.config import BotConfig, Side

logger = logging.getLogger(__name__)


class HyperliquidAPI:
    """
    Hyperliquid API client with connection management and error handling.
    Handles all exchange operations including orders, positions, and account data.
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.account = Account.from_key(config.PRIVATE_KEY)
        self.address = self.account.address

        # Initialize SDK clients
        base_url = self.config.API_URL
        self.info = Info(base_url, skip_ws=True)

        # Create Exchange client with optional API wallet support
        exchange_kwargs = (
            {"account_address": config.ACCOUNT_ADDRESS}
            if config.ACCOUNT_ADDRESS
            else {}
        )
        self.exchange = Exchange(self.account, base_url, **exchange_kwargs)

        # Asset index cache
        self._asset_index: Optional[int] = None

        # Connection state
        self._connected = False
        self._last_error = None

    async def check_connection(self) -> bool:
        """Check if API connection is healthy"""
        try:
            await self.get_asset_index()
            self._connected = True
            self._last_error = None
            return True
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            logger.error(f"Connection check failed: {e}")
            return False

    async def get_asset_index(self) -> int:
        """Get asset index from exchange metadata"""
        if self._asset_index is not None:
            return self._asset_index

        try:
            meta_data = await asyncio.to_thread(self.info.meta)

            for i, asset in enumerate(meta_data["universe"]):
                if asset["name"] == self.config.ASSET:
                    self._asset_index = i
                    self.config.ASSET_INDEX = i
                    logger.info(f"Found {self.config.ASSET} at index {i}")
                    return i

            raise ValueError(f"{self.config.ASSET} not found in universe")

        except Exception as e:
            logger.error(f"Failed to get asset index: {e}")
            raise

    async def place_order(
        self,
        side: Side,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        cloid: Optional[str] = None,
        order_type: str = "limit",
    ) -> Dict:
        """
        Place order on the exchange.

        Args:
            side: Order side (LONG/SHORT)
            price: Limit price
            quantity: Order quantity in base asset
            reduce_only: Whether to reduce existing position
            cloid: Client order ID for idempotency
            order_type: "limit" (GTC), "post_only" (Post-Only/Alon), "ioc" (IOC)

        Returns:
            Dict with order result
        """
        try:
            await self.get_asset_index()

            # Build order_type parameter for HyperLiquid SDK
            if order_type == "post_only":
                hl_order_type = {"limit": {"tif": "Alo"}}
            elif order_type == "ioc":
                hl_order_type = {"limit": {"tif": "Ioc"}}
            else:
                hl_order_type = {"limit": {"tif": "Gtc"}}

            order_result = await asyncio.to_thread(
                self.exchange.order,
                coin=self.config.ASSET,
                is_buy=(side == Side.LONG),
                sz=quantity,
                limit_px=price,
                order_type=hl_order_type,
                reduce_only=reduce_only,
                cloid=cloid,
            )

            if order_result.get("status") == "ok":
                return {
                    "status": "ok",
                    "response": order_result.get("response", {}),
                }
            else:
                error_msg = order_result.get("response", {}).get(
                    "error", "Unknown error"
                )
                logger.error(f"Order failed: {error_msg}")
                return {"status": "error", "msg": error_msg}

        except Exception as e:
            logger.error(f"Order placement exception: {e}")
            return {"status": "error", "msg": str(e)}

    async def cancel_order(self, oid: int) -> Dict:
        """Cancel order by order ID"""
        try:
            result = await asyncio.to_thread(
                self.exchange.cancel, coin=self.config.ASSET, oid=oid
            )

            if result.get("status") == "ok":
                logger.info(f"Cancelled order {oid}")
                return {"status": "ok"}
            else:
                error_msg = result.get("response", {}).get("error", "Unknown error")
                return {"status": "error", "msg": error_msg}

        except Exception as e:
            logger.error(f"Cancel order exception: {e}")
            return {"status": "error", "msg": str(e)}

    async def cancel_all_orders(self) -> Dict:
        """Cancel all open orders for the asset"""
        try:
            open_orders = await self.get_open_orders()

            if open_orders:
                cancel_list = [
                    {"coin": self.config.ASSET, "oid": o["oid"]} for o in open_orders
                ]
                result = await asyncio.to_thread(self.exchange.bulk_cancel, cancel_list)

                if result.get("status") == "ok":
                    logger.info(f"Cancelled {len(cancel_list)} orders")
                    return {"status": "ok"}
                else:
                    error_msg = result.get("response", {}).get("error", "Unknown error")
                    return {"status": "error", "msg": error_msg}

            return {"status": "ok"}

        except Exception as e:
            logger.error(f"Cancel all orders exception: {e}")
            return {"status": "error", "msg": str(e)}

    async def get_open_orders(self) -> List[Dict]:
        """Get all open orders for the asset"""
        try:
            orders = await asyncio.to_thread(self.info.open_orders, self.config.ASSET)
            return orders if orders else []
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    async def get_positions(self) -> List[Dict]:
        """Get current positions from exchange"""
        try:
            if not self.address:
                return []

            user_state = await asyncio.to_thread(self.info.user_state, self.address)
            asset_positions = user_state.get("assetPositions", [])

            positions = []
            for pos_data in asset_positions:
                position = pos_data.get("position", {})
                if position:  # Only include non-empty positions
                    positions.append(position)

            return positions

        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    async def get_mids(self) -> Dict:
        """Get current mid prices for all assets"""
        try:
            return await asyncio.to_thread(self.info.all_mids)
        except Exception as e:
            logger.error(f"Failed to get mids: {e}")
            return {}

    async def get_balance(self) -> Dict:
        """Get account balance and margin information"""
        try:
            if not self.address:
                return {}

            user_state = await asyncio.to_thread(self.info.user_state, self.address)
            margin_summary = user_state.get("marginSummary", {})
            cross_margin_summary = user_state.get("crossMarginSummary", {})

            return {
                "account_value": margin_summary.get("accountValue", 0),
                "total_margin_used": margin_summary.get("totalMarginUsed", 0),
                "total_npos": cross_margin_summary.get("totalNpos", 0),
                "margin_summary": margin_summary,
                "cross_margin_summary": cross_margin_summary,
            }

        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {}

    async def get_user_state(self) -> Dict:
        """Get full user state from exchange"""
        try:
            if not self.address:
                return {}
            return await asyncio.to_thread(self.info.user_state, self.address)
        except Exception as e:
            logger.error(f"Failed to get user state: {e}")
            return {}

    async def set_leverage(self, leverage: int, is_cross: bool = True) -> Dict:
        """Set leverage for the asset"""
        try:
            result = await asyncio.to_thread(
                self.exchange.update_leverage,
                leverage=leverage,
                coin=self.config.ASSET,
                is_cross=is_cross,
            )

            if result.get("status") == "ok":
                logger.info(f"Set leverage to {leverage}x")
                return {"status": "ok"}
            else:
                error_msg = result.get("response", {}).get("error", "Unknown error")
                return {"status": "error", "msg": error_msg}

        except Exception as e:
            logger.error(f"Set leverage exception: {e}")
            return {"status": "error", "msg": str(e)}

    async def get_order_status(self, oid: int) -> Optional[Dict]:
        """Get order status by order ID"""
        try:
            open_orders = await self.get_open_orders()

            for order in open_orders:
                if order.get("oid") == oid:
                    return order

            return None

        except Exception as e:
            logger.error(f"Failed to get order status: {e}")
            return None

    async def get_recent_fills(self, limit: int = 100) -> List[Dict]:
        """Get recent trade fills for the account"""
        try:
            if not self.address:
                return []

            fills = await asyncio.to_thread(self.info.user_fills, self.address)
            return fills[:limit] if fills else []

        except Exception as e:
            logger.error(f"Failed to get recent fills: {e}")
            return []

    @property
    def is_connected(self) -> bool:
        """Check if currently connected"""
        return self._connected

    @property
    def last_error(self) -> Optional[str]:
        """Get last connection error"""
        return self._last_error
