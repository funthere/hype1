# HYPE_KING Backtesting Bot

A comprehensive backtesting framework with a highly profitable **Mean Reversion** strategy.

## 🚀 Current Strategy - MEAN REVERSION (145% Return in 90 Days!)

### Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| Asset | HYPE | Trading symbol |
| Timeframe | 5 minutes | Candle interval |
| Leverage | 5x | Conservative leverage |
| Order Type | Market | Immediate execution |
| Confidence Threshold | 62 | Signal strength required |
| Risk per Trade | 8% | Capital at risk per trade |
| Take Profit | 1.5x ATR | Quick profit targets |
| Stop Loss | 0.6x ATR | Tight stops |
| R:R Ratio | 2.5:1 | Expected reward-to-risk |

### Strategy Logic

**Mean Reversion Approach:**
1. **LONG Entry**: When RSI < 35 OR price at lower Bollinger Band (oversold)
2. **SHORT Entry**: When RSI > 65 OR price at upper Bollinger Band (overbought)
3. **Trend Filter**: Only long if below EMA-20, only short if above EMA-20
4. **Volume Confirmation**: Volume spikes trigger earlier entries
5. **Dynamic Position Sizing**: Based on signal strength (4%-12% risk)

### 90-Day Backtest Results

```
============================================================
HYPE_KING BACKTEST RESULTS (90 Days)
============================================================

📊 PERFORMANCE SUMMARY
------------------------------------------------------------
Initial Capital:        $10,000.00
Final Capital:          $24,550.84
Total P&L:              $14,550.84
Total Return:           +145.51%       🚀
Max Drawdown:           3.64%          ✅ Excellent!
Sharpe Ratio:           17.34          📈 Outstanding!

📈 TRADE STATISTICS
------------------------------------------------------------
Total Trades:           902
Winning Trades:         473
Losing Trades:          429
Win Rate:               52.4%          ✅ Above break-even!
Avg Win:                $57.16
Avg Loss:               -$29.10
Profit Factor:          2.17           ✅ $2.17 won per $1 lost!

💰 COST ANALYSIS
------------------------------------------------------------
Total Fees Paid:        $5,204.86
Net Cost:               $5,204.86
```

## Files

- `hype_king_bot.py` - Main backtesting engine
- `run_backtest.py` - Simple runner script
- `requirements.txt` - Dependencies

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run 90-day backtest
python run_backtest.py
```

## Using Your Own Data

```python
from hype_king_bot import BacktestEngine, HYPEKingConfig
import pandas as pd

# Load your data (needs: timestamp, open, high, low, close, volume)
df = pd.read_csv('your_data.csv')

# Run backtest
bot = BacktestEngine(initial_capital=10000)
results = bot.run(df)
bot.print_results(results)
```

## Output Files

- `hype_king_trades.csv` - Individual trade details
- `hype_king_equity.csv` - Equity curve over time

## Strategy Comparison

| Strategy | Return | Win Rate | Max DD | Profit Factor |
|----------|--------|----------|--------|--------------|
| **Mean Reversion** | **+145%** | **52.4%** | **3.6%** | **2.17** |
| Breakout Pullback | -7% | 25.7% | 9.0% | 0.87 |
| Trend Following | -35% | 20.4% | 36.3% | 0.56 |

## Why Mean Reversion Works

The strategy excels because:
1. **Oscillating markets** - Most markets range more than trend
2. **Extreme conditions** - RSI and Bollinger Bands identify reversals
3. **Trend alignment** - EMA filter ensures trading with the broader trend
4. **Quick exits** - Tight TP/SL captures short-term reversals
5. **High win rate** - 52.4% means more winners than losers

## Customization

Adjust parameters in `HYPEKingConfig`:

```python
config = HYPEKingConfig()
config.LEVERAGE = 10  # Increase for more aggression
config.RISK_PER_TRADE_PCT = 0.10  # Higher risk per trade
config.TP_ATR_MULTIPLIER = 2.0  # Wider profit targets
```

## Disclaimer

Backtesting uses historical data. Past performance doesn't guarantee future results. Always test thoroughly and use proper risk management.
