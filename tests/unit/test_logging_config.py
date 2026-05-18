"""
Tests for structured logging configuration.

Tests cover:
- JSON formatter output structure
- Rotation handler configuration
- Correlation ID set/get via contextvars
- TradeLoggerAdapter auto-injecting trade_id
"""

import json
import logging
import logging.handlers
import os
import tempfile
from unittest import mock

import pytest

from src.core.logging_config import (
    JSONFormatter,
    ConsoleFormatter,
    TradeLoggerAdapter,
    setup_logging,
    set_trade_id,
    get_trade_id,
    _trade_id_var,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_trade_id():
    """Ensure trade_id context var is cleared before/after each test."""
    set_trade_id(None)
    yield
    set_trade_id(None)


@pytest.fixture()
def temp_log_file():
    """Create a temporary log file path and clean up after test."""
    fd, path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


# ---------------------------------------------------------------------------
# JSON Formatter tests
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    """Tests for JSONFormatter output structure."""

    def _make_record(self, msg="test message", **kwargs):
        """Create a LogRecord for testing."""
        logger = logging.getLogger("test_json")
        record = logger.makeRecord(
            name="test_json",
            level=logging.INFO,
            fn="test_module.py",
            lno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in kwargs.items():
            setattr(record, k, v)
        return record

    def test_json_output_is_valid_json(self):
        fmt = JSONFormatter()
        record = self._make_record("hello world")
        output = fmt.format(record)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_json_required_fields(self):
        fmt = JSONFormatter()
        record = self._make_record("hello world")
        output = fmt.format(record)
        data = json.loads(output)

        required_fields = [
            "timestamp", "level", "logger", "message",
            "module", "function", "line",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_json_message_content(self):
        fmt = JSONFormatter()
        record = self._make_record("order placed")
        data = json.loads(fmt.format(record))
        assert data["message"] == "order placed"

    def test_json_level_info(self):
        fmt = JSONFormatter()
        record = self._make_record()
        data = json.loads(fmt.format(record))
        assert data["level"] == "INFO"

    def test_json_logger_name(self):
        fmt = JSONFormatter()
        record = self._make_record()
        data = json.loads(fmt.format(record))
        assert data["logger"] == "test_json"

    def test_json_includes_trade_id_from_context(self):
        set_trade_id("TRADE-123")
        fmt = JSONFormatter()
        record = self._make_record()
        data = json.loads(fmt.format(record))
        assert data["trade_id"] == "TRADE-123"

    def test_json_includes_trade_id_from_record(self):
        fmt = JSONFormatter()
        record = self._make_record(trade_id="TRADE-456")
        data = json.loads(fmt.format(record))
        assert data["trade_id"] == "TRADE-456"

    def test_json_no_trade_id_when_unset(self):
        fmt = JSONFormatter()
        record = self._make_record()
        data = json.loads(fmt.format(record))
        assert "trade_id" not in data

    def test_json_extra_fields_merged(self):
        fmt = JSONFormatter()
        record = self._make_record(custom_field="custom_value")
        data = json.loads(fmt.format(record))
        assert data["custom_field"] == "custom_value"

    def test_json_exception_included(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        logger = logging.getLogger("test_exc")
        record = logger.makeRecord(
            name="test_exc",
            level=logging.ERROR,
            fn="test.py",
            lno=1,
            msg="error",
            args=(),
            exc_info=exc_info,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


# ---------------------------------------------------------------------------
# Rotation handler tests
# ---------------------------------------------------------------------------

class TestRotationHandler:
    """Tests for RotatingFileHandler configuration."""

    def test_file_handler_is_rotating(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1

    def test_max_bytes_10mb(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers[0].maxBytes == 10 * 1024 * 1024

    def test_backup_count_5(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers[0].backupCount == 5

    def test_file_handler_uses_json_formatter(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file, json_format=True)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert isinstance(file_handlers[0].formatter, JSONFormatter)

    def test_file_handler_plain_text_when_json_false(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file, json_format=False)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert not isinstance(file_handlers[0].formatter, JSONFormatter)

    def test_console_handler_always_present(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file)
        root = logging.getLogger()

        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(stream_handlers) >= 1

    def test_console_handler_uses_console_formatter(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file)
        root = logging.getLogger()

        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert any(
            isinstance(h.formatter, ConsoleFormatter) for h in stream_handlers
        )

    def test_no_file_handler_when_log_file_is_none(self):
        setup_logging(level=logging.INFO, log_file=None)
        root = logging.getLogger()

        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 0


# ---------------------------------------------------------------------------
# Correlation ID tests
# ---------------------------------------------------------------------------

class TestCorrelationID:
    """Tests for trade_id context variable set/get."""

    def test_get_returns_none_initially(self):
        assert get_trade_id() is None

    def test_set_and_get(self):
        set_trade_id("TRADE-001")
        assert get_trade_id() == "TRADE-001"

    def test_set_none_clears(self):
        set_trade_id("TRADE-001")
        set_trade_id(None)
        assert get_trade_id() is None

    def test_overwrite(self):
        set_trade_id("TRADE-001")
        set_trade_id("TRADE-002")
        assert get_trade_id() == "TRADE-002"

    def test_different_values(self):
        set_trade_id("short_12.5_20260519120000")
        assert get_trade_id() == "short_12.5_20260519120000"


# ---------------------------------------------------------------------------
# TradeLoggerAdapter tests
# ---------------------------------------------------------------------------

class TestTradeLoggerAdapter:
    """Tests for TradeLoggerAdapter injecting trade_id."""

    def test_adapter_injects_trade_id_from_context(self):
        set_trade_id("TRADE-CTX")
        base_logger = logging.getLogger("test_adapter")
        adapter = TradeLoggerAdapter(base_logger)

        # Process a message
        msg, kwargs = adapter.process("test", {})
        assert kwargs["extra"]["trade_id"] == "TRADE-CTX"

    def test_adapter_explicit_trade_id_takes_precedence(self):
        set_trade_id("TRADE-CTX")
        base_logger = logging.getLogger("test_adapter")
        adapter = TradeLoggerAdapter(base_logger)

        msg, kwargs = adapter.process("test", {"extra": {"trade_id": "TRADE-EXPLICIT"}})
        assert kwargs["extra"]["trade_id"] == "TRADE-EXPLICIT"

    def test_adapter_no_trade_id_when_unset(self):
        base_logger = logging.getLogger("test_adapter")
        adapter = TradeLoggerAdapter(base_logger)

        msg, kwargs = adapter.process("test", {})
        assert "trade_id" not in kwargs.get("extra", {})

    def test_adapter_preserves_other_extra_fields(self):
        set_trade_id("TRADE-123")
        base_logger = logging.getLogger("test_adapter")
        adapter = TradeLoggerAdapter(base_logger)

        msg, kwargs = adapter.process("test", {"extra": {"order_id": "ORD-1"}})
        assert kwargs["extra"]["order_id"] == "ORD-1"
        assert kwargs["extra"]["trade_id"] == "TRADE-123"


# ---------------------------------------------------------------------------
# Integration: setup_logging writes JSON to file
# ---------------------------------------------------------------------------

class TestSetupLoggingIntegration:
    """Integration tests for setup_logging producing JSON output."""

    def test_log_output_is_valid_json(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file, json_format=True)

        test_logger = logging.getLogger("integration_test")
        test_logger.info("integration test message")

        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()

        with open(temp_log_file, "r") as f:
            lines = f.readlines()

        # At least one line should be from our message
        found = False
        for line in lines:
            data = json.loads(line.strip())
            if data.get("message") == "integration test message":
                found = True
                assert data["level"] == "INFO"
                assert "timestamp" in data
                break

        assert found, "Expected log message not found in output"

    def test_log_with_trade_id_in_json(self, temp_log_file):
        setup_logging(level=logging.INFO, log_file=temp_log_file, json_format=True)

        set_trade_id("TRADE-999")
        test_logger = logging.getLogger("trade_id_test")
        test_logger.info("message with trade id")

        for handler in logging.getLogger().handlers:
            handler.flush()

        with open(temp_log_file, "r") as f:
            lines = f.readlines()

        found = False
        for line in lines:
            data = json.loads(line.strip())
            if data.get("message") == "message with trade id":
                found = True
                assert data["trade_id"] == "TRADE-999"
                break

        assert found, "Expected log message with trade_id not found"
