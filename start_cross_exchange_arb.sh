#!/bin/bash
# Start cross-exchange funding rate arb bot (HyperLiquid vs dYdX v4)
# Paper mode by default – add --live for mainnet

cd /home/ubuntu/hyperliquid-bot
source venv/bin/activate

BOT_PID=$(pgrep -f "run_cross_exchange_arb.py" 2>/dev/null)
if [ -n "$BOT_PID" ]; then
    echo "Bot already running (PID $BOT_PID), killing first..."
    kill $BOT_PID 2>/dev/null
    sleep 2
fi

MODE="${1:---paper}"
CONFIG="${2:-cross_exchange_arb_config.yaml}"

echo "Starting cross-exchange arb bot (mode=$MODE)..."
setsid python3 run_cross_exchange_arb.py \
    "$MODE" \
    --config "$CONFIG" \
    >> /tmp/cross_exchange_arb.log 2>&1 &

echo "Bot started with PID $!"
echo "Log: /tmp/cross_exchange_arb.log"
echo "Config: $CONFIG"
