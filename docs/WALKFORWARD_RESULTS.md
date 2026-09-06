# Walk-Forward Validation — First Results

> **CORRECTION (2026-09-05, later same day):** the trend-following rows
> below were measured before two defects were found — a nan fail-open in
> the ADX gate and an index-misalignment that made ADX nan at the newest
> candle inside the walk-forward adapter. With a functioning gate the
> trend numbers change materially; see `docs/PARAMETER_STUDY.md`, which
> supersedes this document's trend section. The momentum section is
> unaffected (momentum does not use ADX).

**Run date:** 2026-09-05 · **Data:** Hyperliquid, backfilled via `scripts/backfill_candles.py`
**Harness:** `src/analytics/walk_forward.py` · **Run commands:** `scripts/run_walk_forward.py`

These are the project's first out-of-sample numbers with conservative fill
simulation (stop fills before take-profit when a candle touches both) and
taker fees charged on both legs (0.05%).

## Momentum — HYPE 15m, default BotConfig parameters

Data covers 52.2 days (5,014 candles; the exchange caps 15m history).
Train 2,000 / test 1,000 candles per fold, $10k account.

| Fold | Trades | Win rate | Net PnL | Fees | Profit factor |
|---|---|---|---|---|---|
| 0 | 138 | 15.9% | −$319 | $276 | 0.46 |
| 1 | 137 | 13.9% | −$485 | $274 | 0.45 |
| 2 | 129 | 10.9% | −$418 | $258 | 0.44 |
| 3 | 3 | 0.0% | −$11 | $6 | 0.00 |
| **All** | **407** | — | **−$1,233** | **$814** | **0.44** |

**Verdict: no edge.** Negative in every fold with remarkable consistency
(PF 0.44–0.46), win rate 11–16% against a ~25% break-even. Fees explain
about two-thirds of the loss; the rest is gross directional losses. This
confirms `docs/archive/HYPE_REAL_RESULTS.md` with 3× the data. Do not
trade this configuration live; parameter search without a holdout cannot
rescue a 0.44 profit factor.

## Trend following — 1h, post pullback-entry fix

Train 1,200 / test 480 candles per fold, $10k account, flat 10% notional
sizing (no cross-coin volatility scaling in the harness).

| Coin | Trades | Net PnL | Profit factor | Fold PFs |
|---|---|---|---|---|
| HYPE | 20 | −$4.67 | 0.98 | 0.43 / 4.64 |
| BTC | 15 | −$15.19 | 0.84 | 0.44 / 15.40 |
| ETH | 28 | −$149.41 | 0.49 | 0.17 / 0.92 |
| SOL | 23 | +$76.59 | 1.40 | 0.54 / 3.04 |

**Verdict: inconclusive, break-even-ish.** Aggregates hover around 1.0,
but fold-level variance is enormous (SOL: −$58 then +$134). Sample sizes
(15–28 trades per coin) are below the scorecard's 30-trade confidence
floor. Unlike momentum, there is no consistent bleed — the pullback entry
fix removed the obvious loss machine. Keep paper-trading with the
scorecard running; do not promote.

## Caveats

- One coin per fold replay: trend's live volatility-adjusted sizing and
  trailing-stop exit ladder are simplified in the harness (documented in
  `src/strategy/trend_engine.py`).
- HYPE 15m history is capped by the exchange; the 1h comparisons span the
  full 90 days.
- Momentum's tight 0.4×ATR stop interacts with 15m noise; a wider stop is
  a hypothesis worth a *pre-registered* re-test, not a curve fit.

## Reproduce

```bash
python3 scripts/backfill_candles.py --coin HYPE --interval 15m --days 90
python3 scripts/run_walk_forward.py --csv data/HYPE_15m_90d.csv \
    --strategy momentum --train 2000 --test 1000
python3 scripts/backfill_candles.py --coin SOL --interval 1h --days 90
python3 scripts/run_walk_forward.py --csv data/SOL_1h_90d.csv \
    --strategy trend --train 1200 --test 480
```
