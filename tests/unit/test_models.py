"""
Unit tests for unified domain models in src.core.models
"""

import pytest
from datetime import datetime, timedelta

from src.core.models import Side, OrderStatus, PositionStatus, Position, Trade


# ---------------------------------------------------------------------------
# Side enum
# ---------------------------------------------------------------------------


class TestSide:
    """Test the unified Side enum."""

    def test_long_value(self):
        assert Side.LONG.value == "LONG"

    def test_short_value(self):
        assert Side.SHORT.value == "SHORT"

    def test_members(self):
        assert set(Side.__members__.keys()) == {"LONG", "SHORT"}

    def test_identity_with_config_reexport(self):
        """Side imported via config should be the same class as from models."""
        from src.core.config import Side as ConfigSide

        assert Side is ConfigSide

    def test_identity_with_init_reexport(self):
        """Side imported via __init__ should be the same class."""
        from src.core import Side as InitSide

        assert Side is InitSide


# ---------------------------------------------------------------------------
# OrderStatus enum
# ---------------------------------------------------------------------------


class TestOrderStatus:
    """Test the unified OrderStatus enum."""

    def test_all_values(self):
        expected = {
            "PENDING": "pending",
            "OPEN": "open",
            "FILLED": "filled",
            "PARTIALLY_FILLED": "partially_filled",
            "CANCELLED": "cancelled",
            "REJECTED": "rejected",
        }
        for name, value in expected.items():
            assert OrderStatus[name].value == value

    def test_member_count(self):
        assert len(OrderStatus) == 6

    def test_identity_with_config_reexport(self):
        from src.core.config import OrderStatus as ConfigOS

        assert OrderStatus is ConfigOS


# ---------------------------------------------------------------------------
# PositionStatus enum
# ---------------------------------------------------------------------------


class TestPositionStatus:
    """Test the unified PositionStatus enum."""

    def test_open_value(self):
        assert PositionStatus.OPEN.value == "open"

    def test_closed_value(self):
        assert PositionStatus.CLOSED.value == "closed"

    def test_member_count(self):
        assert len(PositionStatus) == 2

    def test_identity_with_strategy_imports(self):
        """PositionStatus used in strategies should be the same class."""
        from src.core.models import PositionStatus as ModelsPS

        assert PositionStatus is ModelsPS


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------


class TestPosition:
    """Test the Position dataclass."""

    def test_create_minimal(self):
        now = datetime.now()
        pos = Position(
            side=Side.LONG,
            entry_price=100.0,
            quantity=10.0,
            tp_price=105.0,
            sl_price=98.0,
            entry_time=now,
            leverage=5,
        )
        assert pos.side == Side.LONG
        assert pos.entry_price == 100.0
        assert pos.quantity == 10.0
        assert pos.tp_price == 105.0
        assert pos.sl_price == 98.0
        assert pos.entry_time == now
        assert pos.leverage == 5
        # Defaults
        assert pos.oid is None
        assert pos.cloid is None
        assert pos.status == OrderStatus.OPEN
        assert pos.unrealized_pnl == 0.0

    def test_create_with_all_fields(self):
        now = datetime.now()
        pos = Position(
            side=Side.SHORT,
            entry_price=200.0,
            quantity=5.0,
            tp_price=190.0,
            sl_price=210.0,
            entry_time=now,
            leverage=3,
            oid=42,
            cloid="my-order",
            status=OrderStatus.FILLED,
            unrealized_pnl=25.5,
        )
        assert pos.oid == 42
        assert pos.cloid == "my-order"
        assert pos.status == OrderStatus.FILLED
        assert pos.unrealized_pnl == 25.5

    def test_position_is_same_via_config(self):
        """Position imported from config should be the same class."""
        from src.core.config import Position as ConfigPos

        assert Position is ConfigPos

    def test_position_is_same_via_init(self):
        """Position imported from core __init__ should be the same class."""
        from src.core import Position as InitPos

        assert Position is InitPos


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------


class TestTrade:
    """Test the Trade dataclass."""

    def test_create_minimal(self):
        entry = datetime.now() - timedelta(hours=1)
        trade = Trade(
            side=Side.LONG,
            entry_price=100.0,
            exit_price=105.0,
            quantity=10.0,
            entry_time=entry,
        )
        assert trade.side == Side.LONG
        assert trade.entry_price == 100.0
        assert trade.exit_price == 105.0
        assert trade.quantity == 10.0
        assert trade.entry_time == entry
        # Defaults
        assert trade.exit_time is None
        assert trade.pnl == 0.0
        assert trade.fees == 0.0
        assert trade.notes == ""

    def test_create_with_all_fields(self):
        entry = datetime.now() - timedelta(hours=2)
        exit_ = datetime.now() - timedelta(hours=1)
        trade = Trade(
            side=Side.SHORT,
            entry_price=100.0,
            exit_price=95.0,
            quantity=20.0,
            entry_time=entry,
            exit_time=exit_,
            pnl=100.0,
            fees=4.0,
            notes="Great short setup",
        )
        assert trade.exit_time == exit_
        assert trade.pnl == 100.0
        assert trade.fees == 4.0
        assert trade.notes == "Great short setup"

    def test_trade_is_same_via_config(self):
        from src.core.config import Trade as ConfigTrade

        assert Trade is ConfigTrade


# ---------------------------------------------------------------------------
# Cross-module identity
# ---------------------------------------------------------------------------


class TestCrossModuleIdentity:
    """Ensure all import paths resolve to the exact same classes."""

    def test_side_identity_all_paths(self):
        from src.core.models import Side as ModelsSide
        from src.core.config import Side as ConfigSide
        from src.core import Side as InitSide

        assert ModelsSide is ConfigSide is InitSide

    def test_order_status_identity_all_paths(self):
        from src.core.models import OrderStatus as ModelsOS
        from src.core.config import OrderStatus as ConfigOS
        from src.core import OrderStatus as InitOS

        assert ModelsOS is ConfigOS is InitOS

    def test_position_status_available_from_init(self):
        from src.core import PositionStatus as InitPS
        from src.core.models import PositionStatus as ModelsPS

        assert InitPS is ModelsPS

    def test_position_identity_all_paths(self):
        from src.core.models import Position as ModelsPos
        from src.core.config import Position as ConfigPos
        from src.core import Position as InitPos

        assert ModelsPos is ConfigPos is InitPos

    def test_trade_identity_all_paths(self):
        from src.core.models import Trade as ModelsTrade
        from src.core.config import Trade as ConfigTrade
        from src.core import Trade as InitTrade

        assert ModelsTrade is ConfigTrade is InitTrade


# ---------------------------------------------------------------------------
# Enum usability
# ---------------------------------------------------------------------------


class TestEnumUsability:
    """Test that enums work correctly for common operations."""

    def test_side_comparison(self):
        assert Side.LONG == Side.LONG
        assert Side.LONG != Side.SHORT

    def test_side_from_value(self):
        assert Side("LONG") == Side.LONG
        assert Side("SHORT") == Side.SHORT

    def test_order_status_from_value(self):
        assert OrderStatus("filled") == OrderStatus.FILLED

    def test_position_status_from_value(self):
        assert PositionStatus("open") == PositionStatus.OPEN
        assert PositionStatus("closed") == PositionStatus.CLOSED

    def test_side_value_for_serialization(self):
        """Side.value should be usable for JSON/db serialization."""
        assert Side.LONG.value == "LONG"
        assert Side.SHORT.value == "SHORT"
        # Constructing back from value
        assert Side(Side.LONG.value) == Side.LONG

    def test_order_status_value_for_serialization(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus(OrderStatus.CANCELLED.value) == OrderStatus.CANCELLED
