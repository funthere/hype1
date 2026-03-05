# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Running the bot
make run-paper        # Paper trading (simulated, no API keys needed)
make run-testnet      # Testnet trading (requires .env with credentials)
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

This is a HYPE/USDC trading bot for Hyperliquid DEX with two parallel implementations:

### 1. Legacy Monolithic Implementation (`hype_trading_bot.py`)
- Single-file implementation with embedded `HyperliquidAPI`, `MarketDataFeed`, `StrategyEngine`, `TradingBot`
- Uses `hyperliquid-python-sdk` for exchange integration
- Entry points: `hype_paper_trading_bot.py`, `hype_testnet_bot.py`
- Includes FastAPI server (`bot_api_server.py`) and Streamlit dashboard (`hype_dashboard.py`)

### 2. Modular Implementation (`src/`)
- Separated concerns across modules:
  - `src/core/config.py` - Configuration and data models (`BotConfig`, `Side`, `Position`, `Trade`)
  - `src/core/strategy.py` - Strategy logic and risk management
  - `src/exchange/connector.py` - Hyperliquid API wrapper using SDK
  - `src/exchange/market_data.py` - WebSocket market data feed
  - `src/bot/trading_bot.py` - Main trading bot orchestrator
  - `src/storage/database.py` - SQLite persistence
  - `src/notifications/telegram.py` - Telegram notifications
  - `src/analytics/` - Performance analysis modules

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

- `hype_paper_trading_bot.py` - Paper trading (no credentials needed)
- `hype_testnet_bot.py` - Testnet trading (requires `PRIVATE_KEY`, `ADDRESS` in env)
- `hype_dashboard.py` - Streamlit dashboard (connects to bot's API server)
- `run_modular_bot.py` - Alternative entry for modular `src/bot/trading_bot.py`
- `run_multi_asset_bot.py` - Multi-asset trading (experimental)
