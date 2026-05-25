"""
dYdX v4 Indexer REST API Client

Uses the public Indexer API for reading funding rates and market data.
dYdX v4 runs on Cosmos SDK (Celestia); order placement requires
Cosmos-based signing which is out of scope for this initial implementation.
The bot therefore places *paper* orders on the dYdX side and only executes
real orders on HyperLiquid (or both in paper mode).

Endpoints used:
  GET /v4/markets                    → perp market metadata
  GET /v4/perpetualMarkets/:ticker    → funding rate & oracle price
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Number of seconds between successive requests (rate-limit courtesy)
_MIN_REQUEST_INTERVAL = 0.3


class DydxClient:
    """Thin async wrapper around the dYdX v4 Indexer REST API."""

    def __init__(self, base_url: str = "https://indexer.v4protocol.io") -> None:
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

    async def _get(self, path: str) -> Any:
        client = await self._ensure_client()
        # Simple rate-limit throttle
        elapsed = time.time() - self._last_request_ts
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_ts = time.time()

        try:
            resp = await client.get(path)
            resp.raise_for_status()
            data = resp.json()
            return data
        except httpx.HTTPStatusError as exc:
            logger.error("dYdX HTTP %d for %s: %s", exc.response.status_code, path, exc)
            return None
        except Exception as exc:
            logger.error("dYdX request error for %s: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_perpetual_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Return the full perpetual market object for *ticker*."""
        data = await self._get(f"/v4/perpetualMarkets/{ticker}")
        return data.get("market") if data else None

    async def get_funding_rate(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Return a normalised dict: ``{rate_hourly, oracle_px, ticker}``."""
        market = await self.get_perpetual_market(ticker)
        if market is None:
            return None

        # dYdX v4 returns "fundingRate" as parts-per-million *per hour*.
        # Positive rate = longs pay shorts.
        rate_ppm = market.get("fundingRate", "0")
        try:
            rate_hourly = float(rate_ppm) / 1_000_000
        except (ValueError, TypeError):
            rate_hourly = 0.0

        oracle_px = 0.0
        try:
            oracle_px = float(market.get("oraclePrice", "0"))
        except (ValueError, TypeError):
            pass

        return {
            "ticker": ticker,
            "rate_hourly": rate_hourly,
            "oracle_px": oracle_px,
        }

    async def get_all_funding_rates(
        self, tickers: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch funding rates for multiple tickers concurrently."""
        tasks = [self.get_funding_rate(t) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: Dict[str, Dict[str, Any]] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.warning("dYdX fetch failed for %s: %s", ticker, result)
                continue
            if result is not None and isinstance(result, dict):
                out[ticker] = result
        return out

    async def healthcheck(self) -> bool:
        """Quick connectivity test."""
        data = await self._get("/v4/time")
        return data is not None
