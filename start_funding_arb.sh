#!/bin/bash
# Start funding arb bot detached from any parent process
# Uses setsid to create new session so gateway restart won't kill it

cd /home/ubuntu/hyperliquid-bot
source venv/bin/activate

BOT_PID=$(pgrep -f "run_funding_arb.py" 2>/dev/null)
if [ -n "$BOT_PID" ]; then
    echo "Bot already running (PID $BOT_PID), killing first..."
    kill $BOT_PID 2>/dev/null
    sleep 2
fi

echo "Starting funding arb bot..."
setsid python3 run_funding_arb.py \
    --paper \
    --capital 1000 \
    --coins BTC,ETH,SOL,HYPE,VIRTUAL,PEPE,FARTCOIN,PUMP,BIO,SUI,AVAX \
    --interval 60 \
    >> /tmp/funding_arb_bot.log 2>&1 &

echo "Bot started with PID $!"
echo "Log: /tmp/funding_arb_bot.log"
