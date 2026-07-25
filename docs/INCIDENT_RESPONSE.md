# Trading Incident Response

## 1. Stop new exposure

- Stop the relevant process or trigger its configured emergency control.
- Do **not** restart it until confirmed exchange positions and open orders are
  recorded.
- Do not assume a local `closed` status means an exchange position is flat.

## 2. Preserve evidence

Copy the SQLite database, application logs, configuration **without secrets**,
and the relevant exchange order/fill history. Record timestamps in UTC. Do not
run cleanup tooling against the evidence copy.

## 3. Establish authoritative exposure

Use the exchange's account position, open order, and fill history views. Match
client order IDs and exchange order IDs. If account reads fail, treat exposure
as unknown; do not submit duplicate exits merely because local state is stale.

## 4. Contain and recover

Cancel stale entry orders. For confirmed open exposure, submit a deliberately
reviewed reduce-only exit and verify fills plus a flat position snapshot. For a
partially filled exit, retain and manage only the confirmed residual quantity.
If fills are missing, retain an unresolved state rather than synthesizing P&L.

## 5. Credential and communications response

Rotate compromised credentials following [SECURITY.md](../SECURITY.md). Notify
the designated operator of confirmed exposure, unknown exposure, orders
submitted, and the recovery decision. Complete a retrospective before restoring
any automation.
