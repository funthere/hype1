# Integration Tests

This directory contains integration tests that verify the interaction between multiple components of the HYPE Trading Bot.

## Test Structure

### Database Integration (`test_database_integration.py`)
Tests the persistence layer with a real SQLite database (in-memory for tests).

**Coverage:**
- Database initialization and table creation
- Position CRUD operations (create, update, close)
- Trade persistence and retrieval
- Daily summary storage
- Event logging
- Trade statistics calculation
- Profit factor calculation
- Date-based filtering
- Context manager usage

### Exchange Integration (`test_exchange_integration.py`)
Tests the HyperliquidAPI with mocked exchange connections.

**Coverage:**
- API initialization
- Connection checks
- Asset index fetching and caching
- Order placement (with options)
- Order cancellation
- Bulk order operations
- Position retrieval
- Mid prices
- Account balance
- Leverage setting
- Order status tracking
- Recent fills
- Error handling
- API wallet support
- Connectivity and reconnection

### Market Data Integration (`test_market_data_integration.py`)
Tests the WebSocket-based market data feed with mocked connections.

**Coverage:**
- Feed initialization
- Callback registration and removal
- WebSocket connection and disconnection
- Subscription to market data channels
- Candle message processing
- Multiple callback support
- Async callback handling
- Current candle tracking
- Reconnection logic
- Max reconnect attempts
- Historical candle fetching
- End-to-end message flow

## Running Integration Tests

Integration tests use the `@pytest.mark.integration` marker. To run only integration tests:

```bash
pytest -m integration tests/integration/
```

To run all tests (unit + integration):

```bash
pytest tests/
```

To run with coverage:

```bash
pytest --cov=src --cov-report=html tests/
```

## Test Dependencies

Integration tests rely on mocked external services to ensure:
- **Fast execution** - No network latency
- **Determinism** - No external state changes
- **Isolation** - Tests don't affect real systems
- **Repeatability** - Same results every run

## Mocking Strategy

1. **External APIs** (Hyperliquid, Telegram)
   - Mocked using `unittest.mock`
   - Return consistent responses
   - Simulate errors when needed

2. **Database**
   - Uses in-memory SQLite
   - Real persistence operations tested
   - Temporary files cleaned up after tests

3. **WebSockets**
   - Mocked connection layer
   - Real message parsing tested
   - Async operations properly handled

## Adding New Integration Tests

When adding new integration tests:

1. **Mark with `@pytest.mark.integration`**
2. **Use `asyncio` for async tests**
3. **Mock external dependencies**
4. **Test realistic scenarios**
5. **Verify side effects** (database writes, notifications sent)
6. **Test error conditions**
7. **Test edge cases**

## Best Practices

1. **Isolate tests** - Each test should be independent
2. **Use fixtures** - Share common setup code
3. **Arrange-Act-Assert** - Clear test structure
4. **Mock carefully** - Only mock what's necessary
5. **Test async properly** - Use `pytest-asyncio` for async code
6. **Clean up resources** - Use context managers where possible
7. **Verify assertions** - Check both positive and negative cases

## Coverage Goals

Integration tests aim to cover:
- **Component interactions** - How modules work together
- **Data flow** - How data moves through the system
- **Error handling** - How failures are propagated
- **State management** - How system state changes
- **External interfaces** - How the bot talks to outside services

Unit tests cover individual component logic, while integration tests verify the complete system behavior.