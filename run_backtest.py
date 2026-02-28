"""
Quick runner for HYPE_KING backtest
"""

from hype_king_bot import BacktestEngine, HYPEKingConfig, generate_sample_data
import pandas as pd


def load_csv_data(file_path: str) -> pd.DataFrame:
    """Load OHLCV data from CSV file"""
    df = pd.read_csv(file_path)
    return df


if __name__ == "__main__":
    # Option 1: Use sample data
    print("Loading sample data...")
    df = generate_sample_data(days=90)

    # Option 2: Load from CSV (uncomment to use)
    # df = load_csv_data("path/to/your/hype_data.csv")
    # Required columns: timestamp, open, high, low, close, volume

    # Initialize and run backtest
    bot = BacktestEngine(
        initial_capital=10000.0,
        config=HYPEKingConfig()
    )

    results = bot.run(df)
    bot.print_results(results)

    # Export results to CSV
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
        } for t in results["trades_list"] if not t.is_open])

        trades_df.to_csv("hype_king_trades.csv", index=False)
        print("\n✅ Trades exported to hype_king_trades.csv")

    # Export equity curve
    equity_df = pd.DataFrame(results["equity_curve"], columns=["timestamp", "equity"])
    equity_df.to_csv("hype_king_equity.csv", index=False)
    print("✅ Equity curve exported to hype_king_equity.csv")
