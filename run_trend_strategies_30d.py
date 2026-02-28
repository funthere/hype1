"""
Compare Trend-Following Strategies on Real HYPE/USDC Data (30 days)
"""
import pandas as pd
from trend_following_strategies import (
    TrendFollowingEngine,
    EMAConfig,
    BreakoutConfig,
    MomentumConfig
)


def print_results(results, strategy_name):
    """Print formatted results"""
    s = results['summary']
    t = results['trades']
    c = results['costs']

    print(f"\n{'='*60}")
    print(f"{strategy_name}")
    print('='*60)

    print(f"Total Return:       {s['total_return_pct']:>10.2f}%")
    print(f"Max Drawdown:       {s['max_drawdown_pct']:>10.2f}%")
    print(f"Sharpe Ratio:       {s['sharpe_ratio']:>10.2f}")
    print(f"Total Trades:       {t['total_trades']:>10}")
    print(f"Win Rate:           {t['win_rate_pct']:>9.1f}%")
    print(f"Profit Factor:      {t['profit_factor']:>10.2f}")
    print(f"Avg Win:            ${t['avg_win']:>9,.2f}")
    print(f"Avg Loss:           ${t['avg_loss']:>9,.2f}")
    print(f"Net Fees:           ${c['net_cost']:>10,.2f}")

    return {
        'return': s['total_return_pct'],
        'dd': s['max_drawdown_pct'],
        'sharpe': s['sharpe_ratio'],
        'win_rate': t['win_rate_pct'],
        'profit_factor': t['profit_factor']
    }


if __name__ == "__main__":
    # Load real HYPE data
    print("Loading real HYPE/USDC data from Hyperliquid...")
    df = pd.read_csv('hyperliquid_hype_5m_30d.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    print(f"\nDataset: {len(df)} candles")
    print(f"Period: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Price range: ${df['close'].min():.4f} - ${df['close'].max():.4f}")

    days = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / (24 * 3600)
    print(f"Duration: {days:.1f} days (Note: Hyperliquid has limited HYPE history)\n")

    all_results = {}

    # Test EMA Crossover
    print("\n" + "="*70)
    print("Testing EMA Crossover Trend Following")
    print("="*70)
    ema_engine = TrendFollowingEngine(initial_capital=10000, config=EMAConfig())
    all_results['EMA Crossover'] = print_results(
        ema_engine.run_trend_backtest(df.copy(), strategy='ema'),
        "EMA Crossover (8/21 EMA)"
    )

    # Test Breakout
    print("\n" + "="*70)
    print("Testing Donchian Channel Breakout")
    print("="*70)
    breakout_engine = TrendFollowingEngine(initial_capital=10000, config=BreakoutConfig())
    all_results['Breakout'] = print_results(
        breakout_engine.run_trend_backtest(df.copy(), strategy='breakout'),
        "Donchian Breakout (20 period)"
    )

    # Test Momentum
    print("\n" + "="*70)
    print("Testing Momentum Trend Following")
    print("="*70)
    momentum_engine = TrendFollowingEngine(initial_capital=10000, config=MomentumConfig())
    all_results['Momentum'] = print_results(
        momentum_engine.run_trend_backtest(df.copy(), strategy='momentum'),
        "Momentum with Trend (3/10 ROC)"
    )

    # Summary comparison
    print("\n" + "="*70)
    print("STRATEGY COMPARISON")
    print("="*70)
    print(f"{'Strategy':<20} {'Return':>10} {'Win Rate':>10} {'Profit Fx':>10} {'Max DD':>10}")
    print("-"*70)

    for name, metrics in all_results.items():
        print(f"{name:<20} {metrics['return']:>9.1f}%   {metrics['win_rate']:>9.1f}%     {metrics['profit_factor']:>9.2f}     {metrics['dd']:>9.1f}%")

    # Find best strategy
    best_return = max(all_results.items(), key=lambda x: x[1]['return'])
    best_pf = max(all_results.items(), key=lambda x: x[1]['profit_factor'])

    print("\n" + "="*70)
    print("BEST PERFORMERS")
    print("="*70)
    print(f"Best Return:        {best_return[0]} ({best_return[1]['return']:.1f}%)")
    print(f"Best Profit Factor: {best_pf[0]} ({best_pf[1]['profit_factor']:.2f})")

    # Annualized metrics
    print("\n" + "="*70)
    print("ANNUALIZED PERFORMANCE (based on {:.1f} days)".format(days))
    print("="*70)
    for name, metrics in all_results.items():
        annualized_return = (1 + metrics['return']/100) ** (365/days) - 1
        print(f"{name:<20} {annualized_return*100:>10.1f}% annualized")
