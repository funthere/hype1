#!/usr/bin/env python3
"""Quick funding rate arb scanner — HL vs Binance."""

import requests

HL_URL = "https://api.hyperliquid.xyz/info"
BN_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def main():
    # HL meta + funding
    hl = requests.post(HL_URL, json={"type": "metaAndAssetCtxs"}, timeout=15).json()
    universe = hl[0]["universe"]
    ctxs = hl[1]

    # Binance funding
    bn = requests.get(BN_URL, params={"limit": 100}, timeout=15).json()

    bn_rates = {}
    for e in bn:
        coin = e["symbol"].replace("USDT", "")
        bn_rates[coin] = float(e["fundingRate"]) / 8  # per hour

    hl_rates = {}
    for i, ctx in enumerate(ctxs):
        name = universe[i]["name"]
        hl_rates[name] = float(ctx.get("funding", "0")) / 8

    # Cross-exchange arbs
    arbs = []
    for coin, hr in hl_rates.items():
        br = bn_rates.get(coin)
        if br is None:
            continue
        spread = hr - br
        if abs(spread) > 0.000001:
            annual = abs(spread) * 24 * 365 * 100
            arbs.append((coin, hr, br, spread, annual))

    arbs.sort(key=lambda x: abs(x[3]), reverse=True)

    print("=== TOP 20 CROSS-EXCHANGE ARBS (HL vs Binance) ===")
    print(
        f"{'Coin':<10} {'HL%/hr':>10} {'BN%/hr':>10} {'Spread%/hr':>12} {'Direction':<25} {'Annual%':>8}"
    )
    print("-" * 85)
    for coin, hr, br, sp, ann in arbs[:20]:
        d = "SHORT HL/LONG BN" if sp > 0 else "LONG HL/SHORT BN"
        print(
            f"{coin:<10} {hr * 100:>9.5f}% {br * 100:>9.5f}% {sp * 100:>11.6f}% {d:<25} {ann:>7.1f}%"
        )


if __name__ == "__main__":
    main()
