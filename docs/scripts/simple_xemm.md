# Simple Cross-Exchange Market Making (XEMM) Strategy: Mathematical Framework and Implementation Guide

**Author:** Trading Framework Team  
**Date:** 2024-03-06

## Table of Contents
- [Simple Cross-Exchange Market Making (XEMM) Strategy: Mathematical Framework and Implementation Guide](#simple-cross-exchange-market-making-xemm-strategy-mathematical-framework-and-implementation-guide)
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
    - [Execution Risk](#execution-risk)
    - [Market Movement Risk](#market-movement-risk)
    - [Slippage Risk](#slippage-risk)
    - [Exchange Risk](#exchange-risk)
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

The Simple Cross-Exchange Market Making (XEMM) strategy provides a framework for arbitrage between two exchanges. This document outlines the mathematical framework and implementation guide for the strategy as implemented in the `simple_xemm.py` script. By placing orders on a maker exchange and hedging filled orders on a taker exchange, the strategy aims to profit from price differentials while minimizing directional market risk.

## Strategy Overview

The Simple XEMM strategy operates through the following key mechanisms:

1. Placing limit orders on a maker exchange at calculated prices based on the taker exchange's prices
2. When orders on the maker exchange are filled, immediately executing hedging trades on the taker exchange
3. Continuously monitoring the price spread between exchanges to maintain profitability
4. Regularly refreshing orders when they age or when the spread falls below the minimum threshold

Unlike pure market making strategies that profit from bid-ask spreads on a single exchange, XEMM creates a market on one exchange while using another exchange as a reference and hedging venue. This approach aims to reduce inventory risk and provide more consistent returns.

## Mathematical Framework

### Notation and Parameters

- **$P_{maker}^{bid}$, $P_{maker}^{ask}$**: Bid and ask prices on the maker exchange
- **$P_{taker}^{bid}$, $P_{taker}^{ask}$**: Bid and ask prices on the taker exchange
- **$S_{bps}$**: Target spread in basis points (1 bp = 0.01%)
- **$S_{min}$**: Minimum acceptable spread in basis points
- **$Q$**: Order amount (in base asset units)
- **$f_{maker}$**: Fee rate on the maker exchange
- **$f_{taker}$**: Fee rate on the taker exchange
- **$B_{base,M}$, $B_{quote,M}$**: Available base and quote asset balances on maker exchange
- **$B_{base,T}$, $B_{quote,T}$**: Available base and quote asset balances on taker exchange
- **$\alpha$**: Slippage buffer in basis points
- **$t_{max}$**: Maximum order age in seconds

### Price Determination Model

The strategy calculates maker exchange order prices based on taker exchange prices:

**Maker Buy (Bid) Price:**
$$P_{maker}^{bid} = P_{taker}^{sell} \times (1 - \frac{S_{bps}}{10000})$$

**Maker Sell (Ask) Price:**
$$P_{maker}^{ask} = P_{taker}^{buy} \times (1 + \frac{S_{bps}}{10000})$$

Where:
- $P_{taker}^{sell}$ is the price to sell on the taker exchange (for hedging maker buys)
- $P_{taker}^{buy}$ is the price to buy on the taker exchange (for hedging maker sells)

### Order Placement Logic

For each trading cycle, the strategy:

1. Obtains current prices from the taker exchange
2. Calculates maker order prices using the formulas above
3. Places buy and sell orders on the maker exchange if not already placed
4. Monitors existing orders and cancels them if:
   - The spread falls below the minimum threshold
   - The order age exceeds the maximum allowed age

Buy order cancellation condition:
$$P_{maker}^{bid} > P_{taker}^{sell} \times (1 - \frac{S_{min}}{10000}) \text{ OR } t_{order} > t_{max}$$

Sell order cancellation condition:
$$P_{maker}^{ask} < P_{taker}^{buy} \times (1 + \frac{S_{min}}{10000}) \text{ OR } t_{order} > t_{max}$$

### Profit and Loss Model

The expected profit for a complete cycle (maker buy filled and hedged with taker sell) is:

$$E[Profit_{buy}] = Q \times (P_{taker}^{sell} \times (1 - f_{taker}) - P_{maker}^{bid} \times (1 + f_{maker}))$$

For a maker sell filled and hedged with taker buy:

$$E[Profit_{sell}] = Q \times (P_{maker}^{ask} \times (1 - f_{maker}) - P_{taker}^{buy} \times (1 + f_{taker}))$$

The net expected profit from both sides is:

$$E[Profit_{net}] = E[Profit_{buy}] + E[Profit_{sell}]$$

This can be simplified to:

$$E[Profit_{net}] \approx Q \times P_{avg} \times (\frac{2 \times S_{bps}}{10000} - f_{maker} - f_{taker})$$

Where $P_{avg}$ is the average price between exchanges. This formula shows that the strategy is profitable when the spread exceeds the combined fees.

## Profit Mechanism

### How the Strategy Makes Money

The Simple XEMM strategy generates profit through arbitrage between two exchanges. There are three core profit mechanisms:

1. **Price Differential Capture**: The strategy profits from price differences between the maker and taker exchanges. By setting maker exchange order prices at a calculated distance from taker exchange prices, it creates a profitable spread.

2. **Immediate Hedging**: When an order is filled on the maker exchange, the strategy immediately executes a corresponding order on the taker exchange to lock in the price differential and minimize market risk.

3. **Two-way Profit Potential**: The strategy can generate profit in both directions:
   - When buying on the maker exchange and selling on the taker exchange
   - When selling on the maker exchange and buying on the taker exchange

This strategy is effectively a form of statistical arbitrage that capitalizes on the persistent price differences between exchanges while managing inventory risk through hedging.

### Profit Example

Let's illustrate with a concrete example:

**Initial Parameters:**
- Trading Pair: ETH-USDT
- Maker Exchange: KuCoin
- Taker Exchange: Binance
- Spread ($S_{bps}$): 10 basis points (0.1%)
- Order Amount ($Q$): 0.5 ETH
- Maker Fee ($f_{maker}$): 0.1% (0.001)
- Taker Fee ($f_{taker}$): 0.1% (0.001)

**Scenario 1: Maker Buy Order Filled**
- Taker (Binance) sell price: 2,000 USDT
- Maker (KuCoin) buy price: $2,000 \times (1 - 0.001) = 1,998$ USDT
- Buy 0.5 ETH on KuCoin at 1,998 USDT, total cost: 999 USDT
- Maker fee: $0.5 \times 1,998 \times 0.001 = 0.999$ USDT
- Hedge by selling 0.5 ETH on Binance at 2,000 USDT, revenue: 1,000 USDT
- Taker fee: $0.5 \times 2,000 \times 0.001 = 1$ USDT
- Profit: $1,000 - 999 - 0.999 - 1 = -0.999$ USDT

**Scenario 2: Maker Sell Order Filled**
- Taker (Binance) buy price: 2,000 USDT
- Maker (KuCoin) sell price: $2,000 \times (1 + 0.001) = 2,002$ USDT
- Sell 0.5 ETH on KuCoin at 2,002 USDT, revenue: 1,001 USDT
- Maker fee: $0.5 \times 2,002 \times 0.001 = 1.001$ USDT
- Hedge by buying 0.5 ETH on Binance at 2,000 USDT, cost: 1,000 USDT
- Taker fee: $0.5 \times 2,000 \times 0.001 = 1$ USDT
- Profit: $1,001 - 1,000 - 1.001 - 1 = -1.001$ USDT

**Total Profit for Full Cycle**:
- Combined profit: $-0.999 + (-1.001) = -2$ USDT

Wait, this shows a loss! Let's analyze why:
In this example, our spread (10 bps = 0.1%) is exactly equal to the combined fees (0.1% + 0.1% = 0.2%), resulting in a net loss. For the strategy to be profitable, we need:

**Adjusted Parameters for Profitability:**
- Spread ($S_{bps}$): 25 basis points (0.25%)
- Other parameters unchanged

**Recalculated Scenario with 25 bps Spread:**
- Maker buy price: $2,000 \times (1 - 0.0025) = 1,995$ USDT
- Maker sell price: $2,000 \times (1 + 0.0025) = 2,005$ USDT
- Buy cycle profit: $1,000 - 997.5 - 0.9975 - 1 = 0.5025$ USDT
- Sell cycle profit: $1,002.5 - 1,000 - 1.0025 - 1 = 0.4975$ USDT
- Combined profit: $0.5025 + 0.4975 = 1$ USDT

With a sufficiently large spread that exceeds the combined fee costs, the strategy becomes profitable. This example demonstrates the importance of parameter tuning for this strategy.

## Major Risks

While the XEMM strategy reduces directional market risk through hedging, it faces several other significant risk factors:

### Execution Risk

- **Hedge Execution Delay**: The time gap between a maker order fill and its hedge execution on the taker exchange exposes the strategy to price movements.
  
- **Failed Hedges**: If the hedge order fails to execute (due to liquidity issues, exchange problems, or rapid price movements), the strategy is exposed to unhedged positions and directional risk.

- **Fill Rate Imbalance**: One side (buy or sell) may experience more fills than the other, leading to inventory imbalances over time.

### Market Movement Risk

- **Rapid Price Convergence**: If the price differential between exchanges suddenly narrows, orders may need to be canceled and replaced at less favorable prices.

- **Correlation Breakdown**: The price correlation between exchanges may temporarily break down during market stress, leading to potential losses.

- **Flash Crashes/Spikes**: Extreme price movements on one exchange may result in filled orders that cannot be profitably hedged on the other exchange.

### Slippage Risk

- **Market Impact**: Larger orders may experience slippage beyond the calculated prices, especially on less liquid exchanges.

- **Depth Limitations**: Order book depth may be insufficient to execute the hedge at the expected price, leading to worse execution prices.

- **Hidden Costs**: The true cost of execution may be higher than anticipated due to wider spreads during volatile periods.

### Exchange Risk

- **API Failures**: Exchange API outages or rate limits may prevent timely order placement, cancellation, or execution.

- **Exchange Downtime**: If one exchange becomes unavailable, hedged positions may become unbalanced.

- **Withdrawal/Deposit Delays**: Moving funds between exchanges (if needed) introduces additional risks and costs.

### Risk Mitigation Strategies

The Simple XEMM implementation includes several risk management features:

1. **Slippage Buffer**: The `slippage_buffer_spread_bps` parameter adds a buffer to hedge execution prices to increase the likelihood of successful hedges.

2. **Minimum Spread Check**: Orders are canceled and replaced if the spread falls below the `min_spread_bps` threshold, preventing unprofitable trades.

3. **Maximum Order Age**: Orders are refreshed after `max_order_age` seconds to prevent stale orders during changing market conditions.

4. **Budget Checking**: Orders are adjusted based on available balances on both exchanges to prevent failed trades.

Additional risk controls that could be implemented include:

1. **Position Limits**: Setting maximum position sizes or imbalances between exchanges.

2. **Correlation Monitoring**: Pausing the strategy if price correlation between exchanges weakens.

3. **Volatility-based Adjustments**: Widening spreads during high volatility periods.

4. **Circuit Breakers**: Automatically stopping the strategy during extreme market conditions.

## Implementation Architecture

### Configuration Parameters

The strategy is configured through the following parameters:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `maker_exchange` | string | Exchange where maker orders will be placed | "kucoin_paper_trade" |
| `maker_pair` | string | Trading pair for maker orders | "ETH-USDT" |
| `taker_exchange` | string | Exchange where hedge orders will be placed | "binance_paper_trade" |
| `taker_pair` | string | Trading pair for hedge orders | "ETH-USDT" |
| `order_amount` | decimal | Order amount (in base asset) | 0.1 |
| `spread_bps` | decimal | Target spread in basis points | 10 |
| `min_spread_bps` | decimal | Minimum acceptable spread | 0 |
| `slippage_buffer_spread_bps` | decimal | Buffer for hedge execution slippage | 100 |
| `max_order_age` | integer | Maximum order age in seconds | 120 |

### Core Functions

The strategy implementation revolves around the following key functions:

1. **`on_tick()`**: Main execution loop that runs every clock tick, checks prices, places orders, and monitors existing orders
2. **`buy_hedging_budget()`** and **`sell_hedging_budget()`**: Calculate available capital for hedging operations
3. **`is_active_maker_order()`**: Verifies if an order is currently active on the maker exchange
4. **`did_fill_order()`**: Handles order fill events and triggers hedging logic
5. **`place_buy_order()`** and **`place_sell_order()`**: Execute buy and sell orders with slippage protection
6. **`exchanges_df()`** and **`active_orders_df()`**: Generate data frames for status reporting

### Execution Flow

The strategy execution follows this sequence:

1. **Initialization**: Set up the markets and configuration parameters
2. **Order Creation**: On each tick, check if buy and sell orders need to be placed:
   - Get current prices from the taker exchange
   - Calculate maker order prices based on taker prices and spread
   - Create and place maker orders if not already active
3. **Order Monitoring**: For each active order:
   - Check current market conditions and order age
   - Cancel orders if the spread falls below minimum or if orders are too old
4. **Hedging**: When a maker order is filled:
   - Detect the fill event
   - Immediately place a corresponding hedge order on the taker exchange
5. **Reporting**: Provide status updates on current orders, prices, and spreads

## Optimization Techniques

### Parameter Tuning

The performance of the Simple XEMM strategy can be optimized by tuning these key parameters:

1. **Spread Setting**: 
   - The `spread_bps` should be set to exceed the combined trading fees on both exchanges
   - Higher spreads reduce fill probability but increase profit per trade
   - Optimal spread depends on exchange fee structures: $S_{bps} > (f_{maker} + f_{taker}) \times 10000 + buffer$

2. **Minimum Spread**:
   - Setting `min_spread_bps` > 0 prevents execution when profit margins are too thin
   - Typically set slightly above the breakeven point: $S_{min} \geq (f_{maker} + f_{taker}) \times 10000$

3. **Slippage Buffer**:
   - Adjust `slippage_buffer_spread_bps` based on market liquidity
   - Larger buffers ensure hedge execution but reduce profitability
   - Optimal buffer depends on market volatility and average slippage observed

4. **Order Age**:
   - Shorter `max_order_age` values increase order refresh frequency, adapting better to changing markets
   - Longer values reduce API calls but may lead to stale orders
   - Consider market volatility when setting this parameter

5. **Order Size**:
   - Smaller sizes reduce slippage but may not maximize profit potential
   - Larger sizes increase profit potential but face higher slippage and execution risk
   - Optimal size depends on market liquidity and capital constraints

### Risk Management

Advanced risk management techniques include:

1. **Dynamic Parameter Adjustment**:
   - Automatically widen spreads during volatile periods
   - Reduce order size when liquidity is low
   - Extend order age in stable markets

2. **Exchange-Specific Optimization**:
   - Select exchanges with complementary fee structures and liquidity profiles
   - Consider exchanges with maker rebates for additional profit
   - Monitor exchange reliability and adjust risk parameters accordingly

3. **Portfolio Balance**:
   - Maintain balanced positions across exchanges
   - Set limits on maximum imbalance between exchanges
   - Implement rebalancing logic if imbalances exceed thresholds

## Performance Metrics

Key metrics to evaluate the strategy's performance include:

1. **Profit per Trade**: Average profit captured per completed trade cycle
2. **Fill Rate**: Percentage of placed orders that get filled
3. **Hedge Success Rate**: Percentage of hedges successfully executed at or better than target prices
4. **Spread Capture Efficiency**: Actual profit as a percentage of theoretical maximum profit
5. **Exchange Price Correlation**: Stability of price relationships between the maker and taker exchanges
6. **Position Balance**: Difference in inventory between exchanges over time

## Practical Implementation Guidelines

For optimal implementation of the Simple XEMM strategy:

1. **Exchange Selection**:
   - Choose exchanges with consistent price relationships
   - Select exchanges with high reliability and API stability
   - Consider fee structures - ideally use an exchange with maker rebates as the maker exchange

2. **Initial Parameter Settings**:
   - Start with conservative spreads (at least 3x the combined fees)
   - Use moderate order sizes (0.01-0.1 ETH or equivalent for major pairs)
   - Set reasonable order refresh times (60-120 seconds initially)

3. **Monitoring and Adjustment**:
   - Track hedge execution success rates and adjust slippage buffer accordingly
   - Monitor both sides (buy and sell) for balanced execution
   - Watch for changes in exchange correlations and fee structures

4. **Capital Allocation**:
   - Maintain sufficient balances on both exchanges
   - Allocate capital based on expected trade frequency and size
   - Consider the costs of transferring funds between exchanges if rebalancing is needed

## Conclusion

The Simple XEMM strategy offers a structured approach to cross-exchange arbitrage, using one exchange for making markets and another for hedging. By capitalizing on persistent price differentials between exchanges, it aims to generate profits while minimizing directional market risk.

This mathematical framework provides a foundation for implementing and optimizing the strategy. The key to success lies in careful parameter selection, robust risk management, and continuous monitoring of market conditions and exchange relationships.

While the basic strategy is relatively straightforward, advanced implementations might incorporate dynamic parameter adjustments, sophisticated hedging techniques, and additional risk controls to enhance performance across varied market conditions.

For traders seeking to mitigate the directional risk inherent in single-exchange market making, the XEMM approach offers a valuable alternative that can potentially deliver more consistent returns in volatile cryptocurrency markets. 