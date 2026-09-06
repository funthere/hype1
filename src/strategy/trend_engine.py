"""
Walk-forward adapter for the trend-following signal logic.

Adapts :class:`TrendFollowingStrategy`'s indicator/signal layer to the
``update_candle``/``generate_signal`` engine contract that
:class:`~src.analytics.walk_forward.WalkForwardValidator` replays, so the
trend strategy can be validated out-of-sample exactly like the momentum
engine.

Deliberate simplification versus live behavior: sizing is flat
``POSITION_SIZE_PCT`` notional (no volatility-adjusted scaling against a
cross-coin median ATR — the harness replays one coin at a time), and exit
simulation is the harness's tp/sl/fold-end model rather than the live
trailing-stop ladder.
"""

import logging
from typing import Dict, Optional

import pandas as pd

from ..core.config import Side
from .trend_following import (
    TrendFollowingConfig,
    TrendFollowingStrategy,
    TrendPositionSide,
)

logger = logging.getLogger(__name__)

_FRAME_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class TrendFollowingEngine:
    """One-coin signal engine matching the walk-forward harness contract."""

    def __init__(
        self,
        config: Optional[TrendFollowingConfig] = None,
        max_rows: int = 200,
    ):
        self.config = config or TrendFollowingConfig()
        self._delegate = TrendFollowingStrategy(self.config, api=None, db=None)
        self._frame = pd.DataFrame(columns=_FRAME_COLUMNS)
        self._max_rows = max_rows

    def update_candle(self, candle: Dict) -> None:
        """Append one candle; keeps the most recent ``max_rows`` rows."""
        row = pd.DataFrame([{col: candle.get(col) for col in _FRAME_COLUMNS}])
        if self._frame.empty:
            self._frame = row
        else:
            self._frame = pd.concat([self._frame, row], ignore_index=True)
        self._frame = self._frame.drop_duplicates(subset="timestamp", keep="last")
        self._frame = self._frame.sort_values("timestamp").reset_index(drop=True)
        if len(self._frame) > self._max_rows:
            self._frame = self._frame.iloc[-self._max_rows :].reset_index(drop=True)

    def generate_signal(self, capital: float) -> Optional[Dict]:
        """Translate a trend signal into the harness's tp/sl/quantity shape."""
        min_rows = self.config.TREND_EMA_PERIOD + 10
        if len(self._frame) < min_rows:
            return None

        signal = self._delegate._analyze_trend(self.coin_label, self._frame)
        if signal is None:
            return None

        price = float(signal["price"])
        atr = float(signal["atr"])
        side = signal["side"]
        if side == TrendPositionSide.LONG:
            stop_loss = price - atr * self.config.ATR_STOP_MULT
            take_profit = price + atr * self.config.ATR_TP_MULT
        else:
            stop_loss = price + atr * self.config.ATR_STOP_MULT
            take_profit = price - atr * self.config.ATR_TP_MULT

        notional = capital * self.config.POSITION_SIZE_PCT
        quantity = round(notional / price, 6)
        if quantity <= 0:
            return None

        return {
            "action": Side(side.value),
            "confidence": signal["confidence"],
            "entry_price": price,
            "tp_price": take_profit,
            "sl_price": stop_loss,
            "quantity": quantity,
            "atr": atr,
        }

    @property
    def coin_label(self) -> str:
        """Label used only for logs inside the indicator layer."""
        return "WALKFORWARD"
