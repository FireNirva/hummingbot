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
        default=Decimal("1.2"),
        description="Price must be this % below EMA to ENTER downtrend (hysteresis entry)",
        json_schema_extra={"is_updatable": True},
    )
    ema_downtrend_exit_threshold_pct: Decimal = Field(
        default=Decimal("1.2"),
        description="Price must recover to within this % of EMA to EXIT downtrend (hysteresis exit). "
                    "Set equal to entry threshold for symmetric behavior, or lower for sticky downtrend.",
        json_schema_extra={"is_updatable": True},
    )
    regime_confirm_seconds: int = Field(
        default=7200,
        description="Seconds a new regime must persist before it is confirmed (debounce). "
                    "0 = instant switching. 7200 = 2 hours.",
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

    # --- Resume existing position on startup ---
    resume_existing_position: bool = Field(
        default=False,
        description="If True, on startup query chain for existing positions on this pool and resume managing them",
        json_schema_extra={"prompt": "Resume existing on-chain position on startup? (true/false):", "prompt_on_new": True},
    )


# Force Pydantic v2 to rebuild the validator schema so that fields added in this
# subclass are not rejected as "extra" when the parent uses extra='forbid'.
LpTrendRebalancerConfig.model_rebuild()


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

        # Regime state with hysteresis + debounce
        self._current_regime: MarketRegime = MarketRegime.UNKNOWN  # confirmed regime
        self._raw_regime: MarketRegime = MarketRegime.UNKNOWN  # instantaneous (pre-debounce)
        self._pending_regime: MarketRegime = MarketRegime.UNKNOWN  # candidate awaiting confirmation
        self._pending_regime_since: float = 0.0  # timestamp when pending regime started
        self._in_downtrend_hysteresis: bool = False  # hysteresis state for downtrend
        self._ema_value: Optional[Decimal] = None
        self._last_candle_close: Optional[Decimal] = None

        # Position timing
        self._position_open_timestamp: Optional[float] = None
        self._position_open_regime: Optional[MarketRegime] = None

        # Periodic status logging
        self._last_status_log_time: float = 0
        self._status_log_interval: float = 30  # log every 30 seconds

        # Resume: track whether we've already checked for existing positions
        self._resume_checked: bool = False
        self._resume_position_address: Optional[str] = None

    # ------------------------------------------------------------------
    # Override: update_processed_data — add EMA + regime detection + resume check
    # ------------------------------------------------------------------
    async def update_processed_data(self):
        # Call parent to update pool price
        await super().update_processed_data()

        # One-time check for existing on-chain positions to resume
        if self.config.resume_existing_position and not self._resume_checked:
            self._resume_checked = True
            await self._discover_existing_position()

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
                self._raw_regime = self._detect_regime_raw(self._last_candle_close, self._ema_value)
                self._apply_regime_debounce()
            else:
                self._current_regime = MarketRegime.UNKNOWN
        except Exception as e:
            self.logger().debug(f"EMA calculation error: {e}")
            self._current_regime = MarketRegime.UNKNOWN

    async def _discover_existing_position(self):
        """Query chain for existing positions on this pool. Store the best one for resume."""
        try:
            connector = self.market_data_provider.get_connector(self.config.connector_name)
            if not hasattr(connector, 'get_user_positions'):
                self.logger().warning("Connector does not support get_user_positions — resume disabled")
                return

            positions = await connector.get_user_positions(pool_address=self.config.pool_address)
            if not positions:
                self.logger().info("No existing positions found on chain for this pool")
                return

            # Pick the position with the highest total value (base * price + quote)
            best_pos = None
            best_value = Decimal("0")
            for pos in positions:
                value = Decimal(str(pos.base_token_amount)) * Decimal(str(pos.price)) + Decimal(str(pos.quote_token_amount))
                if value > best_value:
                    best_value = value
                    best_pos = pos

            if best_pos:
                self._resume_position_address = best_pos.address
                self.logger().info(
                    f"Found existing position to resume: {best_pos.address}, "
                    f"range=[{best_pos.lower_price:.2f}, {best_pos.upper_price:.2f}], "
                    f"value≈${best_value:.2f}"
                )
        except Exception as e:
            self.logger().warning(f"Failed to query existing positions: {e}")

    def _detect_regime_raw(self, price: Decimal, ema: Decimal) -> MarketRegime:
        """Detect regime with hysteresis on the downtrend threshold.

        Uses different thresholds for entering vs exiting downtrend to prevent
        flapping when price oscillates near the boundary.

        - Enter downtrend: deviation < -ema_downtrend_threshold_pct (e.g. -1.2%)
        - Exit downtrend:  deviation > -ema_downtrend_exit_threshold_pct (e.g. -1.2%)
        """
        if ema == 0:
            return MarketRegime.UNKNOWN
        deviation_pct = (price - ema) / ema * Decimal("100")

        if not self._in_downtrend_hysteresis:
            # Not in downtrend: need to cross entry threshold to enter
            if deviation_pct < -self.config.ema_downtrend_threshold_pct:
                self._in_downtrend_hysteresis = True
                return MarketRegime.DOWNTREND
            elif deviation_pct > self.config.ema_uptrend_threshold_pct:
                return MarketRegime.UPTREND
            else:
                return MarketRegime.SIDEWAYS
        else:
            # In downtrend: need to cross exit threshold to leave
            if deviation_pct > -self.config.ema_downtrend_exit_threshold_pct:
                self._in_downtrend_hysteresis = False
                if deviation_pct > self.config.ema_uptrend_threshold_pct:
                    return MarketRegime.UPTREND
                return MarketRegime.SIDEWAYS
            else:
                return MarketRegime.DOWNTREND

    def _apply_regime_debounce(self):
        """Apply debounce: require regime to persist for confirm_seconds before switching.

        This prevents rapid regime flips caused by price noise near thresholds.
        The drawdown stop-loss provides protection during the debounce period.
        """
        now = time.time()
        confirm_secs = self.config.regime_confirm_seconds

        if confirm_secs <= 0 or self._current_regime == MarketRegime.UNKNOWN:
            # No debounce, or first detection after EMA warmup — instant switching
            if self._raw_regime != self._current_regime:
                prev = self._current_regime
                self._current_regime = self._raw_regime
                self.logger().info(
                    f"Regime changed: {prev.value} → {self._current_regime.value} "
                    f"({'warmup complete' if prev == MarketRegime.UNKNOWN else 'instant'})"
                )
            return

        if self._raw_regime == self._current_regime:
            # Stable — reset pending
            self._pending_regime = self._current_regime
            self._pending_regime_since = now
            return

        if self._raw_regime != self._pending_regime:
            # New candidate — start timer
            self._pending_regime = self._raw_regime
            self._pending_regime_since = now
            return

        # Same candidate persisting — check if confirmed
        elapsed = now - self._pending_regime_since
        if elapsed >= confirm_secs:
            prev = self._current_regime
            self._current_regime = self._pending_regime
            self.logger().info(
                f"Regime confirmed: {prev.value} → {self._current_regime.value} "
                f"(held for {elapsed:.0f}s, threshold={confirm_secs}s)"
            )

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

        # --- Wait for resume discovery to complete before creating new positions ---
        if executor is None and self.config.resume_existing_position and not self._resume_checked:
            self.logger().debug("Waiting for position discovery before creating new position...")
            return []

        # --- Resume existing position on first run ---
        if executor is None and self._resume_position_address and self._current_executor_id is None:
            self.logger().info(f"Creating resume executor for position {self._resume_position_address}")
            resume_config = LPExecutorConfig(
                timestamp=self.market_data_provider.time(),
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair,
                pool_address=self.config.pool_address,
                lower_price=Decimal("0"),  # will be populated from chain
                upper_price=Decimal("0"),  # will be populated from chain
                base_amount=Decimal("0"),
                quote_amount=Decimal("0"),
                side=self.config.side,
                position_offset_pct=self.config.position_offset_pct,
                resume_position_address=self._resume_position_address,
                keep_position=False,
                reward_claim_interval_seconds=self.config.reward_claim_interval_seconds,
            )
            self._resume_position_address = None  # consumed
            return [CreateExecutorAction(
                controller_id=self.config.id,
                executor_config=resume_config,
            )]

        # --- Periodic status logging (must run before any early return) ---
        now = time.time()
        if now - self._last_status_log_time >= self._status_log_interval:
            self._last_status_log_time = now
            self._log_periodic_status(executor)

        # --- Idle in downtrend or unknown regime: block new position creation ---
        if executor is None and self.config.idle_in_downtrend and self._current_regime in (MarketRegime.DOWNTREND, MarketRegime.UNKNOWN):
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

    def _log_periodic_status(self, executor: Optional[ExecutorInfo]):
        """Log strategy status every _status_log_interval seconds."""
        ema_str = f"{float(self._ema_value):.2f}" if self._ema_value else "N/A"
        close_str = f"{float(self._last_candle_close):.2f}" if self._last_candle_close else "N/A"
        pool_str = f"{float(self._pool_price):.2f}" if self._pool_price else "N/A"

        if executor:
            custom = executor.custom_info
            state = custom.get("state", "UNKNOWN")
            pos_addr = custom.get("position_address", "N/A")
            current_price = custom.get("current_price", 0)
            lower = custom.get("lower_price", 0)
            upper = custom.get("upper_price", 0)
            total_value = custom.get("total_value_quote", 0)
            unrealized_pnl = custom.get("unrealized_pnl_quote", 0)
            base_fee = custom.get("base_fee", 0)
            quote_fee = custom.get("quote_fee", 0)
            rewards_claimed = custom.get("total_rewards_claimed", 0)
            rewards_usd = custom.get("total_rewards_claimed_usd", 0)
            rewards_count = custom.get("reward_claims_count", 0)

            duration_str = "N/A"
            if self._position_open_timestamp:
                elapsed_h = (time.time() - self._position_open_timestamp) / 3600
                max_h = self._get_max_duration_hours()
                duration_str = f"{elapsed_h:.1f}h / {max_h}h" if max_h else f"{elapsed_h:.1f}h"

            self.logger().info(
                f"[STATUS] regime={self._current_regime.value} | EMA({self.config.ema_period})={ema_str} "
                f"close={close_str} pool={pool_str} | "
                f"position={pos_addr} state={state} | "
                f"range=[{lower:.2f}, {upper:.2f}] price={current_price:.2f} | "
                f"value=${total_value:.2f} pnl=${unrealized_pnl:.2f} | "
                f"fees={base_fee:.6f}+{quote_fee:.6f} | "
                f"rewards={rewards_claimed:.6f} AERO (${rewards_usd:.2f}, {rewards_count} claims) | "
                f"duration={duration_str} | dd_limit=-{self.config.max_drawdown_pct}% | "
                f"reward_cfg={self.config.reward_claim_interval_seconds}"
            )
        else:
            debounce_info = ""
            if self._raw_regime != self._current_regime:
                elapsed = time.time() - self._pending_regime_since if self._pending_regime_since else 0
                debounce_info = (f" | pending={self._raw_regime.value} "
                                 f"({elapsed:.0f}s/{self.config.regime_confirm_seconds}s)")
            self.logger().info(
                f"[STATUS] regime={self._current_regime.value} | EMA({self.config.ema_period})={ema_str} "
                f"close={close_str} pool={pool_str} | "
                f"NO ACTIVE POSITION | idle_in_downtrend={self.config.idle_in_downtrend}"
                f"{debounce_info}"
            )

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


# Clean up parent class names from module namespace so that
# inspect.getmembers() in load_controller_configs() only finds
# LpTrendRebalancerConfig (the most-derived config class).
del LPRebalancer, LPRebalancerConfig
