"""
Quick runner for the best HYPE/USDC strategies

Usage:
    python3 run_best_strategy.py --strategy ema_1h
    python3 run_best_strategy.py --strategy momentum_15m
    python3 run_best_strategy.py --strategy breakout_5m
"""

import argparse
import pandas as pd
from best_strategies import (
    HYPE_EMA_1h_Config,
    HYPE_Momentum_15m_Aggressive,
    HYPE_Breakout_5m_Optimized,
    run_ema_1h_backtest,
    run_momentum_15m_backtest,
    run_breakout_5m_backtest
)


def print_results(results, strategy_name):
    """Print formatted backtest results"""
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


def main():
    parser = argparse.ArgumentParser(description='Run best HYPE/USDC strategies')
    parser.add_argument('--strategy', type=str, default='ema_1h',
                        choices=['ema_1h', 'momentum_15m', 'breakout_5m', 'all'],
                        help='Strategy to run')
    parser.add_argument('--capital', type=float, default=10000,
                        help='Initial capital (default: 10000)')

    args = parser.parse_args()

    print("=" * 70)
    print("HYPE/USDC BEST STRATEGIES BACKTEST")
    print("=" * 70)

    if args.strategy == 'all':
        print("\nRunning all strategies...\n")

        # EMA 1h
        print("\n" + "=" * 70)
        print("EMA Crossover (1 Hour)")
        print("=" * 70)
        ema_results = run_ema_1h_backtest(initial_capital=args.capital)
        print_results(ema_results, "EMA Crossover (1h)")

        # Momentum 15m
        print("\n" + "=" * 70)
        print("Momentum Aggressive (15 Minute)")
        print("=" * 70)
        mom_results = run_momentum_15m_backtest(initial_capital=args.capital)
        print_results(mom_results, "Momentum Aggressive (15m)")

        # Breakout 5m
        print("\n" + "=" * 70)
        print("Donchian Breakout (5 Minute)")
        print("=" * 70)
        breakout_results = run_breakout_5m_backtest(initial_capital=args.capital)
        print_results(breakout_results, "Donchian Breakout (5m)")

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"{'Strategy':<25} {'Return':>10} {'Win Rate':>10} {'PF':>8} {'DD':>8}")
        print("-" * 70)

        strategies = [
            ("EMA 1h", ema_results),
            ("Momentum 15m", mom_results),
            ("Breakout 5m", breakout_results),
        ]

        for name, results in strategies:
            s = results['summary']
            t = results['trades']
            print(f"{name:<25} {s['total_return_pct']:>9.1f}%   {t['win_rate_pct']:>9.1f}%   {t['profit_factor']:>6.2f}   {s['max_drawdown_pct']:>6.1f}%")

    elif args.strategy == 'ema_1h':
        results = run_ema_1h_backtest(initial_capital=args.capital)
        print_results(results, "EMA Crossover (1 Hour)")

    elif args.strategy == 'momentum_15m':
        results = run_momentum_15m_backtest(initial_capital=args.capital)
        print_results(results, "Momentum Aggressive (15m)")

    elif args.strategy == 'breakout_5m':
        results = run_breakout_5m_backtest(initial_capital=args.capital)
        print_results(results, "Donchian Breakout (5m)")


if __name__ == "__main__":
    main()
