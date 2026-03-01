#!/usr/bin/env python3
"""
Force Close All Positions Script for Hyperliquid Trading Bot

This script can be run to immediately close all open positions.
It works by:
1. Creating the control file (.force_close_positions) that the bot checks
2. Optionally sending a SIGUSR1 signal to the running bot process

Usage:
    python3 force_close_positions.py [--signal]
    ./force_close_positions.py [--signal]

    --signal    Also send SIGUSR1 to the bot process (faster)
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def create_control_file():
    """Create the .force_close_positions control file"""
    control_file = Path(".force_close_positions")
    control_file.touch()
    print(f"✓ Created control file: {control_file.absolute()}")
    print("  Bot will close all positions on next loop iteration")
    return True


def send_signal_to_bot(bot_name_pattern="hype"):
    """Send SIGUSR1 signal to running bot process"""
    try:
        # Find bot process
        result = subprocess.run(
            ["pgrep", "-f", bot_name_pattern],
            capture_output=True,
            text=True
        )

        pids = result.stdout.strip().split('\n') if result.stdout.strip() else []

        if not pids or not pids[0]:
            print(f"⚠️  No running bot process found matching '{bot_name_pattern}'")
            return False

        for pid in pids:
            if pid:
                subprocess.run(["kill", "-USR1", pid])
                print(f"✓ Sent SIGUSR1 signal to bot (PID: {pid})")
        return True

    except FileNotFoundError:
        print("⚠️  Could not send signal (pgrep/kill not available)")
        return False
    except Exception as e:
        print(f"⚠️  Error sending signal: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Force close all positions in Hyperliquid trading bot"
    )
    parser.add_argument(
        "--signal",
        action="store_true",
        help="Also send SIGUSR1 signal to bot (faster response)"
    )
    parser.add_argument(
        "--bot-pattern",
        default="hype",
        help="Process name pattern to find the bot (default: 'hype')"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("FORCE CLOSE ALL POSITIONS")
    print("=" * 60)

    # Create control file
    created = create_control_file()

    if created and args.signal:
        print()
        send_signal_to_bot(args.bot_pattern)

    print()
    print("Monitor logs to confirm:")
    print("  - Paper trading: tail -f hype_paper_bot.log")
    print("  - Testnet:       tail -f hype_testnet_bot.log")
    print("  - Mainnet:       tail -f hype_bot.log")


if __name__ == "__main__":
    main()
