#!/usr/bin/env python3
"""
Examples of using the HYPE Trading Bot Analytics Features

This file demonstrates:
1. Performance Analysis
2. Health Monitoring
3. Adaptive Parameters
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import BotConfig, Trade, Side
from src.analytics import (
    PerformanceAnalyzer,
    HealthMonitor,
    AdaptiveParameterManager,
    VolatilityDetector,
    TrendDetector,
)


# ============================================
# EXAMPLE 1: Performance Analysis
# ============================================

def example_performance_analysis():
    """Calculate performance metrics from trade history"""
    print("\n" + "="*60)
    print("EXAMPLE 1: Performance Analysis")
    print("="*60)

    # Create analyzer
    analyzer = PerformanceAnalyzer(initial_capital=10000)

    # Add some sample trades
    sample_trades = [
        Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=5),
            exit_time=datetime.now() - timedelta(hours=4),
            pnl=50.0,
            fees=2.0
        ),
        Trade(
            side=Side.SHORT,
            entry_price=102.0,
            exit_price=98.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=3),
            exit_time=datetime.now() - timedelta(hours=2),
            pnl=40.0,
            fees=2.0
        ),
        Trade(
            side=Side.LONG,
            entry_price=99.0,
            exit_price=97.0,
            quantity=10.0,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now(),
            pnl=-20.0,
            fees=2.0
        ),
    ]

    for trade in sample_trades:
        analyzer.add_trade(trade)

    # Calculate metrics
    metrics = analyzer.calculate_metrics()

    # Print report
    print(analyzer.generate_report())

    # Get equity curve
    equity_curve = analyzer.get_equity_curve()
    print(f"\nEquity curve points: {len(equity_curve)}")

    # Get drawdown curve
    drawdown_curve = analyzer.get_drawdown_curve()
    print(f"Drawdown curve points: {len(drawdown_curve)}")


# ============================================
# EXAMPLE 2: Health Monitoring
# ============================================

async def example_health_monitoring():
    """Monitor API and system health"""
    print("\n" + "="*60)
    print("EXAMPLE 2: Health Monitoring")
    print("="*60)

    monitor = HealthMonitor()

    # Simulate API calls
    print("\n📡 Simulating API calls...")

    # Record some API calls
    await monitor.record_api_call("info/mids", 45.2, True)
    await monitor.record_api_call("exchange/order", 123.5, True)
    await monitor.record_api_call("info/user_state", 67.8, True)
    await monitor.record_api_call("exchange/order", 2341.2, False, "Rate limit exceeded")

    # Record some orders
    print("\n📊 Recording order executions...")

    monitor.record_order(
        side="LONG",
        expected_price=100.00,
        filled_price=100.02,
        quantity=10.0,
        fill_time_ms=150,
        status="filled"
    )

    monitor.record_order(
        side="SHORT",
        expected_price=102.00,
        filled_price=102.05,
        quantity=10.0,
        fill_time_ms=200,
        status="filled"
    )

    # Get health snapshot
    snapshot = monitor.get_health_snapshot()

    print(f"\n📈 Health Status: {snapshot.status.value}")
    print(f"   API Latency: {snapshot.api.avg_latency_ms:.0f}ms")
    print(f"   Fill Rate: {snapshot.execution.fill_rate:.1%}")
    print(f"   CPU Usage: {snapshot.system.cpu_percent:.0f}%")

    # Print summary
    print(monitor.get_summary())


# ============================================
# EXAMPLE 3: Adaptive Parameters
# ============================================

def example_adaptive_parameters():
    """Demonstrate adaptive parameter adjustment"""
    print("\n" + "="*60)
    print("EXAMPLE 3: Adaptive Parameters")
    print("="*60)

    config = BotConfig()
    config.LEVERAGE = 10
    config.RISK_PER_TRADE_PCT = 0.10
    config.CONFIDENCE_THRESHOLD = 50

    # Create adaptive manager
    manager = AdaptiveParameterManager(config)

    print("\n📊 Initial Parameters:")
    print(f"   Leverage: {config.LEVERAGE}x")
    print(f"   Risk: {config.RISK_PER_TRADE_PCT:.1%}")
    print(f"   Confidence: {config.CONFIDENCE_THRESHOLD}")

    # Simulate price updates in different market conditions
    print("\n📈 Simulating different market conditions...")

    # Normal volatility, uptrend (bull)
    print("\n1. Normal volatility, Bull trend:")
    for i in range(30):
        price = 100 + i * 0.5 + __import__('random').uniform(-0.5, 0.5)
        manager.update_market_data(price)

    params = manager.get_parameters()
    print(f"   Phase: {params.market_phase.value}")
    print(f"   Volatility: {params.volatility_regime.value}")
    print(f"   Adapted Leverage: {params.leverage}x")
    print(f"   Adapted Risk: {params.risk_per_trade:.1%}")

    # High volatility, downtrend (bear)
    print("\n2. High volatility, Bear trend:")
    for i in range(30):
        price = 115 - i * 1.5 + __import__('random').uniform(-2, 2)
        manager.update_market_data(price)

    params = manager.get_parameters()
    print(f"   Phase: {params.market_phase.value}")
    print(f"   Volatility: {params.volatility_regime.value}")
    print(f"   Adapted Leverage: {params.leverage}x")
    print(f"   Adapted Risk: {params.risk_per_trade:.1%}")

    # Check signal filtering
    print("\n🎯 Signal Filtering:")
    print(f"   LONG signal at 55% confidence: ", end="")
    print(f"FILTERED" if manager.should_filter_signal(55, Side.LONG) else "ACCEPTED")

    print(f"   SHORT signal at 55% confidence: ", end="")
    print(f"FILTERED" if manager.should_filter_signal(55, Side.SHORT) else "ACCEPTED")

    print(manager.get_summary())


# ============================================
# EXAMPLE 4: Using with Trading Bot
# ============================================

def example_integrated_usage():
    """Show how to integrate analytics into trading bot"""
    print("\n" + "="*60)
    print("EXAMPLE 4: Integrated Usage")
    print("="*60)

    code = '''
from src.bot.trading_bot import TradingBot
from src.analytics import PerformanceAnalyzer, HealthMonitor

class EnhancedTradingBot(TradingBot):
    """Trading bot with analytics"""

    def __init__(self, config):
        super().__init__(config)

        # Add analytics
        self.performance = PerformanceAnalyzer(
            initial_capital=self.starting_capital
        )
        self.health = HealthMonitor()

    async def _close_position(self, position, exit_price, reason):
        # Call parent method
        await super()._close_position(position, exit_price, reason)

        # Track performance
        if hasattr(self, 'trades') and self.trades:
            self.performance.add_trade(self.trades[-1])

    async def _process_signal(self, signal):
        # Wrap API call with health monitoring
        async with self.health.api_call("place_order") as timer:
            result = await self.api.place_order(...)

        return result

    def get_performance_report(self):
        """Generate performance report"""
        return self.performance.generate_report()

    def get_health_report(self):
        """Get health summary"""
        return self.health.get_summary()
'''
    print(code)


# ============================================
# MAIN
# ============================================

async def main():
    """Run all examples"""
    import asyncio

    print("\n" + "🔬 " * 20)
    print("HYPE Trading Bot - Analytics Examples")
    print("🔬 " * 20)

    example_performance_analysis()
    await example_health_monitoring()
    example_adaptive_parameters()
    example_integrated_usage()

    print("\n" + "="*60)
    print("Examples completed!")
    print("="*60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
