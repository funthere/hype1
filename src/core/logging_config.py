"""
Structured Logging Configuration for HyperLiquid Trading Bot

Provides JSON-formatted logging with log rotation and correlation ID (trade_id)
support via contextvars for tracing trades across log entries.
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

# ---------------------------------------------------------------------------
# Correlation ID (trade_id) support via contextvars
# ---------------------------------------------------------------------------

_trade_id_var: ContextVar[Optional[str]] = ContextVar("trade_id", default=None)


def set_trade_id(trade_id: Optional[str]) -> None:
    """Set the trade_id correlation ID for the current async context."""
    _trade_id_var.set(trade_id)


def get_trade_id() -> Optional[str]:
    """Get the current trade_id correlation ID."""
    return _trade_id_var.get()


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Format log records as JSON lines with structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Inject trade_id correlation ID from context var
        trade_id = get_trade_id()
        if trade_id is not None:
            log_entry["trade_id"] = trade_id

        # Inject trade_id from record if set via adapter or extra
        record_trade_id = getattr(record, "trade_id", None)
        if record_trade_id is not None:
            log_entry["trade_id"] = record_trade_id

        # Merge any extra fields added via extra={...}
        reserved = {
            "name",
            "msg",
            "args",
            "created",
            "relativeCreated",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "pathname",
            "filename",
            "module",
            "thread",
            "threadName",
            "process",
            "processName",
            "levelname",
            "levelno",
            "message",
            "msecs",
            "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in reserved and not key.startswith("_"):
                log_entry[key] = value

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Plain-text formatter for console readability
# ---------------------------------------------------------------------------


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with optional trade_id display."""

    def format(self, record: logging.LogRecord) -> str:
        trade_id = getattr(record, "trade_id", None) or get_trade_id()
        if trade_id:
            # Inject trade_id into the record so the format string can use it
            record.trade_id_display = f"[{trade_id}] "
        else:
            record.trade_id_display = ""

        fmt = (
            "%(asctime)s [%(levelname)s] %(name)s %(trade_id_display)s%(message)s"
        )
        self._style._fmt = fmt
        return super().format(record)


# ---------------------------------------------------------------------------
# TradeLoggerAdapter — automatically injects trade_id into log records
# ---------------------------------------------------------------------------


class TradeLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that automatically injects trade_id into every log record.

    Usage::

        logger = TradeLoggerAdapter(logging.getLogger(__name__))
        logger.info("Placing order")  # trade_id from context var is auto-injected
    """

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        # Prefer explicitly set trade_id, fall back to context var
        if "trade_id" not in extra:
            ctx_trade_id = get_trade_id()
            if ctx_trade_id is not None:
                extra["trade_id"] = ctx_trade_id
        kwargs["extra"] = extra
        return msg, kwargs


# ---------------------------------------------------------------------------
# setup_logging — main entry point
# ---------------------------------------------------------------------------


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    json_format: bool = True,
) -> None:
    """Configure structured logging for the trading bot.

    Args:
        level: Logging level (e.g. logging.INFO, logging.DEBUG).
        log_file: Path to the log file. If ``None``, only console logging is used.
            When provided, a RotatingFileHandler is attached with 10 MB max size
            and 5 backup files.
        json_format: If ``True``, the file handler uses JSON formatting.
            The console handler always uses plain text for readability.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove any existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()

    # --- Console handler (always plain text) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console_handler)

    # --- File handler (JSON or plain text, with rotation) ---
    if log_file:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
        )
        file_handler.setLevel(level)

        if json_format:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                )
            )

        root_logger.addHandler(file_handler)
