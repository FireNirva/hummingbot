from decimal import Decimal
from typing import Literal, Optional

from hummingbot.strategy_v2.executors.data_types import ConnectorPair, ExecutorConfigBase


class ArbitrageExecutorConfig(ExecutorConfigBase):
    type: Literal["arbitrage_executor"] = "arbitrage_executor"
    buying_market: ConnectorPair
    selling_market: ConnectorPair
    order_amount: Decimal
    min_profitability: Decimal
    gas_conversion_price: Optional[Decimal] = None
    concurrent_orders_submission: bool = True
    prioritize_non_amm_first: bool = False
    retry_failed_orders: bool = True
    execution_mode: Literal["auto", "cex_first", "dex_first"] = "auto"
    buying_quote_id: Optional[str] = None
    selling_quote_id: Optional[str] = None
