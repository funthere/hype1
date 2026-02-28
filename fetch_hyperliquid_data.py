"""
Fetch real historical data from Hyperliquid.xyz for HYPE/USDC
"""
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time


def get_market_info():
    """Get all available markets from Hyperliquid"""
    url = "https://api.hyperliquid.xyz/info"
    response = requests.post(url, json={}, headers={'Content-Type': 'application/json'})
    data = response.json()

    if 'universe' in data:
        return data['universe']
    return []


def fetch_hyperliquid_candles(symbol='HYPE', interval='5m', days=30):
    """
    Fetch OHLCV candle data from Hyperliquid.xyz using their API

    Args:
        symbol: Trading pair (default: HYPE)
        interval: Candle interval ('1m', '5m', '15m', '1h', '4h', '1d')
        days: Number of days of history to fetch

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    # First, get available markets to find the correct symbol
    print("Fetching available markets...")
    markets = get_market_info()

    # Try to find matching symbol
    matching_symbols = [m for m in markets if symbol.upper() in m.upper()]

    if not matching_symbols:
        print(f"Symbol '{symbol}' not found. Available symbols containing '{symbol}':")
        for m in markets[:50]:
            if symbol.lower() in m.lower():
                print(f"  - {m}")
        if not matching_symbols:
            print(f"\nFirst 20 available markets:")
            for i, m in enumerate(markets[:20]):
                print(f"  {i+1}. {m}")
        raise ValueError(f"Symbol '{symbol}' not found in markets")

    # Use the first matching symbol
    actual_symbol = matching_symbols[0]
    print(f"Using symbol: {actual_symbol}")

    # Hyperliquid uses POST request for candles
    base_url = "https://api.hyperliquid.xyz/candles"

    # Convert interval
    interval_map = {
        '1m': '1',
        '5m': '5',
        '15m': '15',
        '1h': '60',
        '4h': '240',
        '1d': '1440'
    }

    hl_interval = interval_map.get(interval, '5')

    # Calculate time range
    end_time = int(time.time())
    start_time = end_time - (days * 24 * 60 * 60)

    all_candles = []

    print(f"Fetching {days} days of {actual_symbol} {interval} data...")

    # Hyperliquid API format
    req = {
        "coin": actual_symbol,
        "interval": hl_interval,
        "startTime": start_time,
        "endTime": end_time
    }

    try:
        response = requests.post(base_url, json=req, headers={'Content-Type': 'application/json'}, timeout=30)
        response.raise_for_status()

        data = response.json()

        # Hyperliquid returns array of arrays
        if data and isinstance(data, list):
            for candle in data:
                # Format: [time_ms, open, high, low, close, volume, ...]
                if len(candle) >= 6:
                    all_candles.append({
                        'timestamp': int(candle[0]),
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': float(candle[5])
                    })

            print(f"Fetched {len(all_candles)} candles")
        else:
            print(f"Unexpected response format: {data[:200] if data else 'empty'}")

    except Exception as e:
        print(f"Error fetching candles: {e}")

    if not all_candles:
        raise ValueError(f"No candles retrieved for {actual_symbol}")

    # Create DataFrame
    df = pd.DataFrame(all_candles)

    # Convert timestamp to datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

    # Remove duplicates and sort
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)

    print(f"\nSuccessfully fetched {len(df)} candles")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Price range: ${df['close'].min():.4f} - ${df['close'].max():.4f}")
    print(f"Total volume: ${df['volume'].sum():,.0f}")

    return df, actual_symbol


if __name__ == "__main__":
    # Test fetching
    try:
        df, actual_symbol = fetch_hyperliquid_candles(symbol='HYPE', interval='5m', days=90)
        filename = f'hyperliquid_{actual_symbol}_5m.csv'
        df.to_csv(filename, index=False)
        print(f"\nData saved to {filename}")
        print("\nFirst 5 rows:")
        print(df.head())
        print("\nData stats:")
        print(df.describe())
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

