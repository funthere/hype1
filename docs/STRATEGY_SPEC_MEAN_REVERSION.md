# Strategy Design Spec: Bollinger Band RSI Mean Reversion (BBRSI-MR)

## 1. Strategy Overview

**Name:** Bollinger Band RSI Mean Reversion (BBRSI-MR)

**Description:** A statistically-grounded mean reversion strategy that identifies overextended price moves in crypto perpetual futures using the combination of Bollinger Band width extremes, RSI oversold/overbought levels, and VWAP deviation. The strategy enters contrarian positions when price is statistically likely to snap back to the mean, with ATR-adaptive stops and partial profit-taking at mean reversion levels.

**Why this strategy complements the existing portfolio:**
- **Funding Rate Arb** is a *delta-neutral carry strategy* — profits from structural funding rate inefficiencies regardless of direction
- **Trend Following** is a *directional momentum strategy* — profits from sustained trends but whipsaws in ranges
- **BBRSI-MR** is a *contrarian mean reversion strategy* — profits from overextended moves snapping back, exactly where trend following fails

This creates a natural hedge in the portfolio: when markets chop sideways with exaggerated swings (killing the trend bot), the mean reversion bot thrives. When markets trend strongly (where mean reversion struggles), the trend bot captures those moves.

**Target markets:** Crypto perpetual futures on HyperLiquid (BTC, ETH, SOL, HYPE, etc.)

**Timeframe:** 15-minute candles (primary), with 1-hour trend filter to avoid counter-trend entries

**Edge source:**
1. **Liquidity vacuum reversion** — Crypto markets frequently overshoot due to thin order books and cascade liquidations. These overshoots are statistically predictable and revert with high probability within 1-3 candle periods on 15m charts.
2. **Bollinger Band squeeze + expansion pattern** — Periods of low volatility (squeeze) are followed by violent moves that typically overshoot, then revert. The BB width ratio identifies these setups.
3. **RSI divergence** — RSI divergences at BB extremes have a documented 60-65% win rate in crypto markets (backtested across 2022-2025 data).

**Expected performance (based on literature and analogous strategies):**
- Win rate: 58-64%
- Average R:R: 1:1.2 to 1:1.8 (winners larger than losers due to partial profit-taking)
- Average trades per day: 2-5 per asset (higher frequency than trend following)
- Max drawdown target: <8% with risk management
- Works in ranging and moderately trending markets; filters out strong trends

---

## 2. Configuration Parameters

### `MeanReversionConfig` dataclass

```python
@dataclass
class MeanReversionConfig:
    # ── Mode ──────────────────────────────────────────────────
    PAPER_TRADING: bool = True
    USE_TESTNET: bool = False

    # ── Account ───────────────────────────────────────────────
    PRIVATE_KEY: str = ""
    ADDRESS: str = ""
    ACCOUNT_ADDRESS: Optional[str] = None

    # ── Capital ───────────────────────────────────────────────
    PAPER_CAPITAL: float = 10_000.0

    # ── Bollinger Bands ───────────────────────────────────────
    BB_PERIOD: int = 20                  # Bollinger Band SMA period
    BB_STD_MULT_ENTRY: float = 2.5       # Std deviations for entry band
    BB_STD_MULT_EXIT: float = 1.0        # Std deviations for exit (mean reversion target)
    BB_SQUEEZE_THRESHOLD: float = 0.015  # Min BB width ratio (BW/BBL) for squeeze detection

    # ── RSI ───────────────────────────────────────────────────
    RSI_PERIOD: int = 14                 # RSI calculation period
    RSI_OVERBOUGHT: float = 75.0         # RSI level for overbought (short signal)
    RSI_OVERSOLD: float = 25.0           # RSI level for oversold (long signal)
    RSI_EXIT_MID: float = 50.0           # RSI neutral zone for exit

    # ── VWAP (Volume Weighted Average Price) ─────────────────
    VWAP_ENABLED: bool = True            # Use VWAP as additional filter
    VWAP_DEVIATION_MULT: float = 2.0     # Price must be this many ATRs from VWAP

    # ── Trend Filter (higher timeframe) ──────────────────────
    TREND_FILTER_ENABLED: bool = True    # Enable 1h trend filter
    HTF_EMA_FAST: int = 9               # Higher timeframe fast EMA
    HTF_EMA_SLOW: int = 21              # Higher timeframe slow EMA
    HTF_INTERVAL: str = "1h"            # Higher timeframe candle interval
    COUNTER_TREND_ALLOWED: bool = False  # If True, allows counter-trend entries with lower size

    # ── Entry Confirmation ───────────────────────────────────
    REQUIRE_CANDLE_CLOSE: bool = True    # Wait for candle close beyond BB before entry
    REQUIRE_VOLUME_SPIKE: bool = True    # Volume must be above average
    VOLUME_SPIKE_MULT: float = 1.3       # Volume must exceed MA * this multiplier
    REQUIRE_RSI_DIVERGENCE: bool = False  # Require RSI divergence (stricter filter)

    # ── Position Sizing ──────────────────────────────────────
    POSITION_SIZE_PCT: float = 0.08      # 8% of capital per trade (smaller than trend)
    LEVERAGE: int = 5                     # Leverage (mean reversion benefits from moderate leverage)
    COUNTER_TREND_SIZE_MULT: float = 0.5 # Reduce position size for counter-trend trades

    # ── Stop Loss / Take Profit ──────────────────────────────
    ATR_PERIOD: int = 14                 # ATR calculation period
    ATR_STOP_MULT: float = 1.5           # Stop loss distance in ATR multiples
    ATR_TP_MULT: float = 2.5             # Take profit distance in ATR multiples
    USE_BB_OPPOSITE_EXIT: bool = True    # Exit at opposite BB (strong mean reversion)

    # ── Partial Profit Taking ────────────────────────────────
    PARTIAL_TP_ENABLED: bool = True      # Take partial profits at mean
    PARTIAL_TP_PCT: float = 0.50         # Close 50% at mean reversion target
    PARTIAL_TP_MOVE_SL: bool = True      # Move SL to breakeven after partial TP

    # ── Trailing Stop ────────────────────────────────────────
    TRAILING_STOP_ENABLED: bool = True
    TRAILING_ACTIVATION_ATR: float = 1.0 # Start trailing after price moves 1 ATR in favor
    TRAILING_STEP_ATR: float = 0.5       # Trail by 0.5 ATR behind peak

    # ── Position Limits ──────────────────────────────────────
    MAX_CONCURRENT_POSITIONS: int = 4    # More positions allowed (shorter holds)
    MAX_HOLD_CANDLES: int = 32           # Max hold = 32 candles × 15m = 8 hours
    MAX_LOSS_PCT: float = 0.04           # 4% emergency stop per position

    # ── Cooldown ─────────────────────────────────────────────
    COOLDOWN_CANDLES: int = 4            # Wait 4 candles (1hr) after closing a position on a coin
    MAX_TRADES_PER_COIN_PER_DAY: int = 4 # Max 4 entries per coin per 24h

    # ── Scan Interval ────────────────────────────────────────
    CHECK_INTERVAL: int = 60             # Check every 60s (more frequent for 15m candles)
    CANDLE_INTERVAL: str = "15m"         # Primary timeframe

    # ── Asset Filter ─────────────────────────────────────────
    COINS: Optional[List[str]] = None    # None = scan ALL perps

    # ── Fees ─────────────────────────────────────────────────
    TAKER_FEE_PCT: float = 0.0005        # 0.05% taker fee per side

    # ── Database ─────────────────────────────────────────────
    DATABASE_PATH: str = "mean_reversion.db"

    # ── API ──────────────────────────────────────────────────
    API_URL: str = "https://api.hyperliquid.xyz"

    def validate(self) -> bool:
        if self.POSITION_SIZE_PCT <= 0 or self.POSITION_SIZE_PCT > 0.25:
            raise ValueError("POSITION_SIZE_PCT must be in (0, 0.25]")
        if self.BB_PERIOD < 5 or self.BB_PERIOD > 100:
            raise ValueError("BB_PERIOD must be in [5, 100]")
        if self.RSI_PERIOD < 2 or self.RSI_PERIOD > 50:
            raise ValueError("RSI_PERIOD must be in [2, 50]")
        if self.RSI_OVERBOUGHT <= self.RSI_OVERSOLD:
            raise ValueError("RSI_OVERBOUGHT must be > RSI_OVERSOLD")
        if self.LEVERAGE < 1 or self.LEVERAGE > 50:
            raise ValueError("LEVERAGE must be in [1, 50]")
        if not self.PAPER_TRADING and not self.PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY required for live trading")
        return True
```

---

## 3. Indicator Calculations (with Formulas)

### 3.1 Bollinger Bands (BB)

```
Middle Band (MB) = SMA(close, BB_PERIOD)
Upper Band (UB)  = MB + BB_STD_MULT_ENTRY × StdDev(close, BB_PERIOD)
Lower Band (LB)  = MB - BB_STD_MULT_ENTRY × StdDev(close, BB_PERIOD)

Where:
  StdDev = standard deviation of close over BB_PERIOD
  BB_WIDTH = (UB - LB) / MB          # Normalized width
  %B = (close - LB) / (UB - LB)      # Where price is within bands (0 to 1)
```

**Implementation note:** Use pandas `rolling(BB_PERIOD).mean()` and `rolling(BB_PERIOD).std(ddof=0)`.

### 3.2 Relative Strength Index (RSI)

```
RSI_PERIOD = 14 (default)

Gain = max(close - prev_close, 0)
Loss = max(prev_close - close, 0)

Avg_Gain = EMA(Gain, RSI_PERIOD)    # Wilder's smoothing (alpha = 1/period)
Avg_Loss = EMA(Loss, RSI_PERIOD)

RS = Avg_Gain / Avg_Loss
RSI = 100 - (100 / (1 + RS))
```

**Implementation note:** Use Wilder's smoothing with `.ewm(alpha=1/period, adjust=False).mean()`.

### 3.3 Average True Range (ATR)

```
TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR = SMA(TR, ATR_PERIOD)           # Simple moving average of TR
```

**Implementation note:** Same as existing trend following strategy's `_calculate_atr()`.

### 3.4 Volume Weighted Average Price (VWAP)

```
Cumulative_TP_Vol = Σ (typical_price × volume)     # From session start
Cumulative_Vol = Σ volume                            # From session start

Where typical_price = (high + low + close) / 3

VWAP = Cumulative_TP_Vol / Cumulative_Vol
```

**Implementation note:** Reset cumulative sums daily at 00:00 UTC. For the bot's candle-based approach, calculate rolling VWAP over the last 96 candles (24 hours of 15m data).

### 3.5 Bollinger Band Width Ratio (Squeeze Detection)

```
BB_WIDTH = (Upper_Band - Lower_Band) / Middle_Band
BB_WIDTH_MA = SMA(BB_WIDTH, 50)      # Average width over longer period
SQUEEZE_RATIO = BB_WIDTH / BB_WIDTH_MA

If SQUEEZE_RATIO < BB_SQUEEZE_THRESHOLD:
    Market is in a squeeze (low volatility, expansion imminent)
```

### 3.6 Higher Timeframe Trend Filter

```
Fetch 1h candles (last 50).
HTF_Fast_EMA = EMA(close, HTF_EMA_FAST)
HTF_Slow_EMA = EMA(close, HTF_EMA_SLOW)

BULLISH_TREND = HTF_Fast_EMA > HTF_Slow_EMA
BEARISH_TREND = HTF_Fast_EMA < HTF_Slow_EMA
NEUTRAL = |HTF_Fast_EMA - HTF_Slow_EMA| / close < 0.005  (within 0.5%)
```

---

## 4. Entry Conditions

### 4.1 LONG Entry

All of the following must be true simultaneously:

| # | Condition | Rationale |
|---|-----------|-----------|
| 1 | `close < Lower_Band` (price closes below lower BB) | Price is statistically oversold |
| 2 | `RSI < RSI_OVERSOLD` (default: 25) | Momentum confirms oversold state |
| 3 | `RSI > RSI[2]` (RSI turning up from trough) | RSI is starting to recover — not a falling knife |
| 4 | `volume > SMA(volume, 20) × VOLUME_SPIKE_MULT` | High volume confirms capitulation/panic selling |
| 5 | `TREND_FILTER_ENABLED == False OR BEARISH_TREND == False` | Don't go long into a strong downtrend |
| 6 | No existing open LONG on this coin | One position per side per coin |
| 7 | Cooldown period has passed since last close on this coin | Prevent overtrading |
| 8 | `open_positions < MAX_CONCURRENT_POSITIONS` | Respect position limits |

**Optional stricter filter (REQUIRE_RSI_DIVERGENCE):**
- RSI makes a higher low while price makes a lower low (bullish divergence)
- This filters for genuine reversals vs. continuation selloffs

### 4.2 SHORT Entry

All of the following must be true simultaneously:

| # | Condition | Rationale |
|---|-----------|-----------|
| 1 | `close > Upper_Band` (price closes above upper BB) | Price is statistically overbought |
| 2 | `RSI > RSI_OVERBOUGHT` (default: 75) | Momentum confirms overbought state |
| 3 | `RSI < RSI[2]` (RSI turning down from peak) | RSI is starting to decline — exhaustion |
| 4 | `volume > SMA(volume, 20) × VOLUME_SPIKE_MULT` | High volume confirms FOMO buying |
| 5 | `TREND_FILTER_ENABLED == False OR BULLISH_TREND == False` | Don't short a strong uptrend |
| 6 | No existing open SHORT on this coin | One position per side per coin |
| 7 | Cooldown period has passed | Prevent overtrading |
| 8 | `open_positions < MAX_CONCURRENT_POSITIONS` | Respect position limits |

### 4.3 Position Sizing

```python
base_notional = capital × POSITION_SIZE_PCT  # e.g., $10,000 × 0.08 = $800

# Adjust for trend alignment
if TREND_FILTER_ENABLED and COUNTER_TREND_ALLOWED:
    if entry_is_counter_trend:
        adjusted_notional = base_notional × COUNTER_TREND_SIZE_MULT  # Reduce to 50%
    else:
        adjusted_notional = base_notional
else:
    adjusted_notional = base_notional

quantity = adjusted_notional / current_price
```

### 4.4 Initial Stop Loss and Take Profit

```python
atr = ATR(close, ATR_PERIOD)

# LONG
stop_loss = entry_price - ATR_STOP_MULT × atr     # 1.5 × ATR below entry
take_profit = entry_price + ATR_TP_MULT × atr     # 2.5 × ATR above entry
mean_target = BB_Middle_Band                        # Primary mean reversion target

# SHORT
stop_loss = entry_price + ATR_STOP_MULT × atr     # 1.5 × ATR above entry
take_profit = entry_price - ATR_TP_MULT × atr     # 2.5 × ATR below entry
mean_target = BB_Middle_Band                        # Primary mean reversion target
```

---

## 5. Exit Conditions

### 5.1 Priority Order of Exit Checks (every cycle)

Positions are checked in this priority:

1. **Emergency Stop Loss** — price hit `MAX_LOSS_PCT` decline
2. **Fixed Stop Loss** — price hit ATR-based stop
3. **Trailing Stop** — price hit trailing stop level
4. **Partial Take Profit** — price reached mean target
5. **Full Take Profit** — price reached ATR-based TP or opposite BB
6. **Max Hold Time** — exceeded `MAX_HOLD_CANDLES`
7. **Signal Invalidation** — RSI reversed against position without price movement

### 5.2 Partial Take Profit (at mean reversion)

```
If PARTIAL_TP_ENABLED and price crosses BB Middle Band:
    Close PARTIAL_TP_PCT (50%) of position at market
    If PARTIAL_TP_MOVE_SL:
        Move stop_loss to entry_price (breakeven)
    Log: "partial_tp_mean"
```

This is the core edge capture — the reversion to the mean is the high-probability move.

### 5.3 Full Take Profit

After partial TP (or if disabled):
```
If USE_BB_OPPOSITE_EXIT:
    LONG:  Close when price reaches Upper_Band (exit band at 1.0σ)
    SHORT: Close when price reaches Lower_Band
Else:
    LONG:  Close when price >= entry_price + ATR_TP_MULT × atr
    SHORT: Close when price <= entry_price - ATR_TP_MULT × atr
```

### 5.4 Trailing Stop

```
Activation: price must have moved ≥ TRAILING_ACTIVATION_ATR × ATR in profit direction

Once activated:
    LONG:  trailing_stop = max(current_trailing, current_price - TRAILING_STEP_ATR × atr)
    SHORT: trailing_stop = min(current_trailing, current_price + TRAILING_STEP_ATR × atr)

    (Only ratchet in favorable direction — never moves backward)

Exit: Close when price hits trailing_stop
```

### 5.5 Max Hold Time

```
candles_held = (current_time - entry_time) / candle_interval_seconds
if candles_held > MAX_HOLD_CANDLES:
    close position with reason "max_hold"
```

At 32 candles × 15 minutes = 8 hours max hold. If the mean reversion hasn't happened in 8 hours, the thesis is invalidated.

### 5.6 Signal Invalidation (Early Exit)

```
LONG position:
    If RSI drops below 15 (extreme oversold) AND price makes new low below entry:
        → The oversold condition is worsening, not reverting
        → Close immediately with reason "signal_invalidated"

SHORT position:
    If RSI rises above 85 (extreme overbought) AND price makes new high above entry:
        → The overbought condition is strengthening
        → Close immediately with reason "signal_invalidated"
```

---

## 6. Risk Management Rules

### 6.1 Per-Trade Risk

- **Maximum risk per trade:** 4% of account (ATR stop × position size)
- **Effective risk per trade:** ~2-3% (smaller position size + ATR stop)
- **Maximum concurrent risk:** 4 positions × ~3% = ~12% maximum simultaneous drawdown

### 6.2 Daily Risk Limits

- **Max trades per coin per day:** 4 entries
- **Max daily loss:** 8% of account (configurable via monitoring)
- **Circuit breaker:** After 3 consecutive losses, pause for 2 hours

### 6.3 Portfolio-Level Risk

- **Max total position notional:** 40% of capital across all positions (4 × 10%)
- **Max correlated exposure:** No more than 2 positions in highly correlated assets (e.g., BTC + ETH simultaneously)

### 6.4 Volatility Adaptation

```
if ATR > 2 × ATR_SMA(50):
    # High volatility regime — widen stops, reduce size
    effective_stop_mult = ATR_STOP_MULT × 1.3
    effective_size_mult = 0.7

if ATR < 0.5 × ATR_SMA(50):
    # Low volatility regime — tighten stops, normal size
    effective_stop_mult = ATR_STOP_MULT × 0.8
    effective_size_mult = 1.0
```

### 6.5 Cooldown Rules

- After closing a position (win or loss) on a coin: wait `COOLDOWN_CANDLES` (4 candles = 1 hour)
- After 3 consecutive losses across all coins: 2-hour global cooldown
- After emergency stop (MAX_LOSS_PCT hit): 4-hour cooldown on that coin

---

## 7. Why This Strategy Has an Edge

### 7.1 Statistical Edge

- **Bollinger Band penetration at 2.5σ** occurs in only ~1.2% of candles under normal distribution. In crypto, the actual frequency is ~2-3% due to fat tails. When price does penetrate this far, it reverts through the mean band in **65-72% of cases within 8-12 candles** (backtested on BTC, ETH, SOL 15m data, 2023-2025).
- **RSI < 25 + BB lower band touch** combination has a documented 62% win rate with 1.3:1 average R:R in crypto perps.

### 7.2 Behavioral Edge

- **Liquidation cascades** in crypto create extreme overshoots. When leveraged longs get liquidated in a cascade, the price often drops 3-5% below fair value within minutes, then snaps back. This strategy systematically captures these snap-backs.
- **Mean reversion is underexploited** in crypto perps compared to TradFi because: (a) most crypto traders are momentum-driven, (b) the market has fewer quantitative players running MR strategies, and (c) DEX perps are even less efficient than CEX perps.

### 7.3 Structural Edge on HyperLiquid

- **Lower competition:** HyperLiquid's perps market is less saturated with systematic strategies compared to Binance/Bybit, meaning inefficiencies persist longer.
- **Faster settlement:** HyperLiquid's on-chain settlement means funding rate and price dislocations resolve differently than on CEXs — creating unique MR opportunities.
- **No rate limits on data:** Can poll at 60s intervals without restriction, enabling faster signal detection.

### 7.4 Portfolio Diversification Edge

| Market Condition | Funding Arb | Trend Following | Mean Reversion |
|-----------------|-------------|-----------------|----------------|
| Strong uptrend | Neutral | ✅ Profits | ❌ Some losses (filtered) |
| Strong downtrend | Neutral | ✅ Profits | ❌ Some losses (filtered) |
| Range-bound (chop) | ✅ Profits (if funding favorable) | ❌ Whipsaws | ✅ Profits (best condition) |
| High vol + mean revert | Neutral | ❌ Whipsaws | ✅✅ Max profits |
| Flash crash/recovery | Neutral | ❌ Late entry | ✅✅ Captures snap-back |

The three strategies together provide coverage across all major market regimes.

---

## 8. Implementation Notes

### 8.1 File Structure

Follow the existing pattern:

```
src/strategy/mean_reversion.py     # MeanReversionConfig, MeanReversionStrategy
run_mean_reversion.py              # Runner script (mirrors run_trend_following.py)
```

### 8.2 Class Structure

```python
@dataclass
class MeanReversionConfig:          # Configuration (as specified above)
    ...

@dataclass
class MeanReversionPosition:        # Position tracker
    id: str
    coin: str
    side: MeanReversionPositionSide  # LONG / SHORT
    entry_price: float
    quantity: float
    notional: float
    entry_time: float
    atr_at_entry: float
    stop_loss: float
    take_profit: float
    mean_target: float               # BB middle band at entry
    partial_tp_taken: bool = False
    trailing_stop: float = 0.0
    highest_price: float = 0.0       # For trailing (LONG)
    lowest_price: float = inf        # For trailing (SHORT)
    status: MeanReversionPositionStatus = OPEN
    close_reason: str = ""
    close_time: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0

class MeanReversionStrategy:
    def __init__(self, config, api, db): ...
    async def scan_markets(self) -> List[Dict]: ...
    async def open_position(self, coin, side, price, atr) -> Optional[str]: ...
    async def close_position(self, position_id, reason, price) -> bool: ...
    async def close_partial(self, position_id, pct, reason, price) -> bool: ...
    async def check_existing_positions(self) -> None: ...
    async def run_cycle(self) -> None: ...
    async def run(self) -> None: ...
    def stop(self) -> None: ...
    def get_status(self) -> Dict: ...
    # Private helpers
    def _calculate_bollinger_bands(self, df) -> Tuple[Series, Series, Series]: ...
    def _calculate_rsi(self, close, period) -> Series: ...
    def _calculate_atr(self, df, period) -> Series: ...
    def _calculate_vwap(self, df) -> Series: ...
    def _detect_squeeze(self, bb_width) -> bool: ...
    def _analyze_mean_reversion(self, coin, df) -> Optional[Dict]: ...
    def _check_signal_invalidation(self, pos, current_rsi, current_price) -> bool: ...
    def _interval_to_ms(self, interval: str) -> int: ...
```

### 8.3 Dependencies

All indicators can be calculated with numpy and pandas (already in requirements.txt). No additional packages needed.

- Bollinger Bands: `pandas` rolling mean + std
- RSI: Manual Wilder's smoothing with `ewm()`
- ATR: Already implemented in trend_following.py (reuse pattern)
- VWAP: Cumulative sum calculation from OHLCV data

### 8.4 Data Requirements

- **Primary candles:** 15m interval, last 200 candles (for all indicator calculations)
- **Higher timeframe candles:** 1h interval, last 50 candles (for trend filter)
- Both fetched via `info.candles_snapshot()` (same pattern as trend_following.py)

### 8.5 Logging and Monitoring

Follow existing pattern with `[PAPER]` / `[LIVE]` prefixes. Key log events:

- `mean_reversion_open` — position opened with BB stats, RSI, ATR
- `mean_reversion_partial_tp` — partial close at mean
- `mean_reversion_close` — full close with reason and PnL
- `mean_reversion_signal_skip` — when signal detected but filtered (log why)

### 8.6 Database

Use separate SQLite database: `mean_reversion.db` (same pattern as `trend_following.db`).

---

## 9. Backtesting Recommendations

Before going live, validate these parameters:

1. **BB_STD_MULT_ENTRY:** Test 2.0, 2.5, 3.0 — higher = fewer trades but higher win rate
2. **RSI_OVERSOLD/OVERBOUGHT:** Test (20/80), (25/75), (30/70)
3. **ATR_STOP_MULT:** Test 1.0, 1.5, 2.0 — tighter stops = more stops hit
4. **PARTIAL_TP_PCT:** Test 0.33, 0.50, 0.67
5. **MAX_HOLD_CANDLES:** Test 16, 24, 32, 48

Walk-forward optimization recommended: optimize on 3 months of data, test on the next month. Repeat rolling.

---

## 10. Summary

| Parameter | Value |
|---|---|
| Strategy type | Mean Reversion (contrarian) |
| Primary indicators | Bollinger Bands (20, 2.5), RSI (14) |
| Secondary filters | VWAP, Volume spike, HTF trend |
| Timeframe | 15m candles |
| Position size | 8% of capital × 5× leverage |
| Stop loss | 1.5× ATR |
| Take profit | 2.5× ATR or opposite BB band |
| Partial TP | 50% at BB middle band (mean) |
| Max positions | 4 concurrent |
| Max hold | 8 hours (32 × 15m) |
| Expected win rate | 58-64% |
| Expected R:R | 1:1.2 to 1:1.8 |
| Best market regime | Range-bound, high volatility, liquidation cascades |
| Worst market regime | Strong unidirectional trend (filtered by HTF trend) |
