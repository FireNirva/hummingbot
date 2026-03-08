import os
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
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class BasicCexDexArbConfig(StrategyV2ConfigBase):
    """
    CEX-DEX 基础套利策略配置类
    
    此策略使用固定订单量，自动评估两个方向的套利机会（CEX买+DEX卖 / DEX买+CEX卖），
    选择盈利率最高的方向执行。支持不同 quote 资产的汇率转换（如 WETH vs USDT）。
    
    配置说明：
    - cex_connector / dex_connector：CEX 和 DEX 连接器名称，策略会在 start 时初始化
    - trading_pair：必须使用统一的 Hummingbot 交易对格式，如 "IRON-USDT"
    - order_amount：固定的订单量（base 资产数量），与 v1 策略的 order_amount 等效
    - min_profitability：期望的最小净收益率（小数形式，0.015 = 1.5%）
    - cex_fee_rate / dex_fee_rate：交易手续费率估算，用于计算净收益
    - gas_token_price_quote：gas 代币价格（用 CEX quote 资产计价），用于折算链上手续费
    - quote_conversion_rate：DEX quote 资产转换为 CEX quote 资产的汇率
      * 若两侧都是 USDT，则为 1
      * 若 DEX 是 WETH，CEX 是 USDT，则填入 WETH/USDT 的实时价格
    """
    script_file_name: str = os.path.basename(__file__)
    markets: Dict[str, Set[str]] = {}
    candles_config: List = []
    controllers_config: List[str] = []

    # === CEX 配置 ===
    cex_connector: str = Field(
        default="gate_io",
        description="CEX 连接器名称，如 gate_io, binance 等"
    )
    cex_trading_pair: str = Field(
        default="IRON-USDT",
        description="CEX 交易对，格式：BASE-QUOTE"
    )

    # === DEX 配置 ===
    dex_connector: str = Field(
        default="uniswap/amm",
        description="DEX 连接器名称（通过 Gateway），如 uniswap/amm"
    )
    dex_trading_pair: str = Field(
        default="IRON-WETH",
        description="DEX 交易对，base 资产必须与 CEX 一致"
    )

    # === 订单量与盈利阈值 ===
    order_amount: Decimal = Field(
        default=Decimal("50"),
        gt=0,
        description="固定订单量（base 资产数量），等同于 v1 策略的 order_amount"
    )
    min_profitability: Decimal = Field(
        default=Decimal("0.015"),
        gt=0,
        description="最小净收益率阈值（小数形式），0.015 = 1.5%"
    )

    # === 手续费估算 ===
    cex_fee_rate: Decimal = Field(
        default=Decimal("0.001"),
        ge=0,
        description="CEX taker 手续费率估算（小数形式），0.001 = 0.1%"
    )
    dex_fee_rate: Decimal = Field(
        default=Decimal("0.0005"),
        ge=0,
        description="DEX swap 手续费率估算（小数形式），0.0005 = 0.05%"
    )

    # === Gas 费用估算 ===
    gas_token_price_quote: Decimal = Field(
        default=Decimal("3800"),
        gt=0,
        description="Gas 代币价格（用 CEX quote 资产计价），如 1 ETH = 3800 USDT"
    )
    quote_conversion_rate: Decimal = Field(
        default=Decimal("3800"),
        gt=0,
        description="DEX quote 资产转换为 CEX quote 资产的汇率，如 1 WETH = 3800 USDT"
    )
    gas_token: str = Field(
        default="ETH",
        description="Gas 代币符号，用于链上交易费用"
    )

    # === 执行器控制 ===
    max_concurrent_executors: int = Field(
        default=1,
        ge=1,
        description="最大并发执行器数量，建议设置为 1 避免冲突"
    )

    @field_validator("order_amount", "min_profitability", "cex_fee_rate",
                     "dex_fee_rate", "gas_token_price_quote", "quote_conversion_rate")
    @classmethod
    def quantize_decimal_fields(cls, value: Decimal) -> Decimal:
        """确保 Decimal 字段正确序列化"""
        return Decimal(str(value))


class BasicCexDexArb(StrategyV2Base):
    """
    CEX-DEX 基础套利策略（Strategy V2）
    
    功能：
    - 使用固定订单量评估套利机会
    - 自动比较两个方向（CEX买+DEX卖 vs DEX买+CEX卖）的盈利率
    - 选择盈利率最高且超过阈值的方向执行
    - 支持不同 quote 资产的汇率转换（通过 FixedRateSource）
    - 每次只启动一个 ArbitrageExecutor，由 executor 负责具体下单
    
    与 v1 策略的区别：
    - v1 使用 StrategyPyBase，v2 使用 StrategyV2Base
    - v1 直接下单，v2 通过 ArbitrageExecutor 管理订单生命周期
    - v2 提供更好的状态管理和错误处理
    """

    def __init__(self, connectors: Dict[str, ConnectorBase], config: BasicCexDexArbConfig):
        # 初始化 markets 配置（如果未设置）
        if len(config.markets) == 0:
            config.markets = {
                config.cex_connector: {config.cex_trading_pair},
                config.dex_connector: {config.dex_trading_pair},
            }
        
        super().__init__(connectors, config)
        self.config = config

        # 用于存储待执行的套利动作
        self._pending_create_action: Optional[CreateExecutorAction] = None
        
        # 用于防止重复创建异步任务
        self._evaluation_task: Optional = None
        self._evaluation_lock = False

        # 解析交易对，提取 base 和 quote 资产
        self._cex_base, self._cex_quote = split_hb_trading_pair(config.cex_trading_pair)
        dex_base, self._dex_quote = split_hb_trading_pair(config.dex_trading_pair)

        # 验证 CEX 和 DEX 的 base 资产必须一致
        if dex_base != self._cex_base:
            raise ValueError(
                f"CEX 与 DEX 的 base 资产不一致：CEX={self._cex_base}, DEX={dex_base}。"
                f"套利要求两侧交易相同的 base 资产。"
            )

        # 转换配置参数为 Decimal 类型
        self._cex_fee_rate = Decimal(str(self.config.cex_fee_rate))
        self._dex_fee_rate = Decimal(str(self.config.dex_fee_rate))
        self._quote_conversion_rate = Decimal(str(self.config.quote_conversion_rate))
        self._gas_token_price_quote = Decimal(str(self.config.gas_token_price_quote))
        # Gas 转换比率：用于将 gas 代币换算成 CEX quote 资产
        self._gas_conversion_ratio = Decimal("1") / self._gas_token_price_quote

        try:
            self.logger().info(
                f"初始化 CEX-DEX 基础套利策略，已加载连接器: {list(self.connectors.keys())}"
            )
            self.logger().info(
                f"交易对: CEX={config.cex_trading_pair}, DEX={config.dex_trading_pair}"
            )
            self.logger().info(
                f"订单量: {config.order_amount} {self._cex_base}, "
                f"最小盈利率: {config.min_profitability * 100}%"
            )
            
            # 设置市场信息（MarketTradingPairTuple）
            self._setup_market_info()
            
            # 设置汇率源（用于不同 quote 资产的转换）
            self._setup_rate_source()
            
            self.logger().info("策略初始化完成，等待市场数据...")
        except Exception as exc:
            self.logger().error(f"初始化策略时出错: {exc}", exc_info=True)
            raise

    @classmethod
    def init_markets(cls, config: BasicCexDexArbConfig):
        """
        初始化策略需要的市场连接器
        
        此方法在策略启动前由框架调用，用于告知系统需要初始化哪些连接器和交易对
        """
        cls.markets = {
            config.cex_connector: {config.cex_trading_pair},
            config.dex_connector: {config.dex_trading_pair},
        }

    def on_tick(self):
        """
        策略主循环，每个 tick 周期调用一次（默认 1 秒）
        
        完整流程：
        1. 检查策略状态和连接器就绪
        2. 检查是否已有活跃执行器（限制并发）
        3. 获取两个方向的套利提案
        4. 计算两个方向的盈利率
        5. 选择盈利率最高且超过阈值的方向
        6. 检查余额并准备执行动作
        """
        super().on_tick()
        
        # 检查策略是否已停止
        if self._is_stop_triggered:
            return

        # 检查连接器是否就绪
        cex_ready = self.connectors[self.config.cex_connector].ready
        dex_ready = self.connectors[self.config.dex_connector].ready

        if not cex_ready or not dex_ready:
            self.logger().warning(
                f"等待连接器就绪... CEX: {cex_ready}, DEX: {dex_ready}"
            )
            return

        # 阶段 3：检查是否已有活跃执行器（限制并发）
        if self._has_active_executor():
            # 已有执行器在运行，跳过本次评估
            return

        # 防止重复创建评估任务
        if self._evaluation_lock:
            return
        
        # 检查上一个评估任务是否完成
        if self._evaluation_task is not None:
            if not self._evaluation_task.done():
                # 上一个任务还在运行，跳过本次
                return

        # 阶段 2 & 3：异步评估套利机会并准备执行动作
        import asyncio
        try:
            # 创建新的评估任务
            self._evaluation_task = asyncio.create_task(self._evaluate_and_prepare_action())
        except Exception as e:
            self.logger().error(f"创建评估任务时出错: {e}", exc_info=True)

    async def _evaluate_and_prepare_action(self):
        """
        异步评估套利机会并准备执行动作
        
        完整流程：
        1. 获取套利提案
        2. 计算两个方向的盈利率
        3. 选择最佳方向
        4. 检查余额
        5. 准备 CreateExecutorAction
        """
        # 设置锁，防止重复执行
        if self._evaluation_lock:
            return
        
        self._evaluation_lock = True
        
        try:
            # 步骤 1：获取套利提案
            proposals = await self._get_arbitrage_proposals(self.config.order_amount)
            
            if not proposals:
                self.logger().info("无法获取有效的套利提案")
                return

            # 步骤 2：计算两个方向的盈利率
            profit_results = {}  # {direction: (profit_pct, buy_price, sell_price)}
            
            for proposal in proposals:
                # 分离 CEX 和 DEX 两侧
                split_result = self._split_proposal_sides(
                    proposal=proposal,
                    cex_market=self._market_info_cex.market,
                    dex_market=self._market_info_dex.market,
                )
                
                if split_result is None:
                    continue
                
                cex_side, dex_side = split_result
                
                # 评估此方向的盈利率
                evaluation = self._evaluate_direction_profit(
                    self.config.order_amount,
                    cex_side,
                    dex_side
                )
                
                if evaluation is None:
                    continue
                
                direction, profit_pct, buy_price, sell_price = evaluation
                profit_results[direction] = (profit_pct, buy_price, sell_price)
                
                # 输出此方向的盈利率日志
                direction_name = "CEX买入+DEX卖出" if direction == "cex_buy" else "DEX买入+CEX卖出"
                self.logger().info(
                    f"{direction_name}方向: 净收益率 {(profit_pct * 100).quantize(Decimal('0.01'))}%"
                )

            # 如果没有任何有效的盈利评估结果
            if not profit_results:
                self.logger().info("暂无套利机会，无法获取有效报价")
                return

            # 步骤 3：选择盈利率最高的方向
            best_direction = max(profit_results.keys(), key=lambda d: profit_results[d][0])
            best_profit, best_buy_price, best_sell_price = profit_results[best_direction]
            
            direction_name = "CEX买入+DEX卖出" if best_direction == "cex_buy" else "DEX买入+CEX卖出"
            
            # 步骤 4：检查是否满足最小盈利率阈值
            if best_profit < self.config.min_profitability:
                self.logger().info(
                    f"最佳方向 ({direction_name}) 净收益率 {(best_profit * 100).quantize(Decimal('0.01'))}% "
                    f"低于阈值 {(self.config.min_profitability * 100).quantize(Decimal('0.01'))}%，"
                    f"暂不执行套利"
                )
                return

            # 步骤 5：检查余额
            if not self._check_sufficient_balance(
                self.config.order_amount,
                best_direction,
                best_buy_price
            ):
                return

            # 步骤 6：准备 CreateExecutorAction
            self.logger().info(
                f"选择方向: {direction_name}，订单量: {self._format_decimal(self.config.order_amount)} {self._cex_base}"
            )
            self.logger().info(
                f"预估净收益率: {(best_profit * 100).quantize(Decimal('0.01'))}%，"
                f"满足阈值 {(self.config.min_profitability * 100).quantize(Decimal('0.01'))}%"
            )

            # 阶段 3：构造 ArbitrageExecutorConfig
            if best_direction == "cex_buy":
                # CEX买入 + DEX卖出
                buying_market = ConnectorPair(
                    connector_name=self.config.cex_connector,
                    trading_pair=self.config.cex_trading_pair
                )
                selling_market = ConnectorPair(
                    connector_name=self.config.dex_connector,
                    trading_pair=self.config.dex_trading_pair
                )
            else:
                # DEX买入 + CEX卖出
                buying_market = ConnectorPair(
                    connector_name=self.config.dex_connector,
                    trading_pair=self.config.dex_trading_pair
                )
                selling_market = ConnectorPair(
                    connector_name=self.config.cex_connector,
                    trading_pair=self.config.cex_trading_pair
                )

            executor_config = ArbitrageExecutorConfig(
                buying_market=buying_market,
                selling_market=selling_market,
                order_amount=self.config.order_amount,
                min_profitability=self.config.min_profitability,
                gas_conversion_price=self._gas_conversion_ratio,
            )

            # 创建执行动作并存储
            self._pending_create_action = CreateExecutorAction(executor_config=executor_config)
            
            self.logger().info(
                f"已准备套利执行器，等待执行..."
            )
        
        except Exception as e:
            self.logger().error(f"评估套利机会时出错: {e}", exc_info=True)
        finally:
            # 释放锁
            self._evaluation_lock = False

    def _setup_market_info(self):
        """
        设置 CEX 和 DEX 的市场信息（MarketTradingPairTuple）
        
        MarketTradingPairTuple 包含：
        - market: ConnectorBase 实例
        - trading_pair: 交易对字符串
        - base_asset: base 资产符号
        - quote_asset: quote 资产符号
        """
        self._market_info_cex = MarketTradingPairTuple(
            market=self.connectors[self.config.cex_connector],
            trading_pair=self.config.cex_trading_pair,
            base_asset=self._cex_base,
            quote_asset=self._cex_quote
        )
        
        self._market_info_dex = MarketTradingPairTuple(
            market=self.connectors[self.config.dex_connector],
            trading_pair=self.config.dex_trading_pair,
            base_asset=self._cex_base,  # base 资产与 CEX 一致
            quote_asset=self._dex_quote
        )

        # 提取额外的固定费用（如 gas 费用）
        self._extra_fees_cex: List[TokenAmount] = []
        self._extra_fees_dex: List[TokenAmount] = []

        # 检查 CEX 是否有网络交易费用（一般 CEX 没有，但某些链上 CEX 可能有）
        if hasattr(self._market_info_cex.market, "network_transaction_fee"):
            fee = self._market_info_cex.market.network_transaction_fee
            if fee is not None:
                self._extra_fees_cex.append(
                    TokenAmount(token=fee.token, amount=Decimal(str(fee.amount)))
                )

        # 检查 DEX 的网络交易费用（gas 费用）
        if hasattr(self._market_info_dex.market, "network_transaction_fee"):
            fee = self._market_info_dex.market.network_transaction_fee
            if fee is not None:
                self._extra_fees_dex.append(
                    TokenAmount(token=fee.token, amount=Decimal(str(fee.amount)))
                )

    def _setup_rate_source(self):
        """
        设置固定汇率源，用于不同 quote 资产之间的转换
        
        例如：
        - DEX 使用 WETH 作为 quote
        - CEX 使用 USDT 作为 quote
        - 需要将 WETH 换算成 USDT 来统一计算盈利
        
        汇率配置：
        - DEX_QUOTE → CEX_QUOTE: quote_conversion_rate（如 1 WETH = 3800 USDT）
        - GAS_TOKEN → CEX_QUOTE: gas_token_price_quote（如 1 ETH = 3800 USDT）
        """
        self._rate_source = FixedRateSource()

        # 添加 DEX quote 到 CEX quote 的汇率
        self._add_rate_pair(
            self._dex_quote,
            self._cex_quote,
            self._quote_conversion_rate
        )

        # 添加 gas 代币到 quote 资产的汇率
        gas_token = getattr(self.config, "gas_token", "ETH")
        if self.config.gas_token_price_quote is not None:
            gas_to_cex_quote = self._gas_token_price_quote
            self._add_rate_pair(gas_token, self._cex_quote, gas_to_cex_quote)

            # 如果 DEX quote 与 CEX quote 不同，还需要添加 gas 到 DEX quote 的汇率
            if self._dex_quote != self._cex_quote:
                gas_to_dex_quote = gas_to_cex_quote / self._quote_conversion_rate
                self._add_rate_pair(gas_token, self._dex_quote, gas_to_dex_quote)

    def _add_rate_pair(self, base: str, quote: str, rate: Decimal):
        """
        添加一对汇率及其倒数
        
        参数：
        - base: 基础资产符号
        - quote: 计价资产符号
        - rate: 汇率（1 base = rate quote）
        
        例如：_add_rate_pair("WETH", "USDT", 3800) 会添加：
        - WETH-USDT: 3800（1 WETH = 3800 USDT）
        - USDT-WETH: 0.000263（1 USDT = 0.000263 WETH）
        """
        if rate <= Decimal("0"):
            return

        # 添加正向汇率
        self._rate_source.add_rate(f"{base}-{quote}", rate)

        # 添加反向汇率（倒数）
        reciprocal = Decimal("1") / rate
        self._rate_source.add_rate(f"{quote}-{base}", reciprocal)

    # ========== 阶段 2：利润评估逻辑 ==========

    async def _get_arbitrage_proposals(self, amount: Decimal) -> Optional[List[ArbProposal]]:
        """
        获取套利提案（两个方向）
        
        调用 create_arb_proposals() 函数获取两个方向的套利提案：
        1. CEX买入 + DEX卖出
        2. DEX买入 + CEX卖出
        
        参数：
        - amount: 订单量（base 资产数量）
        
        返回：
        - List[ArbProposal]: 包含两个方向的提案列表
        - None: 如果获取失败
        """
        try:
            proposals = await create_arb_proposals(
                market_info_1=self._market_info_cex,
                market_info_2=self._market_info_dex,
                market_1_extra_flat_fees=self._extra_fees_cex,
                market_2_extra_flat_fees=self._extra_fees_dex,
                order_amount=amount,
            )
            return proposals
        except Exception as exc:
            self.logger().warning(f"获取套利提案失败: {exc}")
            return None

    def _convert_flat_fees_to_quote(self, fees: List[TokenAmount]) -> Decimal:
        """
        将固定费用（如 gas 费用）转换为 CEX quote 资产计价
        
        参数：
        - fees: TokenAmount 列表（每个包含 token 符号和 amount）
        
        返回：
        - Decimal: 换算成 CEX quote 资产的总费用
        
        例如：gas 费用 0.001 ETH，ETH/USDT = 3800，则返回 3.8 USDT
        """
        total = Decimal("0")
        for fee in fees:
            amount = Decimal(str(fee.amount))
            if amount <= Decimal("0"):
                continue

            # 查找该代币到 CEX quote 的汇率
            pair = f"{fee.token}-{self._cex_quote}"
            rate = self._rate_source.get_pair_rate(pair)
            if rate is None:
                self.logger().warning(
                    f"未找到汇率 {pair}，无法换算费用 {amount} {fee.token}"
                )
                continue

            # 累加换算后的费用
            total += amount * rate

        return total

    def _convert_price_to_cex_quote(self, price: Decimal, quote_token: str) -> Optional[Decimal]:
        """
        将价格转换为 CEX quote 资产计价
        
        参数：
        - price: 原始价格（用 quote_token 计价）
        - quote_token: 原始计价资产符号
        
        返回：
        - Decimal: 换算成 CEX quote 资产的价格
        - None: 如果找不到汇率
        
        例如：DEX 价格 0.1 WETH，WETH/USDT = 3800，则返回 380 USDT
        """
        # 如果本身就是 CEX quote，直接返回
        if quote_token == self._cex_quote:
            return price

        # 查找汇率
        pair = f"{quote_token}-{self._cex_quote}"
        rate = self._rate_source.get_pair_rate(pair)
        if rate is None:
            self.logger().warning(f"未找到汇率 {pair}，无法转换价格")
            return None

        # 转换价格
        return price * rate

    @staticmethod
    def _split_proposal_sides(
        proposal: ArbProposal,
        cex_market: ConnectorBase,
        dex_market: ConnectorBase,
    ) -> Optional[Tuple[ArbProposalSide, ArbProposalSide]]:
        """
        从套利提案中分离出 CEX 和 DEX 的两侧
        
        参数：
        - proposal: 套利提案
        - cex_market: CEX 连接器实例
        - dex_market: DEX 连接器实例
        
        返回：
        - Tuple[ArbProposalSide, ArbProposalSide]: (CEX侧, DEX侧)
        - None: 如果无法分离
        """
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
    ) -> Optional[Tuple[str, Decimal, Optional[Decimal], Optional[Decimal]]]:
        """
        评估某个套利方向的净收益率
        
        参数：
        - amount: 订单量（base 资产数量）
        - cex_side: CEX 侧的提案（包含价格和费用信息）
        - dex_side: DEX 侧的提案（包含价格和费用信息）
        
        返回：
        - Tuple[str, Decimal, Decimal, Decimal]:
          * direction: 方向标识（"cex_buy" 或 "dex_buy"）
          * profit_pct: 净收益率（小数形式）
          * buy_price: 买入价格（用于计算所需资金）
          * sell_price: 卖出价格（用于参考）
        - None: 如果计算失败
        
        计算逻辑：
        1. 提取 CEX 和 DEX 的报价
        2. 将 DEX 价格转换为 CEX quote 计价（统一单位）
        3. 计算买入方的总成本 = 订单金额 + 手续费 + gas费
        4. 计算卖出方的总收益 = 订单金额 - 手续费 - gas费
        5. 净收益率 = (总收益 - 总成本) / 总成本
        """
        try:
            # 提取 CEX 和 DEX 的报价
            cex_price = Decimal(str(cex_side.quote_price))
            dex_price = Decimal(str(dex_side.quote_price))
        except (InvalidOperation, TypeError):
            self.logger().warning("无法解析价格，跳过此方向")
            return None

        # 将 DEX 价格转换为 CEX quote 计价
        dex_price_in_cex_quote = self._convert_price_to_cex_quote(
            dex_price,
            dex_side.market_info.quote_asset
        )
        if dex_price_in_cex_quote is None:
            return None

        # 换算固定费用（gas 费用等）为 CEX quote 计价
        cex_flat_fees = self._convert_flat_fees_to_quote(cex_side.extra_flat_fees)
        dex_flat_fees = self._convert_flat_fees_to_quote(dex_side.extra_flat_fees)

        # 根据 CEX 侧的买卖方向确定套利方向
        if cex_side.is_buy:
            # 方向 1：CEX买入 + DEX卖出
            direction = "cex_buy"

            # 计算总成本（买入方）
            total_cost = amount * cex_price  # 买入 base 资产花费的 quote
            total_cost += total_cost * self._cex_fee_rate  # 加上 CEX 手续费
            total_cost += cex_flat_fees  # 加上 CEX 的固定费用

            # 计算总收益（卖出方）
            total_revenue = amount * dex_price_in_cex_quote  # 卖出 base 资产获得的 quote
            total_revenue -= total_revenue * self._dex_fee_rate  # 减去 DEX 手续费
            total_revenue -= dex_flat_fees  # 减去 DEX 的固定费用（gas）

            buy_price = cex_price
            sell_price = dex_price_in_cex_quote
        else:
            # 方向 2：DEX买入 + CEX卖出
            direction = "dex_buy"

            # 计算总收益（卖出方）
            total_revenue = amount * cex_price  # 在 CEX 卖出 base 资产获得的 quote
            total_revenue -= total_revenue * self._cex_fee_rate  # 减去 CEX 手续费
            total_revenue -= cex_flat_fees  # 减去 CEX 的固定费用

            # 计算总成本（买入方）
            total_cost = amount * dex_price_in_cex_quote  # 在 DEX 买入 base 资产花费的 quote
            total_cost += total_cost * self._dex_fee_rate  # 加上 DEX 手续费
            total_cost += dex_flat_fees  # 加上 DEX 的固定费用（gas）

            buy_price = dex_price_in_cex_quote
            sell_price = cex_price

        # 防止除零错误
        if total_cost <= Decimal("0"):
            self.logger().warning("总成本为零或负数，无法计算收益率")
            return None

        # 计算净收益率
        profit_pct = (total_revenue - total_cost) / total_cost

        return direction, profit_pct, buy_price, sell_price

    def _check_sufficient_balance(
        self,
        amount: Decimal,
        direction: str,
        buy_price: Decimal
    ) -> bool:
        """
        检查账户余额是否足够执行套利
        
        参数：
        - amount: 订单量（base 资产数量）
        - direction: 方向标识（"cex_buy" 或 "dex_buy"）
        - buy_price: 买入价格（用于计算所需 quote 资金）
        
        返回：
        - bool: True 表示余额充足，False 表示余额不足
        """
        if direction == "cex_buy":
            # CEX买入：需要检查 CEX 的 quote 余额
            required_quote = amount * buy_price
            available_quote = Decimal(str(
                self.connectors[self.config.cex_connector].get_available_balance(self._cex_quote)
            ))

            if available_quote < required_quote:
                self.logger().error(
                    f"CEX ({self.config.cex_connector}) {self._cex_quote} 余额不足：" 
                    f"需要 {self._format_decimal(required_quote)}，"
                    f"可用 {self._format_decimal(available_quote)}"
                )
                return False

        elif direction == "dex_buy":
            # DEX买入：需要检查 DEX 的 base 余额（用于在 DEX 卖出）
            available_base = Decimal(str(
                self.connectors[self.config.dex_connector].get_available_balance(self._cex_base)
            ))

            if available_base < amount:
                self.logger().error(
                    f"DEX ({self.config.dex_connector}) {self._cex_base} 余额不足：" 
                    f"需要 {self._format_decimal(amount)}，"
                    f"可用 {self._format_decimal(available_base)}"
                )
                return False

        return True

    @staticmethod
    def _format_decimal(value: Optional[Decimal]) -> str:
        """
        格式化 Decimal 数值，去除尾部的零
        
        参数：
        - value: Decimal 值
        
        返回：
        - str: 格式化后的字符串
        """
        if value is None:
            return "N/A"
        formatted = format(value, "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted or "0"

    # ========== 阶段 3：执行器接入 ==========

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        """
        推送待执行的套利动作
        
        此方法由 Strategy V2 框架定期调用，用于获取策略准备执行的动作。
        如果有待执行的 CreateExecutorAction，返回后框架会创建相应的执行器。
        
        返回：
        - List[CreateExecutorAction]: 待执行的动作列表
        """
        if self._pending_create_action is not None:
            action = self._pending_create_action
            self._pending_create_action = None  # 清空待执行动作
            return [action]
        return []

    def stop_actions_proposal(self) -> List:
        """
        推送待停止的执行器动作
        
        此策略不主动停止执行器，由执行器自己管理生命周期。
        """
        return []

    def _has_active_executor(self) -> bool:
        """
        检查是否已有活跃的执行器
        
        用于限制并发执行器数量，避免同时运行多个套利循环导致资金冲突。
        
        返回：
        - bool: True 表示已达到并发限制，False 表示可以创建新执行器
        """
        active_executors = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.is_active
        )
        return len(active_executors) >= self.config.max_concurrent_executors


# ========== 后续扩展建议 ==========
#
# 1. 监听执行器完成事件，统计成功/失败次数
#    - 在策略类中添加执行器事件监听器
#    - 记录每次套利的实际盈利、滑点、gas 消耗等指标
#    - 生成定期报告，分析策略表现
#
# 2. 根据历史执行结果动态调整 min_profitability
#    - 如果成功率高，可以降低阈值以增加交易频率
#    - 如果滑点经常导致实际盈利低于预期，提高阈值
#    - 实现自适应的盈利阈值算法
#
# 3. 实现止损机制：连续失败 N 次后暂停策略
#    - 添加 consecutive_failures 计数器
#    - 监听 MarketOrderFailureEvent 事件
#    - 达到阈值后自动触发 self.stop()
#    - 发送告警通知运营人员
#
# 4. 添加 Telegram/Discord 通知集成
#    - 套利机会触发时发送通知
#    - 执行成功/失败时发送详细报告
#    - 余额不足、连接器断开等异常情况发送告警
#
# 5. 实现更精细的 gas 费用预估（调用 Gateway API）
#    - 当前使用固定的 gas_token_price_quote
#    - 可以实时查询 gas price 和 gas limit
#    - 根据网络拥堵情况动态调整 gas 费用估算
#    - 在高 gas 费用时暂停策略以避免亏损
#
# 6. 支持多交易对套利
#    - 当前策略仅支持单一交易对
#    - 可以扩展为同时监控多个交易对
#    - 每个交易对独立计算盈利率
#    - 选择全局最优的套利机会
#
# 7. 实现价格冲击保护
#    - 在大额订单时，考虑市场深度和价格冲击
#    - 动态调整订单量以确保实际成交价格在可接受范围内
#    - 添加最大滑点保护参数
#
# 8. 集成风险管理模块
#    - 设置每日/每周最大亏损限额
#    - 监控持仓风险和市场波动
#    - 自动调整仓位大小
#
# 9. 优化异步任务管理
#    - 当前在 on_tick 中创建异步任务，可能导致任务堆积
#    - 使用更优雅的任务队列和锁机制
#    - 确保同一时间只有一个评估任务在运行
#
# 10. 添加回测功能
#     - 使用历史数据验证策略有效性
#     - 优化参数配置（order_amount, min_profitability 等）
#     - 估算预期收益和风险
#

