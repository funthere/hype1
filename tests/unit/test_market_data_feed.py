"""
Unit tests for the WebSocket market data feed's failure paths.

Covers the behaviours the integration tests cannot reach deterministically:
reconnect backoff, the listen loop's exception paths, malformed messages,
and callback failure isolation. No network, no sleeps.
"""

import json
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
import websockets

from src.core.config import BotConfig
from src.exchange.market_data import MarketDataFeed


class FakeWebSocket:
    """Async-iterable scripted transport."""

    def __init__(self, messages=(), error=None):
        self._messages = list(messages)
        self._error = error
        self.sent = []
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for message in self._messages:
            if self._error is not None:
                raise self._error
            yield message
        if self._error is not None:
            raise self._error

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


def make_config() -> BotConfig:
    config = BotConfig()
    config.PAPER_TRADING = True
    return config


def candle_message(ts_ms=1_700_000_000_000, close="100.5") -> str:
    return json.dumps(
        {
            "channel": "candle",
            "data": {
                "t": ts_ms,
                "o": "100.0",
                "h": "101.0",
                "l": "99.0",
                "c": close,
                "v": "123.0",
            },
        }
    )


@pytest.mark.asyncio
async def test_connect_retries_with_backoff_then_succeeds():
    feed = MarketDataFeed(make_config())
    good_ws = FakeWebSocket()
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    connector = AsyncMock(side_effect=[ConnectionError("down"), good_ws])
    with patch("websockets.connect", new=connector):
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await feed.connect()

    assert feed.is_connected is True
    assert feed.reconnect_attempts == 0  # reset on success
    assert delays == [2.0]  # first backoff
    assert "subscribe" in good_ws.sent[0]


@pytest.mark.asyncio
async def test_connect_gives_up_after_max_attempts():
    feed = MarketDataFeed(make_config())
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    with patch("websockets.connect", side_effect=ConnectionError("still down")):
        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(ConnectionError):
                await feed.connect()

    assert len(delays) == feed._max_reconnect_attempts - 1
    assert delays[0] == 2.0
    assert delays[1] == 4.0  # exponential
    assert max(delays) <= 60  # capped


@pytest.mark.asyncio
async def test_listen_marks_disconnected_on_connection_closed():
    feed = MarketDataFeed(make_config())
    feed.connected = True
    feed._ws = FakeWebSocket(error=websockets.exceptions.ConnectionClosed(None, None))

    await feed._listen()

    assert feed.is_connected is False


@pytest.mark.asyncio
async def test_listen_survives_unexpected_transport_error():
    feed = MarketDataFeed(make_config())
    feed.connected = True
    feed._ws = FakeWebSocket(error=RuntimeError("transport exploded"))

    await feed._listen()  # must not raise

    assert feed.is_connected is False


@pytest.mark.asyncio
async def test_listen_stops_when_disconnected():
    feed = MarketDataFeed(make_config())
    feed.connected = False  # a disconnect raced the loop
    seen = []
    feed.on_candle_update(lambda candle: seen.append(candle))
    feed._ws = FakeWebSocket(messages=[candle_message()])

    await feed._listen()

    assert seen == []  # message consumed but not processed after disconnect


@pytest.mark.asyncio
async def test_handle_message_ignores_invalid_json():
    feed = MarketDataFeed(make_config())
    seen = []
    feed.on_candle_update(lambda candle: seen.append(candle))

    await feed._handle_message("this is not json{")
    await feed._handle_message(json.dumps({"channel": "other"}))  # ignored channel

    assert seen == []


@pytest.mark.asyncio
async def test_failing_callback_does_not_block_others():
    feed = MarketDataFeed(make_config())
    seen = []

    def broken(candle):
        raise RuntimeError("callback bug")

    feed.on_candle_update(broken)
    feed.on_candle_update(lambda candle: seen.append(candle["close"]))

    await feed._process_candle(
        {"t": 1_700_000_000_000, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1"}
    )

    assert seen == [1.0]  # second callback still ran
    assert feed.current_candle["close"] == 1.0


@pytest.mark.asyncio
async def test_malformed_candle_is_swallowed():
    feed = MarketDataFeed(make_config())
    await feed._process_candle({"unexpected": "shape"})  # no raise
    assert feed.current_candle is None


@pytest.mark.asyncio
async def test_historical_candles_returns_empty_frame():
    feed = MarketDataFeed(make_config())
    frame = await feed.get_historical_candles()
    assert isinstance(frame, pd.DataFrame)
    assert frame.empty
