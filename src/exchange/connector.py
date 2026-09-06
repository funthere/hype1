"""
Hyperliquid Exchange API connector using official SDK
"""

import asyncio
import logging
import math
from typing import List, Optional, Dict

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

from ..core.config import BotConfig, Side
from ..execution import OrderRequest, OrderSubmission, PositionRead, SubmissionStatus
from .retry import retry_with_backoff

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
        """Probe the exchange with a live request.

        Deliberately avoids get_asset_index for the configured asset: that
        result is cached, so a repeat check would report healthy without
        touching the network.
        """
        try:
            await asyncio.to_thread(self.info.meta)
            self._connected = True
            self._last_error = None
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            logger.error(f"Connection check failed: {e}")
            return False

    async def get_asset_index(self, coin: Optional[str] = None) -> int:
        """Get an asset index from exchange metadata.

        The configured asset remains cached; request-scoped assets are looked up
        independently so multi-asset callers cannot accidentally trade HYPE.
        """
        target_coin = coin or self.config.ASSET
        if target_coin == self.config.ASSET and self._asset_index is not None:
            return self._asset_index

        try:
            meta_data = await asyncio.to_thread(self.info.meta)
            for index, asset in enumerate(meta_data["universe"]):
                if asset["name"] == target_coin:
                    if target_coin == self.config.ASSET:
                        self._asset_index = index
                        self.config.ASSET_INDEX = index
                    logger.info("Found %s at index %s", target_coin, index)
                    return index
            raise ValueError(f"{target_coin} not found in universe")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Failed to get asset index for %s: %s", target_coin, exc)
            raise

    @staticmethod
    def _order_type(order_type: str) -> Dict:
        """Map supported internal time-in-force names to SDK payloads."""
        if order_type == "post_only":
            return {"limit": {"tif": "Alo"}}
        if order_type == "ioc":
            return {"limit": {"tif": "Ioc"}}
        return {"limit": {"tif": "Gtc"}}

    @staticmethod
    def _extract_order_id(response: Dict) -> Optional[int]:
        """Extract an order id from known Hyperliquid acknowledgement shapes."""
        for candidate in (
            response.get("oid"),
            response.get("order", {}).get("oid"),
            response.get("data", {}).get("oid"),
        ):
            if candidate is not None:
                try:
                    return int(candidate)
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _coerce_side(side) -> Side:
        """Normalize any side-like value (Side, str, or foreign enum) to Side.

        Side direction comparisons must never silently fail: a plain string
        or a foreign enum compared against ``Side`` evaluates False and used
        to flip every order to a sell.
        """
        raw = getattr(side, "value", side)
        try:
            return Side(raw)
        except ValueError:
            raise ValueError(f"Unusable order side: {side!r}") from None

    async def submit_order(self, request: OrderRequest) -> OrderSubmission:
        """Submit one idempotent order without treating acknowledgement as a fill.

        Order writes are intentionally *not* retried. A transport error after a
        write is ambiguous; callers must reconcile using the stable client ID
        before issuing another request.
        """
        side = self._coerce_side(request.side)
        try:
            await self.get_asset_index(request.coin)
            raw = await asyncio.to_thread(
                self.exchange.order,
                coin=request.coin,
                is_buy=side == Side.LONG,
                sz=request.quantity,
                limit_px=request.price,
                order_type=self._order_type(request.order_type),
                reduce_only=request.reduce_only,
                cloid=request.client_order_id or None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Order submission outcome unknown for %s: %s",
                request.client_order_id,
                exc,
            )
            return OrderSubmission(
                status=SubmissionStatus.UNKNOWN,
                client_order_id=request.client_order_id,
                message=str(exc),
            )

        response = raw.get("response", {}) if isinstance(raw, dict) else {}
        if isinstance(raw, dict) and raw.get("status") == "ok":
            return OrderSubmission(
                status=SubmissionStatus.ACCEPTED,
                client_order_id=request.client_order_id,
                exchange_order_id=self._extract_order_id(response),
                raw=response,
            )

        message = response.get("error", "Unknown exchange rejection")
        logger.error("Order rejected for %s: %s", request.client_order_id, message)
        return OrderSubmission(
            status=SubmissionStatus.REJECTED,
            client_order_id=request.client_order_id,
            message=message,
            raw=response,
        )

    async def place_order(
        self,
        side: Side,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        cloid: Optional[str] = None,
        order_type: str = "limit",
        coin: Optional[str] = None,
    ) -> Dict:
        """Compatibility wrapper returning the legacy acknowledgement mapping.

        New execution lifecycle code should use :meth:`submit_order`; neither
        interface interprets an accepted order as an executed fill.
        """
        request = OrderRequest(
            coin=coin or self.config.ASSET,
            side=side,
            quantity=quantity,
            price=price,
            client_order_id=cloid or "",
            reduce_only=reduce_only,
            order_type=order_type,
        )
        submission = await self.submit_order(request)
        if submission.status == SubmissionStatus.ACCEPTED:
            response = dict(submission.raw)
            if submission.exchange_order_id is not None:
                response.setdefault("oid", submission.exchange_order_id)
            return {"status": "ok", "response": response}
        if submission.status == SubmissionStatus.UNKNOWN:
            return {"status": "unknown", "msg": submission.message}
        return {"status": "error", "msg": submission.message}

    async def cancel_order(self, oid: int) -> Dict:
        """Cancel order by order ID"""
        try:

            async def _cancel():
                return await asyncio.to_thread(
                    self.exchange.cancel, coin=self.config.ASSET, oid=oid
                )

            result = await retry_with_backoff(_cancel)

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

            async def _get_orders():
                return await asyncio.to_thread(self.info.open_orders, self.config.ASSET)

            orders = await retry_with_backoff(_get_orders)
            return orders if orders else []
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    async def get_positions(self) -> PositionRead:
        """Read current exchange positions without masking a failed snapshot as flat.

        A caller must check ``available`` before making a destructive lifecycle
        decision. ``PositionRead`` remains iterable for temporary legacy-call
        compatibility.
        """
        if not self.address:
            return PositionRead.unavailable("Missing account address")

        try:

            async def _get_positions():
                return await asyncio.to_thread(self.info.user_state, self.address)

            user_state = await retry_with_backoff(_get_positions)
            positions = []
            for pos_data in user_state.get("assetPositions", []):
                position = pos_data.get("position", {})
                if position:
                    positions.append(position)
            return PositionRead.success(positions)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Failed to read exchange positions: %s", exc)
            return PositionRead.unavailable(str(exc))

    async def get_mids(self) -> Dict:
        """Get current mid prices for all assets"""
        try:

            async def _get_mids():
                return await asyncio.to_thread(self.info.all_mids)

            return await retry_with_backoff(_get_mids)
        except Exception as e:
            logger.error(f"Failed to get mids: {e}")
            return {}

    async def get_meta_and_asset_ctxs(self) -> tuple:
        """Get universe metadata and per-asset market context.

        Returns the raw ``(meta, asset_ctxs)`` pair; callers parse it for
        their own needs (coin universe, funding rates, mark prices).
        Raises on failure so callers can distinguish an outage from empty
        data.
        """
        raw = await retry_with_backoff(
            lambda: asyncio.to_thread(self.info.meta_and_asset_ctxs)
        )
        if not raw or len(raw) < 2:
            raise ValueError("meta_and_asset_ctxs returned unexpected format")
        return raw

    async def get_candles(
        self,
        coin: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> list:
        """Get candlestick snapshots for a coin in [start_time_ms, end_time_ms].

        Returns a list of raw candle dicts (keys: t, T, s, i, o, c, h, l, v, n).
        Raises on failure so callers can distinguish an outage from empty data.
        """
        raw = await retry_with_backoff(
            lambda: asyncio.to_thread(
                self.info.candles_snapshot,
                coin,
                interval,
                start_time_ms,
                end_time_ms,
            )
        )
        return list(raw) if raw else []

    async def get_funding_rate(self, coin: Optional[str] = None) -> Dict:
        """Get funding rate for a specific asset.

        Args:
            coin: Asset symbol (defaults to configured ASSET).

        Returns:
            Dict with keys: funding_rate, mark_px, mid_px, open_interest.
            Empty dict on failure.
        """
        target_coin = coin or self.config.ASSET
        try:

            async def _get_funding():
                return await asyncio.to_thread(self.info.meta_and_asset_ctxs)

            raw = await retry_with_backoff(_get_funding)

            if not raw or len(raw) < 2:
                logger.warning("meta_and_asset_ctxs returned unexpected format")
                return {}

            meta, ctxs = raw[0], raw[1]
            universe = meta.get("universe", [])

            for idx, ctx in enumerate(ctxs):
                if idx >= len(universe):
                    break
                if universe[idx].get("name") == target_coin:
                    return {
                        "funding_rate": float(ctx.get("funding", "0") or "0"),
                        "mark_px": float(ctx.get("markPx", "0") or "0"),
                        "mid_px": float(ctx.get("midPx", "0") or "0"),
                        "open_interest": float(ctx.get("openInterest", "0") or "0"),
                    }

            logger.warning(f"{target_coin} not found in universe for funding rate")
            return {}

        except Exception as e:
            logger.error(f"Failed to get funding rate for {target_coin}: {e}")
            return {}

    async def close_position(
        self,
        side: Side,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict:
        """Close an existing position by placing a reduce-only order.

        Args:
            side: The side of the position being closed (opposite order side).
            quantity: Size to close.
            price: Limit price (defaults to market via IOC).

        Returns:
            Dict with order result.
        """
        try:
            await self.get_asset_index()

            # Use IOC for immediate fill if no price specified
            if price is None:
                mids = await self.get_mids()
                price = float(mids.get(self.config.ASSET, 0))
                if price <= 0:
                    return {"status": "error", "msg": "Cannot close: no valid price"}

            # Close = opposite side with reduce_only
            close_side = Side.SHORT if side == Side.LONG else Side.LONG

            async def _close():
                return await asyncio.to_thread(
                    self.exchange.order,
                    coin=self.config.ASSET,
                    is_buy=(close_side == Side.LONG),
                    sz=quantity,
                    limit_px=price,
                    order_type={"limit": {"tif": "Ioc"}},
                    reduce_only=True,
                )

            result = await retry_with_backoff(_close)

            if result.get("status") == "ok":
                logger.info(f"Closed {side.value} position: {quantity} @ {price}")
                return {"status": "ok", "response": result.get("response", {})}
            else:
                error_msg = result.get("response", {}).get("error", "Unknown error")
                logger.error(f"Close position failed: {error_msg}")
                return {"status": "error", "msg": error_msg}

        except Exception as e:
            logger.error(f"Close position exception: {e}")
            return {"status": "error", "msg": str(e)}

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

    # ------------------------------------------------------------------
    # Spot market methods (for funding arb delta-neutral hedge)
    # ------------------------------------------------------------------

    async def place_spot_order(
        self,
        coin: str,
        is_buy: bool,
        price: float,
        quantity: float,
        order_type: str = "ioc",
    ) -> Dict:
        """Place a spot order on HyperLiquid.

        HyperLiquid spot assets are prefixed with '@' internally (e.g. '@BTC').
        Uses the same exchange.order() with coin='<ASSET>' for perp-converted
        spot or the raw spot token name.

        Args:
            coin: Spot coin name (e.g. 'BTC', 'ETH', 'HYPE')
            is_buy: True to buy, False to sell
            price: Limit price
            quantity: Size in base asset
            order_type: "ioc" (default for immediate fill) or "limit" (GTC)

        Returns:
            Dict with order result
        """
        try:
            # Build order type
            if order_type == "ioc":
                hl_order_type = {"limit": {"tif": "Ioc"}}
            elif order_type == "post_only":
                hl_order_type = {"limit": {"tif": "Alo"}}
            else:
                hl_order_type = {"limit": {"tif": "Gtc"}}

            # HyperLiquid uses the coin name directly for perp-spot
            # (the SDK handles the internal naming)
            order_result = await asyncio.to_thread(
                self.exchange.order,
                coin=coin,
                is_buy=is_buy,
                sz=quantity,
                limit_px=price,
                order_type=hl_order_type,
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
                logger.error(f"Spot order failed for {coin}: {error_msg}")
                return {"status": "error", "msg": error_msg}

        except Exception as e:
            logger.error(f"Spot order exception for {coin}: {e}")
            return {"status": "error", "msg": str(e)}

    async def get_spot_balance(self, coin: str) -> float:
        """Get spot token balance for a given coin.

        Returns:
            Balance as float, 0.0 if not found.
        """
        try:
            user_state = await asyncio.to_thread(self.info.user_state, self.address)
            # Spot balances are in 'balances' array
            balances = user_state.get("balances", [])
            for b in balances:
                if b.get("coin", "").upper() == coin.upper():
                    return float(b.get("total", 0))
            return 0.0
        except Exception as e:
            logger.error(f"Failed to get spot balance for {coin}: {e}")
            return 0.0

    async def get_spot_mid_price(self, coin: str) -> Optional[float]:
        """Get mid price for a spot market.

        Uses all_mids which returns both perp and spot prices.
        """
        try:
            mids = await asyncio.to_thread(self.info.all_mids)
            # Spot mids may be under the same key or '@COIN' format
            for key in [coin, f"@{coin}", coin.upper()]:
                val = mids.get(key)
                if val:
                    return float(val)
            return None
        except Exception as e:
            logger.error(f"Failed to get spot mid for {coin}: {e}")
            return None

    async def get_sz_decimals(self, coin: Optional[str] = None) -> int:
        """Get the exchange size precision (szDecimals) for a coin."""
        target_coin = coin or self.config.ASSET
        await self.get_asset_index(target_coin)
        meta_data = await asyncio.to_thread(self.info.meta)
        for asset in meta_data["universe"]:
            if asset["name"] == target_coin:
                return int(asset.get("szDecimals", 0))
        raise ValueError(f"{target_coin} not found in universe")

    async def round_size(self, coin: Optional[str], quantity: float) -> float:
        """Floor a quantity to the exchange's size precision for a coin.

        Orders with more precision than szDecimals are rejected by the
        exchange; flooring guarantees a valid size (never rounds up into
        more exposure than requested).
        """
        target_coin = coin or self.config.ASSET
        sz_decimals = await self.get_sz_decimals(target_coin)
        factor = 10**sz_decimals
        return math.floor(quantity * factor) / factor
