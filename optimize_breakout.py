"""
Optimize Donchian Breakout Strategy Parameters on Real HYPE Data
"""
import pandas as pd
import numpy as np
from itertools import product
from trend_following_strategies import TrendFollowingEngine, TrendConfig
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class OptimizedBreakoutConfig(TrendConfig):
    """Configurable Donchian Breakout for optimization"""
    ASSET: str = "HYPE"
    TIMEFRAME: str = "5m"
    LEVERAGE: int = 5
    ORDER_TYPE = TrendConfig.ORDER_TYPE

    # Risk parameters
    RISK_PER_TRADE_PCT: float = 0.08
    TP_ATR_MULTIPLIER: float = 2.0
    SL_ATR_MULTIPLIER: float = 0.8

    # Breakout parameters
    CHANNEL_PERIOD: int = 20
    BREAKOUT_CONFIRMATION: bool = True
    MIN_ATR_MULT: float = 0.5
    MAX_ATR_MULT: float = 3.0
    CHANNEL_EXIT: bool = True

    # Position sizing
    MAX_CONCURRENT_POSITIONS: int = 1
    TRADE_COOLDOWN_BARS: int = 6
    MAX_DAILY_TRADES: int = 15

    # Trailing stop
    USE_TRAILING_STOP: bool = True
    TRAIL_ACTIVATION_PCT: float = 0.5
    TRAIL_STOP_ATR: float = 0.6

    # Fees
    MAKER_FEE_PCT: float = -0.0002
    TAKER_FEE_PCT: float = 0.0004

    # Will be set during optimization
    CONFIDENCE_THRESHOLD: int = 60


def optimize_parameters(df: pd.DataFrame) -> Tuple[dict, dict]:
    """
    Run grid search optimization on Donchian Breakout parameters

    Returns:
        (best_params, all_results)
    """

    # Parameter grid
    param_grid = {
        'CHANNEL_PERIOD': [15, 20, 25, 30],
        'CONFIDENCE_THRESHOLD': [55, 60, 65, 70],
        'TP_ATR_MULTIPLIER': [1.5, 2.0, 2.5, 3.0],
        'SL_ATR_MULTIPLIER': [0.6, 0.8, 1.0],
        'MIN_ATR_MULT': [0.3, 0.5, 0.7],
        'TRAIL_STOP_ATR': [0.4, 0.6, 0.8],
    }

    # Generate all combinations (sample to reduce computation)
    all_results = []
    total_combos = (
        len(param_grid['CHANNEL_PERIOD']) *
        len(param_grid['CONFIDENCE_THRESHOLD']) *
        len(param_grid['TP_ATR_MULTIPLIER']) *
        len(param_grid['SL_ATR_MULTIPLIER']) *
        len(param_grid['MIN_ATR_MULT']) *
        len(param_grid['TRAIL_STOP_ATR'])
    )

    print(f"Testing {total_combos} parameter combinations...")
    print("=" * 70)

    tested = 0
    for (chan_per, conf_thresh, tp_mult, sl_mult, min_atr, trail_atr) in product(
        param_grid['CHANNEL_PERIOD'],
        param_grid['CONFIDENCE_THRESHOLD'],
        param_grid['TP_ATR_MULTIPLIER'],
        param_grid['SL_ATR_MULTIPLIER'],
        param_grid['MIN_ATR_MULT'],
        param_grid['TRAIL_STOP_ATR']
    ):
        tested += 1
        if tested % 50 == 0:
            print(f"Progress: {tested}/{total_combos} ({100*tested/total_combos:.0f}%)")

        # Create config with these parameters
        config = OptimizedBreakoutConfig()
        config.CHANNEL_PERIOD = chan_per
        config.CONFIDENCE_THRESHOLD = conf_thresh
        config.TP_ATR_MULTIPLIER = tp_mult
        config.SL_ATR_MULTIPLIER = sl_mult
        config.MIN_ATR_MULT = min_atr
        config.TRAIL_STOP_ATR = trail_atr

        # Calculate RR ratio
        rr_ratio = tp_mult / sl_mult if sl_mult > 0 else 0
        config.EXPECTED_RR_RATIO = rr_ratio

        # Run backtest
        try:
            engine = TrendFollowingEngine(initial_capital=10000, config=config)
            results = engine.run_trend_backtest(df.copy(), strategy='breakout')

            s = results['summary']
            t = results['trades']

            # Store results
            all_results.append({
                'CHANNEL_PERIOD': chan_per,
                'CONFIDENCE_THRESHOLD': conf_thresh,
                'TP_ATR_MULTIPLIER': tp_mult,
                'SL_ATR_MULTIPLIER': sl_mult,
                'MIN_ATR_MULT': min_atr,
                'TRAIL_STOP_ATR': trail_atr,
                'RR_RATIO': rr_ratio,
                'total_return_pct': s['total_return_pct'],
                'max_drawdown_pct': s['max_drawdown_pct'],
                'sharpe_ratio': s['sharpe_ratio'],
                'total_trades': t['total_trades'],
                'win_rate_pct': t['win_rate_pct'],
                'profit_factor': t['profit_factor'],
            })
        except Exception as e:
            pass

    print(f"\nCompleted {tested} tests\n")

    # Convert to DataFrame for analysis
    results_df = pd.DataFrame(all_results)

    # Find best by different metrics
    best_return = results_df.loc[results_df['total_return_pct'].idxmax()]
    best_sharpe = results_df.loc[results_df['sharpe_ratio'].idxmax()]
    best_profit_factor = results_df.loc[results_df['profit_factor'].idxmax()]
    best_winrate = results_df.loc[results_df['win_rate_pct'].idxmax()]

    # Calculate composite score (return * sharpe * profit_factor)
    results_df['composite_score'] = (
        results_df['total_return_pct'] *
        results_df['sharpe_ratio'] *
        (results_df['profit_factor'] / 10)
    )
    best_composite = results_df.loc[results_df['composite_score'].idxmax()]

    return {
        'best_return': best_return.to_dict(),
        'best_sharpe': best_sharpe.to_dict(),
        'best_profit_factor': best_profit_factor.to_dict(),
        'best_winrate': best_winrate.to_dict(),
        'best_composite': best_composite.to_dict(),
    }, results_df


if __name__ == "__main__":
    # Load real HYPE data
    print("Loading real HYPE/USDC data from Hyperliquid...")
    df = pd.read_csv('hyperliquid_hype_5m_90d.csv')
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    print(f"\nDataset: {len(df)} candles")
    print(f"Period: {df['timestamp'].min()} to {df['timestamp'].max()}")

    days = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / (24 * 3600)
    print(f"Duration: {days:.1f} days\n")

    # Run optimization
    best_params, all_results = optimize_parameters(df)

    # Print results
    print("\n" + "=" * 70)
    print("OPTIMIZATION RESULTS")
    print("=" * 70)

    print("\n🏆 BEST RETURN")
    print("-" * 70)
    b = best_params['best_return']
    print(f"Return:           {b['total_return_pct']:.2f}%")
    print(f"Sharpe:           {b['sharpe_ratio']:.2f}")
    print(f"Win Rate:         {b['win_rate_pct']:.1f}%")
    print(f"Profit Factor:    {b['profit_factor']:.2f}")
    print(f"Max DD:           {b['max_drawdown_pct']:.2f}%")
    print(f"Trades:           {int(b['total_trades'])}")
    print(f"\nParameters:")
    print(f"  CHANNEL_PERIOD:       {int(b['CHANNEL_PERIOD'])}")
    print(f"  CONFIDENCE_THRESHOLD: {int(b['CONFIDENCE_THRESHOLD'])}")
    print(f"  TP_ATR_MULTIPLIER:    {b['TP_ATR_MULTIPLIER']}")
    print(f"  SL_ATR_MULTIPLIER:    {b['SL_ATR_MULTIPLIER']}")
    print(f"  MIN_ATR_MULT:         {b['MIN_ATR_MULT']}")
    print(f"  TRAIL_STOP_ATR:       {b['TRAIL_STOP_ATR']}")

    print("\n⭐ BEST SHARPE RATIO")
    print("-" * 70)
    b = best_params['best_sharpe']
    print(f"Sharpe:           {b['sharpe_ratio']:.2f}")
    print(f"Return:           {b['total_return_pct']:.2f}%")
    print(f"Win Rate:         {b['win_rate_pct']:.1f}%")
    print(f"Profit Factor:    {b['profit_factor']:.2f}")
    print(f"Max DD:           {b['max_drawdown_pct']:.2f}%")
    print(f"\nParameters:")
    print(f"  CHANNEL_PERIOD:       {int(b['CHANNEL_PERIOD'])}")
    print(f"  CONFIDENCE_THRESHOLD: {int(b['CONFIDENCE_THRESHOLD'])}")
    print(f"  TP_ATR_MULTIPLIER:    {b['TP_ATR_MULTIPLIER']}")
    print(f"  SL_ATR_MULTIPLIER:    {b['SL_ATR_MULTIPLIER']}")

    print("\n💎 BEST PROFIT FACTOR")
    print("-" * 70)
    b = best_params['best_profit_factor']
    print(f"Profit Factor:    {b['profit_factor']:.2f}")
    print(f"Return:           {b['total_return_pct']:.2f}%")
    print(f"Sharpe:           {b['sharpe_ratio']:.2f}")
    print(f"Win Rate:         {b['win_rate_pct']:.1f}%")
    print(f"Max DD:           {b['max_drawdown_pct']:.2f}%")
    print(f"\nParameters:")
    print(f"  CHANNEL_PERIOD:       {int(b['CHANNEL_PERIOD'])}")
    print(f"  CONFIDENCE_THRESHOLD: {int(b['CONFIDENCE_THRESHOLD'])}")
    print(f"  TP_ATR_MULTIPLIER:    {b['TP_ATR_MULTIPLIER']}")
    print(f"  SL_ATR_MULTIPLIER:    {b['SL_ATR_MULTIPLIER']}")

    print("\n🎯 BEST COMPOSITE SCORE")
    print("-" * 70)
    b = best_params['best_composite']
    print(f"Composite:        {b['composite_score']:.2f}")
    print(f"Return:           {b['total_return_pct']:.2f}%")
    print(f"Sharpe:           {b['sharpe_ratio']:.2f}")
    print(f"Profit Factor:    {b['profit_factor']:.2f}")
    print(f"Win Rate:         {b['win_rate_pct']:.1f}%")
    print(f"Max DD:           {b['max_drawdown_pct']:.2f}%")
    print(f"\nParameters:")
    print(f"  CHANNEL_PERIOD:       {int(b['CHANNEL_PERIOD'])}")
    print(f"  CONFIDENCE_THRESHOLD: {int(b['CONFIDENCE_THRESHOLD'])}")
    print(f"  TP_ATR_MULTIPLIER:    {b['TP_ATR_MULTIPLIER']}")
    print(f"  SL_ATR_MULTIPLIER:    {b['SL_ATR_MULTIPLIER']}")
    print(f"  MIN_ATR_MULT:         {b['MIN_ATR_MULT']}")
    print(f"  TRAIL_STOP_ATR:       {b['TRAIL_STOP_ATR']}")

    # Save all results
    all_results.to_csv('breakout_optimization_results.csv', index=False)
    print("\n✅ All results saved to breakout_optimization_results.csv")

    # Show top 10 by return
    print("\n" + "=" * 70)
    print("TOP 10 CONFIGURATIONS BY RETURN")
    print("=" * 70)
    top10 = all_results.nlargest(10, 'total_return_pct')
    for i, row in top10.iterrows():
        print(f"#{len(top10) - list(top10.index).index(i):2d} | "
              f"Return: {row['total_return_pct']:>6.2f}% | "
              f"Sharpe: {row['sharpe_ratio']:>5.2f} | "
              f"Win%: {row['win_rate_pct']:>5.1f}% | "
              f"PF: {row['profit_factor']:>4.2f} | "
              f"Ch: {int(row['CHANNEL_PERIOD']):>2} | "
              f"Conf: {int(row['CONFIDENCE_THRESHOLD']):>2} | "
              f"TP: {row['TP_ATR_MULTIPLIER']:.1f} | "
              f"SL: {row['SL_ATR_MULTIPLIER']:.1f}")
