# Pre-Registered Parameter Study — 2026-09-05

**Data:** Hyperliquid, 90 days 1h (HYPE/BTC/ETH/SOL) and 52 days 15m (HYPE).
**Harness:** `WalkForwardValidator`, conservative fills (stop fills before
take-profit), 0.05% taker fee per side, $10k account.
**Script:** `scripts/run_parameter_study.py` — every configuration below was
committed before its results were computed, and all results are disclosed.

## What the study found first: two real defects

The study's original trend rows were **identical for every configuration**
— the pre-registered ADX change had no effect. Chasing that anomaly
uncovered a chain of two bugs:

1. **The ADX regime gate failed open on nan.** `nan < threshold` is `False`
   in float comparison, so any candle with unmeasurable ADX passed the
   "trend too weak" filter. Control: with `ADX_THRESHOLD=100` (impossible),
   the strategy still traded 20 times.
2. **ADX was nan at ~97% of signal moments in the harness** because
   `_calculate_adx` built its DM series with a fresh `RangeIndex`; on any
   frame whose labels were not 0-based (the adapter's trimmed window), the
   division union-aligned and produced nan at exactly the newest candle.
   The live strategy path was unaffected (0-based frames), but the harness
   had never once evaluated a real ADX value.

Both are fixed with fail-closed guards (nan ADX/ATR/RSI now block the
signal) and index-safe indicator construction, each pinned by regression
tests. The ADX=100 control now yields 0 trades. **Consequence: the trend
numbers previously published in `WALKFORWARD_RESULTS.md` were measured
without a functioning ADX gate and are superseded by this document.**

## Momentum — HYPE 15m (all four configurations lose)

| Config | Change | Trades | Net PnL | PF |
|---|---|---|---|---|
| M0 baseline | defaults | 407 | −$1,233 | 0.44 |
| M1 wide stop | SL 1.5x / TP 3.0x ATR | 176 | −$620 | 0.72 |
| M2 conservative | confidence 70, SL 0.8 | 330 | −$959 | 0.63 |
| M3 slower momentum | ROC_LONG 5 → 20 | 412 | −$1,163 | 0.48 |

**Verdict: closed.** Wider stops cut the bleed roughly in half (fewer
noise stop-outs) but no pre-registered variant reaches break-even. The
momentum strategy has no edge on HYPE 15m under any documented hypothesis;
retiring it from consideration rather than tuning further.

## Trend following — 1h, four coins, with a functioning regime gate

| Config | Change | Coin | Trades | Net PnL | PF |
|---|---|---|---|---|---|
| T0 baseline | defaults | HYPE | 19 | −$111 | 0.65 |
| T0 | | BTC | 10 | +$14 | 1.26 |
| T0 | | ETH | 22 | −$162 | 0.38 |
| T0 | | SOL | 16 | +$124 | 1.97 |
| T1 stronger regime | ADX 30 → 40 | HYPE | 11 | −$4 | 0.98 |
| T1 | | BTC | 8 | −$30 | 0.49 |
| T1 | | ETH | 15 | −$108 | 0.40 |
| T1 | | SOL | 11 | +$129 | 2.71 |
| T2 wide ATR stop | SL 3.0 / trail 3.5 | HYPE | 13 | +$65 | 1.29 |
| T2 | | BTC | 9 | +$8 | 1.14 |
| T2 | | ETH | 16 | −$50 | 0.73 |
| T2 | | SOL | 14 | +$107 | 1.73 |

**Verdict: T2 is the only surviving hypothesis — and it is not yet
evidence.** With the gate working, the baseline bleeds on 2 of 4 coins;
tightening ADX alone does not help systematically. T2 (wide ATR stop,
3.0x, with 3.5x trailing) is positive on 3 of 4 coins with shallow ETH
losses — consistent with the original noise-stop diagnosis — but with
9–16 trades per coin it is far below the scorecard's 30-trade confidence
floor, and the multiplicity of the grid (three configs) means the best
row is the most likely to be luck. T2 advances to a fresh out-of-sample
window (new data, same parameters, pre-committed); nothing is promoted.

## Prior-window re-test (2026-09-05, same day)

The study window (most recent 90 days) was used to select T2, so it cannot
also confirm it. The **prior 90 days** (roughly 2026-03 → 2026-06, the
first half of the 180-day backfill) were never touched by any config
choice, making them a legitimate pre-committed out-of-sample window.

**Decision rule, stated before running:** T2 survives if its aggregate
profit factor across the four coins is ≥ 1.0 on the prior window. Fold
geometry, fees, and capital identical to the study; parameters frozen at
T2's committed values (ATR stop 3.0x, trailing 3.5x); no re-tuning.

| Config | Trades | Net PnL | Aggregate PF | Per-coin PF |
|---|---|---|---|---|
| T0 baseline | 80 | +$320 | 1.38 | 1.01 / 1.99 / 1.59 / 1.62 |
| **T2 wide stop** | **69** | **+$363** | **1.44** | 1.53 / 1.71 / 0.99 / 1.66 |

**Result: T2 survives its criterion.** Combined with the study window,
T2 is the only configuration in the grid that is aggregate-positive on
**both** windows (T0 is −$135 on the study window): 121 trades total,
positive net on each window independently, and its one study-window
loser (ETH) is flat rather than bad here.

**Honest reading, three caveats:**

1. The prior window was simply more trend-friendly — even the baseline is
   profitable on it. The window confirms "no harm", not "wide stop adds
   edge"; T2 vs T0 within the prior window is roughly a tie (+$363 vs
   +$320).
2. Per-coin trade counts (28–35 across both windows) only now reach the
   scorecard's 30-trade confidence floor at the portfolio level, not per
   coin.
3. Same-day selection and test share one instrument universe and data
   vendor; a live-forward paper period remains the real test.

**Disposition: T2 earns live-forward paper trading** — run the trend
runner with `ATR_STOP_MULT=3.0, TRAILING_STOP_MULT=3.5` under the
scorecard, alongside the funding-arb paper experiment. Mainnet remains
gated; nothing here changes that.

## Reproduce

```bash
python3 scripts/backfill_candles.py --coin HYPE --interval 15m --days 90
python3 scripts/backfill_candles.py --coin BTC --interval 1h --days 90  # + ETH, SOL, HYPE 1h
python3 scripts/run_parameter_study.py
# prior-window re-test: backfill 180d, slice candles earlier than the
# study window's first timestamp, run T2 overrides through the validator
```
