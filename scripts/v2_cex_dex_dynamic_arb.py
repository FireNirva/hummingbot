import asyncio
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Set, Tuple

from pydantic import Field, field_validator

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.utils.fixed_rate_source import FixedRateSource
from hummingbot.strategy.amm_arb.data_types import ArbProposal, ArbProposalSide, TokenAmount
from hummingbot.strategy.amm_arb.utils import create_arb_proposals
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction


class DynamicCexDexArbConfig(StrategyV2ConfigBase):
    """
    配置说明：
    - cex_connector / dex_connector：策略会在 start 命令里初始化这两个连接器。
    - trading pair 必须使用统一的 Hummingbot 交易对格式，如 "GPS-USDT"。
    - size_increment / max_order_amount：动态扫描时每次增加的 base 数量及最大考虑的交易量。
    - min_profitability：期望的最小净收益率（百分比，0.001 = 0.1%）。
    - cex_fee_rate / dex_fee_rate：近似的 taker 手续费，用于快速预估真实收益（executor 仍会以交易所返回的费用重新验证）。
    - gas_token_price_quote：gas 代币的现价（quote 计价），用于折算链上手续费；脚本内部会自动求倒数传递给 executor。
    - quote_conversion_rate：DEX 收到的 quote 资产换算成 CEX quote 资产时的兑换比；若两侧都是 USDT，则为 1；若 DEX 是 USDC，CEX 是 USDT，可填 1 (假设 1:1) 或填入实时汇率。
    """
    script_file_name: str = os.path.basename(__file__)
    markets: Dict[str, Set[str]] = {}
    candles_config: List = []
    controllers_config: List[str] = []

    cex_connector: str = Field(default="gate_io", description="CEX connector name, e.g. gate_io")
    cex_trading_pair: str = Field(default="VIRTUAL-USDT", description="CEX trading pair, e.g. GPS-USDT")
    dex_connector: str = Field(default="uniswap/router", description="DEX connector name via Gateway, e.g. uniswap/router")
    dex_trading_pair: str = Field(default="VIRTUAL-USDC", description="DEX trading pair, base asset must match CEX base")

    min_profitability: Decimal = Field(default=Decimal("0.001"), gt=0, description="Minimum expected net profitability (in quote %)")
    size_increment: Decimal = Field(default=Decimal("1"), gt=0, description="Incremental step when scanning order size (base units)")
    max_order_amount: Decimal = Field(default=Decimal("100"), gt=0, description="Maximum base order size to consider")
    max_cex_notional: Decimal = Field(default=Decimal("1000"), gt=0, description="Maximum quote notional to spend on CEX per trade")

    cex_fee_rate: Decimal = Field(default=Decimal("0.001"), ge=0, description="Estimated CEX taker fee (decimal form)")
    dex_fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, description="Estimated DEX swap fee (decimal form)")
    gas_token_price_quote: Decimal = Field(default=Decimal("2500"), gt=0, description="Gas token price denominated in CEX quote asset")
    quote_conversion_rate: Decimal = Field(default=Decimal("1"), gt=0, description="DEX quote -> CEX quote conversion rate")
    gas_token: str = Field(default="ETH", description="Gas token symbol used on the DEX chain")

    max_concurrent_executors: int = Field(default=1, ge=1)
    no_opportunity_log_interval: int = Field(
        default=30,
        ge=0,
        description="Minimum seconds between repeated 'no opportunity' log messages."
    )

    @field_validator("size_increment", "max_order_amount", "max_cex_notional", "cex_fee_rate",
                     "dex_fee_rate", "min_profitability", "gas_token_price_quote", "quote_conversion_rate")
    @classmethod
    def quantize_decimal_fields(cls, value: Decimal) -> Decimal:
        return Decimal(str(value))


class DynamicCexDexArb(StrategyV2Base):
    """
    动态 CEX-DEX 套利策略（Strategy V2）
    - 自动扫描 Gate.io (或其他 CEX) 的盘口深度与目标 DEX 的 swap 报价；
    - 根据期望的最小净利润阈值，动态计算最佳撮合数量；
    - 每次只启动一个 ArbitrageExecutor，由 executor 负责具体下单与收尾。
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: DynamicCexDexArbConfig):
        if len(config.markets) == 0:
            config.markets = {
                config.cex_connector: {config.cex_trading_pair},
                config.dex_connector: {config.dex_trading_pair},
            }
        super().__init__(connectors, config)
        self.config = config

        self._pending_create_action: Optional[CreateExecutorAction] = None
        self._evaluation_lock = asyncio.Lock()
        self._evaluation_task: Optional[asyncio.Task] = None

        self._cex_base, self._cex_quote = split_hb_trading_pair(config.cex_trading_pair)
        dex_base, self._dex_quote = split_hb_trading_pair(config.dex_trading_pair)

        if dex_base != self._cex_base:
            raise ValueError("CEX 与 DEX 的 base asset 不一致，无法执行套利。")

        self._cex_fee_rate = Decimal(str(self.config.cex_fee_rate))
        self._dex_fee_rate = Decimal(str(self.config.dex_fee_rate))
        self._quote_conversion_rate = Decimal(str(self.config.quote_conversion_rate))
        self._gas_token_price_quote = Decimal(str(self.config.gas_token_price_quote))
        self._gas_conversion_ratio = Decimal("1") / self._gas_token_price_quote
        self._no_opportunity_log_interval = float(self.config.no_opportunity_log_interval)
        self._last_no_opportunity_log: float = 0.0
        try:
            self.logger().info(f"初始化套利脚本，已加载连接器: {list(self.connectors.keys())}")
            self._setup_market_info()
            self._setup_rate_source()
        except Exception as exc:
            self.logger().error(f"初始化套利脚本时出错: {exc}", exc_info=True)
            raise

    @classmethod
    def init_markets(cls, config: DynamicCexDexArbConfig):
        cls.markets = {
            config.cex_connector: {config.cex_trading_pair},
            config.dex_connector: {config.dex_trading_pair},
        }

    def on_tick(self):
        super().on_tick()
        if self._is_stop_triggered:
            return
        if self._evaluation_task is None or self._evaluation_task.done():
            if not self._has_active_executor():
                self._evaluation_task = asyncio.create_task(self._evaluate_and_queue_action())

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        if self._pending_create_action is not None:
            action = self._pending_create_action
            self._pending_create_action = None
            return [action]
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        return []

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions = super().determine_executor_actions()
        return actions

    def _has_active_executor(self) -> bool:
        active = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.is_active
        )
        return len(active) >= self.config.max_concurrent_executors

    async def _evaluate_and_queue_action(self):
        if self._evaluation_lock.locked():
            return
        async with self._evaluation_lock:
            try:
                (amount,
                 est_profit,
                 max_seen_profit,
                 max_seen_amount,
                 latest_sell_profit,
                 latest_buy_profit) = await self._find_best_trade_size()

                self._log_direction_profits(latest_sell_profit, latest_buy_profit)

                if amount > Decimal("0"):
                    executor_config = ArbitrageExecutorConfig(
                        buying_market=ConnectorPair(
                            connector_name=self.config.cex_connector,
                            trading_pair=self.config.cex_trading_pair,
                        ),
                        selling_market=ConnectorPair(
                            connector_name=self.config.dex_connector,
                            trading_pair=self.config.dex_trading_pair,
                        ),
                        order_amount=amount,
                        min_profitability=self.config.min_profitability,
                        gas_conversion_price=self._gas_conversion_ratio,
                    )
                    self.logger().info(
                        f"触发套利执行器：数量 {self._format_decimal(amount)}，预估净收益率 {(est_profit * Decimal(100)).quantize(Decimal('0.0001'))}%"
                    )
                    self._pending_create_action = CreateExecutorAction(executor_config=executor_config)
                else:
                    now = self.current_timestamp
                    if now is None:
                        now = time.time()
                    if (now - self._last_no_opportunity_log) >= self._no_opportunity_log_interval:
                        if max_seen_profit is not None:
                            self.logger().info(
                                "暂无套利机会，当前最佳净收益率 "
                                f"{(max_seen_profit * Decimal(100)).quantize(Decimal('0.0001'))}% "
                                f"(尝试下单量 {self._format_decimal(max_seen_amount)})"
                            )
                        else:
                            self.logger().info("暂无套利机会，无法从市场获取有效报价。")
                        self._last_no_opportunity_log = now
            except Exception as e:
                self.logger().error(f"评估套利机会时出错: {e}", exc_info=True)

    async def _find_best_trade_size(self) -> Tuple[Decimal, Decimal, Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        """
        扫描从 size_increment 开始到 max_order_amount 的交易量，找到净收益率最高且超过阈值的量。
        """
        cex_connector = self.connectors[self.config.cex_connector]
        dex_connector = self.connectors[self.config.dex_connector]

        available_quote = Decimal(str(cex_connector.get_available_balance(self._cex_quote)))
        available_base_dex = Decimal(str(dex_connector.get_available_balance(self._cex_base)))

        if available_quote <= Decimal("0") or available_base_dex <= Decimal("0"):
            return Decimal("0"), Decimal("0"), None, None, None, None

        max_amount = min(self.config.max_order_amount, available_base_dex)
        if max_amount <= Decimal("0"):
            return Decimal("0"), Decimal("0"), None, None, None, None

        best_amount = Decimal("0")
        best_profit = Decimal("-1")
        max_seen_profit: Optional[Decimal] = None
        max_seen_amount: Optional[Decimal] = None
        latest_sell_profit: Optional[Decimal] = None
        latest_buy_profit: Optional[Decimal] = None

        amount = self.config.size_increment
        iteration_cap = int((self.config.max_order_amount / self.config.size_increment)) + 2

        for _ in range(iteration_cap):
            quantized_amount = self.market_data_provider.quantize_order_amount(
                self.config.cex_connector, self.config.cex_trading_pair, amount
            )
            quantized_amount = self.market_data_provider.quantize_order_amount(
                self.config.dex_connector, self.config.dex_trading_pair, quantized_amount
            )

            if quantized_amount <= Decimal("0"):
                amount += self.config.size_increment
                continue
            if quantized_amount > max_amount:
                break

            profitability_result = await self._calculate_profitability(quantized_amount)
            if profitability_result is None:
                amount += self.config.size_increment
                continue

            profit_map, buy_quote_price = profitability_result
            profit_cex_buy = profit_map.get("cex_buy")  # buy on CEX, sell on DEX
            profit_cex_sell = profit_map.get("cex_sell")  # sell on CEX, buy on DEX

            if profit_cex_sell is not None:
                latest_sell_profit = profit_cex_sell
            if profit_cex_buy is not None:
                latest_buy_profit = profit_cex_buy

            if profit_cex_buy is None or buy_quote_price is None:
                amount += self.config.size_increment
                continue

            notional_quote = buy_quote_price * quantized_amount
            if notional_quote > available_quote or notional_quote > self.config.max_cex_notional:
                break

            est_profit_pct = profit_cex_buy

            if est_profit_pct >= self.config.min_profitability and est_profit_pct > best_profit:
                best_profit = est_profit_pct
                best_amount = quantized_amount
            if max_seen_profit is None or est_profit_pct > max_seen_profit:
                max_seen_profit = est_profit_pct
                max_seen_amount = quantized_amount

            amount += self.config.size_increment

        if best_amount > Decimal("0"):
            return best_amount, best_profit, max_seen_profit, max_seen_amount, latest_sell_profit, latest_buy_profit
        return Decimal("0"), Decimal("0"), max_seen_profit, max_seen_amount, latest_sell_profit, latest_buy_profit

    @staticmethod
    def _format_decimal(value: Optional[Decimal]) -> str:
        if value is None:
            return "N/A"
        formatted = format(value, "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted or "0"

    def _setup_market_info(self):
        self._market_info_cex = MarketTradingPairTuple(
            market=self.connectors[self.config.cex_connector],
            trading_pair=self.config.cex_trading_pair,
            base_asset=self._cex_base,
            quote_asset=self._cex_quote
        )
        self._market_info_dex = MarketTradingPairTuple(
            market=self.connectors[self.config.dex_connector],
            trading_pair=self.config.dex_trading_pair,
            base_asset=self._cex_base,
            quote_asset=self._dex_quote
        )
        self._extra_fees_cex: List[TokenAmount] = []
        self._extra_fees_dex: List[TokenAmount] = []
        if hasattr(self._market_info_cex.market, "network_transaction_fee"):
            fee = self._market_info_cex.market.network_transaction_fee
            if fee is not None:
                self._extra_fees_cex.append(TokenAmount(token=fee.token, amount=Decimal(str(fee.amount))))
        if hasattr(self._market_info_dex.market, "network_transaction_fee"):
            fee = self._market_info_dex.market.network_transaction_fee
            if fee is not None:
                self._extra_fees_dex.append(TokenAmount(token=fee.token, amount=Decimal(str(fee.amount))))

    def _setup_rate_source(self):
        self._rate_source = FixedRateSource()
        self._add_rate_pair(self._dex_quote, self._cex_quote, self._quote_conversion_rate)
        gas_token = getattr(self.config, "gas_token", "ETH")
        if self.config.gas_token_price_quote is not None:
            gas_to_buy = self._gas_token_price_quote
            self._add_rate_pair(gas_token, self._cex_quote, gas_to_buy)
            if self._dex_quote != self._cex_quote:
                dex_rate = gas_to_buy / self._quote_conversion_rate
                self._add_rate_pair(gas_token, self._dex_quote, dex_rate)

    def _add_rate_pair(self, base: str, quote: str, rate: Decimal):
        if rate <= Decimal("0"):
            return
        self._rate_source.add_rate(f"{base}-{quote}", rate)
        reciprocal = Decimal("1") / rate
        self._rate_source.add_rate(f"{quote}-{base}", reciprocal)

    def _convert_flat_fees_to_quote(self, fees: List[TokenAmount]) -> Decimal:
        total = Decimal("0")
        for fee in fees:
            amount = Decimal(str(fee.amount))
            if amount <= Decimal("0"):
                continue
            pair = f"{fee.token}-{self._cex_quote}"
            rate = self._rate_source.get_pair_rate(pair)
            if rate is None:
                continue
            total += amount * rate
        return total

    def _convert_price_to_cex_quote(self, price: Decimal, quote_token: str) -> Optional[Decimal]:
        pair = f"{quote_token}-{self._cex_quote}"
        rate = self._rate_source.get_pair_rate(pair)
        if rate is None:
            return None
        return price * rate

    @staticmethod
    def _split_proposal_sides(
        proposal: ArbProposal,
        cex_market: ConnectorBase,
        dex_market: ConnectorBase,
    ) -> Optional[Tuple[ArbProposalSide, ArbProposalSide]]:
        sides = [proposal.first_side, proposal.second_side]
        cex_side = next((side for side in sides if side.market_info.market == cex_market), None)
        dex_side = next((side for side in sides if side.market_info.market == dex_market), None)
        if cex_side is None or dex_side is None:
            return None
        return cex_side, dex_side

    def _evaluate_direction_profit(
        self,
        amount: Decimal,
        cex_side: ArbProposalSide,
        dex_side: ArbProposalSide,
    ) -> Optional[Tuple[str, Decimal, Optional[Decimal]]]:
        try:
            cex_price = Decimal(str(cex_side.quote_price))
            dex_price = Decimal(str(dex_side.quote_price))
        except (InvalidOperation, TypeError):
            return None

        dex_price_in_cex_quote = self._convert_price_to_cex_quote(dex_price, dex_side.market_info.quote_asset)
        if dex_price_in_cex_quote is None:
            return None

        cex_flat_fees = self._convert_flat_fees_to_quote(cex_side.extra_flat_fees)
        dex_flat_fees = self._convert_flat_fees_to_quote(dex_side.extra_flat_fees)

        if cex_side.is_buy:
            direction = "cex_buy"
            total_cost = amount * cex_price
            total_cost += total_cost * self._cex_fee_rate
            total_cost += cex_flat_fees

            total_revenue = amount * dex_price_in_cex_quote
            total_revenue -= total_revenue * self._dex_fee_rate
            total_revenue -= dex_flat_fees

            buy_quote_price = cex_price
        else:
            direction = "cex_sell"
            total_revenue = amount * cex_price
            total_revenue -= total_revenue * self._cex_fee_rate
            total_revenue -= cex_flat_fees

            total_cost = amount * dex_price_in_cex_quote
            total_cost += total_cost * self._dex_fee_rate
            total_cost += dex_flat_fees

            buy_quote_price = None

        if total_cost <= Decimal("0"):
            return None

        profit_pct = (total_revenue - total_cost) / total_cost
        return direction, profit_pct, buy_quote_price

    async def _calculate_profitability(
        self, amount: Decimal
    ) -> Optional[Tuple[Dict[str, Decimal], Optional[Decimal]]]:
        try:
            proposals = await create_arb_proposals(
                market_info_1=self._market_info_cex,
                market_info_2=self._market_info_dex,
                market_1_extra_flat_fees=self._extra_fees_cex,
                market_2_extra_flat_fees=self._extra_fees_dex,
                order_amount=amount,
            )
        except Exception as exc:
            self.logger().warning(f"获取套利提案失败: {exc}")
            return None

        if not proposals:
            return None

        profit_map: Dict[str, Decimal] = {}
        buy_quote_price: Optional[Decimal] = None

        for proposal in proposals:
            split_result = self._split_proposal_sides(
                proposal=proposal,
                cex_market=self._market_info_cex.market,
                dex_market=self._market_info_dex.market,
            )
            if split_result is None:
                continue

            cex_side, dex_side = split_result
            evaluation = self._evaluate_direction_profit(amount, cex_side, dex_side)
            if evaluation is None:
                continue

            direction, profitability, maybe_buy_price = evaluation
            profit_map[direction] = profitability
            if direction == "cex_buy" and maybe_buy_price is not None:
                buy_quote_price = maybe_buy_price

        if not profit_map:
            return None

        return profit_map, buy_quote_price

    def _log_direction_profits(self, profit_sell: Optional[Decimal], profit_buy: Optional[Decimal]):
        if profit_sell is not None:
            percent = (profit_sell * Decimal(100)).quantize(Decimal("0.0001"))
            self.logger().info(
                f"buy at {self.config.dex_connector}, sell at {self.config.cex_connector}: {percent}%"
            )
        if profit_buy is not None:
            percent = (profit_buy * Decimal(100)).quantize(Decimal("0.0001"))
            self.logger().info(
                f"sell at {self.config.dex_connector}, buy at {self.config.cex_connector}: {percent}%"
            )
