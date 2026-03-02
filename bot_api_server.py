"""
FastAPI Server for HYPE Trading Bot Dashboard

This module provides a REST API and WebSocket server for monitoring
and controlling the HYPE trading bot from a web dashboard.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set
from dataclasses import asdict

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# Pydantic models for API requests/responses
class BotStatusResponse(BaseModel):
    """Bot status response model"""
    is_running: bool
    is_paused: bool
    mode: str  # "paper", "testnet", "mainnet"
    asset: str
    timeframe: str
    leverage: int
    start_time: Optional[str]
    uptime_seconds: float


class PositionResponse(BaseModel):
    """Position response model"""
    side: str
    entry_price: float
    quantity: float
    tp_price: float
    sl_price: float
    entry_time: str
    leverage: int
    unrealized_pnl: float
    status: str


class TradeResponse(BaseModel):
    """Trade response model"""
    side: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    entry_time: str
    exit_time: Optional[str]
    pnl: float
    fees: float


class StatsResponse(BaseModel):
    """Statistics response model"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_fees: float
    starting_capital: float
    current_capital: float
    total_return_pct: float
    max_drawdown_pct: float
    daily_trades: int


class CircuitBreakerResponse(BaseModel):
    """Circuit breaker status response"""
    enabled: bool
    is_triggered: bool
    consecutive_losses: int
    max_consecutive_losses: int
    cooldown_until: Optional[str]
    cooldown_minutes: int


class ConfigResponse(BaseModel):
    """Configuration response"""
    asset: str
    timeframe: str
    leverage: int
    risk_per_trade_pct: float
    tp_atr_multiplier: float
    sl_atr_multiplier: float
    max_positions: int
    max_daily_trades: int
    max_daily_loss_pct: float
    confidence_threshold: int


class ControlActionRequest(BaseModel):
    """Control action request"""
    action: str  # "pause", "resume", "close_all", "reset_cb", "update_param"
    params: Optional[Dict] = None


class TradingBotAPI:
    """
    FastAPI server wrapper for Trading Bot.

    This provides REST endpoints and WebSocket for real-time monitoring
    and control of the trading bot.
    """

    def __init__(self, bot, host: str = "127.0.0.1", port: int = 8000):
        """
        Initialize the API server.

        Args:
            bot: TradingBot instance to wrap
            host: Host to bind to
            port: Port to bind to
        """
        self.bot = bot
        self.host = host
        self.port = port
        self.app = FastAPI(title="HYPE Trading Bot API", version="1.0.0")
        self.server: Optional[uvicorn.Server] = None
        self.websocket_clients: Set[WebSocket] = set()

        self._setup_routes()

    def _setup_routes(self):
        """Setup API routes"""

        @self.app.get("/")
        async def root():
            """Root endpoint"""
            return {
                "name": "HYPE Trading Bot API",
                "version": "1.0.0",
                "status": "running"
            }

        @self.app.get("/api/status", response_model=BotStatusResponse)
        async def get_status():
            """Get current bot status"""
            uptime = 0
            if self.bot.start_time:
                uptime = (datetime.now() - self.bot.start_time).total_seconds()

            return BotStatusResponse(
                is_running=self.bot.is_running,
                is_paused=self.bot.is_paused,
                mode="paper" if self.bot.config.PAPER_TRADING else (
                    "testnet" if self.bot.config.USE_TESTNET else "mainnet"
                ),
                asset=self.bot.config.ASSET,
                timeframe=self.bot.config.TIMEFRAME,
                leverage=self.bot.config.LEVERAGE,
                start_time=self.bot.start_time.isoformat() if self.bot.start_time else None,
                uptime_seconds=uptime
            )

        @self.app.get("/api/positions", response_model=List[PositionResponse])
        async def get_positions():
            """Get open positions"""
            positions = []
            for pos in self.bot.positions:
                positions.append(PositionResponse(
                    side=pos.side.value,
                    entry_price=pos.entry_price,
                    quantity=pos.quantity,
                    tp_price=pos.tp_price,
                    sl_price=pos.sl_price,
                    entry_time=pos.entry_time.isoformat(),
                    leverage=pos.leverage,
                    unrealized_pnl=pos.unrealized_pnl,
                    status=pos.status.value
                ))
            return positions

        @self.app.get("/api/trades", response_model=List[TradeResponse])
        async def get_trades(limit: int = 50):
            """Get trade history"""
            trades = []
            for trade in self.bot.trades[-limit:]:
                trades.append(TradeResponse(
                    side=trade.side.value,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    quantity=trade.quantity,
                    entry_time=trade.entry_time.isoformat(),
                    exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
                    pnl=trade.pnl,
                    fees=trade.fees
                ))
            return trades

        @self.app.get("/api/stats", response_model=StatsResponse)
        async def get_stats():
            """Get trading statistics"""
            winning_trades = [t for t in self.bot.trades if t.pnl > 0]
            losing_trades = [t for t in self.bot.trades if t.pnl < 0]
            win_rate = (len(winning_trades) / len(self.bot.trades) * 100) if self.bot.trades else 0
            total_pnl = sum(t.pnl for t in self.bot.trades)
            total_fees = sum(t.fees for t in self.bot.trades)
            total_return_pct = ((self.bot.current_capital - self.bot.starting_capital) /
                              self.bot.starting_capital * 100) if self.bot.starting_capital > 0 else 0

            return StatsResponse(
                total_trades=len(self.bot.trades),
                winning_trades=len(winning_trades),
                losing_trades=len(losing_trades),
                win_rate=round(win_rate, 2),
                total_pnl=round(total_pnl, 2),
                total_fees=round(total_fees, 2),
                starting_capital=round(self.bot.starting_capital, 2),
                current_capital=round(self.bot.current_capital, 2),
                total_return_pct=round(total_return_pct, 2),
                max_drawdown_pct=round(self.bot.max_drawdown_pct, 2) if hasattr(self.bot, 'max_drawdown_pct') else 0,
                daily_trades=self.bot.daily_trade_count
            )

        @self.app.get("/api/circuit-breaker", response_model=CircuitBreakerResponse)
        async def get_circuit_breaker():
            """Get circuit breaker status"""
            cooldown_until = None
            if self.bot.circuit_breaker_until:
                cooldown_until = self.bot.circuit_breaker_until.isoformat()

            return CircuitBreakerResponse(
                enabled=self.bot.config.CIRCUIT_BREAKER_ENABLED,
                is_triggered=self.bot.circuit_breaker_triggered,
                consecutive_losses=self.bot.consecutive_losses,
                max_consecutive_losses=self.bot.config.MAX_CONSECUTIVE_LOSSES,
                cooldown_until=cooldown_until,
                cooldown_minutes=self.bot.config.CIRCUIT_BREAKER_COOLDOWN_MINUTES
            )

        @self.app.get("/api/config", response_model=ConfigResponse)
        async def get_config():
            """Get bot configuration"""
            return ConfigResponse(
                asset=self.bot.config.ASSET,
                timeframe=self.bot.config.TIMEFRAME,
                leverage=self.bot.config.LEVERAGE,
                risk_per_trade_pct=self.bot.config.RISK_PER_TRADE_PCT,
                tp_atr_multiplier=self.bot.config.TP_ATR_MULTIPLIER,
                sl_atr_multiplier=self.bot.config.SL_ATR_MULTIPLIER,
                max_positions=self.bot.config.MAX_POSITIONS,
                max_daily_trades=self.bot.config.MAX_DAILY_TRADES,
                max_daily_loss_pct=self.bot.config.MAX_DAILY_LOSS_PCT,
                confidence_threshold=self.bot.config.CONFIDENCE_THRESHOLD
            )

        @self.app.post("/api/control")
        async def control_action(request: ControlActionRequest):
            """Execute control action"""
            action = request.action
            params = request.params or {}

            try:
                if action == "pause":
                    self.bot.pause_trading()
                    await self._broadcast_update({"type": "bot_paused"})
                    return {"status": "success", "message": "Trading paused"}

                elif action == "resume":
                    self.bot.resume_trading()
                    await self._broadcast_update({"type": "bot_resumed"})
                    return {"status": "success", "message": "Trading resumed"}

                elif action == "close_all":
                    closed = await self.bot.close_all_positions()
                    await self._broadcast_update({"type": "positions_closed", "count": closed})
                    return {"status": "success", "message": f"Closed {closed} positions"}

                elif action == "reset_cb":
                    self.bot.reset_circuit_breaker()
                    await self._broadcast_update({"type": "circuit_breaker_reset"})
                    return {"status": "success", "message": "Circuit breaker reset"}

                elif action == "update_param":
                    param_name = params.get("name")
                    param_value = params.get("value")
                    if param_name and param_value is not None:
                        self.bot.update_config_param(param_name, param_value)
                        await self._broadcast_update({
                            "type": "config_updated",
                            "param": param_name,
                            "value": param_value
                        })
                        return {"status": "success", "message": f"Updated {param_name}"}
                    else:
                        return JSONResponse(
                            status_code=400,
                            content={"status": "error", "message": "Missing name or value"}
                        )

                else:
                    return JSONResponse(
                        status_code=400,
                        content={"status": "error", "message": f"Unknown action: {action}"}
                    )

            except Exception as e:
                logger.error(f"Control action error: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": str(e)}
                )

        @self.app.post("/api/manual-trade")
        async def manual_trade(params: Dict):
            """Place a manual trade"""
            try:
                side_str = params.get("side", "LONG")
                quantity = params.get("quantity")
                price = params.get("price")

                if not quantity:
                    return JSONResponse(
                        status_code=400,
                        content={"status": "error", "message": "Missing quantity"}
                    )

                side = self.bot.Side.LONG if side_str == "LONG" else self.bot.Side.SHORT

                result = await self.bot.place_manual_trade(side, quantity, price)
                await self._broadcast_update({
                    "type": "manual_trade_placed",
                    "side": side_str,
                    "quantity": quantity
                })

                return {"status": "success", "data": result}

            except Exception as e:
                logger.error(f"Manual trade error: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"status": "error", "message": str(e)}
                )

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates"""
            await websocket.accept()
            self.websocket_clients.add(websocket)

            try:
                # Send initial state
                await websocket.send_json({
                    "type": "connected",
                    "message": "Connected to HYPE Trading Bot"
                })

                # Keep connection alive and handle client messages
                while True:
                    data = await websocket.receive_text()
                    # Handle client ping/heartbeat
                    if data == "ping":
                        await websocket.send_json({"type": "pong"})

            except WebSocketDisconnect:
                self.websocket_clients.discard(websocket)
                logger.info("WebSocket client disconnected")

    async def _broadcast_update(self, data: Dict):
        """Broadcast update to all connected WebSocket clients"""
        if not self.websocket_clients:
            return

        message = json.dumps(data)
        disconnected = set()

        for client in self.websocket_clients:
            try:
                await client.send_json(data)
            except Exception:
                disconnected.add(client)

        # Remove disconnected clients
        self.websocket_clients -= disconnected

    async def broadcast_trade_update(self, trade_type: str, data: Dict):
        """Broadcast trade-related updates"""
        await self._broadcast_update({
            "type": "trade_update",
            "trade_type": trade_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })

    async def broadcast_position_update(self, data: Dict):
        """Broadcast position updates"""
        await self._broadcast_update({
            "type": "position_update",
            "data": data,
            "timestamp": datetime.now().isoformat()
        })

    async def broadcast_stats_update(self, data: Dict):
        """Broadcast statistics updates"""
        await self._broadcast_update({
            "type": "stats_update",
            "data": data,
            "timestamp": datetime.now().isoformat()
        })

    async def start(self):
        """Start the API server"""
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning"  # Reduce noise from uvicorn
        )
        self.server = uvicorn.Server(config)

        logger.info(f"API server running on http://{self.host}:{self.port}")
        await self.server.serve()

    def start_in_background(self):
        """Start API server in background task"""
        task = asyncio.create_task(self.start())
        return task

    async def stop(self):
        """Stop the API server"""
        if self.server:
            self.server.should_exit = True
            logger.info("API server stopped")
