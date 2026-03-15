import asyncio
import json
import math
import os
import re
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, TextIO, Tuple

from pydantic import Field, field_validator

from hummingbot import prefix_path
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.core.utils.fixed_rate_source import FixedRateSource
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


@dataclass
class DexQuoteSnapshot:
    price: Decimal
    timestamp: float
    reference_cex_price: Decimal
    trigger_source: str
    refresh_reason: str
    latency_ms: Optional[float] = None
    quote_id: Optional[str] = None


@dataclass
class QuoteRefreshDecision:
    should_refresh: bool
    trigger_source: str
    trigger_reason: str
    cex_move_pct: Optional[Decimal] = None
    dex_event_signal: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class DexWatchMarket:
    connector_name: str
    trading_pair: str
    pool_address: Optional[str] = None

    @property
    def watch_key(self) -> str:
        if self.pool_address:
            return f"{self.connector_name}|{self.trading_pair}|{self.pool_address}"
        return f"{self.connector_name}|{self.trading_pair}"


@dataclass
class DexWatchSnapshot:
    watch_key: str
    connector_name: str
    trading_pair: str
    price: Decimal
    base_token_amount: Decimal
    quote_token_amount: Decimal
    timestamp: float


@dataclass
class ExecutorObservation:
    opportunity_id: str
    direction: str
    expected_profit: Decimal
    amount: Decimal
    trigger_source: str
    refresh_reason: str
    created_timestamp: float


class AggregatorCexDexArbConfig(StrategyV2ConfigBase):
    """
    Aggregator-friendly CEX-DEX arbitrage strategy.

    This strategy is designed for router / aggregator connectors such as:
    - 0x/router
    - uniswap/router
    - kyberswap/router (when available)
    - paraswap/router (when available)
    - odos/router (when available)

    Phase 1 goals:
    - Use cheap CEX data every tick
    - Reuse recent DEX quotes instead of re-querying every cycle
    - Apply quote cooldown and per-minute budget for external aggregators
    - Trigger expensive DEX quotes only when the estimated edge is near the threshold
    """

    script_file_name: str = os.path.basename(__file__)
    markets: Dict[str, Set[str]] = {}
    candles_config: List = []
    controllers_config: List[str] = []

    cex_connector: str = Field(default="gate_io", description="CEX connector name")
    cex_trading_pair: str = Field(default="BRETT-USDT", description="CEX trading pair")
    dex_connector: str = Field(default="0x/router", description="DEX / router connector name")
    dex_trading_pair: str = Field(default="BRETT-USDC", description="DEX / router trading pair")

    order_amount: Decimal = Field(default=Decimal("5000"), gt=0, description="Fixed base order amount")
    min_profitability: Decimal = Field(default=Decimal("0.003"), gt=0, description="Minimum net profitability")

    cex_fee_rate: Decimal = Field(default=Decimal("0.002"), ge=0, description="Estimated CEX taker fee")
    dex_fee_rate: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        description="Optional extra DEX percent fee. Keep at 0 when router quotes are already net prices.",
    )

    gas_token_price_quote: Decimal = Field(
        default=Decimal("2000"),
        gt=0,
        description="Deprecated alias for gas_price. Gas token price denominated in the CEX quote asset.",
    )
    rate_oracle_enabled: bool = Field(
        default=True,
        description="Use the global Hummingbot rate oracle for quote and gas conversions.",
    )
    quote_conversion_rate: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        description="DEX quote asset to CEX quote asset conversion rate",
    )
    gas_token: str = Field(default="ETH", description="Native gas token symbol")
    gas_price: Optional[Decimal] = Field(
        default=None,
        gt=0,
        description="Fixed gas token price in the CEX quote asset. Used when rate_oracle_enabled is false.",
    )

    max_concurrent_executors: int = Field(default=1, ge=1)
    executor_concurrent_orders_submission: bool = Field(
        default=False,
        description="Submit both arbitrage legs concurrently. Disable to submit sequentially.",
    )
    executor_prioritize_non_amm_first: bool = Field(
        default=True,
        description="When sequential, place the non-gateway / non-AMM leg first.",
    )
    executor_retry_failed_orders: bool = Field(
        default=False,
        description="Retry failed arbitrage leg orders from the executor.",
    )
    no_opportunity_log_interval: int = Field(default=30, ge=0)

    aggregator_quote_cooldown_sec: Decimal = Field(
        default=Decimal("5"),
        ge=0,
        description="Minimum seconds between quote refreshes for the same DEX direction",
    )
    aggregator_quote_ttl_sec: Decimal = Field(
        default=Decimal("20"),
        gt=0,
        description="Max age for a cached DEX quote before it is considered stale",
    )
    aggregator_quote_budget_per_minute: int = Field(
        default=24,
        ge=1,
        description="Maximum number of external DEX quote requests per rolling minute",
    )
    quote_trigger_buffer_pct: Decimal = Field(
        default=Decimal("0.002"),
        ge=0,
        description="Refresh DEX quotes when estimated edge is within this buffer below threshold",
    )
    cex_price_move_trigger_pct: Decimal = Field(
        default=Decimal("0.0015"),
        ge=0,
        description="Refresh DEX quote if the relevant CEX price moved by at least this amount",
    )
    dex_event_trigger_enabled: bool = Field(
        default=False,
        description="Enable DEX watcher polling and pool-change-triggered quote refreshes.",
    )
    dex_event_watch_markets: List[str] = Field(
        default_factory=list,
        description="Watch pools in format connector|trading_pair or connector|trading_pair|pool_address.",
    )
    dex_event_poll_interval_sec: Decimal = Field(
        default=Decimal("3"),
        gt=0,
        description="Minimum seconds between watcher polls for DEX event detection.",
    )
    dex_event_price_move_trigger_pct: Decimal = Field(
        default=Decimal("0.003"),
        ge=0,
        description="Trigger DEX event refresh when watcher pool price moves by at least this amount.",
    )
    dex_event_base_amount_move_trigger_pct: Decimal = Field(
        default=Decimal("0.05"),
        ge=0,
        description="Trigger DEX event refresh when watcher pool base liquidity changes by at least this amount.",
    )
    dex_event_quote_amount_move_trigger_pct: Decimal = Field(
        default=Decimal("0.05"),
        ge=0,
        description="Trigger DEX event refresh when watcher pool quote liquidity changes by at least this amount.",
    )
    phase0_event_logging_enabled: bool = Field(
        default=True,
        description="Enable Phase 0 structured jsonl event logging.",
    )
    phase0_event_log_directory: str = Field(
        default="",
        description="Optional directory for Phase 0 jsonl event logs. Uses logs/strategy_metrics by default.",
    )
    phase0_event_log_file_prefix: str = Field(
        default="v2_cex_dex_aggregator_arb",
        description="File prefix for Phase 0 jsonl event logs.",
    )
    phase0_metrics_summary_interval_sec: int = Field(
        default=60,
        ge=0,
        description="Seconds between Phase 0 metrics summary logs. Set to 0 to disable summaries.",
    )
    circuit_breaker_enabled: bool = Field(
        default=True,
        description="Enable automatic shutdown when repeated quote failures indicate a request storm.",
    )
    circuit_breaker_trip_on_vendor_429: bool = Field(
        default=True,
        description="Stop immediately when a vendor throttling / rate-limit error is observed.",
    )
    circuit_breaker_trip_on_network_error: bool = Field(
        default=True,
        description="Stop after repeated network errors while polling DEX quotes or watcher pools.",
    )
    circuit_breaker_network_error_threshold: int = Field(
        default=6,
        ge=1,
        description="Trip after this many consecutive network errors.",
    )
    circuit_breaker_empty_quote_threshold: int = Field(
        default=12,
        ge=1,
        description="Trip after this many consecutive empty quote responses.",
    )
    circuit_breaker_quote_failure_threshold: int = Field(
        default=12,
        ge=1,
        description="Trip after this many consecutive quote refresh failures of any kind.",
    )
    circuit_breaker_order_failure_threshold: int = Field(
        default=3,
        ge=1,
        description="Trip after this many consecutive DEX order failures.",
    )

    @field_validator(
        "order_amount",
        "min_profitability",
        "cex_fee_rate",
        "dex_fee_rate",
        "gas_token_price_quote",
        "gas_price",
        "quote_conversion_rate",
        "aggregator_quote_cooldown_sec",
        "aggregator_quote_ttl_sec",
        "quote_trigger_buffer_pct",
        "cex_price_move_trigger_pct",
        "dex_event_poll_interval_sec",
        "dex_event_price_move_trigger_pct",
        "dex_event_base_amount_move_trigger_pct",
        "dex_event_quote_amount_move_trigger_pct",
    )
    @classmethod
    def quantize_decimal_fields(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def parse_watch_market_entries(entries: List[str]) -> List[DexWatchMarket]:
        watch_markets: List[DexWatchMarket] = []
        for raw_entry in entries:
            entry = raw_entry.strip()
            if not entry:
                continue
            parts = [part.strip() for part in entry.split("|")]
            if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
                raise ValueError(
                    f"Invalid dex_event_watch_markets entry '{raw_entry}'. "
                    "Expected format connector|trading_pair or connector|trading_pair|pool_address."
                )
            connector_name, trading_pair = parts[0], parts[1]
            pool_address = parts[2] if len(parts) == 3 and parts[2] else None
            watch_markets.append(
                DexWatchMarket(
                    connector_name=connector_name,
                    trading_pair=trading_pair,
                    pool_address=pool_address,
                )
            )
        return watch_markets


class AggregatorCexDexArb(StrategyV2Base):
    BUY_DEX_SELL_CEX = "buy_dex_sell_cex"
    BUY_CEX_SELL_DEX = "buy_cex_sell_dex"

    def __init__(self, connectors: Dict[str, ConnectorBase], config: AggregatorCexDexArbConfig):
        if len(config.markets) == 0:
            config.markets = {
                config.cex_connector: {config.cex_trading_pair},
                config.dex_connector: {config.dex_trading_pair},
            }
            if config.dex_event_trigger_enabled:
                for watch_market in config.parse_watch_market_entries(config.dex_event_watch_markets):
                    config.markets.setdefault(watch_market.connector_name, set()).add(watch_market.trading_pair)
        super().__init__(connectors, config)
        self.config = config

        self._pending_create_action: Optional[CreateExecutorAction] = None
        self._evaluation_task: Optional[asyncio.Task] = None
        self._evaluation_lock = asyncio.Lock()

        self._cex_base, self._cex_quote = split_hb_trading_pair(config.cex_trading_pair)
        dex_base, self._dex_quote = split_hb_trading_pair(config.dex_trading_pair)
        if dex_base != self._cex_base:
            raise ValueError("CEX and DEX trading pairs must share the same base asset.")

        self._cex_fee_rate = Decimal(str(config.cex_fee_rate))
        self._dex_fee_rate = Decimal(str(config.dex_fee_rate))
        self._rate_oracle_enabled = bool(config.rate_oracle_enabled)
        self._quote_conversion_rate = Decimal(str(config.quote_conversion_rate))
        resolved_gas_price = config.gas_price if config.gas_price is not None else config.gas_token_price_quote
        self._gas_token_price_quote = Decimal(str(resolved_gas_price))
        self._gas_conversion_ratio = Decimal("1") / self._gas_token_price_quote

        self._quote_cache: Dict[str, DexQuoteSnapshot] = {}
        self._last_quote_request_ts: Dict[str, float] = {}
        self._quote_request_timestamps: Deque[float] = deque()
        self._last_no_opportunity_log = 0.0
        self._last_budget_log = 0.0
        self._last_metrics_summary_log = 0.0
        self._dex_watch_markets: List[DexWatchMarket] = self.config.parse_watch_market_entries(
            self.config.dex_event_watch_markets
        )
        self._dex_watch_snapshots: Dict[str, DexWatchSnapshot] = {}
        self._last_dex_watch_poll_ts = 0.0
        self._dex_event_generation = 0
        self._latest_dex_event_signal: Optional[Dict[str, Any]] = None
        self._last_consumed_dex_event_generation: Dict[str, int] = {
            self.BUY_DEX_SELL_CEX: 0,
            self.BUY_CEX_SELL_DEX: 0,
        }
        self._last_reported_dex_event_generation: Dict[str, int] = {
            self.BUY_DEX_SELL_CEX: 0,
            self.BUY_CEX_SELL_DEX: 0,
        }

        self._metrics: Counter = Counter()
        self._trigger_source_counts: Counter = Counter()
        self._trigger_reason_counts: Counter = Counter()
        self._close_type_counts: Counter = Counter()
        self._quote_latency_ms_total = 0.0
        self._quote_latency_samples = 0
        self._estimate_realized_delta_abs_total = Decimal("0")
        self._estimate_realized_delta_samples = 0
        self._executor_observations: Dict[str, ExecutorObservation] = {}
        self._executor_status_cache: Dict[str, Tuple[str, Optional[str]]] = {}
        self._closed_executor_ids: Set[str] = set()
        self._consecutive_quote_failures = 0
        self._consecutive_empty_quotes = 0
        self._consecutive_network_errors = 0
        self._consecutive_order_failures = 0
        self._circuit_breaker_active = False
        self._circuit_breaker_reason: Optional[str] = None
        self._circuit_breaker_context: Optional[Dict[str, Any]] = None

        self._event_log_path: Optional[Path] = None
        self._event_log_handle: Optional[TextIO] = None
        self._event_log_failure_reported = False
        self._event_pairs: List[Tuple[MarketEvent, SourceInfoEventForwarder]] = []
        self._listeners_registered = False

        self._rate_source = RateOracle.get_instance() if self._rate_oracle_enabled else FixedRateSource()
        self._setup_rate_source()
        self._initialize_phase0_observability()

    @classmethod
    def init_markets(cls, config: AggregatorCexDexArbConfig):
        markets = {
            config.cex_connector: {config.cex_trading_pair},
            config.dex_connector: {config.dex_trading_pair},
        }
        if config.dex_event_trigger_enabled:
            for watch_market in config.parse_watch_market_entries(config.dex_event_watch_markets):
                markets.setdefault(watch_market.connector_name, set()).add(watch_market.trading_pair)
        cls.markets = markets

    def on_tick(self):
        super().on_tick()
        self.update_executors_info()
        self._track_executor_lifecycle()
        self._maybe_emit_metrics_summary()
        if self._is_stop_triggered:
            return
        self._execute_local_executor_actions()
        self.update_executors_info()
        self._track_executor_lifecycle()
        self._maybe_emit_metrics_summary()
        if self._has_active_executor():
            return
        if self._evaluation_task is None or self._evaluation_task.done():
            self._evaluation_task = asyncio.create_task(self._evaluate_and_queue_action())

    async def on_stop(self):
        await super().on_stop()
        self.update_executors_info()
        self._track_executor_lifecycle()
        self._emit_event("strategy_stop", metrics=self._build_metrics_summary_payload())
        self._maybe_emit_metrics_summary(force=True)
        self._unregister_market_event_listeners()
        self._close_event_log()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        if self._pending_create_action is not None:
            action = self._pending_create_action
            self._pending_create_action = None
            return [action]
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        return []

    def _has_active_executor(self) -> bool:
        live_executor_count = 0
        for executor in self.get_all_executors():
            status = executor.status.name if isinstance(executor.status, Enum) else str(executor.status)
            if status != RunnableStatus.TERMINATED.name:
                live_executor_count += 1
        return live_executor_count >= self.config.max_concurrent_executors

    def _execute_local_executor_actions(self):
        actions = self.determine_executor_actions()
        if not actions:
            return
        self._dispatch_executor_actions(actions)

    def _dispatch_executor_actions(self, actions: List[Any]):
        if not actions:
            return
        for action in actions:
            if isinstance(action, CreateExecutorAction):
                observation = self._executor_observations.get(action.executor_config.id)
                self._metrics["executors_created"] += 1
                self._emit_event(
                    "executor_created",
                    executor_id=action.executor_config.id,
                    controller_id=action.controller_id,
                    opportunity_id=observation.opportunity_id if observation else None,
                    direction=observation.direction if observation else None,
                    expected_profit_pct=observation.expected_profit if observation else None,
                    amount=observation.amount if observation else None,
                    trigger_source=observation.trigger_source if observation else None,
                    refresh_reason=observation.refresh_reason if observation else None,
                    buying_connector=action.executor_config.buying_market.connector_name,
                    buying_trading_pair=action.executor_config.buying_market.trading_pair,
                    selling_connector=action.executor_config.selling_market.connector_name,
                    selling_trading_pair=action.executor_config.selling_market.trading_pair,
                )
            elif isinstance(action, StopExecutorAction):
                self._emit_event(
                    "executor_stop_requested",
                    executor_id=action.executor_id,
                    controller_id=action.controller_id,
                    keep_position=action.keep_position,
                )
        self.executor_orchestrator.execute_actions(actions)

    async def _evaluate_and_queue_action(self):
        if self._evaluation_lock.locked():
            return
        async with self._evaluation_lock:
            self._metrics["cheap_scout_cycles"] += 1
            amount = self._quantize_amount(self.config.order_amount)
            if amount <= Decimal("0"):
                return

            await self._poll_dex_watchers_if_needed()

            cex_prices = self._get_cex_vwap_prices(amount)
            if cex_prices is None:
                self._metrics["opportunities_skipped"] += 1
                self._emit_event(
                    "opportunity_skipped",
                    reason="missing_cex_vwap",
                    amount=amount,
                )
                return
            cex_ask, cex_bid = cex_prices

            profit_map = self._estimate_profit_map(amount, cex_ask, cex_bid)
            await self._refresh_quotes_if_needed(amount, cex_ask, cex_bid, profit_map)
            profit_map = self._estimate_profit_map(amount, cex_ask, cex_bid)

            best_direction, best_profit = self._select_best_direction(profit_map)
            self._maybe_log_status(profit_map, best_direction, best_profit, amount)

            if best_direction is None or best_profit is None or best_profit < self.config.min_profitability:
                self._metrics["opportunities_skipped"] += 1
                skip_reason = "no_valid_dex_quote" if best_profit is None or best_direction is None else "below_threshold"
                self._emit_event(
                    "opportunity_skipped",
                    reason=skip_reason,
                    amount=amount,
                    best_direction=best_direction,
                    best_profit_pct=best_profit,
                    threshold_pct=self.config.min_profitability,
                    profit_map=profit_map,
                    cex_ask=cex_ask,
                    cex_bid=cex_bid,
                )
                return

            snapshot = self._quote_cache.get(best_direction)
            opportunity_id = f"opp-{uuid.uuid4().hex[:12]}"
            budget_ok, budget_context = self._passes_budget_constraint(
                direction=best_direction,
                amount=amount,
                cex_ask=cex_ask,
                cex_bid=cex_bid,
                dex_snapshot=snapshot,
            )
            if not budget_ok:
                self._metrics["opportunities_skipped"] += 1
                self.logger().info(budget_context["message"])
                self._emit_event(
                    "opportunity_skipped",
                    reason="insufficient_balance",
                    amount=amount,
                    best_direction=best_direction,
                    best_profit_pct=best_profit,
                    threshold_pct=self.config.min_profitability,
                    cex_ask=cex_ask,
                    cex_bid=cex_bid,
                    balance_context=budget_context,
                )
                return
            self._metrics["opportunities_detected"] += 1
            self._emit_event(
                "opportunity_detected",
                opportunity_id=opportunity_id,
                direction=best_direction,
                expected_profit_pct=best_profit,
                amount=amount,
                cex_ask=cex_ask,
                cex_bid=cex_bid,
                dex_quote_price=snapshot.price if snapshot else None,
                dex_quote_age_sec=self._snapshot_age(snapshot),
                trigger_source=snapshot.trigger_source if snapshot else None,
                refresh_reason=snapshot.refresh_reason if snapshot else None,
                dex_quote_latency_ms=snapshot.latency_ms if snapshot else None,
                dex_quote_id=snapshot.quote_id if snapshot else None,
            )
            executor_config = self._build_executor_config(best_direction, amount)
            self._executor_observations[executor_config.id] = ExecutorObservation(
                opportunity_id=opportunity_id,
                direction=best_direction,
                expected_profit=best_profit,
                amount=amount,
                trigger_source=snapshot.trigger_source if snapshot else "unknown",
                refresh_reason=snapshot.refresh_reason if snapshot else "unknown",
                created_timestamp=self._now(),
            )
            self.logger().info(
                "Triggering V2 arbitrage executor: direction=%s amount=%s expected_net_profit=%s%%",
                best_direction,
                self._format_decimal(amount),
                self._format_pct(best_profit),
            )
            if self._has_active_executor():
                return
            self._dispatch_executor_actions([CreateExecutorAction(executor_config=executor_config)])

    def _quantize_amount(self, amount: Decimal) -> Decimal:
        quantized = self.market_data_provider.quantize_order_amount(
            self.config.cex_connector, self.config.cex_trading_pair, amount
        )
        quantized = self.market_data_provider.quantize_order_amount(
            self.config.dex_connector, self.config.dex_trading_pair, quantized
        )
        return quantized

    def _get_cex_vwap_prices(self, amount: Decimal) -> Optional[Tuple[Decimal, Decimal]]:
        try:
            connector = self.connectors[self.config.cex_connector]
            ask = Decimal(str(connector.get_vwap_for_volume(self.config.cex_trading_pair, True, amount).result_price))
            bid = Decimal(str(connector.get_vwap_for_volume(self.config.cex_trading_pair, False, amount).result_price))
        except Exception as exc:
            self.logger().warning(f"Could not get CEX VWAP prices: {exc}")
            if self._is_network_error(exc):
                self._record_network_error(
                    source="cex_vwap",
                    error=exc,
                    context={
                        "connector": self.config.cex_connector,
                        "trading_pair": self.config.cex_trading_pair,
                        "amount": amount,
                    },
                )
            return None
        if ask <= Decimal("0") or bid <= Decimal("0"):
            return None
        self._reset_network_error_streak()
        return ask, bid

    def _setup_rate_source(self):
        if self._rate_oracle_enabled:
            return
        self._add_rate_pair(self._dex_quote, self._cex_quote, self._quote_conversion_rate)
        gas_token = getattr(self.config, "gas_token", "ETH")
        gas_to_quote = self._gas_token_price_quote
        self._add_rate_pair(gas_token, self._cex_quote, gas_to_quote)
        if self._dex_quote != self._cex_quote:
            dex_rate = gas_to_quote / self._quote_conversion_rate
            self._add_rate_pair(gas_token, self._dex_quote, dex_rate)

    def _add_rate_pair(self, base: str, quote: str, rate: Decimal):
        if rate <= Decimal("0"):
            return
        self._rate_source.add_rate(f"{base}-{quote}", rate)
        self._rate_source.add_rate(f"{quote}-{base}", Decimal("1") / rate)

    async def _poll_dex_watchers_if_needed(self):
        if not self.config.dex_event_trigger_enabled or not self._dex_watch_markets:
            return
        now = self._now()
        if self._last_dex_watch_poll_ts and (now - self._last_dex_watch_poll_ts) < float(self.config.dex_event_poll_interval_sec):
            return
        self._last_dex_watch_poll_ts = now
        self._metrics["dex_watch_polls"] += 1

        tasks = [self._fetch_dex_watch_snapshot(watch_market) for watch_market in self._dex_watch_markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        triggered_signals: List[Dict[str, Any]] = []
        for watch_market, result in zip(self._dex_watch_markets, results):
            if isinstance(result, Exception):
                self._metrics["dex_watch_errors"] += 1
                self._record_network_error(
                    source="dex_watch_poll",
                    error=result,
                    context={
                        "watch_key": watch_market.watch_key,
                        "connector": watch_market.connector_name,
                        "trading_pair": watch_market.trading_pair,
                        "pool_address": watch_market.pool_address,
                    },
                )
                self._emit_event(
                    "dex_watch_poll",
                    status="error",
                    watch_key=watch_market.watch_key,
                    connector=watch_market.connector_name,
                    trading_pair=watch_market.trading_pair,
                    pool_address=watch_market.pool_address,
                    error=str(result),
                )
                continue

            snapshot = result
            self._reset_network_error_streak()
            if snapshot is None:
                self._emit_event(
                    "dex_watch_poll",
                    status="empty",
                    watch_key=watch_market.watch_key,
                    connector=watch_market.connector_name,
                    trading_pair=watch_market.trading_pair,
                    pool_address=watch_market.pool_address,
                )
                continue

            previous_snapshot = self._dex_watch_snapshots.get(snapshot.watch_key)
            self._dex_watch_snapshots[snapshot.watch_key] = snapshot
            if previous_snapshot is None:
                self._emit_event(
                    "dex_watch_poll",
                    status="bootstrap",
                    watch_key=snapshot.watch_key,
                    connector=snapshot.connector_name,
                    trading_pair=snapshot.trading_pair,
                    pool_price=snapshot.price,
                    base_token_amount=snapshot.base_token_amount,
                    quote_token_amount=snapshot.quote_token_amount,
                )
                continue

            price_move_pct = self._relative_move_pct(snapshot.price, previous_snapshot.price)
            base_amount_move_pct = self._relative_move_pct(snapshot.base_token_amount, previous_snapshot.base_token_amount)
            quote_amount_move_pct = self._relative_move_pct(snapshot.quote_token_amount, previous_snapshot.quote_token_amount)

            should_trigger = (
                price_move_pct >= self.config.dex_event_price_move_trigger_pct
                or base_amount_move_pct >= self.config.dex_event_base_amount_move_trigger_pct
                or quote_amount_move_pct >= self.config.dex_event_quote_amount_move_trigger_pct
            )
            if not should_trigger:
                continue

            signal = {
                "watch_key": snapshot.watch_key,
                "connector": snapshot.connector_name,
                "trading_pair": snapshot.trading_pair,
                "pool_price": snapshot.price,
                "previous_pool_price": previous_snapshot.price,
                "price_move_pct": price_move_pct,
                "base_token_amount": snapshot.base_token_amount,
                "previous_base_token_amount": previous_snapshot.base_token_amount,
                "base_amount_move_pct": base_amount_move_pct,
                "quote_token_amount": snapshot.quote_token_amount,
                "previous_quote_token_amount": previous_snapshot.quote_token_amount,
                "quote_amount_move_pct": quote_amount_move_pct,
                "timestamp": snapshot.timestamp,
            }
            triggered_signals.append(signal)
            self._emit_event("dex_watch_trigger", **signal)

        if triggered_signals:
            self._metrics["dex_event_trigger_batches"] += 1
            self._metrics["dex_event_trigger_signals"] += len(triggered_signals)
            dominant_signal = max(
                triggered_signals,
                key=lambda signal: max(
                    Decimal(str(signal["price_move_pct"])),
                    Decimal(str(signal["base_amount_move_pct"])),
                    Decimal(str(signal["quote_amount_move_pct"])),
                ),
            )
            self._dex_event_generation += 1
            self._latest_dex_event_signal = {
                **dominant_signal,
                "generation": self._dex_event_generation,
            }

    async def _fetch_dex_watch_snapshot(self, watch_market: DexWatchMarket) -> Optional[DexWatchSnapshot]:
        connector = self.connectors.get(watch_market.connector_name)
        if connector is None:
            raise ValueError(f"Watcher connector {watch_market.connector_name} is not available.")

        if watch_market.pool_address and hasattr(connector, "get_pool_info_by_address"):
            pool_info = await connector.get_pool_info_by_address(watch_market.pool_address)
        elif hasattr(connector, "get_pool_info"):
            pool_info = await connector.get_pool_info(watch_market.trading_pair)
        else:
            raise ValueError(
                f"Connector {watch_market.connector_name} does not support pool info lookups for DEX event watching."
            )

        if pool_info is None:
            return None

        return DexWatchSnapshot(
            watch_key=watch_market.watch_key,
            connector_name=watch_market.connector_name,
            trading_pair=watch_market.trading_pair,
            price=Decimal(str(getattr(pool_info, "price"))),
            base_token_amount=Decimal(str(getattr(pool_info, "base_token_amount"))),
            quote_token_amount=Decimal(str(getattr(pool_info, "quote_token_amount"))),
            timestamp=self._now(),
        )

    def _relative_move_pct(self, current: Decimal, previous: Decimal) -> Decimal:
        if previous <= Decimal("0"):
            return Decimal("0")
        return abs(current - previous) / previous

    def _get_pending_dex_event_signal(self, direction: str) -> Optional[Dict[str, Any]]:
        signal = self._latest_dex_event_signal
        if signal is None:
            return None
        generation = int(signal.get("generation", 0))
        if generation <= self._last_consumed_dex_event_generation.get(direction, 0):
            return None
        return signal

    def _mark_dex_event_consumed(self, direction: str, signal: Optional[Dict[str, Any]]):
        if signal is None:
            return
        generation = int(signal.get("generation", 0))
        if generation > 0:
            self._last_consumed_dex_event_generation[direction] = generation

    def _estimate_profit_map(self, amount: Decimal, cex_ask: Decimal, cex_bid: Decimal) -> Dict[str, Decimal]:
        profit_map: Dict[str, Decimal] = {}
        dex_buy = self._quote_cache.get(self.BUY_DEX_SELL_CEX)
        dex_sell = self._quote_cache.get(self.BUY_CEX_SELL_DEX)

        dex_flat_fee_quote = self._convert_flat_fee_to_cex_quote()

        if dex_buy is not None:
            dex_buy_price_cex_quote = self._convert_price_to_cex_quote(dex_buy.price)
            if dex_buy_price_cex_quote is None or dex_buy_price_cex_quote <= Decimal("0"):
                dex_buy_price_cex_quote = None
            else:
                dex_buy_price_cex_quote = Decimal(str(dex_buy_price_cex_quote))
        else:
            dex_buy_price_cex_quote = None

        if dex_buy is not None and dex_buy_price_cex_quote is not None:
            total_cost = amount * dex_buy_price_cex_quote
            total_cost += total_cost * self._dex_fee_rate
            total_cost += dex_flat_fee_quote

            total_revenue = amount * cex_bid
            total_revenue -= total_revenue * self._cex_fee_rate
            if total_cost > Decimal("0"):
                profit_map[self.BUY_DEX_SELL_CEX] = (total_revenue - total_cost) / total_cost

        if dex_sell is not None:
            dex_sell_price_cex_quote = self._convert_price_to_cex_quote(dex_sell.price)
            if dex_sell_price_cex_quote is None or dex_sell_price_cex_quote <= Decimal("0"):
                dex_sell_price_cex_quote = None
            else:
                dex_sell_price_cex_quote = Decimal(str(dex_sell_price_cex_quote))
        else:
            dex_sell_price_cex_quote = None

        if dex_sell is not None and dex_sell_price_cex_quote is not None:
            total_cost = amount * cex_ask
            total_cost += total_cost * self._cex_fee_rate

            total_revenue = amount * dex_sell_price_cex_quote
            total_revenue -= total_revenue * self._dex_fee_rate
            total_revenue -= dex_flat_fee_quote
            if total_cost > Decimal("0"):
                profit_map[self.BUY_CEX_SELL_DEX] = (total_revenue - total_cost) / total_cost

        return profit_map

    async def _refresh_quotes_if_needed(
        self,
        amount: Decimal,
        cex_ask: Decimal,
        cex_bid: Decimal,
        profit_map: Dict[str, Decimal],
    ):
        candidates = [
            (self.BUY_DEX_SELL_CEX, True, cex_bid, profit_map.get(self.BUY_DEX_SELL_CEX)),
            (self.BUY_CEX_SELL_DEX, False, cex_ask, profit_map.get(self.BUY_CEX_SELL_DEX)),
        ]

        refresh_candidates = []
        for direction, is_buy, ref_price, est_profit in candidates:
            decision = self._get_refresh_decision(direction, ref_price, est_profit)
            snapshot = self._quote_cache.get(direction)
            if snapshot is None:
                self._metrics["quote_cache_misses"] += 1
            elif self._quote_is_stale(snapshot):
                self._metrics["quote_cache_stale"] += 1
            elif not decision.should_refresh:
                self._metrics["quote_cache_hits"] += 1

            if decision.should_refresh:
                should_record_trigger = True
                if decision.trigger_source == "dex_event_trigger" and decision.dex_event_signal is not None:
                    generation = int(decision.dex_event_signal.get("generation", 0))
                    if generation <= self._last_reported_dex_event_generation.get(direction, 0):
                        should_record_trigger = False
                    else:
                        self._last_reported_dex_event_generation[direction] = generation
                if should_record_trigger:
                    self._trigger_source_counts[decision.trigger_source] += 1
                    self._trigger_reason_counts[decision.trigger_reason] += 1
                    self._emit_event(
                        "trigger",
                        direction=direction,
                        trigger_source=decision.trigger_source,
                        trigger_reason=decision.trigger_reason,
                        estimated_profit_pct=est_profit,
                        current_cex_price=ref_price,
                        cex_move_pct=decision.cex_move_pct,
                        dex_event_signal=decision.dex_event_signal,
                        cached_quote_age_sec=self._snapshot_age(snapshot),
                    )
                priority = est_profit if est_profit is not None else Decimal("-999")
                refresh_candidates.append((priority, direction, is_buy, ref_price, decision))

        refresh_candidates.sort(key=lambda row: row[0], reverse=True)
        if refresh_candidates:
            self._metrics["expensive_quote_cycles"] += 1

        for _, direction, is_buy, ref_price, decision in refresh_candidates:
            if self._is_stop_triggered:
                break
            await self._refresh_dex_quote(direction, is_buy, amount, ref_price, decision)

    def _get_refresh_decision(
        self,
        direction: str,
        current_cex_price: Decimal,
        estimated_profit: Optional[Decimal],
    ) -> QuoteRefreshDecision:
        snapshot = self._quote_cache.get(direction)
        if snapshot is None:
            return QuoteRefreshDecision(True, "bootstrap", "missing_snapshot")

        dex_event_signal = self._get_pending_dex_event_signal(direction)
        if dex_event_signal is not None:
            return QuoteRefreshDecision(
                True,
                "dex_event_trigger",
                "watch_pool_changed",
                dex_event_signal=dex_event_signal,
            )

        if self._quote_is_stale(snapshot):
            return QuoteRefreshDecision(True, "heartbeat", "stale_snapshot")

        cex_move_pct: Optional[Decimal] = None
        if snapshot.reference_cex_price <= Decimal("0"):
            return QuoteRefreshDecision(True, "cex_trigger", "missing_reference_price")

        cex_move_pct = abs(current_cex_price - snapshot.reference_cex_price) / snapshot.reference_cex_price
        if cex_move_pct >= self.config.cex_price_move_trigger_pct:
            return QuoteRefreshDecision(True, "cex_trigger", "cex_price_move", cex_move_pct=cex_move_pct)

        if estimated_profit is None:
            return QuoteRefreshDecision(True, "scout", "missing_estimate", cex_move_pct=cex_move_pct)

        trigger_level = self.config.min_profitability - self.config.quote_trigger_buffer_pct
        if estimated_profit >= trigger_level:
            return QuoteRefreshDecision(True, "scout", "near_threshold", cex_move_pct=cex_move_pct)

        return QuoteRefreshDecision(False, "cache", "estimate_below_trigger", cex_move_pct=cex_move_pct)

    def _quote_is_stale(self, snapshot: DexQuoteSnapshot) -> bool:
        return (self._now() - snapshot.timestamp) >= float(self.config.aggregator_quote_ttl_sec)

    async def _refresh_dex_quote(
        self,
        direction: str,
        is_buy: bool,
        amount: Decimal,
        reference_cex_price: Decimal,
        decision: QuoteRefreshDecision,
    ) -> bool:
        if self._is_stop_triggered:
            return False
        can_request, skip_reason = self._can_request_expensive_quote(direction)
        if not can_request:
            metric_name = "expensive_quote_skipped_by_budget" if skip_reason == "budget" else "expensive_quote_skipped_by_cooldown"
            self._metrics[metric_name] += 1
            self._emit_event(
                "quote_refresh",
                direction=direction,
                connector=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
                status="skipped",
                skip_reason=skip_reason,
                trigger_source=decision.trigger_source,
                trigger_reason=decision.trigger_reason,
                is_buy=is_buy,
                amount=amount,
                reference_cex_price=reference_cex_price,
                cex_move_pct=decision.cex_move_pct,
                dex_event_signal=decision.dex_event_signal,
            )
            return False

        request_ts = self._now()
        self._last_quote_request_ts[direction] = request_ts
        self._quote_request_timestamps.append(request_ts)
        self._metrics["expensive_quote_requests"] += 1
        start = time.perf_counter()
        quote_id: Optional[str] = None
        try:
            quote_price = await self.connectors[self.config.dex_connector].get_quote_price(
                self.config.dex_trading_pair, is_buy, amount
            )
            quote_id = self._get_connector_quote_id(
                connector_name=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
                is_buy=is_buy,
                amount=amount,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            self._quote_latency_ms_total += latency_ms
            self._quote_latency_samples += 1
            self._metrics["expensive_quote_failures"] += 1
            is_vendor_throttle = self._is_vendor_throttle_error(exc)
            is_network_error = self._is_network_error(exc)
            if is_vendor_throttle:
                self._metrics["vendor_429_count"] += 1
            self._emit_event(
                "quote_refresh",
                direction=direction,
                connector=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
                status="error",
                trigger_source=decision.trigger_source,
                trigger_reason=decision.trigger_reason,
                is_buy=is_buy,
                amount=amount,
                reference_cex_price=reference_cex_price,
                cex_move_pct=decision.cex_move_pct,
                dex_event_signal=decision.dex_event_signal,
                latency_ms=latency_ms,
                error=str(exc),
            )
            self._mark_dex_event_consumed(direction, decision.dex_event_signal)
            self._record_quote_error(
                direction=direction,
                error=exc,
                is_network_error=is_network_error,
                is_vendor_throttle=is_vendor_throttle,
                context={
                    "trigger_source": decision.trigger_source,
                    "trigger_reason": decision.trigger_reason,
                    "connector": self.config.dex_connector,
                    "trading_pair": self.config.dex_trading_pair,
                    "is_buy": is_buy,
                    "amount": amount,
                },
            )
            self.logger().warning(f"Failed to refresh DEX quote for {direction}: {exc}")
            return False

        if quote_price is None or quote_price <= Decimal("0"):
            latency_ms = (time.perf_counter() - start) * 1000
            self._quote_latency_ms_total += latency_ms
            self._quote_latency_samples += 1
            self._metrics["expensive_quote_failures"] += 1
            self._emit_event(
                "quote_refresh",
                direction=direction,
                connector=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
                status="empty",
                trigger_source=decision.trigger_source,
                trigger_reason=decision.trigger_reason,
                is_buy=is_buy,
                amount=amount,
                reference_cex_price=reference_cex_price,
                cex_move_pct=decision.cex_move_pct,
                dex_event_signal=decision.dex_event_signal,
                latency_ms=latency_ms,
            )
            self._mark_dex_event_consumed(direction, decision.dex_event_signal)
            self._record_empty_quote(
                direction=direction,
                context={
                    "trigger_source": decision.trigger_source,
                    "trigger_reason": decision.trigger_reason,
                    "connector": self.config.dex_connector,
                    "trading_pair": self.config.dex_trading_pair,
                    "is_buy": is_buy,
                    "amount": amount,
                },
            )
            return False

        now = self._now()
        latency_ms = (time.perf_counter() - start) * 1000
        self._quote_cache[direction] = DexQuoteSnapshot(
            price=Decimal(str(quote_price)),
            timestamp=now,
            reference_cex_price=reference_cex_price,
            trigger_source=decision.trigger_source,
            refresh_reason=decision.trigger_reason,
            latency_ms=latency_ms,
            quote_id=quote_id,
        )
        self._quote_latency_ms_total += latency_ms
        self._quote_latency_samples += 1
        self._metrics["expensive_quote_success"] += 1
        self._record_quote_success()
        self._emit_event(
            "quote_refresh",
            direction=direction,
            connector=self.config.dex_connector,
            trading_pair=self.config.dex_trading_pair,
            status="success",
            trigger_source=decision.trigger_source,
            trigger_reason=decision.trigger_reason,
            is_buy=is_buy,
            amount=amount,
            reference_cex_price=reference_cex_price,
            cex_move_pct=decision.cex_move_pct,
            dex_event_signal=decision.dex_event_signal,
            latency_ms=latency_ms,
            quote_price=quote_price,
            quote_id=quote_id,
        )
        self._mark_dex_event_consumed(direction, decision.dex_event_signal)
        return True

    def _can_request_expensive_quote(self, direction: str) -> Tuple[bool, Optional[str]]:
        now = self._now()
        while self._quote_request_timestamps and (now - self._quote_request_timestamps[0]) > 60:
            self._quote_request_timestamps.popleft()

        last_request = self._last_quote_request_ts.get(direction)
        if last_request is not None and (now - last_request) < float(self.config.aggregator_quote_cooldown_sec):
            return False, "cooldown"

        if len(self._quote_request_timestamps) >= self.config.aggregator_quote_budget_per_minute:
            if now - self._last_budget_log >= max(self.config.no_opportunity_log_interval, 10):
                self.logger().warning(
                    "Skipping external DEX quote refresh: per-minute quote budget exhausted "
                    "(%s/%s).",
                    len(self._quote_request_timestamps),
                    self.config.aggregator_quote_budget_per_minute,
                )
                self._last_budget_log = now
            return False, "budget"
        return True, None

    def _record_quote_success(self):
        self._consecutive_quote_failures = 0
        self._consecutive_empty_quotes = 0
        self._reset_network_error_streak()

    def _record_empty_quote(self, direction: str, context: Optional[Dict[str, Any]] = None):
        self._consecutive_quote_failures += 1
        self._consecutive_empty_quotes += 1
        self._reset_network_error_streak()
        if not self.config.circuit_breaker_enabled:
            return
        if self._consecutive_empty_quotes >= self.config.circuit_breaker_empty_quote_threshold:
            self._trip_circuit_breaker(
                reason="consecutive_empty_quotes",
                context={
                    "direction": direction,
                    "consecutive_empty_quotes": self._consecutive_empty_quotes,
                    "consecutive_quote_failures": self._consecutive_quote_failures,
                    **(context or {}),
                },
            )
        elif self._consecutive_quote_failures >= self.config.circuit_breaker_quote_failure_threshold:
            self._trip_circuit_breaker(
                reason="consecutive_quote_failures",
                context={
                    "direction": direction,
                    "consecutive_empty_quotes": self._consecutive_empty_quotes,
                    "consecutive_quote_failures": self._consecutive_quote_failures,
                    **(context or {}),
                },
            )

    def _record_quote_error(
        self,
        direction: str,
        error: Exception,
        is_network_error: bool,
        is_vendor_throttle: bool,
        context: Optional[Dict[str, Any]] = None,
    ):
        self._consecutive_quote_failures += 1
        self._consecutive_empty_quotes = 0

        if is_network_error:
            self._consecutive_network_errors += 1
        else:
            self._reset_network_error_streak()

        if not self.config.circuit_breaker_enabled:
            return

        if is_vendor_throttle and self.config.circuit_breaker_trip_on_vendor_429:
            self._trip_circuit_breaker(
                reason="vendor_429",
                context={
                    "direction": direction,
                    "error": str(error),
                    "consecutive_quote_failures": self._consecutive_quote_failures,
                    **(context or {}),
                },
            )
            return

        if (
            is_network_error
            and self.config.circuit_breaker_trip_on_network_error
            and self._consecutive_network_errors >= self.config.circuit_breaker_network_error_threshold
        ):
            self._trip_circuit_breaker(
                reason="consecutive_network_errors",
                context={
                    "direction": direction,
                    "error": str(error),
                    "consecutive_network_errors": self._consecutive_network_errors,
                    "consecutive_quote_failures": self._consecutive_quote_failures,
                    **(context or {}),
                },
            )
            return

        if self._consecutive_quote_failures >= self.config.circuit_breaker_quote_failure_threshold:
            self._trip_circuit_breaker(
                reason="consecutive_quote_failures",
                context={
                    "direction": direction,
                    "error": str(error),
                    "consecutive_quote_failures": self._consecutive_quote_failures,
                    **(context or {}),
                },
            )

    def _record_network_error(self, source: str, error: Exception, context: Optional[Dict[str, Any]] = None):
        self._consecutive_network_errors += 1
        if not self.config.circuit_breaker_enabled or not self.config.circuit_breaker_trip_on_network_error:
            return
        if self._consecutive_network_errors >= self.config.circuit_breaker_network_error_threshold:
            self._trip_circuit_breaker(
                reason="consecutive_network_errors",
                context={
                    "source": source,
                    "error": str(error),
                    "consecutive_network_errors": self._consecutive_network_errors,
                    **(context or {}),
                },
            )

    def _reset_network_error_streak(self):
        self._consecutive_network_errors = 0

    def _trip_circuit_breaker(self, reason: str, context: Optional[Dict[str, Any]] = None):
        if self._circuit_breaker_active or self._is_stop_triggered:
            return
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self._circuit_breaker_context = context or {}
        self._pending_create_action = None
        self._metrics["circuit_breaker_trips"] += 1

        self._emit_event(
            "circuit_breaker_trip",
            reason=reason,
            context=self._circuit_breaker_context,
            consecutive_quote_failures=self._consecutive_quote_failures,
            consecutive_empty_quotes=self._consecutive_empty_quotes,
            consecutive_network_errors=self._consecutive_network_errors,
        )

        message = (
            f"Circuit breaker triggered ({reason}). "
            "Stopping strategy to prevent request storms and network collapse."
        )
        self.logger().error(message)
        self.notify_hb_app_with_timestamp(message)
        self._is_stop_triggered = True

        try:
            from hummingbot.client.hummingbot_application import HummingbotApplication

            app = HummingbotApplication.main_application()
            if app is not None:
                app.stop()
        except Exception as exc:
            self.logger().error(f"Failed to stop strategy after circuit breaker trip: {exc}", exc_info=True)

    def _convert_price_to_cex_quote(self, dex_price: Decimal) -> Optional[Decimal]:
        conversion_rate = self._get_quote_conversion_rate()
        if conversion_rate is None:
            return None
        return dex_price * conversion_rate

    def _convert_flat_fee_to_cex_quote(self) -> Decimal:
        connector = self.connectors[self.config.dex_connector]
        gas_fee = getattr(connector, "network_transaction_fee", None)
        if gas_fee is None or getattr(gas_fee, "amount", None) is None:
            return Decimal("0")
        rate = self._get_pair_rate(gas_fee.token, self._cex_quote, fallback=self._gas_token_price_quote)
        if rate is None:
            return Decimal("0")
        return Decimal(str(gas_fee.amount)) * rate

    def _passes_budget_constraint(
        self,
        direction: str,
        amount: Decimal,
        cex_ask: Decimal,
        cex_bid: Decimal,
        dex_snapshot: Optional[DexQuoteSnapshot],
    ) -> Tuple[bool, Dict[str, Any]]:
        if dex_snapshot is None:
            return False, {
                "message": "Can't arbitrage, no valid DEX quote snapshot is available.",
                "direction": direction,
            }

        if direction == self.BUY_DEX_SELL_CEX:
            dex_buy_price = Decimal(str(dex_snapshot.price))
            dex_required_quote = amount * dex_buy_price
            dex_required_quote += dex_required_quote * self._dex_fee_rate
            dex_quote_balance = self._get_available_balance(self.config.dex_connector, self._dex_quote)
            if dex_quote_balance < dex_required_quote:
                return False, self._insufficient_balance_context(
                    connector_name=self.config.dex_connector,
                    token=self._dex_quote,
                    balance=dex_quote_balance,
                    required=dex_required_quote,
                    direction=direction,
                )

            cex_base_balance = self._get_available_balance(self.config.cex_connector, self._cex_base)
            if cex_base_balance < amount:
                return False, self._insufficient_balance_context(
                    connector_name=self.config.cex_connector,
                    token=self._cex_base,
                    balance=cex_base_balance,
                    required=amount,
                    direction=direction,
                )
        else:
            cex_required_quote = amount * cex_ask
            cex_required_quote += cex_required_quote * self._cex_fee_rate
            cex_quote_balance = self._get_available_balance(self.config.cex_connector, self._cex_quote)
            if cex_quote_balance < cex_required_quote:
                return False, self._insufficient_balance_context(
                    connector_name=self.config.cex_connector,
                    token=self._cex_quote,
                    balance=cex_quote_balance,
                    required=cex_required_quote,
                    direction=direction,
                )

            dex_base_balance = self._get_available_balance(self.config.dex_connector, self._cex_base)
            if dex_base_balance < amount:
                return False, self._insufficient_balance_context(
                    connector_name=self.config.dex_connector,
                    token=self._cex_base,
                    balance=dex_base_balance,
                    required=amount,
                    direction=direction,
                )

        gas_fee = self._get_network_transaction_fee()
        if gas_fee is not None and gas_fee["amount"] > Decimal("0"):
            gas_balance = self._get_available_balance(self.config.dex_connector, gas_fee["token"])
            if gas_balance < gas_fee["amount"]:
                return False, self._insufficient_balance_context(
                    connector_name=self.config.dex_connector,
                    token=gas_fee["token"],
                    balance=gas_balance,
                    required=gas_fee["amount"],
                    direction=direction,
                    message_prefix="Can't arbitrage, insufficient DEX gas balance.",
                )

        return True, {}

    def _get_available_balance(self, connector_name: str, token: str) -> Decimal:
        connector = self.connectors[connector_name]
        return Decimal(str(connector.get_available_balance(token)))

    def _get_network_transaction_fee(self) -> Optional[Dict[str, Decimal]]:
        connector = self.connectors[self.config.dex_connector]
        gas_fee = getattr(connector, "network_transaction_fee", None)
        if gas_fee is None or getattr(gas_fee, "amount", None) is None:
            return None
        return {
            "token": str(gas_fee.token),
            "amount": Decimal(str(gas_fee.amount)),
        }

    def _insufficient_balance_context(
        self,
        connector_name: str,
        token: str,
        balance: Decimal,
        required: Decimal,
        direction: str,
        message_prefix: str = "Can't arbitrage, insufficient balance.",
    ) -> Dict[str, Any]:
        message = (
            f"{message_prefix} {connector_name} {token} balance "
            f"({self._format_decimal(balance)}) is below required amount ({self._format_decimal(required)})."
        )
        return {
            "message": message,
            "connector": connector_name,
            "token": token,
            "balance": balance,
            "required": required,
            "direction": direction,
        }

    def _get_pair_rate(self, base: str, quote: str, fallback: Optional[Decimal] = None) -> Optional[Decimal]:
        if base == quote:
            return Decimal("1")
        rate = self._rate_source.get_pair_rate(f"{base}-{quote}")
        if rate is None and fallback is not None and not self._rate_oracle_enabled:
            rate = fallback
        return Decimal(str(rate)) if rate is not None else None

    def _get_quote_conversion_rate(self) -> Optional[Decimal]:
        return self._get_pair_rate(self._dex_quote, self._cex_quote, fallback=self._quote_conversion_rate)

    def _get_gas_conversion_ratio(self) -> Decimal:
        gas_rate = self._get_pair_rate(self.config.gas_token, self._cex_quote, fallback=self._gas_token_price_quote)
        if gas_rate is None or gas_rate <= Decimal("0"):
            return self._gas_conversion_ratio
        return Decimal("1") / gas_rate

    def _select_best_direction(self, profit_map: Dict[str, Decimal]) -> Tuple[Optional[str], Optional[Decimal]]:
        if not profit_map:
            return None, None
        direction = max(profit_map, key=lambda k: profit_map[k])
        return direction, profit_map[direction]

    def _maybe_log_status(
        self,
        profit_map: Dict[str, Decimal],
        best_direction: Optional[str],
        best_profit: Optional[Decimal],
        amount: Decimal,
    ):
        now = self.current_timestamp or time.time()
        if best_direction is not None and best_profit is not None and best_profit >= self.config.min_profitability:
            self.logger().info(
                "buy at %s, sell at %s: %s%%",
                self.config.dex_connector if best_direction == self.BUY_DEX_SELL_CEX else self.config.cex_connector,
                self.config.cex_connector if best_direction == self.BUY_DEX_SELL_CEX else self.config.dex_connector,
                self._format_pct(best_profit),
            )
            return

        if (now - self._last_no_opportunity_log) < self.config.no_opportunity_log_interval:
            return

        dex_buy = profit_map.get(self.BUY_DEX_SELL_CEX)
        dex_sell = profit_map.get(self.BUY_CEX_SELL_DEX)
        if dex_buy is not None:
            self.logger().info(
                "buy at %s, sell at %s: %s%%",
                self.config.dex_connector,
                self.config.cex_connector,
                self._format_pct(dex_buy),
            )
        if dex_sell is not None:
            self.logger().info(
                "buy at %s, sell at %s: %s%%",
                self.config.cex_connector,
                self.config.dex_connector,
                self._format_pct(dex_sell),
            )
        if best_profit is not None:
            self.logger().info(
                "No arbitrage opportunity. Best net profitability %s%% at amount %s.",
                self._format_pct(best_profit),
                self._format_decimal(amount),
            )
        else:
            self.logger().info("No arbitrage opportunity. No valid DEX quote snapshot is available.")
        self._last_no_opportunity_log = now

    def _build_executor_config(self, direction: str, amount: Decimal) -> ArbitrageExecutorConfig:
        if direction == self.BUY_DEX_SELL_CEX:
            buying_market = ConnectorPair(
                connector_name=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
            )
            selling_market = ConnectorPair(
                connector_name=self.config.cex_connector,
                trading_pair=self.config.cex_trading_pair,
            )
        else:
            buying_market = ConnectorPair(
                connector_name=self.config.cex_connector,
                trading_pair=self.config.cex_trading_pair,
            )
            selling_market = ConnectorPair(
                connector_name=self.config.dex_connector,
                trading_pair=self.config.dex_trading_pair,
            )

        return ArbitrageExecutorConfig(
            timestamp=self._now(),
            buying_market=buying_market,
            selling_market=selling_market,
            order_amount=amount,
            min_profitability=self.config.min_profitability,
            gas_conversion_price=self._get_gas_conversion_ratio(),
            concurrent_orders_submission=self.config.executor_concurrent_orders_submission,
            prioritize_non_amm_first=self.config.executor_prioritize_non_amm_first,
            retry_failed_orders=self.config.executor_retry_failed_orders,
        )

    def _get_connector_quote_id(
        self,
        connector_name: str,
        trading_pair: str,
        is_buy: bool,
        amount: Decimal,
    ) -> Optional[str]:
        connector = self.connectors.get(connector_name)
        if connector is None:
            return None
        getter = getattr(connector, "get_recent_quote_id", None)
        if getter is None or not callable(getter):
            return None
        try:
            return getter(trading_pair, is_buy, amount)
        except Exception:
            return None

    def _initialize_phase0_observability(self):
        self._open_event_log()
        self._register_market_event_listeners()
        self._emit_event(
            "strategy_start",
            cex_connector=self.config.cex_connector,
            cex_trading_pair=self.config.cex_trading_pair,
            dex_connector=self.config.dex_connector,
            dex_trading_pair=self.config.dex_trading_pair,
            dex_event_trigger_enabled=self.config.dex_event_trigger_enabled,
            dex_event_watch_markets=self.config.dex_event_watch_markets,
            order_amount=self.config.order_amount,
            min_profitability_pct=self.config.min_profitability,
            rate_oracle_enabled=self.config.rate_oracle_enabled,
            quote_conversion_rate=self.config.quote_conversion_rate,
            gas_price=self.config.gas_price if self.config.gas_price is not None else self.config.gas_token_price_quote,
            event_log_path=str(self._event_log_path) if self._event_log_path else None,
        )

    def _resolve_event_log_path(self) -> Path:
        configured_dir = self.config.phase0_event_log_directory.strip()
        if configured_dir:
            log_dir = Path(os.path.expanduser(configured_dir))
            if not log_dir.is_absolute():
                log_dir = Path(prefix_path()) / configured_dir
        else:
            log_dir = Path(prefix_path()) / "logs" / "strategy_metrics"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self._now()))
        safe_cex = self.config.cex_connector.replace("/", "_")
        safe_dex = self.config.dex_connector.replace("/", "_")
        filename = f"{self.config.phase0_event_log_file_prefix}_{safe_cex}_{safe_dex}_{timestamp}.jsonl"
        return log_dir / filename

    def _open_event_log(self):
        if not self.config.phase0_event_logging_enabled:
            return
        try:
            self._event_log_path = self._resolve_event_log_path()
            self._event_log_handle = open(self._event_log_path, "a", encoding="utf-8", buffering=1)
        except Exception as exc:
            self.logger().warning(f"Could not open Phase 0 event log file: {exc}")
            self._event_log_handle = None

    def _close_event_log(self):
        if self._event_log_handle is None:
            return
        try:
            self._event_log_handle.close()
        finally:
            self._event_log_handle = None

    def _register_market_event_listeners(self):
        if self._listeners_registered:
            return
        self._event_pairs = [
            (MarketEvent.BuyOrderCreated, SourceInfoEventForwarder(self._process_order_created_event)),
            (MarketEvent.SellOrderCreated, SourceInfoEventForwarder(self._process_order_created_event)),
            (MarketEvent.OrderFilled, SourceInfoEventForwarder(self._process_order_filled_event)),
            (MarketEvent.BuyOrderCompleted, SourceInfoEventForwarder(self._process_order_completed_event)),
            (MarketEvent.SellOrderCompleted, SourceInfoEventForwarder(self._process_order_completed_event)),
            (MarketEvent.OrderFailure, SourceInfoEventForwarder(self._process_order_failed_event)),
            (MarketEvent.OrderCancelled, SourceInfoEventForwarder(self._process_order_cancelled_event)),
        ]
        for connector in self.connectors.values():
            for event_tag, forwarder in self._event_pairs:
                connector.add_listener(event_tag, forwarder)
        self._listeners_registered = True

    def _unregister_market_event_listeners(self):
        if not self._listeners_registered:
            return
        for connector in self.connectors.values():
            for event_tag, forwarder in self._event_pairs:
                connector.remove_listener(event_tag, forwarder)
        self._listeners_registered = False

    def _track_executor_lifecycle(self):
        for executor in self.get_all_executors():
            state = (
                executor.status.name if isinstance(executor.status, Enum) else str(executor.status),
                executor.close_type.name if executor.close_type is not None else None,
            )
            if self._executor_status_cache.get(executor.id) != state:
                self._executor_status_cache[executor.id] = state
                self._emit_event(
                    "executor_status",
                    executor_id=executor.id,
                    status=state[0],
                    close_type=state[1],
                    net_pnl_pct=executor.net_pnl_pct,
                    net_pnl_quote=executor.net_pnl_quote,
                    filled_amount_quote=executor.filled_amount_quote,
                    custom_info=executor.custom_info,
                )
            if executor.status == RunnableStatus.TERMINATED and executor.id not in self._closed_executor_ids:
                self._closed_executor_ids.add(executor.id)
                self._record_trade_closed(executor)

    def _record_trade_closed(self, executor: ExecutorInfo):
        self._metrics["trades_closed"] += 1
        close_type = executor.close_type.name if executor.close_type is not None else "UNKNOWN"
        self._close_type_counts[close_type] += 1

        observation = self._executor_observations.get(executor.id)
        expected_profit = observation.expected_profit if observation else None
        pnl_delta = None
        if expected_profit is not None:
            pnl_delta = executor.net_pnl_pct - expected_profit
            self._estimate_realized_delta_abs_total += abs(pnl_delta)
            self._estimate_realized_delta_samples += 1

        self._emit_event(
            "trade_closed",
            executor_id=executor.id,
            close_type=close_type,
            direction=observation.direction if observation else None,
            opportunity_id=observation.opportunity_id if observation else None,
            expected_profit_pct=expected_profit,
            realized_profit_pct=executor.net_pnl_pct,
            pnl_delta_pct=pnl_delta,
            net_pnl_quote=executor.net_pnl_quote,
            cum_fees_quote=executor.cum_fees_quote,
            filled_amount_quote=executor.filled_amount_quote,
            custom_info=executor.custom_info,
        )

    def _process_order_created_event(self, _, market, event: Any):
        connector_name = self._connector_name_from_market(market)
        side = "BUY" if isinstance(event, BuyOrderCreatedEvent) else "SELL"
        self._metrics["orders_created"] += 1
        self._emit_event(
            "order_created",
            connector=connector_name,
            side=side,
            order_id=event.order_id,
            trading_pair=event.trading_pair,
            amount=event.amount,
            price=event.price,
            order_type=event.type,
            exchange_order_id=event.exchange_order_id,
        )

    def _process_order_filled_event(self, _, market, event: OrderFilledEvent):
        connector_name = self._connector_name_from_market(market)
        self._metrics["orders_filled"] += 1
        self._emit_event(
            "order_filled",
            connector=connector_name,
            order_id=event.order_id,
            trading_pair=event.trading_pair,
            trade_type=event.trade_type,
            order_type=event.order_type,
            price=event.price,
            amount=event.amount,
            trade_fee=event.trade_fee,
            exchange_trade_id=event.exchange_trade_id,
            exchange_order_id=event.exchange_order_id,
        )

    def _process_order_completed_event(self, _, market, event: Any):
        connector_name = self._connector_name_from_market(market)
        self._consecutive_order_failures = 0
        side = "BUY" if isinstance(event, BuyOrderCompletedEvent) else "SELL"
        self._metrics["orders_completed"] += 1
        self._emit_event(
            "order_completed",
            connector=connector_name,
            side=side,
            order_id=event.order_id,
            base_asset=event.base_asset,
            quote_asset=event.quote_asset,
            base_asset_amount=event.base_asset_amount,
            quote_asset_amount=event.quote_asset_amount,
            order_type=event.order_type,
            exchange_order_id=event.exchange_order_id,
        )

    def _process_order_failed_event(self, _, market, event: MarketOrderFailureEvent):
        connector_name = self._connector_name_from_market(market)
        error_message = event.error_message or ""
        self._consecutive_order_failures += 1
        self._metrics["orders_failed"] += 1
        if self._is_vendor_throttle_error(error_message):
            self._metrics["vendor_429_count"] += 1
        self._emit_event(
            "order_failed",
            connector=connector_name,
            order_id=event.order_id,
            order_type=event.order_type,
            error_message=error_message,
            error_type=event.error_type,
        )
        if (
            self.config.circuit_breaker_enabled
            and self.config.circuit_breaker_trip_on_vendor_429
            and self._is_vendor_throttle_error(error_message)
        ):
            self._trip_circuit_breaker(
                reason="order_vendor_429",
                context={
                    "connector": connector_name,
                    "order_id": event.order_id,
                    "order_type": str(event.order_type),
                    "error_message": error_message,
                },
            )
            return
        if self.config.circuit_breaker_enabled and self._is_fatal_order_error(error_message):
            self._trip_circuit_breaker(
                reason="fatal_order_error",
                context={
                    "connector": connector_name,
                    "order_id": event.order_id,
                    "order_type": str(event.order_type),
                    "error_message": error_message,
                },
            )
            return
        if (
            self.config.circuit_breaker_enabled
            and connector_name == self.config.dex_connector
            and self._consecutive_order_failures >= self.config.circuit_breaker_order_failure_threshold
        ):
            self._trip_circuit_breaker(
                reason="consecutive_dex_order_failures",
                context={
                    "connector": connector_name,
                    "order_id": event.order_id,
                    "order_type": str(event.order_type),
                    "error_message": error_message,
                    "consecutive_order_failures": self._consecutive_order_failures,
                },
            )
            return
        if self._is_network_error(error_message):
            self._record_network_error(
                source="order_failure",
                error=Exception(error_message),
                context={
                    "connector": connector_name,
                    "order_id": event.order_id,
                    "order_type": str(event.order_type),
                },
            )

    def _process_order_cancelled_event(self, _, market, event: OrderCancelledEvent):
        connector_name = self._connector_name_from_market(market)
        self._metrics["orders_cancelled"] += 1
        self._emit_event(
            "order_cancelled",
            connector=connector_name,
            order_id=event.order_id,
            exchange_order_id=event.exchange_order_id,
        )

    def _connector_name_from_market(self, market: Any) -> str:
        for connector_name, connector in self.connectors.items():
            if connector is market:
                return connector_name
        return getattr(market, "name", "unknown")

    def _emit_event(self, event_type: str, **payload):
        event = {
            "timestamp": self._now(),
            "event_type": event_type,
            "strategy_name": type(self).__name__,
            "script_file_name": self.config.script_file_name,
            **payload,
        }
        serialized = self._serialize_for_json(event)
        if self._event_log_handle is None:
            return
        try:
            self._event_log_handle.write(json.dumps(serialized, ensure_ascii=False) + "\n")
        except Exception as exc:
            if not self._event_log_failure_reported:
                self.logger().warning(f"Could not write Phase 0 event log entry: {exc}")
                self._event_log_failure_reported = True

    def _serialize_for_json(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.name
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): self._serialize_for_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._serialize_for_json(v) for v in value]
        if hasattr(value, "model_dump"):
            return self._serialize_for_json(value.model_dump())
        if hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
            return self._serialize_for_json(vars(value))
        return str(value)

    def _maybe_emit_metrics_summary(self, force: bool = False):
        interval = self.config.phase0_metrics_summary_interval_sec
        if interval <= 0 and not force:
            return
        now = self._now()
        if not force and (now - self._last_metrics_summary_log) < interval:
            return
        summary = self._build_metrics_summary_payload()
        self._emit_event("metrics_summary", **summary)
        self.logger().info(
            "Phase0 metrics summary: quotes=%s success=%s budget_skips=%s opportunities=%s executors=%s trades_closed=%s avg_quote_latency_ms=%s",
            summary["expensive_quote_requests"],
            summary["expensive_quote_success"],
            summary["expensive_quote_skipped_by_budget"],
            summary["opportunities_detected"],
            summary["executors_created"],
            summary["trades_closed"],
            summary["avg_quote_latency_ms"],
        )
        self._last_metrics_summary_log = now

    def _build_metrics_summary_payload(self) -> Dict[str, Any]:
        trades_closed = self._metrics["trades_closed"]
        executors_created = self._metrics["executors_created"]
        quote_requests = self._metrics["expensive_quote_requests"]
        avg_quote_latency_ms = None
        if self._quote_latency_samples > 0:
            avg_quote_latency_ms = round(self._quote_latency_ms_total / self._quote_latency_samples, 4)
        avg_estimate_realized_delta_pct = None
        if self._estimate_realized_delta_samples > 0:
            avg_estimate_realized_delta_pct = self._estimate_realized_delta_abs_total / Decimal(str(self._estimate_realized_delta_samples))
        return {
            "cheap_scout_cycles": self._metrics["cheap_scout_cycles"],
            "dex_watch_polls": self._metrics["dex_watch_polls"],
            "dex_watch_errors": self._metrics["dex_watch_errors"],
            "dex_event_trigger_batches": self._metrics["dex_event_trigger_batches"],
            "dex_event_trigger_signals": self._metrics["dex_event_trigger_signals"],
            "expensive_quote_cycles": self._metrics["expensive_quote_cycles"],
            "expensive_quote_requests": quote_requests,
            "expensive_quote_success": self._metrics["expensive_quote_success"],
            "expensive_quote_failures": self._metrics["expensive_quote_failures"],
            "expensive_quote_skipped_by_budget": self._metrics["expensive_quote_skipped_by_budget"],
            "expensive_quote_skipped_by_cooldown": self._metrics["expensive_quote_skipped_by_cooldown"],
            "quote_cache_hits": self._metrics["quote_cache_hits"],
            "quote_cache_misses": self._metrics["quote_cache_misses"],
            "quote_cache_stale": self._metrics["quote_cache_stale"],
            "opportunities_detected": self._metrics["opportunities_detected"],
            "opportunities_skipped": self._metrics["opportunities_skipped"],
            "executors_created": executors_created,
            "orders_created": self._metrics["orders_created"],
            "orders_filled": self._metrics["orders_filled"],
            "orders_completed": self._metrics["orders_completed"],
            "orders_failed": self._metrics["orders_failed"],
            "orders_cancelled": self._metrics["orders_cancelled"],
            "trades_closed": trades_closed,
            "vendor_429_count": self._metrics["vendor_429_count"],
            "circuit_breaker_active": self._circuit_breaker_active,
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "circuit_breaker_trips": self._metrics["circuit_breaker_trips"],
            "consecutive_quote_failures": self._consecutive_quote_failures,
            "consecutive_empty_quotes": self._consecutive_empty_quotes,
            "consecutive_network_errors": self._consecutive_network_errors,
            "consecutive_order_failures": self._consecutive_order_failures,
            "avg_quote_latency_ms": avg_quote_latency_ms,
            "quote_to_trade_ratio": (Decimal(str(quote_requests)) / Decimal(str(trades_closed))) if trades_closed > 0 else None,
            "quote_to_executor_ratio": (Decimal(str(quote_requests)) / Decimal(str(executors_created))) if executors_created > 0 else None,
            "avg_abs_estimate_realized_pnl_delta_pct": avg_estimate_realized_delta_pct,
            "trigger_source_distribution": dict(self._trigger_source_counts),
            "trigger_reason_distribution": dict(self._trigger_reason_counts),
            "trade_close_type_distribution": dict(self._close_type_counts),
        }

    def _snapshot_age(self, snapshot: Optional[DexQuoteSnapshot]) -> Optional[float]:
        if snapshot is None:
            return None
        return round(self._now() - snapshot.timestamp, 6)

    def _is_vendor_throttle_error(self, error: Any) -> bool:
        message = str(error).lower()
        return "429" in message or "rate limit" in message or "throttle" in message

    def _is_fatal_order_error(self, error: Any) -> bool:
        message = str(error).lower()
        patterns = [
            r"invalid_currency_pair",
            r"not in whitelist",
            r"trading pair .* not available",
            r"currencypair .* is not in whitelist",
            r"insufficient allowance",
            r"allowance .* expired",
            r"permit2 allowance",
            r"please approve",
            r"universal router",
            r"missing transaction hash",
        ]
        return any(re.search(pattern, message) for pattern in patterns)

    def _is_network_error(self, error: Any) -> bool:
        message = str(error).lower()
        patterns = [
            r"cannot connect to host",
            r"connection refused",
            r"temporary failure in name resolution",
            r"name or service not known",
            r"nodename nor servname provided",
            r"network is unreachable",
            r"no route to host",
            r"timed out",
            r"timeout",
            r"missing response",
            r"econnrefused",
            r"server_error",
            r"cannot assign requested address",
            r"dns",
        ]
        return any(re.search(pattern, message) for pattern in patterns)

    def _now(self) -> float:
        current_ts = self.current_timestamp
        if current_ts is None:
            return time.time()
        try:
            current_ts_float = float(current_ts)
        except (TypeError, ValueError):
            return time.time()
        if math.isnan(current_ts_float) or math.isinf(current_ts_float):
            return time.time()
        return current_ts_float

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _format_pct(value: Decimal) -> str:
        pct = (value * Decimal("100")).quantize(Decimal("0.0001"))
        return AggregatorCexDexArb._format_decimal(pct)
