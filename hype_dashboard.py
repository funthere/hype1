"""
HYPE Trading Bot Web Dashboard

A Streamlit-based web dashboard for monitoring and controlling
the HYPE/USDC trading bot.

Usage:
    streamlit run hype_dashboard.py

The dashboard connects to the bot's API server (default: http://127.0.0.1:8000)
"""

import os
import time
from typing import List

import streamlit as st
import requests
import plotly.graph_objects as go
import pandas as pd

# Configuration
API_BASE_URL = os.environ.get("HYPE_BOT_API_URL", "http://127.0.0.1:8000")
REFRESH_INTERVAL = 0.5  # seconds (faster refresh for better UX)


class BotAPIClient:
    """Client for communicating with the bot API"""

    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url

    def get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request to API"""
        try:
            response = requests.get(
                f"{self.base_url}{endpoint}", params=params, timeout=5
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            st.error(f"API Error: {e}")
            return None

    def post(self, endpoint: str, data: dict = None) -> dict:
        """Make POST request to API"""
        try:
            response = requests.post(
                f"{self.base_url}{endpoint}", json=data, timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            st.error(f"API Error: {e}")
            return None

    def get_status(self) -> dict:
        return self.get("/api/status")

    def get_positions(self) -> List[dict]:
        return self.get("/api/positions") or []

    def get_trades(self, limit: int = 50) -> List[dict]:
        return self.get("/api/trades", params={"limit": limit}) or []

    def get_stats(self) -> dict:
        return self.get("/api/stats")

    def get_circuit_breaker(self) -> dict:
        return self.get("/api/circuit-breaker")

    def get_config(self) -> dict:
        return self.get("/api/config")

    def control_action(self, action: str, params: dict = None) -> dict:
        return self.post("/api/control", {"action": action, "params": params})

    def manual_trade(self, side: str, quantity: float, price: float = None) -> dict:
        return self.post(
            "/api/manual-trade", {"side": side, "quantity": quantity, "price": price}
        )


def format_currency(value: float) -> str:
    """Format value as currency"""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif abs(value) >= 1_000:
        return f"${value / 1_000:.2f}K"
    else:
        return f"${value:.2f}"


def format_percentage(value: float) -> str:
    """Format value as percentage"""
    return f"{value:.2f}%"


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"
    else:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        return f"{days}d {hours}h"


def create_pnl_chart(trades: List[dict]) -> go.Figure:
    """Create cumulative P&L chart"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df = df.sort_values("exit_time")
    df["cumulative_pnl"] = df["pnl"].cumsum()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["exit_time"],
            y=df["cumulative_pnl"],
            mode="lines+markers",
            name="Cumulative P&L",
            line=dict(color="#00CC96", width=2),
            marker=dict(size=6),
        )
    )

    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Cumulative P&L Over Time",
        xaxis_title="Time",
        yaxis_title="Cumulative P&L ($)",
        hovermode="x unified",
        template="plotly_dark",
        height=300,
    )

    return fig


def create_win_rate_chart(stats: dict) -> go.Figure:
    """Create win rate pie chart"""
    winning = stats.get("winning_trades", 0)
    losing = stats.get("losing_trades", 0)

    if winning + losing == 0:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    fig = go.Figure(
        data=[
            go.Pie(
                labels=["Winning", "Losing"],
                values=[winning, losing],
                marker=dict(colors=["#00CC96", "#FF6692"]),
                hole=0.4,
            )
        ]
    )

    fig.update_layout(
        title=f"Win Rate: {stats.get('win_rate', 0):.1f}%",
        template="plotly_dark",
        height=300,
        margin=dict(l=0, r=0, t=30, b=0),
    )

    return fig


def render_status_bar(status: dict):
    """Render status bar at top of dashboard"""
    is_running = status.get("is_running", False)
    is_paused = status.get("is_paused", False)
    mode = status.get("mode", "unknown")
    asset = status.get("asset", "N/A")
    uptime = status.get("uptime_seconds", 0)

    # Status indicator
    if is_paused:
        status_icon = "⏸️"
        status_text = "PAUSED"
    elif is_running:
        status_icon = "🟢"
        status_text = "RUNNING"
    else:
        status_icon = "🔴"
        status_text = "STOPPED"

    # Mode badge
    mode_colors = {"paper": "blue", "testnet": "purple", "mainnet": "red"}
    mode_color = mode_colors.get(mode, "gray")

    cols = st.columns([3, 2, 2, 3])
    with cols[0]:
        st.markdown(f"{status_icon} **{status_text}**")
    with cols[1]:
        st.markdown(
            f"<span style='color:{mode_color}'>**{mode.upper()}**</span>",
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(f"**{asset}**")
    with cols[3]:
        st.caption(f"Uptime: {format_duration(uptime)}")

    st.markdown("---")


def render_stats_cards(stats: dict):
    """Render statistics cards"""
    total_pnl = stats.get("total_pnl", 0)
    total_return = stats.get("total_return_pct", 0)
    win_rate = stats.get("win_rate", 0)
    total_trades = stats.get("total_trades", 0)
    current_capital = stats.get("current_capital", 0)

    # P&L color

    cols = st.columns(5)
    with cols[0]:
        st.metric(
            label="Total P&L",
            value=format_currency(total_pnl),
            delta=f"{total_return:.2f}%",
            delta_color="normal" if total_return >= 0 else "inverse",
        )
    with cols[1]:
        st.metric(label="Capital", value=format_currency(current_capital))
    with cols[2]:
        st.metric(label="Win Rate", value=f"{win_rate:.1f}%")
    with cols[3]:
        st.metric(label="Total Trades", value=total_trades)
    with cols[4]:
        st.metric(label="Daily Trades", value=stats.get("daily_trades", 0))


def render_positions(positions: List[dict], client: BotAPIClient):
    """Render open positions table with close buttons"""
    if not positions:
        st.info("No open positions")
        return

    st.subheader(f"Open Positions ({len(positions)})")

    for i, pos in enumerate(positions):
        side = pos["side"]
        pnl = pos.get("unrealized_pnl", 0)
        pnl_color = "🟢" if pnl >= 0 else "🔴"

        with st.expander(
            f"{pnl_color} {side} ${pos['quantity']:.2f} @ ${pos['entry_price']:.4f} | "
            f"P&L: ${pnl:.2f}"
        ):
            cols = st.columns(4)
            with cols[0]:
                st.caption("Entry Price")
                st.write(f"${pos['entry_price']:.4f}")
            with cols[1]:
                st.caption("Take Profit")
                st.write(f"${pos['tp_price']:.4f}")
            with cols[2]:
                st.caption("Stop Loss")
                st.write(f"${pos['sl_price']:.4f}")
            with cols[3]:
                st.caption("Leverage")
                st.write(f"{pos['leverage']}x")

            st.caption(f"Entry: {pos['entry_time']}")

            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"Close Position {i}", key=f"close_{i}"):
                    result = client.control_action("close_all")
                    if result and result.get("status") == "success":
                        st.success("Position closed")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Failed to close position")


def render_trades(trades: List[dict]):
    """Render recent trades table"""
    if not trades:
        st.info("No completed trades")
        return

    st.subheader(f"Recent Trades (Last {len(trades)})")

    # Convert to DataFrame for display
    df = pd.DataFrame(trades)
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df = df.sort_values("exit_time", ascending=False)

    # Add styling
    def style_pnl(val):
        color = "green" if val >= 0 else "red"
        return f"color: {color}"

    # Format for display
    display_df = df.copy()
    display_df["pnl"] = display_df["pnl"].apply(lambda x: f"${x:.2f}")
    display_df["entry_price"] = display_df["entry_price"].apply(lambda x: f"${x:.4f}")
    display_df["exit_price"] = display_df["exit_price"].apply(
        lambda x: f"${x:.4f}" if pd.notna(x) else "-"
    )
    display_df = display_df.rename(
        columns={
            "side": "Side",
            "entry_price": "Entry",
            "exit_price": "Exit",
            "quantity": "Qty",
            "pnl": "P&L",
            "exit_time": "Exit Time",
        }
    )

    st.dataframe(
        display_df[["Side", "Entry", "Exit", "Qty", "P&L", "Exit Time"]],
        width="stretch",
        hide_index=True,
    )


def render_circuit_breaker(cb_status: dict, client: BotAPIClient):
    """Render circuit breaker status and controls"""
    is_triggered = cb_status.get("is_triggered", False)
    consecutive = cb_status.get("consecutive_losses", 0)
    max_consecutive = cb_status.get("max_consecutive_losses", 3)
    enabled = cb_status.get("enabled", True)

    if not enabled:
        return

    if is_triggered:
        st.error(
            f"⛔ Circuit Breaker TRIGGERED! ({consecutive}/{max_consecutive} consecutive losses)"
        )
        cooldown_until = cb_status.get("cooldown_until")
        if cooldown_until:
            st.caption(f"Cooldown until: {cooldown_until}")
    else:
        st.success(
            f"✅ Circuit Breaker OK ({consecutive}/{max_consecutive} consecutive losses)"
        )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Reset Circuit Breaker"):
            result = client.control_action("reset_cb")
            if result and result.get("status") == "success":
                st.success("Circuit breaker reset")
                time.sleep(1)
                st.rerun()


def render_controls(client: BotAPIClient, status: dict):
    """Render bot control buttons"""
    is_paused = status.get("is_paused", False)

    st.subheader("Bot Controls")

    cols = st.columns(4)

    with cols[0]:
        if is_paused:
            if st.button("▶️ Resume Trading", type="primary"):
                result = client.control_action("resume")
                if result and result.get("status") == "success":
                    st.success("Trading resumed")
                    time.sleep(1)
                    st.rerun()
        else:
            if st.button("⏸️ Pause Trading"):
                result = client.control_action("pause")
                if result and result.get("status") == "success":
                    st.success("Trading paused")
                    time.sleep(1)
                    st.rerun()

    with cols[1]:
        if st.button("🚨 Close All Positions"):
            st.warning("Closing all positions...")
            result = client.control_action("close_all")
            if result and result.get("status") == "success":
                st.success("All positions closed")
                time.sleep(1)
                st.rerun()

    with cols[2]:
        if st.button("🔄 Refresh"):
            st.rerun()

    with cols[3]:
        # Auto-refresh toggle (disabled by default for better UX)
        if "auto_refresh" not in st.session_state:
            st.session_state.auto_refresh = False

        auto_refresh = st.checkbox("Auto-refresh", value=st.session_state.auto_refresh)
        st.session_state.auto_refresh = auto_refresh


def render_manual_trade(client: BotAPIClient):
    """Render manual trade form"""
    with st.expander("💱 Manual Trade", expanded=False):
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            side = st.selectbox("Side", ["LONG", "SHORT"])

        with col2:
            quantity = st.number_input(
                "Quantity (USD)", min_value=10.0, value=100.0, step=10.0
            )

        with col3:
            price = st.number_input(
                "Price (optional)", min_value=0.0, value=0.0, step=0.0001
            )

        with col4:
            st.write("")
            if st.button("Place Trade"):
                if quantity > 0:
                    result = client.manual_trade(
                        side, quantity, price if price > 0 else None
                    )
                    if result and result.get("status") == "success":
                        st.success(f"{side} trade placed for ${quantity}")
                    else:
                        st.error("Failed to place trade")
                else:
                    st.error("Please enter a valid quantity")


def render_config(client: BotAPIClient, config: dict):
    """Render configuration with parameter adjustment"""
    with st.expander("⚙️ Configuration", expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            st.caption("Strategy Settings")
            st.write(f"Asset: {config.get('asset')}")
            st.write(f"Timeframe: {config.get('timeframe')}")
            st.write(f"Leverage: {config.get('leverage')}x")
            st.write(f"Confidence Threshold: {config.get('confidence_threshold')}")

        with col2:
            st.caption("Risk Management")
            st.write(f"Risk per Trade: {config.get('risk_per_trade_pct') * 100:.1f}%")
            st.write(f"TP Multiplier: {config.get('tp_atr_multiplier')}x ATR")
            st.write(f"SL Multiplier: {config.get('sl_atr_multiplier')}x ATR")
            st.write(f"Max Positions: {config.get('max_positions')}")


def main():
    """Main dashboard application"""
    st.set_page_config(
        page_title="HYPE Trading Bot Dashboard",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Custom CSS for dark theme
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #0e1117;
        }
        .metric-card {
            background-color: #1e2130;
            padding: 1rem;
            border-radius: 0.5rem;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    # Initialize API client
    client = BotAPIClient()

    # Page header
    st.title("📈 HYPE Trading Bot Dashboard")
    st.caption(f"Connected to: {API_BASE_URL}")

    # Check API connection
    try:
        status = client.get_status()
        if status is None:
            st.error("❌ Cannot connect to bot API. Make sure the bot is running.")
            st.info("Start the bot first: python3 hype_paper_trading_bot.py")
            return
    except Exception as e:
        st.error(f"❌ Connection error: {e}")
        st.info("Start the bot first: python3 hype_paper_trading_bot.py")
        return

    # Render status bar
    render_status_bar(status)

    # Main content - Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["Dashboard", "Positions", "Trades", "Settings"])

    with tab1:
        # Fetch data
        stats = client.get_stats() or {}
        cb_status = client.get_circuit_breaker() or {}
        trades = client.get_trades(limit=50)

        # Stats cards
        render_stats_cards(stats)

        st.markdown("---")

        # Charts
        col1, col2 = st.columns([2, 1])

        # Use timestamp-based keys to avoid duplicate ID errors on refresh
        chart_key = int(time.time() * 1000)

        with col1:
            st.plotly_chart(
                create_pnl_chart(trades), width="stretch", key=f"pnl_chart_{chart_key}"
            )

        with col2:
            st.plotly_chart(
                create_win_rate_chart(stats),
                width="stretch",
                key=f"winrate_chart_{chart_key}",
            )

        st.markdown("---")

        # Circuit Breaker
        render_circuit_breaker(cb_status, client)

        st.markdown("---")

        # Controls
        render_controls(client, status)

    with tab2:
        positions = client.get_positions()
        render_positions(positions, client)

    with tab3:
        trades = client.get_trades(limit=100)
        render_trades(trades)

    with tab4:
        config = client.get_config() or {}
        render_config(client, config)
        st.markdown("---")
        render_manual_trade(client)

    # Auto-refresh at the end (after all content is rendered)
    if st.session_state.get("auto_refresh", True):
        time.sleep(REFRESH_INTERVAL)
        st.rerun()


if __name__ == "__main__":
    main()
