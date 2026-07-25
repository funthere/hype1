# Contributing

## Safety first

This is execution software. Do not run mainnet code, use real credentials, or
modify a live database while developing. Paper and dedicated testnet accounts
are the only permitted test targets.

## Setup

```bash
python -m pip install --require-hashes -r requirements.lock
```

`requirements.in` lists reviewed direct dependencies. Before a mainnet release,
generate and commit a hash-pinned `requirements.lock` using `pip-compile`; CI
checks that the lock workflow exists, and release builds must install the
verified lock.

## Required checks

```bash
make lint
```

```bash
python -m pytest tests/ -m "not external" -v --cov=src --cov-branch
```

Use markers as follows:

- `unit`: no I/O;
- `integration`: deterministic SQLite and scripted-gateway behavior;
- `contract`: adapter request/response normalization;
- `external`: protected manual testnet smoke checks only.

## Execution changes

Any change affecting order submission, positions, reconciliation, persistence,
or risk must include tests for rejection, ambiguous write, partial fill, failed
snapshot, restart/recovery, and duplicate/idempotent invocation where relevant.
No local P&L or terminal trade may be recorded from an order acknowledgement.

Schema changes must be idempotent, upgrade-tested against the previous schema,
and preserve historical trade rows. Review migrations separately from strategy
changes.
