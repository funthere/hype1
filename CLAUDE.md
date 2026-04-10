# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Running the bot
make run-paper        # Paper trading (simulated, no API keys needed)
make run-testnet      # Testnet trading (requires .env with credentials)
make run-mainnet      # Mainnet trading (requires .env with credentials)
make dashboard        # Streamlit dashboard (http://localhost:8501)

# Management
make kill-bot         # Stop any running bot
make kill-dashboard   # Stop the dashboard
make db-backup        # Backup SQLite database with timestamp

# Development
make install          # Install dependencies from requirements.txt
make lint             # Run ruff linter
make format           # Format code with ruff
make test             # Run pytest tests
make clean            # Remove __pycache__, .pytest_cache, etc.

# Logging
make log-paper        # Tail paper trading logs
make log-testnet      # Tail testnet logs
```

Custom ports:
```bash
make run-paper API_PORT=8001
make dashboard DASHBOARD_PORT=8502
```

## Architecture Overview

This is a HYPE/USDC trading bot for Hyperliquid DEX using a modular architecture.

### Modular Implementation (`src/`)
- Separated concerns across modules:
  - `src/core/config.py` - Configuration and data models (`BotConfig`, `Side`, `Position`, `Trade`)
  - `src/core/strategy.py` - Strategy logic and risk management
  - `src/core/survival.py` - Conservative risk profiles for production
  - `src/core/multi_asset.py` - Multi-asset trading with correlation filtering
  - `src/exchange/connector.py` - Hyperliquid API wrapper using SDK
  - `src/exchange/market_data.py` - WebSocket market data feed
  - `src/bot/trading_bot.py` - Main trading bot orchestrator
  - `src/storage/database.py` - SQLite persistence
  - `src/notifications/telegram.py` - Telegram notifications
  - `src/analytics/` - Performance, health, and adaptive analytics modules

### Entry Points
- `run_paper_bot.py` - Paper trading (simulated, no API keys needed)
- `run_testnet_bot.py` - Testnet trading (requires .env with credentials)
- `run_mainnet_bot.py` - Mainnet trading (REAL MONEY - use with caution)
- `run_modular_bot.py` - Alternative entry with CLI arguments (paper/testnet/mainnet)
- `run_multi_asset_bot.py` - Multi-asset trading (experimental)
- `bot_api_server.py` - FastAPI server for dashboard
- `hype_dashboard.py` - Streamlit dashboard

### Key Design Patterns

**Configuration via `BotConfig` dataclass:**
- Supports environment variable loading via `python-dotenv`
- Properties auto-switch API URLs based on `USE_TESTNET`
- All strategy parameters are configurable (ROC, thresholds, multipliers, etc.)

**Async callback pattern:**
- `MarketDataFeed` registers callbacks via `on_candle_update()`
- Callbacks must handle both sync and async returns - check with `asyncio.iscoroutine(result)` before awaiting
- See `src/exchange/market_data.py:165-172` for the pattern

**JSON serialization with Enums:**
- `Side`, `OrderStatus` are Enums that can't be directly JSON serialized
- Database uses `_serialize_enum()` helper in `src/storage/database.py` to convert enums to `.value`
- Always use `.value` when accessing enum values for logging/storage

**Dashboard auto-refresh:**
- Dashboard uses `st.rerun()` in a loop with `time.sleep(REFRESH_INTERVAL)`
- Plotly charts need unique `key` parameter (use timestamp-based key) to avoid "duplicate element ID" errors
- Use `width='stretch'` instead of deprecated `use_container_width=True`

## Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Required for testnet/mainnet
PRIVATE_KEY=0x...
ADDRESS=0x...

# Trading mode
USE_TESTNET=false
PAPER_TRADING=true

# Strategy overrides (optional)
ASSET=HYPE
TIMEFRAME=15m
LEVERAGE=5
RISK_PER_TRADE_PCT=0.08
```

The bot loads `.env` automatically via `load_dotenv()` at module import.

## Important Implementation Details

1. **SDK Integration**: The `HyperliquidAPI` class wraps `hyperliquid-python-sdk` (`Info` for market data, `Exchange` for trading). Don't use raw HTTP requests.

2. **Paper Trading**: When `PAPER_TRADING=True`, orders are simulated locally. The `_place_paper_order()` method creates position objects without calling the exchange.

3. **Circuit Breaker**: Stops trading after `MAX_CONSECUTIVE_LOSSES` (default: 3). Reset via API or by creating `.reset_circuit_breaker` file.

4. **Signal Handlers**: Bot responds to Unix signals for emergency controls:
   - `SIGUSR1` - Force close all positions
   - `SIGUSR2` - Reset circuit breaker

5. **API Server**: FastAPI server runs on `127.0.0.1:8000` when `WEB_UI_ENABLED=True`. Provides REST endpoints and WebSocket for dashboard.

6. **Database**: SQLite stores positions, trades, events. Database file: `trading_bot.db` (gitignored).

## Entry Points

- `run_paper_bot.py` - Paper trading (no credentials needed)
- `run_testnet_bot.py` - Testnet trading (requires `PRIVATE_KEY`, `ADDRESS` in env)
- `run_mainnet_bot.py` - Mainnet trading (REAL MONEY - requires credentials)
- `run_modular_bot.py` - Alternative entry with CLI arguments (paper/testnet/mainnet)
- `run_multi_asset_bot.py` - Multi-asset trading (experimental)
- `hype_dashboard.py` - Streamlit dashboard (connects to bot's API server)
- `bot_api_server.py` - FastAPI server (used by bot internally, duck-typed)