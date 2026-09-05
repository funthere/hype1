# HYPE Trading Bot Web Dashboard

A real-time web dashboard for monitoring and controlling the HYPE/USDC trading bot.

## Features

- **Real-time Monitoring**: Live view of positions, P&L, and trades
- **Bot Controls**: Pause/resume trading, force close positions, reset circuit breaker
- **Interactive Charts**: Cumulative P&L chart, win rate pie chart
- **Circuit Breaker Status**: Visual indicator for risk management state
- **Manual Trading**: Place manual trades directly from the dashboard
- **Configuration**: View and adjust bot parameters

## Quick Start

### 1. Start the Trading Bot

First, start the trading bot in paper trading mode:

```bash
# With default $10,000 capital
python3 hype_paper_trading_bot.py

# Or specify custom capital
HYPERLICUID_PAPER_CAPITAL=5000 python3 hype_paper_trading_bot.py
```

The bot will automatically start the API server on `http://127.0.0.1:8000`.

### 2. Start the Dashboard

In a separate terminal, start the Streamlit dashboard:

```bash
streamlit run hype_dashboard.py
```

The dashboard will open in your browser at `http://localhost:8501`.

### 3. Monitor and Control

- **Dashboard Tab**: Overview of performance, P&L charts, and controls
- **Positions Tab**: View open positions with individual close buttons
- **Trades Tab**: View complete trade history
- **Settings Tab**: View configuration and place manual trades

## Dashboard Tabs

### Dashboard (Main)

- **Status Bar**: Shows bot running state (RUNNING/PAUSED/STOPPED), mode, asset, and uptime
- **Stats Cards**: Total P&L, capital, win rate, total trades, daily trades
- **P&L Chart**: Cumulative profit/loss over time
- **Win Rate Chart**: Pie chart showing winning vs losing trades
- **Circuit Breaker**: Status indicator with reset button
- **Controls**: Pause/resume, close all positions, refresh

### Positions

- Lists all open positions with details
- Individual close buttons for each position
- Shows entry price, TP, SL, leverage, and unrealized P&L

### Trades

- Complete trade history
- Sortable by exit time
- Shows entry/exit prices, quantity, P&L, and fees

### Settings

- Current configuration display
- Manual trade form for placing custom orders

## API Endpoints

The bot's API server exposes the following endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get bot status (running, paused, mode, uptime) |
| `/api/positions` | GET | Get open positions |
| `/api/trades` | GET | Get trade history |
| `/api/stats` | GET | Get trading statistics |
| `/api/circuit-breaker` | GET | Get circuit breaker status |
| `/api/config` | GET | Get bot configuration |
| `/api/control` | POST | Execute control action (pause, resume, close_all, reset_cb) |
| `/api/manual-trade` | POST | Place a manual trade |
| `/ws` | WebSocket | Real-time updates |

## Control Actions

Send control actions via POST to `/api/control`:

```python
import requests

# Pause trading
requests.post("http://127.0.0.1:8000/api/control",
              json={"action": "pause"})

# Resume trading
requests.post("http://127.0.0.1:8000/api/control",
              json={"action": "resume"})

# Close all positions
requests.post("http://127.0.0.1:8000/api/control",
              json={"action": "close_all"})

# Reset circuit breaker
requests.post("http://127.0.0.1:8000/api/control",
              json={"action": "reset_cb"})

# Update configuration parameter
requests.post("http://127.0.0.1:8000/api/control",
              json={"action": "update_param",
                    "params": {"name": "RISK_PER_TRADE_PCT", "value": 0.10}})
```

## Configuration

Configure the API server in the bot configuration:

```python
config.WEB_UI_ENABLED = True      # Enable/disable API server
config.WEB_UI_HOST = "127.0.0.1"  # Bind address
config.WEB_UI_PORT = 8000         # Port number
```

Or via environment variables:

```bash
# Change dashboard API URL
HYPE_BOT_API_URL=http://192.168.1.100:8000 streamlit run hype_dashboard.py
```

## Troubleshooting

### Dashboard shows "Cannot connect to bot API"

- Make sure the trading bot is running
- Check that the API server started successfully (look for log messages)
- Verify the API URL matches the bot's configuration

### Port already in use

If port 8000 is already in use, either:
1. Stop the conflicting service, or
2. Change the `WEB_UI_PORT` in the bot configuration

### Dashboard auto-refresh not working

- Make sure "Auto-refresh" checkbox is enabled
- Check browser console for errors
- Try clicking the "Refresh" button manually

## Development

### File Structure

```
.
├── hype_trading_bot.py       # Main bot with API integration
├── bot_api_server.py         # FastAPI server module
├── hype_dashboard.py         # Streamlit dashboard
├── hype_paper_trading_bot.py # Paper trading entry point
└── hype_testnet_bot.py       # Testnet trading entry point
```

### Adding New Features

1. Add API endpoint in `bot_api_server.py`
2. Add UI component in `hype_dashboard.py`
3. Add control method in `TradingBot` class

## Security Notes

- The API server binds to `127.0.0.1` by default (localhost only)
- Do not expose the API server to the public internet without authentication
- The dashboard uses unencrypted HTTP - only use on trusted networks
- Consider adding API key authentication for production use
