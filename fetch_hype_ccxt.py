"""
Fetch 90 days of HYPE/USDC data from Hyperliquid using ccxt
"""
import ccxt
import pandas as pd
from datetime import datetime, timedelta
import time


def fetch_hyperliquid_hype_5m(days=90):
    """
    Fetch 90 days of 5-minute HYPE/USDC data from Hyperliquid

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    exchange = ccxt.hyperliquid({
        'enableRateLimit': True,
    })

    # Load markets
    print("Loading Hyperliquid markets...")
    markets = exchange.load_markets()

    # HYPE/USDC perpetual
    symbol = 'HYPE/USDC'

    if symbol not in markets:
        print(f"Symbol {symbol} not found!")
        print("Available HYPE markets:")
        for m in markets:
            if 'HYPE' in m:
                print(f"  - {m}")
        raise ValueError(f"{symbol} not available")

    print(f"Fetching {days} days of 5m data for {symbol}...")

    # Calculate time range
    end_time = exchange.milliseconds()
    start_time = end_time - (days * 24 * 60 * 60 * 1000)

    all_candles = []
    current_time = start_time
    batch_num = 0

    # Hyperliquid/ccxt limit is 1000 candles per request
    # 5m candles: 1000 * 5 min = 5000 min = ~3.5 days per batch
    candles_per_batch = 1000
    ms_per_5m = 5 * 60 * 1000

    while current_time < end_time:
        batch_num += 1
        batch_end = min(current_time + (candles_per_batch * ms_per_5m), end_time)

        print(f"  Batch {batch_num}: {datetime.fromtimestamp(current_time/1000).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(batch_end/1000).strftime('%Y-%m-%d')}")

        try:
            ohlcv = exchange.fetch_ohlcv(
                symbol,
                '5m',
                since=current_time,
                limit=candles_per_batch
            )

            if ohlcv:
                all_candles.extend(ohlcv)
                print(f"    Fetched {len(ohlcv)} candles")

                # Move to next batch
                last_time = ohlcv[-1][0]
                current_time = last_time + ms_per_5m
            else:
                print(f"    No data returned")
                break

        except Exception as e:
            print(f"    Error: {e}")
            break

        # Rate limiting
        time.sleep(0.2)

    if not all_candles:
        raise ValueError("No candles retrieved")

    # Create DataFrame
    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    # Convert timestamp
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    # Remove duplicates and sort
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)

    # Filter to date range
    df = df[df['timestamp'] >= pd.to_datetime(start_time, unit='ms')]

    print(f"\nSuccessfully fetched {len(df)} candles")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Price range: ${df['close'].min():.4f} - ${df['close'].max():.4f}")

    return df


if __name__ == "__main__":
    df = fetch_hyperliquid_hype_5m(days=90)

    # Save to CSV
    df.to_csv('hyperliquid_hype_5m_90d.csv', index=False)
    print(f"\nData saved to hyperliquid_hype_5m_90d.csv")
    print(f"\nFirst 5 rows:")
    print(df.head())
    print(f"\nStats:")
    print(df.describe())
