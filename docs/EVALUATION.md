# 📊 Hyperliquid Bot Strategy Evaluation — Why We're Losing

## Executive Summary

**Total simulated loss: -$163.29 across 3 bots.**

| Bot | Trades | Net PnL | Win Rate | Root Cause |
|---|---|---|---|---|
| Trend Following | 32 | **-$99.49** | 6.3% | Multiple fundamental flaws |
| Cross Exchange Arb | 22 | **-$44.06** | N/A | Funding doesn't cover fees |
| Funding Arb | 0 | $0.00 | N/A | Bug: never enters trades |

---

## 🔴 Bot 1: Trend Following — -$99.49

### Problem 1: Trend Reversal Exit = Guaranteed Loss Machine
**Impact: CRITICAL — causes 84% of all losses ($204.89 out of $244 total)**

27 out of 32 trades exited via `trend_reversal`. **Zero wins** from this exit type.

**Root cause:** The `_check_trend_reversal()` method uses EMA crossover (fast < slow for LONG, fast > slow for SHORT) as exit signal. This is fundamentally flawed:
- EMA crossover is a **lagging indicator** — by the time it fires, the adverse move already happened
- It triggers after just `MIN_HOLD_BEFORE_REVERSAL_HOURS = 3.0` hours
- In crypto's high volatility, a 3-hour hold with no profit lock = guaranteed loss on exit
- The exit happens at market price with no limit — slippage adds to loss

**Evidence:** Average loss per trend_reversal exit: `$7.59`. Average hold time for losers: 0.5-4 hours. These are essentially random entries with forced exits.

### Problem 2: Entry on Crossover = Buying Tops / Selling Bottoms
**Impact: HIGH**

The entry requires `prev_fast <= prev_slow` crossing to `current_fast > current_slow` (for SHORT). This means:
- The crossover has ALREADY happened — you're entering AFTER the move started
- In crypto, EMA crossovers often happen at reversal points (whipsaw)
- The pullback filter (`PULLBACK_ATR_MULT = 0.5`) is too tight — most entries are right at the crossover, not at a meaningful pullback

### Problem 3: SHORT_ONLY Mode with 7.7% Win Rate
**Impact: HIGH**

`SHORT_ONLY: bool = True` was set because LONG had 0% win rate in backtest. But SHORT only has 7.7% win rate — also terrible. The strategy itself is broken, not the direction.

### Problem 4: Position Sizing Ignores Volatility
**Impact: MEDIUM**

Positions are sized at fixed % of capital, not adjusted for ATR. A 1% move on VVV (high volatility) hits harder than 1% on BTC (low volatility), but sizing is the same.

### Problem 5: Trailing Stop Never Triggers (Wrong Direction)
**Impact: MEDIUM**

Trailing stop only protects profits. Since 94% of trades are losers, trailing stops never activate. The fixed stop loss (`ATR_STOP_MULT = 2.0`) is the actual exit — but trend_reversal fires before it in most cases.

### Problem 6: VVV and RUNE on Blacklist But Still Traded
**Impact: LOW (already fixed in code, but data shows old trades)**

Blacklist has `("VVV", "RUNE")` but trades show VVV and RUNE losses. These were likely before the blacklist was added.

---

## 🟠 Bot 2: Cross Exchange Arb — -$44.06

### Problem 1: Funding Collected is NEGATIVE
**Impact: CRITICAL**

`total_funding_collected: $-4.11` — the bot is PAYING funding, not collecting it.

**Root cause:** The spread calculation or direction logic is inverted. If HL rate > Binance rate, the bot should SHORT HL (collect positive funding from longs) and LONG Binance. But the net funding is negative, meaning positions are on the wrong side or funding accumulation has a sign error.

### Problem 2: Price Divergence on Close Destroys PnL
**Impact: HIGH**

On close, `dydx_price` defaults to `hl_price` as proxy. But even small divergences between HL and Binance prices on close can wipe out hours of funding collection. With `entry_threshold: 0.001` (0.1%/hr) and fees at ~0.09%, the edge is only ~0.01%/hr — any price slip eats it.

### Problem 3: Too Many Small/Illiquid Coins
**Impact: MEDIUM**

Coins like TST, TRUMP, W, ZEN have thin orderbooks and wide spreads. Price impact on entry/exit is significant. Should stick to majors (BTC, ETH, SOL) where spreads are tightest.

### Problem 4: 5 Max Positions Dilutes Capital
**Impact: MEDIUM**

`max_concurrent_positions: 5` with $10k capital = $2k per position pair. After fees, each position needs significant funding time to be profitable.

---

## 🟡 Bot 3: Funding Arb — $0.00 (0 trades)

### Problem 1: Scan Runs But Never Enters
**Impact: CRITICAL**

PURR has 0.043%/8h funding rate, well above the 0.01%/8h threshold. The bot SHOULD be entering. But 0 trades in the database.

**Suspected cause:** The bot process shows 0% CPU and has been running since May 30. It may be stuck in a rate-limit retry loop (`30s, 60s` waits on 429 errors). The scan interval is 600s (10 min) and 429 retries add 30-90s delays.

**Other possibility:** The `_get_available_capital()` returns 0 or negative after deducting opening fees, preventing new entries.

### Problem 2: CLI Threshold Overrides Config
**Impact: LOW**

`--entry-threshold 0.0001` overrides the default `0.0003`. This is actually fine (more permissive = more entries). The issue is elsewhere.

---

## 📋 Proposed Fixes

### Trend Following (Priority: HIGH)
1. **Remove trend_reversal exit entirely** — it's the #1 loss cause and adds no edge
2. **Replace with time-based trailing stop** — after N hours, tighten trailing stop progressively
3. **Add mean-reversion filter** — don't enter if RSI > 70 (SHORT) or RSI < 30 (LONG)
4. **Increase MIN_HOLD_BEFORE_REVERSAL_HOURS to 12+** — or remove it
5. **Add RSI/MACD confirmation** — EMA crossover alone is too noisy
6. **Volatility-adjusted position sizing** — size inversely proportional to ATR
7. **Consider removing SHORT_ONLY** — or add a regime filter (trend vs mean-reversion market detection)

### Cross Exchange Arb (Priority: MEDIUM)
1. **Fix funding accumulation sign** — debug why `total_funding_collected` is negative
2. **Reduce coins to BTC, ETH, SOL only** — better liquidity, tighter spreads
3. **Increase entry threshold to 0.002** (0.2%/hr) — need bigger edge to survive fees
4. **Add price divergence check on close** — don't close if HL vs Binance spread is too wide
5. **Reduce max positions to 2-3** — concentrate capital

### Funding Arb (Priority: HIGH)
1. **Debug why no entries despite valid rates** — add logging to `open_position()`
2. **Check `_paper_capital`** — may be negative after repeated fee deductions with no position closes
3. **Reset paper capital on restart** — or persist it properly
4. **Add max_concurrent_positions check logging** — verify it's not silently blocking

---

## 💡 Strategic Insight

The fundamental issue across all 3 bots: **the strategies were designed for trending/moving markets but crypto in May 2026 is range-bound/volatile with frequent whipsaws.** No amount of parameter tuning will fix a strategy that's fundamentally mismatched to the market regime.

**Recommendation:** Before implementing fixes, add a market regime detector (trending vs ranging) and only trade when the regime matches the strategy. This alone could prevent 50%+ of losing trades.
