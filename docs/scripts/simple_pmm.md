# Simple Pure Market Making (PMM) Strategy: Mathematical Framework and Implementation Guide

**Author:** Trading Framework Team  
**Date:** 2024-03-06

## Table of Contents
- [Simple Pure Market Making (PMM) Strategy: Mathematical Framework and Implementation Guide](#simple-pure-market-making-pmm-strategy-mathematical-framework-and-implementation-guide)
  - [Table of Contents](#table-of-contents)
  - [Introduction](#introduction)
  - [Strategy Overview](#strategy-overview)
  - [Mathematical Framework](#mathematical-framework)
    - [Notation and Parameters](#notation-and-parameters)
    - [Price Determination Model](#price-determination-model)
    - [Order Placement Logic](#order-placement-logic)
    - [Profit and Loss Model](#profit-and-loss-model)
  - [Profit Mechanism](#profit-mechanism)
    - [How the Strategy Makes Money](#how-the-strategy-makes-money)
    - [Profit Example](#profit-example)
  - [Major Risks](#major-risks)
    - [Directional Market Risk](#directional-market-risk)
    - [Volatility Risk](#volatility-risk)
    - [Fee and Slippage Risk](#fee-and-slippage-risk)
    - [Technical and Execution Risk](#technical-and-execution-risk)
    - [Risk Mitigation Strategies](#risk-mitigation-strategies)
  - [Implementation Architecture](#implementation-architecture)
    - [Configuration Parameters](#configuration-parameters)
    - [Core Functions](#core-functions)
    - [Execution Flow](#execution-flow)
  - [Optimization Techniques](#optimization-techniques)
    - [Parameter Tuning](#parameter-tuning)
    - [Risk Management](#risk-management)
  - [Performance Metrics](#performance-metrics)
  - [Practical Implementation Guidelines](#practical-implementation-guidelines)
  - [Conclusion](#conclusion)

## Introduction

The Simple Pure Market Making (PMM) strategy represents a fundamental approach to providing liquidity in cryptocurrency markets. This document provides a comprehensive mathematical framework and implementation guide for the strategy as implemented in the `simple_pmm.py` script. By placing buy and sell orders around a reference price and regularly refreshing these orders, the strategy aims to capture the bid-ask spread while managing inventory risk.

## Strategy Overview

The Simple PMM strategy operates by:

1. Placing limit buy (bid) and sell (ask) orders at specified distances from a reference price
2. Regularly canceling and replacing these orders to adapt to market conditions
3. Managing order sizes within available balances
4. Processing fills when orders are executed

The strategy is designed to work on a single exchange and trading pair, making it an ideal starting point for market making in cryptocurrency markets.

## Mathematical Framework

### Notation and Parameters

- **$P_{ref}$**: Reference price (mid price or last traded price)
- **$S_{bid}$**: Bid spread as a percentage
- **$S_{ask}$**: Ask spread as a percentage
- **$Q$**: Order amount (in base asset units)
- **$\Delta t$**: Order refresh time interval (in seconds)
- **$B_{base}$**: Available base asset balance
- **$B_{quote}$**: Available quote asset balance

### Price Determination Model

The buy and sell prices are calculated based on the reference price and the configured spreads:

**Bid Price:**
$$P_{bid} = P_{ref} \times (1 - S_{bid})$$

**Ask Price:**
$$P_{ask} = P_{ref} \times (1 + S_{ask})$$

The reference price $P_{ref}$ is determined by the `price_type` parameter:
- If `price_type = "mid"`, then $P_{ref} = \frac{P_{best\_bid} + P_{best\_ask}}{2}$
- If `price_type = "last"`, then $P_{ref} = P_{last\_trade}$

### Order Placement Logic

For each order refresh cycle, the strategy:

1. Cancels all existing orders
2. Creates new order proposals at the calculated prices
3. Adjusts order sizes based on available balances
4. Places the adjusted orders

The order proposals are represented as:

$$Orders = \{(P_{bid}, Q, "buy"), (P_{ask}, Q, "sell")\}$$

Order size adjustment ensures that:
- Buy order value $P_{bid} \times Q \leq B_{quote}$
- Sell order size $Q \leq B_{base}$

### Profit and Loss Model

The expected profit per successful round-trip trade (buy followed by sell) is:

$$E[Profit] = Q \times (P_{ask} - P_{bid}) - Fees$$

Where:
$$Fees = Q \times P_{bid} \times f_{maker} + Q \times P_{ask} \times f_{maker}$$

And $f_{maker}$ is the maker fee rate of the exchange.

The annualized expected return can be estimated as:

$$ROI_{annual} = \frac{E[Profit]}{Q \times P_{bid}} \times \frac{365 \times 24 \times 3600}{\Delta t \times 2} \times p_{fill}$$

Where $p_{fill}$ is the probability of both orders being filled within a cycle.

## Profit Mechanism

### How the Strategy Makes Money

The Simple PMM strategy generates profit through a market making approach, similar to how traditional market makers operate in financial markets. The core profit mechanism consists of:

1. **Spread Capture**: The primary source of profit comes from capturing the bid-ask spread. By placing buy orders at a price lower than the reference price and sell orders higher than the reference price, the strategy aims to profit from the price difference when both orders are executed.

2. **Order Cycling**: The strategy continuously refreshes orders at regular intervals, allowing it to adapt to changing market conditions and maintain optimal pricing for spread capture.

3. **Volume Accumulation**: While individual trade profits may be small (often fractions of a percent), the cumulative effect of multiple successful trades can generate significant returns, especially in markets with higher volatility and trading volumes.

The strategy is most profitable in range-bound, sideways markets where prices oscillate within a defined range, allowing both buy and sell orders to be filled regularly.

### Profit Example

Let's illustrate with a concrete example:

**Initial Parameters:**
- Trading Pair: ETH-USDT
- Reference Price ($P_{ref}$): 2,000 USDT
- Bid Spread ($S_{bid}$): 0.2% (0.002)
- Ask Spread ($S_{ask}$): 0.2% (0.002)
- Order Amount ($Q$): 0.1 ETH
- Maker Fee ($f_{maker}$): 0.1% (0.001)

**Order Placement:**
- Bid Price: $2,000 \times (1 - 0.002) = 1,996$ USDT
- Ask Price: $2,000 \times (1 + 0.002) = 2,004$ USDT

**Scenario: Both Orders Fill**
1. Buy 0.1 ETH at 1,996 USDT, total cost: 199.6 USDT
2. Buy order fee: $0.1 \times 1,996 \times 0.001 = 0.1996$ USDT
3. Sell 0.1 ETH at 2,004 USDT, total revenue: 200.4 USDT
4. Sell order fee: $0.1 \times 2,004 \times 0.001 = 0.2004$ USDT
5. Gross profit: $200.4 - 199.6 = 0.8$ USDT
6. Net profit after fees: $0.8 - 0.1996 - 0.2004 = 0.4$ USDT
7. Return on investment: $\frac{0.4}{199.6} \approx 0.2\%$ per round-trip

If the strategy completes just 2 such round-trips per day, it could generate approximately 0.4% daily return on the deployed capital. This compounds to a theoretical annual return of over 100% (assuming consistent market conditions, which is unlikely in practice).

## Major Risks

While the Simple PMM strategy can be profitable in certain market conditions, it is exposed to several significant risks that traders must understand and manage.

### Directional Market Risk

This is the most significant risk for market making strategies:

- **One-sided Fills**: In trending markets, orders on one side (the direction against the trend) will get filled more frequently than orders on the other side. For example, in a downtrend, buy orders will be filled while sell orders remain unexecuted.

- **Inventory Accumulation**: This leads to accumulating inventory on one side, potentially resulting in significant unrealized losses if the trend continues.

- **Example**: If ETH price starts at 2,000 USDT and begins a sustained decline to 1,800 USDT, your buy orders at 1,996, 1,976, 1,956, etc. will continue to fill as the price drops, while your sell orders remain unfilled. This results in an increasing ETH inventory bought at higher prices than the current market value.

### Volatility Risk

- **Large Price Swings**: Sudden, significant price movements can result in filled orders at unfavorable prices before the strategy can update its orders.

- **Slippage**: During high volatility, the actual execution prices may differ substantially from the placed order prices.

- **Amplified Losses**: In highly volatile conditions, losses can be much larger than the expected spread profits.

### Fee and Slippage Risk

- **Profit Erosion**: If trading fees are high relative to the captured spread, they can significantly reduce or eliminate profitability.

- **Hidden Costs**: Market impact, especially for larger orders or less liquid markets, can create additional implicit costs not captured in the basic profit model.

### Technical and Execution Risk

- **API Failures**: Connectivity issues or exchange API limitations may prevent timely order updates.

- **Latency Issues**: Delays in order placement or cancellation can result in stale orders remaining active during unfavorable price movements.

- **System Outages**: Strategy interruptions may leave open positions unmanaged during critical market movements.

### Risk Mitigation Strategies

The Simple PMM implementation includes some basic risk management features:

1. **Regular Order Refreshing**: By updating orders at fixed intervals, the strategy reduces the risk of stale orders in moving markets.

2. **Budget Constraints**: Automatic order size adjustments prevent overcommitting capital.

3. **Single Exchange Execution**: Operating on a single exchange eliminates cross-exchange execution risk.

For improved risk management, consider implementing:

1. **Inventory Management**: Dynamically adjust order sizes based on current inventory levels to avoid excessive accumulation of one asset.

2. **Dynamic Spread Adjustment**: Widen spreads during higher volatility periods to compensate for increased risk.

3. **Maximum Drawdown Limits**: Implement stop-loss mechanisms that pause the strategy if unrealized losses exceed predefined thresholds.

4. **Trend Detection**: Incorporate basic trend analysis to reduce or pause trading during strong directional markets.

## Implementation Architecture

### Configuration Parameters

The strategy is configured through the following parameters:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `exchange` | string | Exchange where the bot will trade | "binance_paper_trade" |
| `trading_pair` | string | Trading pair in which the bot will place orders | "ETH-USDT" |
| `order_amount` | decimal | Order amount (denominated in base asset) | 0.01 |
| `bid_spread` | decimal | Bid order spread (in percent) | 0.001 (0.1%) |
| `ask_spread` | decimal | Ask order spread (in percent) | 0.001 (0.1%) |
| `order_refresh_time` | integer | Order refresh time (in seconds) | 15 |
| `price_type` | string | Price type to use (mid or last) | "mid" |

### Core Functions

The strategy implementation revolves around the following key functions:

1. **`on_tick()`**: Main execution loop that runs every clock tick
2. **`create_proposal()`**: Generates the order proposal based on current market conditions
3. **`adjust_proposal_to_budget()`**: Adjusts order sizes based on available balances
4. **`place_orders()`**: Executes the orders on the exchange
5. **`cancel_all_orders()`**: Cancels all active orders
6. **`did_fill_order()`**: Handles order fill events

### Execution Flow

The strategy execution follows this sequence:

1. Initialize markets and configuration on startup
2. On each tick, check if it's time to refresh orders
3. If refresh is needed:
   - Cancel all existing orders
   - Calculate new bid and ask prices based on the reference price
   - Create order proposals with the specified amounts
   - Adjust proposals based on available balances
   - Place the adjusted orders on the exchange
4. Process any order fill events that occur

## Optimization Techniques

### Parameter Tuning

The performance of the Simple PMM strategy can be optimized by tuning these key parameters:

1. **Spread Parameters**: 
   - Wider spreads reduce fill probability but increase profit per trade
   - Narrower spreads increase fill probability but reduce profit per trade
   - Optimal spread depends on market volatility: $S_{optimal} \approx k \times \sigma_{daily}$

2. **Order Refresh Time**:
   - Shorter refresh times allow quicker adaptation to market changes
   - Longer refresh times reduce exchange API calls and potential fees
   - Optimal refresh time balances responsiveness with operational efficiency

3. **Order Size**:
   - Larger sizes increase potential profit but also increase inventory risk
   - Smaller sizes reduce risk but may lead to lower overall returns
   - Optimal size depends on available capital and market liquidity

### Risk Management

The strategy implements several risk management techniques:

1. **Budget Constraints**: Orders are automatically adjusted to respect available balances
2. **Regular Order Refreshing**: Prevents stale orders during market movements
3. **Single Exchange Operation**: Eliminates cross-exchange execution risk

Additional risk management could include:
- Inventory management to balance buy/sell exposure
- Volatility-based spread adjustments
- Stop-loss mechanisms for adverse market movements

## Performance Metrics

Key metrics to evaluate the strategy's performance include:

1. **Spread Capture Rate**: Percentage of theoretical spread actually captured
2. **Fill Ratio**: Proportion of placed orders that get filled
3. **Inventory Turnover**: Rate at which inventory is bought and sold
4. **Daily Profit and Loss**: Net trading profit after fees
5. **Sharpe Ratio**: Risk-adjusted return metric

## Practical Implementation Guidelines

For optimal implementation of the Simple PMM strategy:

1. **Market Selection**:
   - Choose markets with sufficient liquidity
   - Select trading pairs with moderate volatility
   - Consider maker fee structures of different exchanges

2. **Initial Parameter Settings**:
   - Start with wider spreads in more volatile markets
   - Use shorter refresh times during market hours with higher activity
   - Begin with smaller order sizes to limit risk during testing

3. **Monitoring and Adjustment**:
   - Regularly review fill rates and adjust spreads accordingly
   - Monitor inventory balance and implement rebalancing if needed
   - Track profit performance across different market conditions

## Conclusion

The Simple PMM strategy provides a foundational approach to market making in cryptocurrency markets. By placing orders around a reference price and regularly refreshing them, traders can attempt to capture the bid-ask spread while managing risk.

The mathematical framework presented here offers a structured approach to understanding and implementing the strategy. Through careful parameter tuning and risk management, traders can adapt the strategy to various market conditions and trading objectives.

Future enhancements might include dynamic spread adjustment based on volatility, inventory management techniques, and integration with advanced order types. The simple architecture makes this strategy an excellent starting point for algorithmic trading in cryptocurrency markets. 