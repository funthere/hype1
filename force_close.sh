#!/bin/bash
# Force close all positions in the Hyperliquid trading bot
#
# Usage:
#   ./force_close.sh [bot_pattern]
#   ./force_close.sh --reset-circuit-breaker [bot_pattern]
#
# Examples:
#   ./force_close.sh                    # Auto-detect and close all positions
#   ./force_close.sh --reset-circuit-breaker  # Reset circuit breaker
#   ./force_close.sh --signal           # Send SIGUSR1 for faster response

BOT_PATTERN="${2:-hype}"
ACTION="${1:-}"

if [[ "$ACTION" == "--reset-circuit-breaker" ]]; then
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         RESET CIRCUIT BREAKER                                ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    # Create reset control file
    touch .reset_circuit_breaker
    echo "✓ Created .reset_circuit_breaker control file"
    echo "  Bot will reset circuit breaker on next loop iteration"
    echo ""

    # Also send signal for faster response
    BOT_PID=$(pgrep -f "$BOT_PATTERN" | head -1)
    if [ -n "$BOT_PID" ]; then
        kill -USR2 "$BOT_PID"
        echo "✓ Sent SIGUSR2 to bot (PID: $BOT_PID)"
        echo "  This should trigger immediate circuit breaker reset"
    else
        echo "⚠️  No running bot found matching: $BOT_PATTERN"
    fi
    echo ""

    echo "Monitor logs to confirm:"
    echo "  Paper trading: tail -f hype_paper_bot.log"
    echo "  Testnet:       tail -f hype_testnet_bot.log"
    echo "  Mainnet:       tail -f hype_bot.log"
    exit 0
fi

USE_SIGNAL=false
if [[ "$ACTION" == "--signal" ]]; then
    USE_SIGNAL=true
    BOT_PATTERN="${2:-hype}"
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         FORCE CLOSE ALL POSITIONS                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Method 1: Create control file (works with all bots)
touch .force_close_positions
echo "✓ Created .force_close_positions control file"
echo "  Bot will close all positions on next loop iteration"
echo ""

# Method 2: Send signal for faster response
if [[ "$USE_SIGNAL" == "true" ]]; then
    BOT_PID=$(pgrep -f "$BOT_PATTERN" | head -1)
    if [ -n "$BOT_PID" ]; then
        kill -USR1 "$BOT_PID"
        echo "✓ Sent SIGUSR1 to bot (PID: $BOT_PID)"
        echo "  This should trigger immediate position close"
    else
        echo "⚠️  No running bot found matching: $BOT_PATTERN"
    fi
    echo ""
fi

echo "Monitor logs to confirm:"
echo "  Paper trading: tail -f hype_paper_bot.log"
echo "  Testnet:       tail -f hype_testnet_bot.log"
echo "  Mainnet:       tail -f hype_bot.log"
