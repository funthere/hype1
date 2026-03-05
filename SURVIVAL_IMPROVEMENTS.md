# Survival Improvements for HYPE Trading Bot

## Current Risk Settings (TOO AGGRESSIVE)
```python
MAX_DAILY_LOSS_PCT = 0.15        # 15% daily loss - TOO HIGH!
RISK_PER_TRADE_PCT = 0.08        # 8% per trade
LEVERAGE = 5                      # 5x leverage
MAX_CONSECUTIVE_LOSSES = 3        # 3 losses triggers pause
```

## Recommended Survival Settings
```python
# More conservative for survival
MAX_DAILY_LOSS_PCT = 0.03        # 3% daily stop (survival-focused)
MAX_DRAWDOWN_PCT = 0.10          # 10% max drawdown shutdown
RISK_PER_TRADE_PCT = 0.02        # 2% per trade (1/4 of current)
LEVERAGE = 3                      # 3x leverage (safer)
MAX_CONSECUTIVE_LOSSES = 2        # Faster circuit breaker
MAX_POSITION Heat = 0.05          # 5% max per "setup"
```

## Priority Survival Improvements:

### 1. Lower Daily Loss Limit (CRITICAL)
Current: 15% → Recommended: 3%

A 15% daily loss means ~5 consecutive 8% risk trades = -40% account.
A 3% daily loss = survives much longer.

### 2. Position Heat Management
Don't add to positions in the same direction that are already losing.

### 3. Volatility-Adjusted Leverage
Reduce leverage when ATR expands (volatility spikes).

### 4. Time-Based Trading Restrictions
Avoid trading during:
- Low liquidity hours (weekends, holidays)
- Major news events (Fed announcements)
- First/last 30 min of trading day

### 5. Maximum Drawdown Circuit Breaker
Shut down entirely if account drops 10% from peak.

### 6. Tiered Risk Reduction
- Start at 2% risk per trade
- Reduce to 1% after 2 consecutive losses
- Reduce to 0.5% after daily loss > 2%

### 7. Correlation Limits
Don't open multiple positions in same asset class moving together.

### 8. Profit Taking Variance
Take partial profits at 1R, move stop to breakeven.
