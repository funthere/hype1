# HYPE/USDC Automated Trading Bot

Modular architecture crypto trading bot for Hyperliquid DEX.

## Features

- **Modular Architecture**: Clean separation of concerns for maintainability
- **Ultra-Optimized Momentum Strategy**: Backtested strategy with strong returns
- **Paper Trading**: Test strategies risk-free with simulated trades
- **Testnet Support**: Validate on testnet before mainnet
- **Real-time Dashboard**: Streamlit web interface for monitoring
- **Risk Management**: Circuit breaker, daily loss limits, dynamic position sizing
- **Telegram Notifications**: Trade alerts and updates
- **Multi-Asset Support**: Trade multiple assets with correlation filtering
- **Survival Mode**: Conservative risk profiles for production

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run paper trading (no credentials needed)
python3 run_paper_bot.py

# Or use Make
make run-paper
```

For trading on testnet or mainnet, configure `.env` with your credentials.

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
# For testnet/mainnet
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

## Running the Bot

### Paper Trading (Recommended First)
```bash
python3 run_paper_bot.py
# Or: make run-paper
```

### Testnet Trading
```bash
# Configure .env first
python3 run_testnet_bot.py
# Or: make run-testnet
```

### Mainnet Trading (Real Money!)
```bash
# Configure .env with mainnet credentials
python3 run_mainnet_bot.py
# Or: make run-mainnet
```

### Web Dashboard

Start the bot in one terminal, then in another:
```bash
make dashboard
```

Open http://localhost:8501 in your browser.

## Architecture

```
src/
├── core/           # Config, data models, strategy, risk management
├── exchange/       # API connector, market data feed
├── bot/            # Main trading bot orchestrator
├── storage/        # SQLite database for persistence
├── notifications/   # Telegram alerts
└── analytics/      # Performance, health, adaptive analytics
```

See `CLAUDE.md` for detailed architecture documentation.

## Testing

```bash
# Run tests
make test

# Run with coverage
pytest --cov=src --cov-report=html
```

## Development

```bash
# Install dev dependencies
make install

# Lint code
make lint

# Format code
make format
```

## Documentation

- `CLAUDE.md` - Architecture and development guide
- `MIGRATION_ANALYSIS.md` - Analysis of modular vs legacy architecture
- `MODULAR_README.md` - Detailed modular architecture docs

## Disclaimer

This is a trading bot for educational purposes. Past performance does not guarantee future results. Always test thoroughly and use proper risk management in live trading.# OpenCode test
