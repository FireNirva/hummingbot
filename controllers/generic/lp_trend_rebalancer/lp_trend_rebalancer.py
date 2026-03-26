"""
LP Trend Rebalancer — extends LPRebalancer with EMA-based regime detection.

Implements the P3.5 strategy from LP Reverse Research:
- EMA 24h trend filter → 3 regimes (uptrend / sideways / downtrend)
- Dynamic range width per regime (wider in uptrend, tighter in sideways)
- Max position duration per regime
- Drawdown stop-loss
- Idle in downtrend (no position)

Inherits all LPRebalancer functionality:
- Auto position creation and rebalancing
- Price limit clamping
- Balance-aware sizing on rebalance
- Status visualization
"""
import logging
import time
from decimal import Decimal
from enum import Enum
from typing import List, Optional

import pandas as pd
from pydantic import Field

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig, LPExecutorStates
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

from ..lp_rebalancer.lp_rebalancer import LPRebalancer, LPRebalancerConfig


class MarketRegime(str, Enum):
    UPTREND = "uptrend"
    SIDEWAYS = "sideways"
    DOWNTREND = "downtrend"
    UNKNOWN = "unknown"


class LpTrendRebalancerConfig(LPRebalancerConfig):
    """
    Extends LPRebalancerConfig with EMA trend detection and regime-based parameters.

    P3.5 defaults:
    - EMA 24h on 1h candles (ema_period=24)
    - Uptrend: width 5000 ticks ≈ 40%, max 120h
    - Sideways: width 2000 ticks ≈ 18%, max 72h
    - Downtrend: idle (no position)
    - Drawdown stop: 8%
    """
    controller_name: str = "lp_trend_rebalancer"

    # --- Candle source for EMA ---
    candles_connector: str = Field(
        default="binance",
        json_schema_extra={"prompt": "Candle data connector (e.g. binance, gate_io):", "prompt_on_new": True},
    )
    candles_trading_pair: str = Field(
        default="ETH-USDT",
        json_schema_extra={"prompt": "Candle trading pair (e.g. ETH-USDT):", "prompt_on_new": True},
    )
    candles_interval: str = Field(
        default="1h",
        json_schema_extra={"prompt": "Candle interval (e.g. 1h, 4h):", "prompt_on_new": True},
    )

    # --- EMA regime detection ---
    ema_period: int = Field(
        default=24,
        json_schema_extra={"prompt": "EMA period (number of candles):", "prompt_on_new": True, "is_updatable": True},
    )
    ema_uptrend_threshold_pct: Decimal = Field(
        default=Decimal("0.5"),
        description="Price must be this % above EMA to count as uptrend",
        json_schema_extra={"is_updatable": True},
    )
    ema_downtrend_threshold_pct: Decimal = Field(
        default=Decimal("0.5"),
        description="Price must be this % below EMA to count as downtrend",
        json_schema_extra={"is_updatable": True},
    )

    # --- Regime-specific position width (override parent position_width_pct) ---
    uptrend_width_pct: Decimal = Field(
        default=Decimal("40"),
        description="Position width % in uptrend (P3.5: ~5000 ticks ≈ 40%)",
        json_schema_extra={"is_updatable": True},
    )
    sideways_width_pct: Decimal = Field(
        default=Decimal("18"),
        description="Position width % in sideways (P3.5: ~2000 ticks ≈ 18%)",
        json_schema_extra={"is_updatable": True},
    )

    # --- Max position duration per regime ---
    uptrend_max_duration_hours: Decimal = Field(
        default=Decimal("120"),
        description="Max hours to hold a position in uptrend before forced close",
        json_schema_extra={"is_updatable": True},
    )
    sideways_max_duration_hours: Decimal = Field(
        default=Decimal("72"),
        description="Max hours to hold a position in sideways before forced close",
        json_schema_extra={"is_updatable": True},
    )

    # --- Drawdown stop ---
    max_drawdown_pct: Decimal = Field(
        default=Decimal("8"),
        description="Close position if unrealized PnL drops below -X%",
        json_schema_extra={"is_updatable": True},
    )

    # --- Idle behavior ---
    idle_in_downtrend: bool = Field(
        default=True,
        description="If True, don't open positions in downtrend regime",
        json_schema_extra={"is_updatable": True},
    )


class LpTrendRebalancer(LPRebalancer):
    """
    LP controller with EMA-based trend detection.

    Adds on top of LPRebalancer:
    1. Regime detection via EMA (uptrend / sideways / downtrend)
    2. Dynamic position width based on regime
    3. Max position duration — force close after N hours
    4. Drawdown stop — force close if PnL < -X%
    5. Idle mode — no positions during downtrend
    """

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: LpTrendRebalancerConfig, *args, **kwargs):
        # Set up candles config before parent init (parent reads candles_config)
        if len(config.candles_config) == 0:
            config.candles_config = [CandlesConfig(
                connector=config.candles_connector,
                trading_pair=config.candles_trading_pair,
                interval=config.candles_interval,
                max_records=max(config.ema_period * 3, 100),
            )]

        # Set position_width_pct to sideways default BEFORE parent init
        # to prevent the inherited default (0.5%) from being used
        config.position_width_pct = config.sideways_width_pct

        super().__init__(config, *args, **kwargs)
        self.config: LpTrendRebalancerConfig = config

        # Regime state
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN
        self._ema_value: Optional[Decimal] = None
        self._last_candle_close: Optional[Decimal] = None

        # Position timing
        self._position_open_timestamp: Optional[float] = None
        self._position_open_regime: Optional[MarketRegime] = None

    # ------------------------------------------------------------------
    # Override: update_processed_data — add EMA + regime detection
    # ------------------------------------------------------------------
    async def update_processed_data(self):
        # Call parent to update pool price
        await super().update_processed_data()

        # Compute EMA from candle data
        try:
            df = self.market_data_provider.get_candles_df(
                connector_name=self.config.candles_connector,
                trading_pair=self.config.candles_trading_pair,
                interval=self.config.candles_interval,
                max_records=self.config.ema_period * 3,
            )
            if df is not None and len(df) >= self.config.ema_period:
                ema = df["close"].ewm(span=self.config.ema_period, adjust=False).mean()
                self._ema_value = Decimal(str(ema.iloc[-1]))
                self._last_candle_close = Decimal(str(df["close"].iloc[-1]))
                self._current_regime = self._detect_regime(self._last_candle_close, self._ema_value)
            else:
                self._current_regime = MarketRegime.UNKNOWN
        except Exception as e:
            self.logger().debug(f"EMA calculation error: {e}")
            self._current_regime = MarketRegime.UNKNOWN

    def _detect_regime(self, price: Decimal, ema: Decimal) -> MarketRegime:
        if ema == 0:
            return MarketRegime.UNKNOWN
        deviation_pct = (price - ema) / ema * Decimal("100")
        if deviation_pct > self.config.ema_uptrend_threshold_pct:
            return MarketRegime.UPTREND
        elif deviation_pct < -self.config.ema_downtrend_threshold_pct:
            return MarketRegime.DOWNTREND
        else:
            return MarketRegime.SIDEWAYS

    # ------------------------------------------------------------------
    # Override: determine_executor_actions — add regime/duration/drawdown
    # ------------------------------------------------------------------
    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions = []
        executor = self.active_executor()

        # --- Track position open time ---
        if executor and self._position_open_timestamp is None:
            state = executor.custom_info.get("state")
            if state in (LPExecutorStates.IN_RANGE.value, LPExecutorStates.OUT_OF_RANGE.value):
                self._position_open_timestamp = time.time()
                self._position_open_regime = self._current_regime
                self.logger().info(
                    f"Position opened — regime={self._current_regime.value}, "
                    f"width={self._get_regime_width_pct()}%"
                )

        # --- Check force-close conditions on active position ---
        if executor:
            force_close_reason = self._check_force_close(executor)
            if force_close_reason:
                self.logger().info(f"Force-closing position: {force_close_reason}")
                self._pending_rebalance = False
                self._position_open_timestamp = None
                self._position_open_regime = None
                return [StopExecutorAction(
                    controller_id=self.config.id,
                    executor_id=executor.id,
                    keep_position=False,
                )]

        # --- Idle in downtrend: block new position creation ---
        if executor is None and self.config.idle_in_downtrend and self._current_regime == MarketRegime.DOWNTREND:
            # Still let parent handle terminated executor cleanup
            if not self.is_tracked_executor_terminated():
                return []
            # Clear tracking so parent doesn't try to create position
            if self._current_executor_id:
                self._current_executor_id = None
            return []

        # --- Regime changed while no position: reset open tracking ---
        if executor is None:
            self._position_open_timestamp = None
            self._position_open_regime = None

        # --- Delegate to parent for normal create/rebalance logic ---
        # Override position_width_pct dynamically before parent runs
        self.config.position_width_pct = self._get_regime_width_pct()
        return super().determine_executor_actions()

    def _check_force_close(self, executor: ExecutorInfo) -> Optional[str]:
        """Check if position should be force-closed. Returns reason string or None."""
        state = executor.custom_info.get("state")
        if state in (LPExecutorStates.OPENING.value, LPExecutorStates.CLOSING.value, LPExecutorStates.COMPLETE.value):
            return None

        # 1. Drawdown stop
        pnl_pct = executor.custom_info.get("unrealized_pnl_quote")
        if pnl_pct is not None and self._position_open_timestamp is not None:
            # Calculate PnL % from executor's custom_info
            total_value = executor.custom_info.get("total_value_quote", 0)
            initial_base = executor.custom_info.get("initial_base_amount", 0)
            initial_quote = executor.custom_info.get("initial_quote_amount", 0)
            current_price = executor.custom_info.get("current_price", 0)
            if current_price and (initial_base or initial_quote):
                initial_value = float(initial_base) * float(current_price) + float(initial_quote)
                if initial_value > 0:
                    actual_pnl_pct = (float(total_value) - initial_value) / initial_value * 100
                    if actual_pnl_pct < -float(self.config.max_drawdown_pct):
                        return f"drawdown {actual_pnl_pct:.2f}% < -{self.config.max_drawdown_pct}%"

        # 2. Max duration
        if self._position_open_timestamp is not None:
            elapsed_hours = (time.time() - self._position_open_timestamp) / 3600
            max_hours = self._get_max_duration_hours()
            if max_hours and elapsed_hours > float(max_hours):
                return f"max duration {elapsed_hours:.1f}h > {max_hours}h ({self._position_open_regime_str()})"

        # 3. Regime changed to downtrend while holding
        if self.config.idle_in_downtrend and self._current_regime == MarketRegime.DOWNTREND:
            return f"regime changed to downtrend (was {self._position_open_regime_str()})"

        return None

    def _position_open_regime_str(self) -> str:
        return self._position_open_regime.value if self._position_open_regime else "unknown"

    def _get_regime_width_pct(self) -> Decimal:
        """Return position width % for current regime."""
        if self._current_regime == MarketRegime.UPTREND:
            return self.config.uptrend_width_pct
        elif self._current_regime == MarketRegime.SIDEWAYS:
            return self.config.sideways_width_pct
        else:
            # Unknown or downtrend — use sideways as default
            return self.config.sideways_width_pct

    def _get_max_duration_hours(self) -> Optional[Decimal]:
        """Return max duration for the regime the position was opened in."""
        regime = self._position_open_regime or self._current_regime
        if regime == MarketRegime.UPTREND:
            return self.config.uptrend_max_duration_hours
        elif regime == MarketRegime.SIDEWAYS:
            return self.config.sideways_max_duration_hours
        return None  # No limit for unknown

    # ------------------------------------------------------------------
    # Override: to_format_status — add regime info
    # ------------------------------------------------------------------
    def to_format_status(self) -> List[str]:
        status = super().to_format_status()

        # Insert regime info after the header (line 3)
        regime_lines = self._format_regime_status()
        # Find the first "+" separator after header and insert before it
        insert_idx = min(4, len(status))
        for line in reversed(regime_lines):
            status.insert(insert_idx, line)

        return status

    def _format_regime_status(self) -> List[str]:
        box_width = 100
        lines = []

        regime_icons = {
            MarketRegime.UPTREND: "▲",
            MarketRegime.SIDEWAYS: "▬",
            MarketRegime.DOWNTREND: "▼",
            MarketRegime.UNKNOWN: "?",
        }
        icon = regime_icons.get(self._current_regime, "?")

        ema_str = f"{float(self._ema_value):.2f}" if self._ema_value else "N/A"
        close_str = f"{float(self._last_candle_close):.2f}" if self._last_candle_close else "N/A"
        width_str = f"{self._get_regime_width_pct()}%"

        line = (
            f"| {icon} Regime: {self._current_regime.value.upper()} "
            f"| EMA({self.config.ema_period}): {ema_str} | Close: {close_str} | Width: {width_str}"
        )
        lines.append(line + " " * (box_width - len(line) + 1) + "|")

        # Position duration
        if self._position_open_timestamp:
            elapsed_h = (time.time() - self._position_open_timestamp) / 3600
            max_h = self._get_max_duration_hours()
            max_str = f"{max_h}h" if max_h else "N/A"
            line = f"| Duration: {elapsed_h:.1f}h / {max_str} | DD limit: -{self.config.max_drawdown_pct}%"
            lines.append(line + " " * (box_width - len(line) + 1) + "|")
        elif self.config.idle_in_downtrend and self._current_regime == MarketRegime.DOWNTREND:
            line = "| IDLE — waiting for uptrend/sideways to open position"
            lines.append(line + " " * (box_width - len(line) + 1) + "|")

        return lines
