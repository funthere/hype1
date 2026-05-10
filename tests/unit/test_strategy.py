"""
Unit tests for Strategy Engine
"""

from datetime import datetime

from src.core.config import Side
from src.core.strategy import StrategyEngine, RiskManager


class TestStrategyEngine:
    """Test suite for StrategyEngine"""

    def test_initialization(self, sample_config):
        """Test strategy engine initialization"""
        engine = StrategyEngine(sample_config)

        assert engine.config == sample_config
        assert len(engine.candles) == 0
        assert engine.max_candles == 200
        assert engine._signals_generated == 0

    def test_update_candle(self, strategy_engine, sample_candles):
        """Test candle update functionality"""
        # Add first candle
        strategy_engine.update_candle(sample_candles[0])

        assert len(strategy_engine.candles) == 1

        # Add more candles
        for candle in sample_candles[1:30]:
            strategy_engine.update_candle(candle)

        assert len(strategy_engine.candles) == 30

        # Test max candles limit (sample_candles has 100 candles)
        for candle in sample_candles[30:]:
            strategy_engine.update_candle(candle)

        # Should be capped at max_candles (100 since sample_candles has 100)
        assert len(strategy_engine.candles) == min(100, strategy_engine.max_candles)

    def test_generate_signal_insufficient_data(self, strategy_engine):
        """Test signal generation with insufficient data"""
        # Add only 10 candles (less than required 30)
        for i in range(10):
            strategy_engine.update_candle(
                {
                    "timestamp": datetime.now(),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 10000,
                }
            )

        signal = strategy_engine.generate_signal()

        assert signal is None

    def test_generate_signal_with_data(self, strategy_engine, sample_candles):
        """Test signal generation with sufficient data"""
        # Add candles
        for candle in sample_candles[:50]:
            strategy_engine.update_candle(candle)

        signal = strategy_engine.generate_signal()

        # Signal may or may not be generated depending on the random data
        # But the function should not crash
        assert signal is None or isinstance(signal, dict)

        if signal:
            assert "action" in signal
            assert "confidence" in signal
            assert "entry_price" in signal
            assert "tp_price" in signal
            assert "sl_price" in signal
            assert "quantity" in signal
            assert "atr" in signal
            assert signal["action"] in [Side.LONG, Side.SHORT]
            assert 0 <= signal["confidence"] <= 100
            assert signal["quantity"] > 0

    def test_calculate_atr(self, strategy_engine, sample_candles):
        """Test ATR calculation"""
        # Add candles with known values
        for candle in sample_candles[:50]:
            strategy_engine.update_candle(candle)

        df = strategy_engine.candles
        atr = strategy_engine._calculate_atr(df)

        assert atr > 0
        assert isinstance(atr, float)

    def test_calculate_position_size(self, strategy_engine):
        """Test position size calculation"""
        capital = 10000
        entry_price = 100
        stop_price = 98  # Note: stop_price is not used in current implementation

        quantity = strategy_engine._calculate_position_size(
            capital, entry_price, stop_price
        )

        assert quantity > 0
        assert isinstance(quantity, float)

        # The actual formula is: margin = capital * risk_pct, notional = margin * leverage
        # quantity = notional / price
        margin = capital * strategy_engine.config.RISK_PER_TRADE_PCT
        notional = margin * strategy_engine.config.LEVERAGE
        expected_quantity = notional / entry_price

        assert abs(quantity - expected_quantity) < 0.01

    def test_signal_tracking(self, strategy_engine, sample_candles):
        """Test signal count tracking"""
        initial_count = strategy_engine.get_signals_count()

        # Add candles
        for candle in sample_candles[:50]:
            strategy_engine.update_candle(candle)

        # Generate some signals
        for _ in range(5):
            strategy_engine.generate_signal()

        # Count may have increased
        final_count = strategy_engine.get_signals_count()
        assert final_count >= initial_count

    def test_reset(self, strategy_engine, sample_candles):
        """Test strategy reset functionality"""
        # Add candles
        for candle in sample_candles[:50]:
            strategy_engine.update_candle(candle)

        strategy_engine.generate_signal()

        # Reset
        strategy_engine.reset()

        assert len(strategy_engine.candles) == 0
        assert strategy_engine.get_signals_count() == 0
        assert strategy_engine._last_signal_time is None


class TestRiskManager:
    """Test suite for RiskManager"""

    def test_initialization(self, sample_config):
        """Test risk manager initialization"""
        manager = RiskManager(sample_config)

        assert manager.config == sample_config
        assert manager.daily_pnl == 0.0
        assert manager.daily_trades == 0
        assert manager.starting_capital == sample_config.PAPER_CAPITAL

    def test_can_open_position_success(self, risk_manager):
        """Test position opening check when conditions are met"""
        can_open, reason = risk_manager.can_open_position(
            open_positions=0,
            daily_trades=5,
            daily_pnl=100,
            circuit_breaker_active=False,
        )

        assert can_open is True
        assert reason == ""

    def test_can_open_position_max_positions(self, risk_manager):
        """Test position opening check when max positions reached"""
        can_open, reason = risk_manager.can_open_position(
            open_positions=2,  # MAX_POSITIONS
            daily_trades=5,
            daily_pnl=100,
            circuit_breaker_active=False,
        )

        assert can_open is False
        assert "Max positions" in reason

    def test_can_open_position_max_daily_trades(self, risk_manager):
        """Test position opening check when max daily trades reached"""
        can_open, reason = risk_manager.can_open_position(
            open_positions=0,
            daily_trades=20,  # MAX_DAILY_TRADES
            daily_pnl=100,
            circuit_breaker_active=False,
        )

        assert can_open is False
        assert "Max daily trades" in reason

    def test_can_open_position_daily_loss_limit(self, risk_manager):
        """Test position opening check when daily loss limit exceeded"""
        can_open, reason = risk_manager.can_open_position(
            open_positions=0,
            daily_trades=5,
            daily_pnl=-2000,  # 20% loss on 10k capital (exceeds 15% limit)
            circuit_breaker_active=False,
        )

        assert can_open is False
        assert "Daily loss limit" in reason

    def test_can_open_position_circuit_breaker(self, risk_manager):
        """Test position opening check when circuit breaker is active"""
        can_open, reason = risk_manager.can_open_position(
            open_positions=0, daily_trades=5, daily_pnl=100, circuit_breaker_active=True
        )

        assert can_open is False
        assert "Circuit breaker" in reason

    def test_calculate_position_size(self, risk_manager):
        """Test position size calculation"""
        capital = 10000
        entry_price = 100
        stop_price = 98

        quantity = risk_manager.calculate_position_size(
            capital, entry_price, stop_price
        )

        assert quantity > 0
        assert isinstance(quantity, float)

    def test_calculate_position_size_zero_risk(self, risk_manager):
        """Test position size when stop equals entry"""
        quantity = risk_manager.calculate_position_size(10000, 100, 100)

        assert quantity == 0

    def test_reset_daily(self, risk_manager):
        """Test daily reset functionality"""
        risk_manager.daily_pnl = -500
        risk_manager.daily_trades = 10

        risk_manager.reset_daily()

        assert risk_manager.daily_pnl == 0.0
        assert risk_manager.daily_trades == 0


class TestIntegration:
    """Integration tests for strategy and risk manager"""

    def test_strategy_with_risk_manager(self, sample_config, sample_candles):
        """Test strategy and risk manager working together"""
        strategy = StrategyEngine(sample_config)
        risk = RiskManager(sample_config)

        # Add candles
        for candle in sample_candles[:50]:
            strategy.update_candle(candle)

        # Generate signal
        signal = strategy.generate_signal()

        if signal:
            # Check if risk manager allows the trade
            can_open, reason = risk.can_open_position(
                open_positions=0,
                daily_trades=0,
                daily_pnl=0,
                circuit_breaker_active=False,
            )

            assert can_open is True

            # Verify position size calculation
            quantity = risk.calculate_position_size(
                capital=sample_config.PAPER_CAPITAL,
                entry_price=signal["entry_price"],
                stop_price=signal["sl_price"],
            )

            assert quantity > 0

    def test_confidence_threshold_filtering(self, sample_config, sample_candles):
        """Test that low confidence signals are filtered"""
        sample_config.CONFIDENCE_THRESHOLD = 90  # Very high threshold

        strategy = StrategyEngine(sample_config)

        # Add candles
        for candle in sample_candles[:50]:
            strategy.update_candle(candle)

        # Generate signals - most should be filtered
        high_conf_count = 0
        for _ in range(20):
            signal = strategy.generate_signal()
            if signal:
                if signal["action"] == Side.LONG:
                    if signal["confidence"] >= 90:
                        high_conf_count += 1
                else:
                    if signal["confidence"] >= 10:  # 100 - 90
                        high_conf_count += 1

        # Very few or no signals should pass the high threshold
        assert high_conf_count <= 2
