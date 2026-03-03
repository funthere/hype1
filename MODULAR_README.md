# Modular Trading Bot Architecture

This document describes the new modular architecture for the HYPE trading bot.

## 📁 Structure

```
src/
├── core/
│   ├── __init__.py
│   ├── config.py          # BotConfig, data models (Side, Position, Trade)
│   └── strategy.py        # StrategyEngine, RiskManager
├── exchange/
│   ├── __init__.py
│   ├── connector.py       # HyperliquidAPI wrapper
│   └── market_data.py     # WebSocket market data feed
├── storage/
│   ├── __init__.py
│   └── database.py        # SQLite persistence
├── notifications/
│   ├── __init__.py
│   └── telegram.py        # Telegram alerts
└── bot/
    ├── __init__.py
    └── trading_bot.py     # Main TradingBot orchestrator
```

## 🚀 Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run the bot
```bash
# Paper trading (default)
python run_modular_bot.py

# Testnet
python run_modular_bot.py --mode testnet

# Mainnet
python run_modular_bot.py --mode mainnet

# With custom parameters
python run_modular_bot.py --asset HYPE --leverage 10 --risk 0.05
```

## 🔌 Configuration

### Environment Variables (.env)
| Variable | Description | Default |
|----------|-------------|---------|
| `PRIVATE_KEY` | Wallet private key | Required |
| `PAPER_TRADING` | Enable paper trading | `true` |
| `USE_TESTNET` | Use testnet | `false` |
| `TELEGRAM_ENABLED` | Enable notifications | `false` |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | - |
| `TELEGRAM_CHAT_ID` | Your chat ID | - |
| `DATABASE_PATH` | SQLite database path | `trading_bot.db` |

### CLI Arguments
| Argument | Description | Default |
|----------|-------------|---------|
| `--mode` | paper/testnet/mainnet | `paper` |
| `--asset` | Asset to trade | `HYPE` |
| `--timeframe` | Trading timeframe | `15m` |
| `--leverage` | Leverage (1-100) | `5` |
| `--risk` | Risk per trade (decimal) | `0.08` |
| `--no-ui` | Disable web UI | `false` |

## 📊 Database Schema

### trades table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| side | TEXT | LONG/SHORT |
| entry_price | REAL | Entry price |
| exit_price | REAL | Exit price |
| quantity | REAL | Position size |
| entry_time | TEXT | Entry timestamp |
| exit_time | TEXT | Exit timestamp |
| pnl | REAL | Profit/Loss |
| fees | REAL | Trading fees |
| notes | TEXT | Optional notes |

### positions table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| side | TEXT | LONG/SHORT |
| entry_price | REAL | Entry price |
| quantity | REAL | Position size |
| tp_price | REAL | Take profit price |
| sl_price | REAL | Stop loss price |
| entry_time | TEXT | Entry timestamp |
| leverage | INTEGER | Leverage multiplier |
| oid | INTEGER | Order ID |
| cloid | TEXT | Client order ID |
| status | TEXT | open/closed |
| unrealized_pnl | REAL | Current unrealized P&L |

### daily_summaries table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| date | TEXT | Date (YYYY-MM-DD) |
| total_trades | INTEGER | Number of trades |
| winning_trades | INTEGER | Winning trades |
| losing_trades | INTEGER | Losing trades |
| total_pnl | REAL | Daily P&L |
| total_fees | REAL | Daily fees |
| win_rate | REAL | Win rate % |
| max_drawdown_pct | REAL | Max drawdown % |
| starting_capital | REAL | Start of day capital |
| ending_capital | REAL | End of day capital |

### events table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| event_type | TEXT | Event type |
| event_data | TEXT | JSON event data |
| message | TEXT | Event message |
| created_at | TEXT | Timestamp |

## 🔔 Telegram Notifications

### Setup
1. Create a bot with [@BotFather](https://t.me/BotFather)
2. Copy the bot token
3. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
4. Add to `.env`:
   ```
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

### Notification Types
- Trade entries
- Trade exits
- Circuit breaker triggers
- Daily summaries
- Errors and warnings
- Bot startup/shutdown

## 🧪 Testing Telegram

```python
from src.notifications.telegram import TelegramNotifier

notifier = TelegramNotifier(bot_token="your_token", chat_id="your_chat_id")
await notifier.test_connection()
```

## 📈 Migrating from CSV

```python
from src.storage.database import DatabaseManager, CSVMigration

db = DatabaseManager("trading_bot.db")
CSVMigration.migrate_trades_from_csv("hype_king_trades.csv", db)
```

## 🔧 API Extensions

The modular design makes it easy to add new features:

### Adding a new indicator
```python
# In src/core/strategy.py
def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
```

### Adding a new notification channel
```python
# Create src/notifications/discord.py
class DiscordNotifier:
    async def notify_trade_entry(self, signal):
        # Send to Discord
        pass
```

### Adding a new exchange
```python
# Create src/exchange/binance_connector.py
class BinanceAPI:
    async def place_order(self, side, price, quantity):
        # Binance order logic
        pass
```

## 📝 License

This code is provided as-is for educational purposes.
