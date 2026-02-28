"""
Run HYPE_KING backtest on real Hyperliquid HYPE/USDC data
"""
from hype_king_bot import BacktestEngine, HYPEKingConfig
import pandas as pd

# Load real Hyperliquid data
print("Loading real HYPE/USDC data from Hyperliquid...")
df = pd.read_csv('hyperliquid_hype_5m_90d.csv')

# Convert timestamp
df['timestamp'] = pd.to_datetime(df['timestamp'])

print(f"\nData Summary:")
print(f"  Candles: {len(df)}")
print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
print(f"  Price range: ${df['close'].min():.4f} - ${df['close'].max():.4f}")
print(f"  Total volume: ${df['volume'].sum():,.0f}")

# Calculate days covered
days_covered = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / (24 * 3600)
print(f"  Dataset covers: {days_covered:.1f} days")

# Run backtest
print("\n" + "=" * 70)
print("RUNNING HYPE_KING BACKTEST ON REAL HYPERLIQUID DATA")
print("=" * 70)

bot = BacktestEngine(initial_capital=10000, config=HYPEKingConfig())
results = bot.run(df)
bot.print_results(results)

# Save results
if results["trades_list"]:
    trades_df = pd.DataFrame([{
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "side": t.side.value,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "quantity": t.quantity,
        "leverage": t.leverage,
        "pnl": t.pnl,
        "pnl_pct": t.pnl_pct,
        "tp_price": t.tp_price,
        "sl_price": t.sl_price,
    } for t in results["trades_list"] if not t.is_open])

    trades_df.to_csv('hyperliquid_hype_king_trades.csv', index=False)
    print("\n✅ Trades exported to hyperliquid_hype_king_trades.csv")

# Export equity curve
equity_df = pd.DataFrame(results["equity_curve"], columns=["timestamp", "equity"])
equity_df.to_csv('hyperliquid_hype_king_equity.csv', index=False)
print("✅ Equity curve exported to hyperliquid_hype_king_equity.csv")

# Performance metrics
annualized_return = (1 + results['summary']['total_return_pct']/100) ** (365/days_covered) - 1
print(f"\n📊 Annualized Return: {annualized_return*100:.2f}%")
