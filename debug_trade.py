import numpy as np
import pandas as pd
from hype_king_bot import BacktestEngine, HYPEKingConfig, generate_sample_data

# Generate data
df = generate_sample_data(days=30)
df['timestamp'] = pd.to_datetime(df['timestamp'])
df.set_index('timestamp', inplace=True)

# Calculate ATR
engine = BacktestEngine(initial_capital=10000)
atr = engine.atr.calculate(df)

print("First 20 ATR values:")
print(atr.head(20))
print(f"\nATR stats:")
print(f"Min: {atr.min():.4f}")
print(f"Max: {atr.max():.4f}")
print(f"Mean: {atr.mean():.4f}")

# Check first trade parameters
first_atr = atr.iloc[20]  # After warmup
price = 89.03
sl_distance = first_atr * 0.5
tp_distance = first_atr * 1.5

print(f"\nFirst trade analysis:")
print(f"ATR at trade 1: {first_atr:.4f}")
print(f"Entry price: ${price:.2f}")
print(f"SL distance (0.5 * ATR): ${sl_distance:.4f}")
print(f"TP distance (1.5 * ATR): ${tp_distance:.4f}")
print(f"Expected SL price: ${price - sl_distance:.2f}")
print(f"Expected TP price: ${price + tp_distance:.2f}")

# Check actual OHLC at that time
print(f"\nActual OHLC at trade 1:")
print(df.iloc[20][['open', 'high', 'low', 'close']])
