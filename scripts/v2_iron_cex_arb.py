"""
V2 CEX-CEX Arbitrage Script for IRON-USDT
Based on simple_arbitrage_example.py

This script performs arbitrage between MEXC and Gate.io for IRON-USDT pair.
Can be controlled via MQTT API (start/stop/status).
"""
import os
from decimal import Decimal
from typing import Any, Dict

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class V2IronCexArbConfig(BaseClientModel):
    """Configuration for V2 IRON CEX Arbitrage"""
    script_file_name: str = os.path.basename(__file__)
    
    exchange_A: str = Field(
        default="mexc",
        json_schema_extra={"prompt": "First exchange", "prompt_on_new": True}
    )
    exchange_B: str = Field(
        default="gate_io",
        json_schema_extra={"prompt": "Second exchange", "prompt_on_new": True}
    )
    trading_pair: str = Field(
        default="IRON-USDT",
        json_schema_extra={"prompt": "Trading pair", "prompt_on_new": True}
    )
    order_amount: Decimal = Field(
        default=Decimal("100"),
        json_schema_extra={"prompt": "Order amount (in base asset)", "prompt_on_new": True}
    )
    min_profitability: Decimal = Field(
        default=Decimal("0.02"),
        json_schema_extra={"prompt": "Minimum profitability (e.g., 0.02 = 2%)", "prompt_on_new": True}
    )


class V2IronCexArb(ScriptStrategyBase):
    """
    V2 CEX-CEX Arbitrage for IRON-USDT between MEXC and Gate.io
    
    Features:
    - Monitors price differences between two exchanges
    - Executes simultaneous buy/sell when profitable
    - Can be controlled via MQTT API
    """
    
    trades_executed = 0
    
    @classmethod
    def init_markets(cls, config: V2IronCexArbConfig):
        cls.markets = {
            config.exchange_A: {config.trading_pair},
            config.exchange_B: {config.trading_pair}
        }

    def __init__(self, connectors: Dict[str, Any], config: V2IronCexArbConfig):
        super().__init__(connectors)
        self.config = config
        self.base, self.quote = config.trading_pair.split("-")

    def on_tick(self):
        """Called every tick (default 1 second)"""
        try:
            vwap_prices = self.get_vwap_prices_for_amount(self.config.order_amount)
            profitability = self.get_profitability_analysis(vwap_prices)
            
            # Log current spread
            buy_a_sell_b_pct = profitability["buy_a_sell_b"]["profitability_pct"]
            buy_b_sell_a_pct = profitability["buy_b_sell_a"]["profitability_pct"]
            
            self.logger().info(
                f"Spread: buy {self.config.exchange_A} sell {self.config.exchange_B}: {buy_a_sell_b_pct:.2%} | "
                f"buy {self.config.exchange_B} sell {self.config.exchange_A}: {buy_b_sell_a_pct:.2%}"
            )
            
            # Check and execute if profitable
            proposal = self.check_profitability_and_create_proposal(profitability, vwap_prices)
            if len(proposal) > 0:
                proposal_adjusted = self.adjust_proposal_to_budget(proposal)
                self.place_orders(proposal_adjusted)
                
        except Exception as e:
            self.logger().error(f"Error in on_tick: {e}")

    def get_vwap_prices_for_amount(self, amount: Decimal) -> Dict:
        """Get Volume Weighted Average Prices from both exchanges"""
        bid_ex_a = self.connectors[self.config.exchange_A].get_vwap_for_volume(self.config.trading_pair, False, amount)
        ask_ex_a = self.connectors[self.config.exchange_A].get_vwap_for_volume(self.config.trading_pair, True, amount)
        bid_ex_b = self.connectors[self.config.exchange_B].get_vwap_for_volume(self.config.trading_pair, False, amount)
        ask_ex_b = self.connectors[self.config.exchange_B].get_vwap_for_volume(self.config.trading_pair, True, amount)
        
        return {
            self.config.exchange_A: {"bid": bid_ex_a.result_price, "ask": ask_ex_a.result_price},
            self.config.exchange_B: {"bid": bid_ex_b.result_price, "ask": ask_ex_b.result_price}
        }

    def get_fees_percentages(self, vwap_prices: Dict) -> Dict:
        """Get trading fees for both exchanges"""
        a_fee = self.connectors[self.config.exchange_A].get_fee(
            base_currency=self.base, quote_currency=self.quote,
            order_type=OrderType.MARKET, order_side=TradeType.BUY,
            amount=self.config.order_amount, price=vwap_prices[self.config.exchange_A]["ask"],
            is_maker=False
        ).percent

        b_fee = self.connectors[self.config.exchange_B].get_fee(
            base_currency=self.base, quote_currency=self.quote,
            order_type=OrderType.MARKET, order_side=TradeType.BUY,
            amount=self.config.order_amount, price=vwap_prices[self.config.exchange_B]["ask"],
            is_maker=False
        ).percent

        return {self.config.exchange_A: a_fee, self.config.exchange_B: b_fee}

    def get_profitability_analysis(self, vwap_prices: Dict) -> Dict:
        """Calculate profitability for both arbitrage directions"""
        fees = self.get_fees_percentages(vwap_prices)
        
        # Buy on A, Sell on B
        buy_a_price = vwap_prices[self.config.exchange_A]["ask"] * (1 + fees[self.config.exchange_A])
        sell_b_price = vwap_prices[self.config.exchange_B]["bid"] * (1 - fees[self.config.exchange_B])
        buy_a_sell_b_profit = (sell_b_price - buy_a_price) / buy_a_price
        
        # Buy on B, Sell on A
        buy_b_price = vwap_prices[self.config.exchange_B]["ask"] * (1 + fees[self.config.exchange_B])
        sell_a_price = vwap_prices[self.config.exchange_A]["bid"] * (1 - fees[self.config.exchange_A])
        buy_b_sell_a_profit = (sell_a_price - buy_b_price) / buy_b_price
        
        return {
            "buy_a_sell_b": {
                "profitability_pct": buy_a_sell_b_profit,
                "buy_price": vwap_prices[self.config.exchange_A]["ask"],
                "sell_price": vwap_prices[self.config.exchange_B]["bid"]
            },
            "buy_b_sell_a": {
                "profitability_pct": buy_b_sell_a_profit,
                "buy_price": vwap_prices[self.config.exchange_B]["ask"],
                "sell_price": vwap_prices[self.config.exchange_A]["bid"]
            }
        }

    def check_profitability_and_create_proposal(self, profitability: Dict, vwap_prices: Dict) -> Dict:
        """Create order proposal if profitable"""
        proposal = {}
        
        if profitability["buy_a_sell_b"]["profitability_pct"] > self.config.min_profitability:
            self.logger().info(
                f"🚀 Profitable! Buy {self.config.exchange_A}, Sell {self.config.exchange_B}: "
                f"{profitability['buy_a_sell_b']['profitability_pct']:.2%}"
            )
            proposal[self.config.exchange_A] = OrderCandidate(
                trading_pair=self.config.trading_pair, is_maker=False,
                order_type=OrderType.MARKET, order_side=TradeType.BUY,
                amount=self.config.order_amount, price=vwap_prices[self.config.exchange_A]["ask"]
            )
            proposal[self.config.exchange_B] = OrderCandidate(
                trading_pair=self.config.trading_pair, is_maker=False,
                order_type=OrderType.MARKET, order_side=TradeType.SELL,
                amount=self.config.order_amount, price=vwap_prices[self.config.exchange_B]["bid"]
            )
            
        elif profitability["buy_b_sell_a"]["profitability_pct"] > self.config.min_profitability:
            self.logger().info(
                f"🚀 Profitable! Buy {self.config.exchange_B}, Sell {self.config.exchange_A}: "
                f"{profitability['buy_b_sell_a']['profitability_pct']:.2%}"
            )
            proposal[self.config.exchange_B] = OrderCandidate(
                trading_pair=self.config.trading_pair, is_maker=False,
                order_type=OrderType.MARKET, order_side=TradeType.BUY,
                amount=self.config.order_amount, price=vwap_prices[self.config.exchange_B]["ask"]
            )
            proposal[self.config.exchange_A] = OrderCandidate(
                trading_pair=self.config.trading_pair, is_maker=False,
                order_type=OrderType.MARKET, order_side=TradeType.SELL,
                amount=self.config.order_amount, price=vwap_prices[self.config.exchange_A]["bid"]
            )
            
        return proposal

    def adjust_proposal_to_budget(self, proposal: Dict) -> Dict:
        """Adjust orders based on available budget"""
        adjusted = {}
        for exchange, order in proposal.items():
            adjusted_order = self.connectors[exchange].budget_checker.adjust_candidate(order, all_or_none=True)
            if adjusted_order.amount > 0:
                adjusted[exchange] = adjusted_order
        return adjusted

    def place_orders(self, proposal: Dict):
        """Place orders on both exchanges"""
        for exchange, order in proposal.items():
            if order.order_side == TradeType.BUY:
                self.buy(exchange, order.trading_pair, order.amount, order.order_type, order.price)
            else:
                self.sell(exchange, order.trading_pair, order.amount, order.order_type, order.price)
        self.trades_executed += 1
        self.logger().info(f"✅ Trade #{self.trades_executed} executed!")

    def did_fill_order(self, event: OrderFilledEvent):
        """Called when an order is filled"""
        self.logger().info(
            f"Order filled: {event.trade_type.name} {event.amount} {self.base} @ {event.price}"
        )

    def format_status(self) -> str:
        """Format status for display"""
        lines = []
        lines.append(f"Strategy: V2 IRON CEX Arbitrage")
        lines.append(f"Exchanges: {self.config.exchange_A} <-> {self.config.exchange_B}")
        lines.append(f"Pair: {self.config.trading_pair}")
        lines.append(f"Order Amount: {self.config.order_amount} {self.base}")
        lines.append(f"Min Profitability: {self.config.min_profitability:.2%}")
        lines.append(f"Trades Executed: {self.trades_executed}")
        return "\n".join(lines)
