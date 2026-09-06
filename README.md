# HYPE/USDC Automated Trading Bot

A modular Python trading bot for Hyperliquid DEX, with paper trading, testnet
operation, strategy analytics, SQLite persistence, and a Streamlit dashboard.

> **Current safety status:** mainnet execution is intentionally source-gated and
> fails closed. The project supports paper trading and supervised testnet
> validation while exchange-authoritative lifecycle and release controls are
> completed. Do not treat an order acknowledgement as a fill.

## Features

- Modular core, exchange, bot, storage, notification, and analytics layers
- Typed execution lifecycle with gateway contracts; order acknowledgement is
  never treated as a fill
- Paper trading with deterministic local fills
- Testnet support for bounded, supervised experiments, plus a runtime
  execution smoke check (`scripts/testnet_smoke.py`)
- SQLite persistence with execution lifecycle state and fill-idempotency ledger
- Circuit breaker, daily-loss, position-count, and notional risk controls
- Walk-forward validation harness, pre-registered parameter studies, and a
  strategy scorecard with an auto-retire policy
- Telegram notifications and Streamlit monitoring dashboard
- Recovery/reconciliation that treats unavailable exchange state as unknown,
  never as a flat account

## Strategy program

Strategies earn capital through evidence, not opinion:

| Strategy | Status | Evidence |
| --- | --- | --- |
| Trend following (pullback entry, wide ATR stop) | Live-forward paper | Positive aggregate on two disjoint 90-day windows; accumulating live-forward data |
| Funding-rate arbitrage | Live-forward paper | Direction-independent edge; entry path instrumented, paper capital persists across restarts |
| Momentum (HYPE 15m) | Retired | Profit factor 0.44–0.72 under every pre-registered configuration |
| Cross-exchange arbitrage | Frozen | Funding-sign defect fixed; both-leg execution unimplemented |

Full numbers and methodology: [docs/PARAMETER_STUDY.md](docs/PARAMETER_STUDY.md)
and [docs/WALKFORWARD_RESULTS.md](docs/WALKFORWARD_RESULTS.md).

## Quick start: paper mode

```bash
python -m pip install --require-hashes -r requirements.lock
```

```bash
make run-paper
```

Copy `.env.example` to `.env` for non-paper configuration. Never commit that
file or place credentials in source, tests, or logs.

## Modes

| Mode | Status | Purpose |
| --- | --- | --- |
| Paper | Supported | Local simulated fills using market data; no order API calls. |
| Testnet | Supervised | Bounded testnet validation with dedicated credentials. |
| Mainnet | Blocked | Every mainnet launcher fails closed until release approval. |

## Conservative risk policy

The enforced mainnet policy is deliberately restrictive:

- 0.5% intended loss at stop per trade; hard maximum 1%
- 2x leverage hard maximum
- 2% daily loss target; hard maximum 3%
- one concurrent position and five trades per day
- per-position notional limited to 20% of equity and an explicit USD cap

Position size is derived from the account-risk budget and **entry-to-stop
distance**. Leverage limits margin feasibility; it never multiplies loss at the
stop. Invalid or uncapped sizes are rejected.

## Evidence tooling

```bash
# Backfill candle history through the gateway seam
python3 scripts/backfill_candles.py --coin HYPE --interval 1h --days 90

# Walk-forward validation of a strategy over that history
python3 scripts/run_walk_forward.py --csv data/HYPE_1h_90d.csv \
    --strategy trend --train 1200 --test 480

# The pre-registered configuration study (all results disclosed)
python3 scripts/run_parameter_study.py

# Bounded execution smoke — self-test needs no credentials
python3 scripts/testnet_smoke.py --mode self-test
```

The live smoke runs testnet orders only via manual workflow dispatch against
the `protected-testnet` environment, and an `UNKNOWN` order outcome aborts
with exit 2 rather than retrying.

## Operational recovery

When a live/testnet process stops or reports uncertain execution state:

1. Stop new entries.
2. Preserve the database and logs.
3. Use the exchange’s positions, open orders, and fills as the authority.
4. Reconcile client and exchange order IDs before sending another order.
5. Do not create local P&L from a mid-price if the matching exchange fill is
   unavailable.

See [the incident runbook](docs/INCIDENT_RESPONSE.md) and
[security policy](SECURITY.md) for detailed procedures.

## Architecture

```text
src/
├── core/        # Configuration, risk policy, models, and strategy logic
├── execution/   # Typed lifecycle, gateway contracts, execution smoke check
├── exchange/    # Hyperliquid/Binance adapters and market-data feed
├── bot/         # Orchestration, reconciliation, and accounting
├── storage/     # SQLite persistence and idempotent execution-fill ledger
├── strategy/    # Trend following, arbitrage strategies, harness adapters
├── notifications/
└── analytics/   # Performance, scorecard, walk-forward harness, backfill
```

The lifecycle boundary distinguishes order submission from execution: a live
exit becomes a terminal trade only after a matching fill and a successful flat
exchange-position snapshot agree.

Strategies depend only on the typed `TradingGateway` and `MarketDataGateway`
protocols in `src/execution/`; the Hyperliquid SDK is imported exclusively
inside `src/exchange/` adapters (enforced by lint rule `TID251`).

## Testing

```bash
make lint
```

```bash
python -m pytest tests/ -m "not external" -v --cov=src --cov-branch
```

Tests are marked `unit`, `integration`, `contract`, or `external`. External
smoke checks require a protected dedicated testnet account and never run in
normal pull-request CI.

## Development and contribution

- [CONTRIBUTING.md](CONTRIBUTING.md) documents the test, migration, and
  execution-change requirements.
- [SECURITY.md](SECURITY.md) explains secret hygiene and vulnerability response.
- `requirements.in` is the reviewed dependency manifest. Generate a
  hash-pinned `requirements.lock` before a release using `pip-compile`.
- See `CLAUDE.md` for repository architecture and commands.

## Disclaimer

This repository is educational and experimental. Trading can lose all deployed
capital. Test strategies in paper mode and supervised testnet environments; do
not enable mainnet operation without an independently reviewed release.
