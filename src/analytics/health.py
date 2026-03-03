"""
Health Monitoring Module

Tracks:
- API latency
- Fill rate monitoring
- Slippage calculation
- Connection health
- System resource usage
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from enum import Enum

import psutil
import numpy as np

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class APIMetrics:
    """API performance metrics"""
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    success_rate: float = 1.0
    error_count: int = 0
    total_requests: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None


@dataclass
class ExecutionMetrics:
    """Order execution metrics"""
    fill_rate: float = 1.0
    avg_slippage_bps: float = 0.0  # Basis points
    total_orders: int = 0
    filled_orders: int = 0
    partial_fills: int = 0
    rejected_orders: int = 0
    avg_fill_time_ms: float = 0.0


@dataclass
class SystemMetrics:
    """System resource metrics"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_mb: float = 0.0
    disk_usage_percent: float = 0.0
    open_files: int = 0
    threads: int = 0


@dataclass
class HealthSnapshot:
    """Complete health snapshot at a point in time"""
    timestamp: datetime
    status: HealthStatus
    api: APIMetrics
    execution: ExecutionMetrics
    system: SystemMetrics
    alerts: List[str] = field(default_factory=list)


class HealthMonitor:
    """
    Monitor bot health and performance metrics.

    Tracks API latency, execution quality, and system resources.
    Generates alerts when thresholds are exceeded.
    """

    # Thresholds for alerts
    MAX_LATENCY_MS = 1000  # Alert if API latency exceeds 1s
    MIN_SUCCESS_RATE = 0.95  # Alert if success rate below 95%
    MIN_FILL_RATE = 0.90  # Alert if fill rate below 90%
    MAX_SLIPPAGE_BPS = 10  # Alert if slippage exceeds 10 bps
    MAX_CPU_PERCENT = 80  # Alert if CPU usage exceeds 80%
    MAX_MEMORY_PERCENT = 85  # Alert if memory usage exceeds 85%

    # Rolling window size for metrics
    LATENCY_WINDOW = 100
    MEMORY_HOURS = 24

    def __init__(self):
        """Initialize health monitor"""
        # Latency tracking
        self._latencies: deque = deque(maxlen=self.LATENCY_WINDOW)
        self._api_errors: deque = deque(maxlen=self.LATENCY_WINDOW)
        self._request_times: deque = deque(maxlen=self.LATENCY_WINDOW)

        # Execution tracking
        self._orders: List[Dict] = []
        self._slippages: deque = deque(maxlen=self.LATENCY_WINDOW)

        # Health snapshots history
        self._snapshots: deque = deque(maxlen=1440)  # 1 per minute for 24 hours

        # Current state
        self._status = HealthStatus.HEALTHY
        self._last_check = None

        # Process for system metrics
        self._process = psutil.Process()

    async def record_api_call(
        self,
        endpoint: str,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None
    ):
        """
        Record an API call for metrics

        Args:
            endpoint: API endpoint called
            latency_ms: Request latency in milliseconds
            success: Whether the request was successful
            error: Error message if failed
        """
        self._latencies.append(latency_ms)
        self._request_times.append(datetime.now())

        if not success:
            self._api_errors.append({
                "endpoint": endpoint,
                "error": error,
                "timestamp": datetime.now()
            })

    def record_order(
        self,
        side: str,
        expected_price: float,
        filled_price: float,
        quantity: float,
        fill_time_ms: float,
        status: str  # "filled", "partial", "rejected"
    ):
        """
        Record an order execution

        Args:
            side: Order side (LONG/SHORT)
            expected_price: Expected fill price
            filled_price: Actual fill price
            quantity: Order quantity
            fill_time_ms: Time to fill in milliseconds
            status: Order status
        """
        # Calculate slippage in basis points
        if expected_price > 0 and filled_price > 0:
            slippage = abs((filled_price - expected_price) / expected_price) * 10000
            self._slippages.append(slippage)

        self._orders.append({
            "side": side,
            "expected_price": expected_price,
            "filled_price": filled_price,
            "quantity": quantity,
            "fill_time_ms": fill_time_ms,
            "status": status,
            "timestamp": datetime.now()
        })

        # Clean old orders
        cutoff = datetime.now() - timedelta(hours=self.MEMORY_HOURS)
        self._orders = [o for o in self._orders if o["timestamp"] > cutoff]

    def get_api_metrics(self) -> APIMetrics:
        """Get current API metrics"""
        if not self._latencies:
            return APIMetrics()

        latencies = list(self._latencies)
        errors = list(self._api_errors)

        # Calculate percentiles
        avg_latency = np.mean(latencies)
        sorted_latencies = np.sort(latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p99_idx = int(len(sorted_latencies) * 0.99)

        recent_errors = [e for e in errors if (datetime.now() - e["timestamp"]).seconds < 300]

        last_error_info = recent_errors[-1] if recent_errors else None

        return APIMetrics(
            avg_latency_ms=avg_latency,
            p95_latency_ms=sorted_latencies[p95_idx] if p95_idx < len(latencies) else latencies[-1],
            p99_latency_ms=sorted_latencies[p99_idx] if p99_idx < len(latencies) else latencies[-1],
            success_rate=1.0 - (len(recent_errors) / max(len(latencies), 1)),
            error_count=len(recent_errors),
            total_requests=len(latencies),
            last_error=last_error_info["error"] if last_error_info else None,
            last_error_time=last_error_info["timestamp"] if last_error_info else None
        )

    def get_execution_metrics(self) -> ExecutionMetrics:
        """Get current execution metrics"""
        if not self._orders:
            return ExecutionMetrics()

        recent_orders = [
            o for o in self._orders
            if (datetime.now() - o["timestamp"]).total_seconds() < 3600
        ]

        if not recent_orders:
            return ExecutionMetrics()

        filled = [o for o in recent_orders if o["status"] == "filled"]
        partial = [o for o in recent_orders if o["status"] == "partial"]
        rejected = [o for o in recent_orders if o["status"] == "rejected"]

        slippages = list(self._slippages)
        avg_slippage = np.mean(slippages) if slippages else 0.0

        fill_times = [o["fill_time_ms"] for o in filled + partial]
        avg_fill_time = np.mean(fill_times) if fill_times else 0.0

        return ExecutionMetrics(
            fill_rate=len(filled) / len(recent_orders) if recent_orders else 0.0,
            avg_slippage_bps=avg_slippage,
            total_orders=len(recent_orders),
            filled_orders=len(filled),
            partial_fills=len(partial),
            rejected_orders=len(rejected),
            avg_fill_time_ms=avg_fill_time
        )

    def get_system_metrics(self) -> SystemMetrics:
        """Get current system resource metrics"""
        try:
            cpu = self._process.cpu_percent(interval=0.1)
            mem_info = self._process.memory_info()
            memory_percent = self._process.memory_percent()

            return SystemMetrics(
                cpu_percent=cpu,
                memory_percent=memory_percent,
                memory_mb=mem_info.rss / 1024 / 1024,
                disk_usage_percent=psutil.disk_usage('/').percent,
                open_files=len(self._process.open_files()),
                threads=self._process.num_threads()
            )
        except Exception as e:
            logger.error(f"Error getting system metrics: {e}")
            return SystemMetrics()

    def get_health_snapshot(self) -> HealthSnapshot:
        """Get complete health snapshot"""
        api = self.get_api_metrics()
        execution = self.get_execution_metrics()
        system = self.get_system_metrics()

        # Determine overall status
        status = self._calculate_status(api, execution, system)

        # Generate alerts
        alerts = self._generate_alerts(api, execution, system)

        snapshot = HealthSnapshot(
            timestamp=datetime.now(),
            status=status,
            api=api,
            execution=execution,
            system=system,
            alerts=alerts
        )

        # Store snapshot
        self._snapshots.append(snapshot)
        self._last_check = datetime.now()
        self._status = status

        return snapshot

    def _calculate_status(
        self,
        api: APIMetrics,
        execution: ExecutionMetrics,
        system: SystemMetrics
    ) -> HealthStatus:
        """Calculate overall health status"""
        issues = 0

        # Check API health
        if api.avg_latency_ms > self.MAX_LATENCY_MS:
            issues += 1
        if api.success_rate < self.MIN_SUCCESS_RATE:
            issues += 2  # Weight errors more heavily

        # Check execution health
        if execution.fill_rate < self.MIN_FILL_RATE:
            issues += 1
        if execution.avg_slippage_bps > self.MAX_SLIPPAGE_BPS:
            issues += 1

        # Check system health
        if system.cpu_percent > self.MAX_CPU_PERCENT:
            issues += 1
        if system.memory_percent > self.MAX_MEMORY_PERCENT:
            issues += 1

        if issues == 0:
            return HealthStatus.HEALTHY
        elif issues <= 2:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.UNHEALTHY

    def _generate_alerts(
        self,
        api: APIMetrics,
        execution: ExecutionMetrics,
        system: SystemMetrics
    ) -> List[str]:
        """Generate list of alerts for current issues"""
        alerts = []

        if api.avg_latency_ms > self.MAX_LATENCY_MS:
            alerts.append(f"⚠️ High API latency: {api.avg_latency_ms:.0f}ms")

        if api.success_rate < self.MIN_SUCCESS_RATE:
            alerts.append(f"⚠️ Low API success rate: {api.success_rate:.1%}")

        if execution.fill_rate < self.MIN_FILL_RATE:
            alerts.append(f"⚠️ Low fill rate: {execution.fill_rate:.1%}")

        if execution.avg_slippage_bps > self.MAX_SLIPPAGE_BPS:
            alerts.append(f"⚠️ High slippage: {execution.avg_slippage_bps:.1f} bps")

        if system.cpu_percent > self.MAX_CPU_PERCENT:
            alerts.append(f"⚠️ High CPU usage: {system.cpu_percent:.0f}%")

        if system.memory_percent > self.MAX_MEMORY_PERCENT:
            alerts.append(f"⚠️ High memory usage: {system.memory_percent:.0f}%")

        return alerts

    @property
    def status(self) -> HealthStatus:
        """Get current health status"""
        return self._status

    @property
    def is_healthy(self) -> bool:
        """Check if system is healthy"""
        return self._status == HealthStatus.HEALTHY

    def get_metrics_history(
        self,
        hours: int = 1
    ) -> List[HealthSnapshot]:
        """Get historical health snapshots"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [s for s in self._snapshots if s.timestamp >= cutoff]

    def get_summary(self) -> str:
        """Get formatted health summary"""
        snapshot = self.get_health_snapshot()

        status_emoji = {
            HealthStatus.HEALTHY: "🟢",
            HealthStatus.DEGRADED: "🟡",
            HealthStatus.UNHEALTHY: "🔴"
        }

        summary = f"""
{status_emoji[snapshot.status]} Health Status: {snapshot.status.value.upper()}

┌─ API METRICS ─────────────────────────────────┐
│ Latency:    {snapshot.api.avg_latency_ms:>6.0f}ms (avg)   {snapshot.api.p95_latency_ms:>6.0f}ms (p95)│
│ Success:    {snapshot.api.success_rate:>6.1%}                           │
│ Errors:     {snapshot.api.error_count:>6}  (last 5min)                 │
└──────────────────────────────────────────────────┘

┌─ EXECUTION METRICS ────────────────────────────┐
│ Fill Rate:  {snapshot.execution.fill_rate:>6.1%}                          │
│ Slippage:   {snapshot.execution.avg_slippage_bps:>6.1f} bps                      │
│ Orders:     {snapshot.execution.total_orders:>6}  (filled: {snapshot.execution.filled_orders:>4})             │
└──────────────────────────────────────────────────┘

┌─ SYSTEM METRICS ───────────────────────────────┐
│ CPU:        {snapshot.system.cpu_percent:>6.0f}%                         │
│ Memory:     {snapshot.system.memory_percent:>6.0f}%  ({snapshot.system.memory_mb:>6.0f} MB)           │
│ Threads:    {snapshot.system.threads:>6}                         │
└──────────────────────────────────────────────────┘
"""

        if snapshot.alerts:
            summary += "\n📢 ALERTS:\n"
            for alert in snapshot.alerts:
                summary += f"  {alert}\n"

        return summary


class APITimer:
    """Context manager for timing API calls"""

    def __init__(self, monitor: HealthMonitor, endpoint: str):
        self.monitor = monitor
        self.endpoint = endpoint
        self.start_time = None
        self.success = False
        self.error = None

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.time() - self.start_time) * 1000

        if exc_type is None:
            self.success = True
        else:
            self.success = False
            self.error = str(exc_val)

        await self.monitor.record_api_call(
            self.endpoint,
            latency_ms,
            self.success,
            self.error
        )


def create_timer(monitor: HealthMonitor, endpoint: str):
    """Create an API timer context manager"""
    return APITimer(monitor, endpoint)
