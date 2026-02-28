"""
HYPE_KING Backtest - Optimized for Real HYPE/USDC on Hyperliquid
"""
from hype_king_bot import BacktestEngine, HYPEKingConfig, OrderType
import pandas as pd


class HypeRealDataConfig(HYPEKingConfig):
    """Configuration optimized for real HYPE/USDC market characteristics"""

    ASSET = "HYPE"
    TIMEFRAME = "5m"
    LEVERAGE = 5
    ORDER_TYPE = OrderType.LIMIT

    # Optimized for real HYPE volatility (seen in data: $25-$32 range)
    CONFIDENCE_THRESHOLD = 60  # Slightly lower for more trades
    RISK_PER_TRADE_PCT = 0.12  # 12% risk (increase for opportunity)
    TP_ATR_MULTIPLIER = 1.4  # Tighter TP for faster exits
    SL_ATR_MULTIPLIER = 0.5  # Tight SL

    # Adaptive
    USE_ADAPTIVE_RR = True
    MIN_TP_MULT = 1.0
    MAX_TP_MULT = 2.0

    # Relaxed filters for more opportunities
    MIN_BB_POSITION = 15  # Wider: < 15 or > 85
    MIN_RSI_LONG = 35  # Less extreme
    MAX_RSI_SHORT = 65

    # Position sizing
    USE_QUALITY_SIZING = True
    MIN_QUALITY_RISK = 0.08
    MAX_QUALITY_RISK = 0.16

    # Risk management
    MAX_CONCURRENT_POSITIONS = 1
    TRADE_COOLDOWN_BARS = 5  # 25 min
    MAX_DAILY_TRADES = 25

    # Fees
    MAKER_FEE_PCT = -0.0002
    TAKER_FEE_PCT = 0.0004
    EXPECTED_RR_RATIO = 2.8  # 1.4:0.5 = 2.8:1


if __name__ == "__main__":
    # Load real Hyperliquid data
    print("Loading real HYPE/USDC data from Hyperliquid...")
    df = pd.read_csv('hyperliquid_hype_5m_90d.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    print(f"\nData Summary:")
    print(f"  Candles: {len(df)}")
    print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"  Price range: ${df['close'].min():.4f} - ${df['close'].max():.4f}")

    # Calculate volatility stats
    df['returns'] = df['close'].pct_change()
    volatility = df['returns'].std() * (252 * 24 * 12) ** 0.5  # Annualized
    print(f"  Volatility: {volatility:.2%} annualized")

    days = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / (24 * 3600)
    print(f"  Dataset covers: {days:.1f} days")

    # Run optimized backtest
    print("\n" + "=" * 70)
    print("HYPE_KING OPTIMIZED FOR REAL HYPE/USDC")
    print("=" * 70)

    bot = BacktestEngine(initial_capital=10000, config=HypeRealDataConfig())
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
            "pnl": t.pnl,
        } for t in results["trades_list"] if not t.is_open])

        trades_df.to_csv('hype_real_optimized_trades.csv', index=False)
        print("\n✅ Trades exported to hype_real_optimized_trades.csv")
