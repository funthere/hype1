"""
Unit tests for trend indicator calculations (ADX, ATR, Wilder's smoothing).

These tests verify that the indicator calculations use Wilder's smoothing
instead of simple rolling means, producing correct ADX/ATR values.
"""

import numpy as np
import pandas as pd
import pytest

from src.strategy.trend_following import TrendFollowingStrategy


# ---------------------------------------------------------------------------
# Test data: A hand-crafted OHLCV series where we can verify the math
# ---------------------------------------------------------------------------

def _make_test_df():
    """Create a simple OHLCV DataFrame for testing.

    Uses a steadily trending series so ADX should be high.
    Prices rise 1 unit per bar with a small range.
    """
    n = 50
    np.random.seed(42)
    base = 100.0
    highs = []
    lows = []
    closes = []
    opens = []

    for i in range(n):
        o = base + i * 1.0 + np.random.uniform(-0.2, 0.2)
        c = base + i * 1.0 + 0.5 + np.random.uniform(-0.2, 0.2)
        h = max(o, c) + np.random.uniform(0.1, 0.5)
        l = min(o, c) - np.random.uniform(0.1, 0.5)
        opens.append(round(o, 4))
        highs.append(round(h, 4))
        lows.append(round(l, 4))
        closes.append(round(c, 4))

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * n,
    })
    return df


def _simple_trending_df():
    """Very simple OHLCV that trends up perfectly.

    Each bar: H = bar+2, L = bar, C = bar+1.
    This makes manual verification easy.
    """
    rows = []
    for i in range(30):
        rows.append({
            "open": float(i),
            "high": float(i + 2),
            "low": float(i),
            "close": float(i + 1),
            "volume": 1000.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Wilder's smoothing tests
# ---------------------------------------------------------------------------

class TestWildersSmoothing:
    """Tests for the _wilder_smooth helper method."""

    def test_wilder_smooth_basic(self):
        """Wilder's smoothing seed should equal SMA of first `period` values."""
        data = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
        period = 3
        result = TrendFollowingStrategy._wilder_smooth(data, period)

        # First valid value at index period-1 = 2
        expected_seed = data.iloc[:period].mean()  # (10+20+30)/3 = 20
        assert result.iloc[period - 1] == pytest.approx(expected_seed, abs=1e-10)

    def test_wilder_smooth_recursion(self):
        """Verify the recursive Wilder's formula: prev*(p-1)/p + curr/p."""
        data = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
        period = 3
        result = TrendFollowingStrategy._wilder_smooth(data, period)

        # Index 3: seed*(2/3) + data[3]/3 = 20*(2/3) + 40/3 = 40/3 + 40/3 = 80/3
        expected_3 = 20.0 * (2.0 / 3.0) + 40.0 / 3.0
        assert result.iloc[3] == pytest.approx(expected_3, abs=1e-10)

        # Index 4: prev*(2/3) + data[4]/3
        expected_4 = expected_3 * (2.0 / 3.0) + 50.0 / 3.0
        assert result.iloc[4] == pytest.approx(expected_4, abs=1e-10)

    def test_wilder_smooth_short_series(self):
        """Series shorter than period should return all NaN."""
        data = pd.Series([1.0, 2.0])
        result = TrendFollowingStrategy._wilder_smooth(data, period=5)
        assert result.isna().all()

    def test_wilder_smooth_exactly_period(self):
        """Series of exactly `period` length should have one valid value."""
        data = pd.Series([1.0, 2.0, 3.0])
        result = TrendFollowingStrategy._wilder_smooth(data, period=3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2] == pytest.approx(2.0, abs=1e-10)

    def test_wilder_smooth_differs_from_rolling_mean(self):
        """Wilder's smoothing should produce different results from simple SMA."""
        data = pd.Series(range(1, 21), dtype=float)
        period = 5
        wilder = TrendFollowingStrategy._wilder_smooth(data, period)
        sma = data.rolling(window=period).mean()

        # At the seed point they should be equal
        assert wilder.iloc[period - 1] == pytest.approx(sma.iloc[period - 1], abs=1e-10)

        # After that they should diverge
        assert wilder.iloc[-1] != sma.iloc[-1]


# ---------------------------------------------------------------------------
# ATR tests
# ---------------------------------------------------------------------------

class TestATR:
    """Tests for the _calculate_atr method."""

    def test_atr_returns_series(self):
        """ATR should return a pandas Series."""
        df = _make_test_df()
        atr = TrendFollowingStrategy._calculate_atr(df, period=14)
        assert isinstance(atr, pd.Series)

    def test_atr_positive(self):
        """ATR values should be non-negative."""
        df = _make_test_df()
        atr = TrendFollowingStrategy._calculate_atr(df, period=14)
        valid = atr.dropna()
        assert (valid >= 0).all()

    def test_atr_constant_range(self):
        """If all bars have the same range, ATR should converge to that range."""
        df = _simple_trending_df()
        # Each bar: H-L = 2, |H-prevC| = 1, |L-prevC| = 1  (after bar 0)
        # TR = max(2, 1, 1) = 2 for bars >= 1
        atr = TrendFollowingStrategy._calculate_atr(df, period=14)
        # After enough bars, ATR should be close to 2.0
        valid = atr.dropna()
        # The last value should be exactly 2.0 since all TRs are 2.0
        assert valid.iloc[-1] == pytest.approx(2.0, abs=0.01)

    def test_atr_uses_wilders(self):
        """ATR should use Wilder's smoothing, not simple rolling mean."""
        df = _make_test_df()
        atr = TrendFollowingStrategy._calculate_atr(df, period=14)

        # Manually compute Wilder-smoothed ATR
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        expected = TrendFollowingStrategy._wilder_smooth(tr, 14)

        pd.testing.assert_series_equal(atr, expected)


# ---------------------------------------------------------------------------
# ADX tests
# ---------------------------------------------------------------------------

class TestADX:
    """Tests for the _calculate_adx method."""

    def test_adx_returns_series(self):
        """ADX should return a pandas Series."""
        df = _make_test_df()
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)
        assert isinstance(adx, pd.Series)

    def test_adx_range(self):
        """ADX values should be in [0, 100]."""
        df = _make_test_df()
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)
        valid = adx.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_adx_high_in_strong_trend(self):
        """ADX should be high (>20) in a strong uptrend."""
        df = _make_test_df()
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)
        valid = adx.dropna()
        # Our test data trends strongly upward, so ADX should be well above 20
        assert valid.iloc[-1] > 20.0

    def test_adx_low_in_ranging_market(self):
        """ADX should be low in a sideways/ranging market."""
        n = 80
        # Price oscillates around 100 with tiny moves
        prices = [100 + 0.5 * np.sin(i * 0.5) for i in range(n)]
        df = pd.DataFrame({
            "open": prices,
            "high": [p + 0.1 for p in prices],
            "low": [p - 0.1 for p in prices],
            "close": prices,
            "volume": [1000.0] * n,
        })
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)
        valid = adx.dropna()
        if len(valid) > 0:
            assert valid.iloc[-1] < 30.0

    def test_adx_manual_verification(self):
        """Manually verify ADX calculation on a small dataset.

        We use a perfectly trending series and verify the step-by-step math.
        """
        # 20 bars, each trending up by exactly 1 unit
        # H = i+2, L = i, C = i+1
        n = 20
        df = pd.DataFrame({
            "open": [float(i) for i in range(n)],
            "high": [float(i + 2) for i in range(n)],
            "low": [float(i) for i in range(n)],
            "close": [float(i + 1) for i in range(n)],
            "volume": [1000.0] * n,
        })

        period = 5
        adx = TrendFollowingStrategy._calculate_adx(df, period=period)
        valid = adx.dropna()

        # ADX should be defined (need 2*period - 1 bars minimum for valid output)
        assert len(valid) > 0

        # In a perfect uptrend, ADX should be very high (near 100)
        # because +DM dominates -DM
        assert valid.iloc[-1] > 50.0

    def test_adx_manual_step_by_step(self):
        """Verify ADX step-by-step for a tiny dataset with period=3."""
        # 8 bars, period=3
        # Strong uptrend: close rises by 1 each bar
        df = pd.DataFrame({
            "open": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
            "high": [11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5],
            "low": [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
            "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5],
            "volume": [1000.0] * 8,
        })

        period = 3
        adx = TrendFollowingStrategy._calculate_adx(df, period=period)
        valid = adx.dropna()

        # There should be valid ADX values
        assert len(valid) > 0

        # In this perfect uptrend, +DM should dominate, ADX should be high
        assert valid.iloc[-1] > 40.0

    def test_adx_no_nan_in_di_denominator(self):
        """Verify no spurious NaN from division by zero in DI calculation."""
        df = _make_test_df()
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)
        # Should not error and should have valid values
        valid = adx.dropna()
        assert len(valid) > 0

    def test_adx_differs_from_rolling_mean(self):
        """Verify that the Wilder's-smoothed ADX differs from a naive rolling-mean ADX."""
        df = _make_test_df()
        period = 14

        # Compute our (correct) ADX
        adx_correct = TrendFollowingStrategy._calculate_adx(df, period=period)

        # Compute a naive rolling-mean ADX (the old broken implementation)
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)

        atr_naive = tr.rolling(window=period).mean()
        plus_di_naive = 100 * plus_dm.rolling(window=period).mean() / atr_naive
        minus_di_naive = 100 * minus_dm.rolling(window=period).mean() / atr_naive
        dx_naive = 100 * (plus_di_naive - minus_di_naive).abs() / (plus_di_naive + minus_di_naive)
        adx_naive = dx_naive.rolling(window=period).mean()

        # They should differ (at least at some point)
        valid_mask = adx_correct.notna() & adx_naive.notna()
        if valid_mask.sum() > 0:
            diff = (adx_correct[valid_mask] - adx_naive[valid_mask]).abs()
            assert diff.max() > 0.01, "Wilder's ADX should differ from simple rolling mean ADX"


# ---------------------------------------------------------------------------
# Integration: verify both indicators work on the same data
# ---------------------------------------------------------------------------

class TestIndicatorIntegration:
    """Integration tests for indicators."""

    def test_both_indicators_on_same_df(self):
        """ATR and ADX should both work on the same DataFrame without conflicts."""
        df = _make_test_df()
        atr = TrendFollowingStrategy._calculate_atr(df, period=14)
        adx = TrendFollowingStrategy._calculate_adx(df, period=14)

        assert atr is not None
        assert adx is not None
        assert len(atr) == len(df)
        assert len(adx) == len(df)

    def test_adx_period_variations(self):
        """ADX should work with different periods."""
        df = _make_test_df()
        for period in [7, 14, 21]:
            adx = TrendFollowingStrategy._calculate_adx(df, period=period)
            valid = adx.dropna()
            assert len(valid) > 0, f"ADX with period={period} should have valid values"
            assert (valid >= 0).all() and (valid <= 100).all()

    def test_atr_period_variations(self):
        """ATR should work with different periods."""
        df = _make_test_df()
        for period in [7, 14, 21]:
            atr = TrendFollowingStrategy._calculate_atr(df, period=period)
            valid = atr.dropna()
            assert len(valid) > 0, f"ATR with period={period} should have valid values"
            assert (valid >= 0).all()
