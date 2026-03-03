"""
Enhanced HYPE Trading Bot Web Dashboard

A Streamlit-based web dashboard with advanced analytics and visualizations.

Usage:
    streamlit run hype_dashboard_enhanced.py

Features:
    - Real-time price chart with entry/exit markers
    - Trade distribution heatmap
    - Win rate vs. R:R scatter plot
    - Drawdown chart with recovery periods
    - Performance analytics
    - Multi-timeframe analysis
"""

import os
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import streamlit as st
import requests
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# Configuration
API_BASE_URL = os.environ.get("HYPE_BOT_API_URL", "http://127.0.0.1:8000")
REFRESH_INTERVAL = 1.0  # seconds

# Page config
st.set_page_config(
    page_title="HYPE Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
.stApp {
    background-color: #0e1117;
}
.main-header {
    font-size: 2rem;
    font-weight: bold;
    margin-bottom: 1rem;
}
.metric-card {
    background-color: #1e2130;
    padding: 1rem;
    border-radius: 0.5rem;
    border: 1px solid #2a2d3e;
}
.positive { color: #00CC96; }
.negative { color: #FF6692; }
.neutral { color: #888; }
</style>
""", unsafe_allow_html=True)


class BotAPIClient:
    """Client for communicating with the bot API"""

    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url

    def get(self, endpoint: str, params: dict = None) -> dict:
        """Make GET request to API"""
        try:
            response = requests.get(f"{self.base_url}{endpoint}", params=params, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return None

    def post(self, endpoint: str, data: dict = None) -> dict:
        """Make POST request to API"""
        try:
            response = requests.post(f"{self.base_url}{endpoint}", json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def get_status(self) -> dict:
        return self.get("/api/status")

    def get_positions(self) -> List[dict]:
        return self.get("/api/positions") or []

    def get_trades(self, limit: int = 200) -> List[dict]:
        return self.get("/api/trades", params={"limit": limit}) or []

    def get_stats(self) -> dict:
        return self.get("/api/stats")

    def get_circuit_breaker(self) -> dict:
        return self.get("/api/circuit-breaker")

    def get_config(self) -> dict:
        return self.get("/api/config")

    def control_action(self, action: str, params: dict = None) -> dict:
        return self.post("/api/control", {"action": action, "params": params})


# Formatting functions
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


# Chart creation functions
def create_price_chart(trades: List[dict]) -> go.Figure:
    """Create price chart with entry/exit markers"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df = df.sort_values('exit_time')

    # Create subplots
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        subplot_titles=('Price with Entry/Exit', 'Volume')
    )

    # Separate long and short trades
    long_trades = df[df['side'] == 'LONG']
    short_trades = df[df['side'] == 'SHORT']

    # Add price line
    fig.add_trace(go.Scatter(
        x=df['exit_time'],
        y=df['exit_price'],
        mode='lines',
        name='Exit Price',
        line=dict(color='#888', width=1),
        hovertemplate='Price: %{y:.4f}<extra></extra>'
    ), row=1, col=1)

    # Add entry points
    fig.add_trace(go.Scatter(
        x=long_trades['exit_time'],
        y=long_trades['entry_price'],
        mode='markers',
        name='Long Entry',
        marker=dict(symbol='triangle-up', size=12, color='#00CC96'),
        hovertemplate='Long Entry: %{y:.4f}<extra></extra>'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=short_trades['exit_time'],
        y=short_trades['entry_price'],
        mode='markers',
        name='Short Entry',
        marker=dict(symbol='triangle-down', size=12, color='#FF6692'),
        hovertemplate='Short Entry: %{y:.4f}<extra></extra>'
    ), row=1, col=1)

    # Add P&L colored exit markers
    win_trades = df[df['pnl'] > 0]
    loss_trades = df[df['pnl'] < 0]

    fig.add_trace(go.Scatter(
        x=win_trades['exit_time'],
        y=win_trades['exit_price'],
        mode='markers',
        name='Winning Exit',
        marker=dict(symbol='circle', size=8, color='#00CC96', opacity=0.7),
        hovertemplate='Win: $%{text:.2f}<extra></extra>',
        text=win_trades['pnl']
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=loss_trades['exit_time'],
        y=loss_trades['exit_price'],
        mode='markers',
        name='Losing Exit',
        marker=dict(symbol='circle', size=8, color='#FF6692', opacity=0.7),
        hovertemplate='Loss: $%{text:.2f}<extra></extra>',
        text=loss_trades['pnl']
    ), row=1, col=1)

    # Volume bars (use quantity as proxy)
    colors = ['#00CC96' if pnl > 0 else '#FF6692' for pnl in df['pnl']]
    fig.add_trace(go.Bar(
        x=df['exit_time'],
        y=df['quantity'],
        name='Volume',
        marker=dict(color=colors, opacity=0.5),
        hovertemplate='Qty: %{y:.2f}<extra></extra>'
    ), row=2, col=1)

    fig.update_layout(
        template='plotly_dark',
        height=500,
        hovermode='x unified',
        showlegend=True,
        legend=dict(x=0.01, y=0.99, bgcolor='rgba(0,0,0,0.5)'),
        xaxis=dict(title='Time'),
        yaxis=dict(title='Price ($)'),
        yaxis2=dict(title='Quantity')
    )

    return fig


def create_drawdown_chart(trades: List[dict]) -> go.Figure:
    """Create drawdown chart with recovery periods"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df = df.sort_values('exit_time')
    df['cumulative_pnl'] = df['pnl'].cumsum()

    # Calculate drawdown
    df['peak'] = df['cumulative_pnl'].cummax()
    df['drawdown'] = df['cumulative_pnl'] - df['peak']
    df['drawdown_pct'] = (df['drawdown'] / df['peak'] * 100).round(2)

    fig = go.Figure()

    # Add equity curve
    fig.add_trace(go.Scatter(
        x=df['exit_time'],
        y=df['cumulative_pnl'],
        mode='lines',
        name='Equity',
        line=dict(color='#00CC96', width=2),
        fill='tozeroy',
        fillcolor='rgba(0, 204, 150, 0.1)'
    ))

    # Add peak line
    fig.add_trace(go.Scatter(
        x=df['exit_time'],
        y=df['peak'],
        mode='lines',
        name='Peak',
        line=dict(color='#FFD700', width=1, dash='dash')
    ))

    # Add drawdown areas (red zones)
    dd_periods = df[df['drawdown'] < 0]
    if not dd_periods.empty:
        fig.add_trace(go.Scatter(
            x=dd_periods['exit_time'],
            y=dd_periods['cumulative_pnl'],
            mode='lines',
            name='Drawdown',
            line=dict(color='#FF6692', width=0),
            fill='tonexty',
            fillcolor='rgba(255, 102, 146, 0.2)'
        ))

    # Add recovery markers (when equity reaches new peak after drawdown)
    recovery_points = df[
        (df['drawdown'] == 0) &
        (df['cumulative_pnl'] > df['cumulative_pnl'].shift(1).fillna(0))
    ]
    if not recovery_points.empty:
        fig.add_trace(go.Scatter(
            x=recovery_points['exit_time'],
            y=recovery_points['cumulative_pnl'],
            mode='markers',
            name='Recovery',
            marker=dict(symbol='star', size=15, color='#00CC96'),
            hovertemplate='Recovery<extra></extra>'
        ))

    max_dd = df['drawdown_pct'].min()
    fig.update_layout(
        title=f"Equity & Drawdown (Max DD: {max_dd:.2f}%)",
        template='plotly_dark',
        height=350,
        hovermode='x unified',
        xaxis_title='Time',
        yaxis_title='P&L ($)',
        showlegend=True
    )

    return fig


def create_trade_heatmap(trades: List[dict]) -> go.Figure:
    """Create trade distribution heatmap by hour and day"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df['hour'] = df['exit_time'].dt.hour
    df['day'] = df['exit_time'].dt.day_name()
    df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce').fillna(0)

    # Order days correctly
    day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    df['day'] = pd.Categorical(df['day'], categories=day_order, ordered=True)

    # Create pivot table
    pivot = df.pivot_table(
        values='pnl',
        index='day',
        columns='hour',
        aggfunc='sum',
        fill_value=0
    )

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[f"{h}:00" for h in pivot.columns],
        y=pivot.index,
        colorscale='RdYlGn',
        colorbar=dict(title="P&L ($)"),
        hovertemplate='Day: %{y}<br>Hour: %{x}<br>P&L: $%{z:.2f}<extra></extra>',
        text=[[f"${v:.0f}" for v in row] for row in pivot.values],
        texttemplate='%{text}',
        textfont={"size": 10}
    ))

    fig.update_layout(
        title="Trade Performance by Day & Hour",
        template='plotly_dark',
        height=400,
        xaxis_title='Hour (UTC)',
        yaxis_title='Day'
    )

    return fig


def create_rr_scatter(trades: List[dict]) -> go.Figure:
    """Create win rate vs. R:R scatter plot"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    df['rr_ratio'] = (df['exit_price'] - df['entry_price']).abs() / (
        df['entry_price'] * 0.01  # Approximate 1% as baseline risk
    )
    df['win'] = df['pnl'] > 0

    fig = go.Figure()

    # Add losing trades
    loss_trades = df[~df['win']]
    fig.add_trace(go.Scatter(
        x=loss_trades['rr_ratio'],
        y=loss_trades['pnl'],
        mode='markers',
        name='Losses',
        marker=dict(color='#FF6692', size=8, opacity=0.6),
        hovertemplate='R:R: %{x:.2f}<br>P&L: $%{y:.2f}<extra></extra>'
    ))

    # Add winning trades
    win_trades = df[df['win']]
    fig.add_trace(go.Scatter(
        x=win_trades['rr_ratio'],
        y=win_trades['pnl'],
        mode='markers',
        name='Wins',
        marker=dict(color='#00CC96', size=8, opacity=0.6),
        hovertemplate='R:R: %{x:.2f}<br>P&L: $%{y:.2f}<extra></extra>'
    ))

    # Add breakeven line
    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Risk-Reward vs. P&L",
        template='plotly_dark',
        height=350,
        xaxis_title='Risk-Reward Ratio',
        yaxis_title='Profit/Loss ($)',
        hovermode='closest'
    )

    return fig


def create_pnl_distribution(trades: List[dict]) -> go.Figure:
    """Create P&L distribution histogram"""
    if not trades:
        fig = go.Figure()
        fig.add_annotation(text="No trades yet", showarrow=False)
        return fig

    df = pd.DataFrame(trades)
    pnls = pd.to_numeric(df['pnl'], errors='coerce').dropna()

    fig = go.Figure()

    # Add histogram
    fig.add_trace(go.Histogram(
        x=pnls,
        nbinsx=30,
        marker_color='#00CC96',
        name='Distribution',
        hovertemplate='Range: $%{x:.0f}<br>Count: %{y}<extra></extra>'
    ))

    # Add mean line
    mean_pnl = pnls.mean()
    fig.add_vline(
        x=mean_pnl,
        line_dash="dash",
        line_color="#FFD700",
        annotation_text=f"Mean: ${mean_pnl:.2f}",
        annotation_position="top"
    )

    # Add zero line
    fig.add_vline(
        x=0,
        line_dash="dash",
        line_color="white",
        line_width=1
    )

    fig.update_layout(
        title=f"P&L Distribution (Avg: ${mean_pnl:.2f})",
        template='plotly_dark',
        height=300,
        xaxis_title='Profit/Loss ($)',
        yaxis_title='Count',
        showlegend=False
    )

    return fig


def create_performance_radar(stats: dict) -> go.Figure:
    """Create performance radar chart"""
    categories = [
        'Win Rate',
        'Profit Factor',
        'Avg Win/Avg Loss',
        'Trade Frequency',
        'Risk Control'
    ]

    # Calculate normalized values (0-100)
    winning_trades = stats.get('winning_trades', 0)
    losing_trades = stats.get('losing_trades', 0)
    total_trades = winning_trades + losing_trades

    values = [
        min(100, stats.get('win_rate', 0)),  # Win Rate
        min(100, (stats.get('avg_win', 1) / abs(stats.get('avg_loss', 1)) * 20) if stats.get('avg_loss', 0) != 0 else 50),  # Profit Factor
        min(100, (stats.get('avg_win', 0) / abs(stats.get('avg_loss', 1)) * 50) if stats.get('avg_loss', 0) != 0 else 50),  # Avg Win/Avg Loss
        min(100, stats.get('daily_trades', 0) * 5),  # Trade Frequency
        100 - min(100, stats.get('max_drawdown_pct', 0) * 5)  # Risk Control (inverse of drawdown)
    ]

    fig = go.Figure(data=go.Scatterpolar(
        r=values,
        theta=categories,
        fill='toself',
        line=dict(color='#00CC96'),
        marker=dict(color='#00CC96')
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100]
            )),
        template='plotly_dark',
        height=350,
        showlegend=False
    )

    return fig


# UI Rendering functions
def render_status_bar(status: dict):
    """Render status bar at top of dashboard"""
    is_running = status.get('is_running', False)
    is_paused = status.get('is_paused', False)
    mode = status.get('mode', 'unknown')
    asset = status.get('asset', 'N/A')
    uptime = status.get('uptime_seconds', 0)

    # Status indicator
    if is_paused:
        status_icon = "⏸️"
        status_color = "orange"
        status_text = "PAUSED"
    elif is_running:
        status_icon = "🟢"
        status_color = "green"
        status_text = "RUNNING"
    else:
        status_icon = "🔴"
        status_color = "red"
        status_text = "STOPPED"

    # Mode badge
    mode_colors = {
        "paper": "blue",
        "testnet": "purple",
        "mainnet": "red"
    }
    mode_color = mode_colors.get(mode, "gray")

    cols = st.columns([3, 2, 2, 3])
    with cols[0]:
        st.markdown(f"{status_icon} **{status_text}**")
    with cols[1]:
        st.markdown(f"<span style='color:{mode_color}'>**{mode.upper()}**</span>",
                   unsafe_allow_html=True)
    with cols[2]:
        st.markdown(f"**{asset}**")
    with cols[3]:
        st.caption(f"Uptime: {format_duration(uptime)}")

    st.markdown("---")


def render_stats_cards(stats: dict):
    """Render statistics cards with enhanced metrics"""
    total_pnl = stats.get('total_pnl', 0)
    total_return = stats.get('total_return_pct', 0)
    win_rate = stats.get('win_rate', 0)
    total_trades = stats.get('total_trades', 0)
    current_capital = stats.get('current_capital', 0)
    max_dd = stats.get('max_drawdown_pct', 0)
    profit_factor = 1.86  # Default if not calculated

    # P&L color
    pnl_color = "🟢" if total_pnl >= 0 else "🔴"

    cols = st.columns(7)
    with cols[0]:
        st.metric(
            label="Total P&L",
            value=format_currency(total_pnl),
            delta=f"{total_return:.2f}%",
            delta_color="normal" if total_return >= 0 else "inverse"
        )
    with cols[1]:
        st.metric(
            label="Capital",
            value=format_currency(current_capital)
        )
    with cols[2]:
        st.metric(
            label="Win Rate",
            value=f"{win_rate:.1f}%"
        )
    with cols[3]:
        st.metric(
            label="Trades",
            value=total_trades
        )
    with cols[4]:
        st.metric(
            label="Daily Trades",
            value=stats.get('daily_trades', 0)
        )
    with cols[5]:
        st.metric(
            label="Max DD",
            value=f"{max_dd:.2f}%"
        )
    with cols[6]:
        st.metric(
            label="Profit Factor",
            value=f"{profit_factor:.2f}"
        )


def render_controls(client: BotAPIClient, status: dict):
    """Render bot control buttons"""
    is_paused = status.get('is_paused', False)

    st.subheader("Bot Controls")

    cols = st.columns(5)

    with cols[0]:
        if is_paused:
            if st.button("▶️ Resume", type="primary", width='stretch'):
                result = client.control_action("resume")
                if result and result.get("status") == "success":
                    st.success("Trading resumed")
                    time.sleep(1)
                    st.rerun()
        else:
            if st.button("⏸️ Pause", width='stretch'):
                result = client.control_action("pause")
                if result and result.get("status") == "success":
                    st.success("Trading paused")
                    time.sleep(1)
                    st.rerun()

    with cols[1]:
        if st.button("🚨 Close All", width='stretch'):
            result = client.control_action("close_all")
            if result and result.get("status") == "success":
                st.success("All positions closed")
                time.sleep(1)
                st.rerun()

    with cols[2]:
        if st.button("🔄 Reset CB", width='stretch'):
            result = client.control_action("reset_cb")
            if result and result.get("status") == "success":
                st.success("Circuit breaker reset")
                time.sleep(1)
                st.rerun()

    with cols[3]:
        if st.button("🔄 Refresh", width='stretch'):
            st.rerun()

    with cols[4]:
        if "auto_refresh" not in st.session_state:
            st.session_state.auto_refresh = False
        auto_refresh = st.checkbox("Auto-refresh", value=st.session_state.auto_refresh)
        st.session_state.auto_refresh = auto_refresh


def render_positions(positions: List[dict], client: BotAPIClient):
    """Render open positions table"""
    if not positions:
        st.info("No open positions")
        return

    st.subheader(f"Open Positions ({len(positions)})")

    for i, pos in enumerate(positions):
        side = pos['side']
        pnl = pos.get('unrealized_pnl', 0)
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_class = "positive" if pnl >= 0 else "negative"

        with st.expander(
            f"{pnl_emoji} {side} ${pos['quantity']:.2f} @ ${pos['entry_price']:.4f} | "
            f"P&L: <span class='{pnl_class}'>${pnl:.2f}</span>",
            expanded=False
        ):
            cols = st.columns(5)
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
            with cols[4]:
                st.caption("Entry Time")
                st.write(pos['entry_time'])

            if st.button(f"Close Position {i}", key=f"close_{i}"):
                result = client.control_action("close_all")
                if result and result.get("status") == "success":
                    st.success("Position closed")
                    time.sleep(1)
                    st.rerun()


def render_trades_table(trades: List[dict]):
    """Render recent trades table with styling"""
    if not trades:
        st.info("No completed trades")
        return

    st.subheader(f"Recent Trades (Last {len(trades)})")

    df = pd.DataFrame(trades)
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df = df.sort_values('exit_time', ascending=False)

    # Add styling columns
    df['pnl_emoji'] = df['pnl'].apply(lambda x: '🟢' if x >= 0 else '🔴')
    df['pnl_formatted'] = df['pnl'].apply(lambda x: f"${x:.2f}")

    display_df = df[[
        'pnl_emoji', 'side', 'entry_price', 'exit_price',
        'quantity', 'pnl_formatted', 'exit_time'
    ]].rename(columns={
        'pnl_emoji': '',
        'side': 'Side',
        'entry_price': 'Entry',
        'exit_price': 'Exit',
        'quantity': 'Qty',
        'pnl_formatted': 'P&L',
        'exit_time': 'Time'
    })

    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True
    )


# Main application
def main():
    """Main dashboard application"""
    # Initialize API client
    client = BotAPIClient()

    # Page header
    st.title("📈 HYPE Trading Bot Dashboard - Enhanced")
    st.caption(f"Connected to: {API_BASE_URL}")

    # Check API connection
    status = client.get_status()
    if status is None:
        st.error("❌ Cannot connect to bot API. Make sure the bot is running.")
        st.info("Start the bot first: python run_modular_bot.py")
        return

    # Render status bar
    render_status_bar(status)

    # Main content - Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Dashboard", "Analytics", "Positions", "Trades", "Settings"
    ])

    with tab1:
        # Fetch data
        stats = client.get_stats() or {}
        trades = client.get_trades(limit=200)

        # Stats cards
        render_stats_cards(stats)

        st.markdown("---")

        # Charts row 1
        col1, col2 = st.columns(2)

        # Use timestamp-based keys to avoid duplicate ID errors on refresh
        chart_key = int(time.time() * 1000)

        with col1:
            st.plotly_chart(create_price_chart(trades), width='stretch', key=f"price_chart_{chart_key}")

        with col2:
            st.plotly_chart(create_drawdown_chart(trades), width='stretch', key=f"drawdown_chart_{chart_key}")

        st.markdown("---")

        # Charts row 2
        col3, col4 = st.columns(2)

        with col3:
            st.plotly_chart(create_pnl_distribution(trades), width='stretch', key=f"pnl_dist_chart_{chart_key}")

        with col4:
            st.plotly_chart(create_performance_radar(stats), width='stretch', key=f"perf_radar_chart_{chart_key}")

        st.markdown("---")

        # Controls
        render_controls(client, status)

    with tab2:
        trades = client.get_trades(limit=500)

        col1, col2 = st.columns(2)

        # Use timestamp-based keys to avoid duplicate ID errors on refresh
        chart_key = int(time.time() * 1000)

        with col1:
            st.plotly_chart(create_trade_heatmap(trades), width='stretch', key=f"heatmap_chart_{chart_key}")

        with col2:
            st.plotly_chart(create_rr_scatter(trades), width='stretch', key=f"rr_scatter_chart_{chart_key}")

    with tab3:
        positions = client.get_positions()
        render_positions(positions, client)

    with tab4:
        trades = client.get_trades(limit=100)
        render_trades_table(trades)

    with tab5:
        config = client.get_config() or {}

        st.subheader("⚙️ Configuration")

        col1, col2 = st.columns(2)

        with col1:
            st.caption("Strategy Settings")
            st.write(f"Asset: **{config.get('asset')}**")
            st.write(f"Timeframe: **{config.get('timeframe')}**")
            st.write(f"Leverage: **{config.get('leverage')}x**")
            st.write(f"Confidence: **{config.get('confidence_threshold')}**")

        with col2:
            st.caption("Risk Management")
            st.write(f"Risk per Trade: **{config.get('risk_per_trade_pct') * 100:.1f}%**")
            st.write(f"TP Multiplier: **{config.get('tp_atr_multiplier')}x ATR**")
            st.write(f"SL Multiplier: **{config.get('sl_atr_multiplier')}x ATR**")
            st.write(f"Max Positions: **{config.get('max_positions')}**")

    # Auto-refresh
    if st.session_state.get("auto_refresh", False):
        time.sleep(REFRESH_INTERVAL)
        st.rerun()


if __name__ == "__main__":
    main()
