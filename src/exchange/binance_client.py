"""
Binance Futures REST API Client

Uses the public Futures API for reading funding rates and market data.
No auth required for market data endpoints.

Endpoints used:
  GET /fapi/v1/fundingRate?symbol=BTCUSDT&limit=1  → latest funding rate
  GET /fapi/v1/ticker/price?symbol=BTCUSDT         → latest price
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 0.15  # Binance is generous with public endpoints

# Symbol mapping: coin -> Binance futures symbol
SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "TST": "TSTUSDT",
    "UNI": "UNIUSDT",
    "W": "WUSDT",
    "ZEN": "ZENUSDT",
    "XRP": "XRPUSDT",
    "TON": "TONUSDT",
    "TRUMP": "TRUMPUSDT",
}


class BinanceClient:
    """Thin async wrapper around Binance Futures public REST API."""

    def __init__(self, base_url: str = "https://fapi.binance.com") -> None:
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=15.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Rate-limited request helper
    # ------------------------------------------------------------------

    async def _get(self, params: Dict[str, Any]) -> Any:
        client = await self._ensure_client()
        elapsed = time.time() - self._last_request_ts
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_ts = time.time()

        try:
            resp = await client.get("/fapi/v1/fundingRate", params=params)
            resp.raise_for_status()
            data = resp.json()
            return data
        except httpx.HTTPStatusError as exc:
            logger.error("Binance HTTP %d for %s: %s", exc.response.status_code, params, exc)
            return None
        except Exception as exc:
            logger.error("Binance request error for %s: %s", params, exc)
            return None

    async def _get_price(self, symbol: str) -> Optional[float]:
        """Fetch latest mark price for a symbol."""
        client = await self._ensure_client()
        try:
            resp = await client.get("/fapi/v1/ticker/price", params={"symbol": symbol})
            resp.raise_for_status()
            return float(resp.json()["price"])
        except Exception as exc:
            logger.error("Binance price fetch error for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_funding_rate(self, coin: str) -> Optional[Dict[str, Any]]:
        """Return normalised dict: {rate_hourly, oracle_px, ticker}.

        Binance funding rate is per 8h. Convert to hourly.
        """
        symbol = SYMBOL_MAP.get(coin)
        if symbol is None:
            logger.warning("No Binance symbol mapping for coin %s", coin)
            return None

        data = await self._get({"symbol": symbol, "limit": 1})
        if data is None or not isinstance(data, list) or len(data) == 0:
            return None

        entry = data[0]
        # Binance rate is per 8h period
        rate_8h = float(entry.get("fundingRate", 0))
        rate_hourly = rate_8h / 8.0

        # Fetch current price
        price = await self._get_price(symbol)
        if price is None:
            price = 0.0

        return {
            "ticker": coin,
            "symbol": symbol,
            "rate_hourly": rate_hourly,
            "rate_8h": rate_8h,
            "oracle_px": price,
            "funding_time": entry.get("fundingTime"),
        }

    async def get_all_funding_rates(
        self, coins: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch funding rates for multiple coins concurrently."""
        tasks = [self.get_funding_rate(c) for c in coins]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: Dict[str, Dict[str, Any]] = {}
        for coin, result in zip(coins, results):
            if isinstance(result, Exception):
                logger.warning("Binance fetch failed for %s: %s", coin, result)
                continue
            if result is not None and isinstance(result, dict):
                out[coin] = result
        return out

    async def healthcheck(self) -> bool:
        """Quick connectivity test."""
        client = await self._ensure_client()
        try:
            resp = await client.get("/fapi/v1/ping")
            return resp.status_code == 200
        except Exception:
            return False
