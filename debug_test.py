import numpy as np
import pandas as pd
from hype_king_bot import BacktestEngine, HYPEKingConfig, generate_sample_data

# Generate data
df = generate_sample_data(days=7)
df['timestamp'] = pd.to_datetime(df['timestamp'])
df.set_index('timestamp', inplace=True)

# Calculate ATR
atr = BacktestEngine(initial_capital=10000).atr.calculate(df)

print(f'Price range: {df["close"].min():.2f} - {df["close"].max():.2f}')
print(f'ATR range: {atr.min():.4f} - {atr.max():.4f}')
print(f'ATR mean: {atr.mean():.4f}')

# Test position sizing
bot = BacktestEngine(initial_capital=10000)
price = 108.0
test_atr = 0.5
quantity = bot.calculate_position_size(price, test_atr)

print(f'\nPosition sizing test:')
print(f'Capital: ${bot.capital:,.2f}')
print(f'Margin available (90%): ${bot.capital * 0.9:,.2f}')
print(f'Notional with 20x: ${bot.capital * 0.9 * 20:,.2f}')
print(f'Quantity at ${price}: {quantity:,.2f}')

# Test P&L
entry = 108.0
exit = 108.50  # 0.50 move
pnl = (exit - entry) * quantity
print(f'\nP&L test:')
print(f'Entry: ${entry}, Exit: ${exit}')
print(f'P&L: ${pnl:,.2f}')
