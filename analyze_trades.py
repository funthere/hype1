import pandas as pd

df = pd.read_csv('hype_king_trades.csv')

# Check recent trades
print('Recent trades with P&L analysis:')
print(df[['entry_price', 'exit_price', 'quantity', 'pnl']].tail(15).to_string())

# Calculate expected vs actual R:R
df['price_move'] = abs(df['exit_price'] - df['entry_price'])
print(f'\nAvg price move: ${df["price_move"].mean():.4f}')
print(f'Avg quantity: {df["quantity"].mean():.2f}')

# Check TP/SL ratio
winning = df[df['pnl'] > 0]
losing = df[df['pnl'] < 0]
print(f'\nWinning trades avg move: ${winning["price_move"].mean():.4f}')
print(f'Losing trades avg move: ${losing["price_move"].mean():.4f}')

if losing["price_move"].mean() > 0:
    print(f'Actual R:R ratio: {winning["price_move"].mean() / losing["price_move"].mean():.2f}')
