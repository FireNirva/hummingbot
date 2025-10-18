# Automated Market Maker Arbitrage (AMM Arb) Strategy: Mathematical Framework and Implementation Guide

**Author:** Trading Framework Team  
**Date:** 2024-03-06

## Table of Contents
- [Automated Market Maker Arbitrage (AMM Arb) Strategy: Mathematical Framework and Implementation Guide](#automated-market-maker-arbitrage-amm-arb-strategy-mathematical-framework-and-implementation-guide)
  - [Table of Contents](#table-of-contents)
  - [Introduction](#introduction)
  - [Strategy Overview](#strategy-overview)
  - [Mathematical Framework](#mathematical-framework)
    - [Notation and Parameters](#notation-and-parameters)
    - [Arbitrage Proposal Model](#arbitrage-proposal-model)
    - [Profit and Loss Calculation](#profit-and-loss-calculation)
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

The Automated Market Maker Arbitrage (AMM Arb) strategy provides a framework for exploiting price differences between any two markets, whether they are centralized exchanges (CEX), decentralized exchanges (DEX), or automated market makers (AMM). This document outlines the mathematical framework and implementation guide for the strategy as implemented in the `amm_arb.py` script. By monitoring price discrepancies and executing trades when profitable opportunities arise, the strategy aims to generate consistent returns while managing associated risks.

## Strategy Overview

The AMM Arb strategy operates through the following key mechanisms:

1. Continuously monitoring prices on two different markets for the same trading pair
2. Identifying arbitrage opportunities where the price difference exceeds a minimum profitability threshold
3. Executing taker orders on both sides of the trade when a profitable opportunity is detected
4. Managing risks through slippage buffers and budget constraints

Unlike market making strategies that profit from providing liquidity, AMM Arb directly capitalizes on price inefficiencies between different trading venues. The strategy is particularly well-suited for environments with high price volatility or where there are structural price differences between markets.

## Mathematical Framework

### Notation and Parameters

- **$P_{market1}^{bid}$, $P_{market1}^{ask}$**: Bid and ask prices on the first market
- **$P_{market2}^{bid}$, $P_{market2}^{ask}$**: Bid and ask prices on the second market
- **$P_{min}$**: Minimum profitability threshold (in percentage)
- **$Q$**: Order amount (in base asset units)
- **$S_1$, $S_2$**: Slippage buffers for market 1 and market 2 (in percentage)
- **$f_1$, $f_2$**: Fee rates on market 1 and market 2
- **$F_1$, $F_2$**: Extra flat fees (e.g., network gas fees) for market 1 and market 2
- **$B_{base,1}$, $B_{quote,1}$**: Available base and quote asset balances on market 1
- **$B_{base,2}$, $B_{quote,2}$**: Available base and quote asset balances on market 2
- **$R_{quote}$**: Conversion rate between quote assets of the two markets

### Arbitrage Proposal Model

The strategy evaluates two possible arbitrage directions:

**Scenario 1: Buy on Market 1, Sell on Market 2**
- Buy price on Market 1: $P_{market1}^{buy}$
- Sell price on Market 2: $P_{market2}^{sell}$
- Profit percentage: $(P_{market2}^{sell} \times (1 - f_2) - P_{market1}^{buy} \times (1 + f_1)) / (P_{market1}^{buy} \times (1 + f_1))$

**Scenario 2: Buy on Market 2, Sell on Market 1**
- Buy price on Market 2: $P_{market2}^{buy}$
- Sell price on Market 1: $P_{market1}^{sell}$
- Profit percentage: $(P_{market1}^{sell} \times (1 - f_1) - P_{market2}^{buy} \times (1 + f_2)) / (P_{market2}^{buy} \times (1 + f_2))$

For an arbitrage opportunity to be considered viable, the profit percentage must exceed the minimum profitability threshold:

$$\text{Profit Percentage} > P_{min}$$

### Profit and Loss Calculation

For a buy and sell arbitrage cycle, the expected profit calculation takes into account:

1. **Trading fees**:
   - Buy side fee: $Q \times P_{buy} \times f_{buy}$
   - Sell side fee: $Q \times P_{sell} \times f_{sell}$

2. **Extra flat fees** (particularly relevant for blockchain transactions):
   - Network fees for transactions on decentralized exchanges

3. **Asset conversion** (when quote assets differ between markets):
   - Adjusted profit: $\text{Profit in Sell Quote Asset} \times R_{quote}$

The `profit_pct()` method in the `ArbProposal` class calculates the profit percentage as follows:

```
buy_spent_net = (buy_side.amount * buy_side.quote_price) + buy_fee_amount
sell_gained_net = (sell_side.amount * sell_side.quote_price) - sell_fee_amount
sell_gained_net_in_buy_quote_currency = sell_gained_net * sell_quote_to_buy_quote_rate
result = (sell_gained_net_in_buy_quote_currency - buy_spent_net) / buy_spent_net
```

This provides a comprehensive measure of profitability that accounts for all costs involved in the arbitrage.

## Profit Mechanism

### How the Strategy Makes Money

The AMM Arb strategy generates profit through three core mechanisms:

1. **Price Differential Exploitation**: The strategy profits from temporary or structural price differences between two markets. When the same asset trades at different prices across markets, the strategy buys from the cheaper market and sells on the more expensive one.

2. **Cross-Market Arbitrage**: By simultaneously executing orders on two different markets, the strategy captures the price spread while minimizing directional market risk. This works for both directions:
   - Buy on Market 1, Sell on Market 2
   - Buy on Market 2, Sell on Market 1

3. **Market Inefficiency Capture**: The strategy takes advantage of market inefficiencies that may arise due to:
   - Liquidity differences between markets
   - Different market microstructures (CEX vs. DEX)
   - Temporary supply/demand imbalances
   - Delayed price discovery across venues

The strategy is particularly effective when there's a consistent price relationship between the two markets with occasional deviations that can be exploited.

### Profit Example

Let's illustrate with a concrete example:

**Initial Parameters:**
- Trading Pair: ETH-USDT
- Market 1: Binance (CEX)
- Market 2: Uniswap (AMM/DEX)
- Minimum Profitability: 0.5% (0.005)
- Order Amount: 1 ETH
- Market 1 Fee: 0.1% (0.001)
- Market 2 Fee: 0.3% (0.003)
- Market 2 Gas Fee: $15 (in USDT)

**Scenario: Buy on Binance, Sell on Uniswap**
- Binance buy price: 2,000 USDT
- Uniswap sell price: 2,025 USDT
- Buy cost on Binance: 2,000 USDT
- Binance trading fee: 2,000 × 0.001 = 2 USDT
- Total buy cost: 2,002 USDT
- Sell revenue on Uniswap: 2,025 USDT
- Uniswap trading fee: 2,025 × 0.003 = 6.075 USDT
- Gas fee on Uniswap: 15 USDT
- Total sell revenue after fees: 2,025 - 6.075 - 15 = 2,003.925 USDT
- Profit: 2,003.925 - 2,002 = 1.925 USDT
- Profit percentage: 1.925 / 2,002 = 0.096% (0.00096)

In this example, despite the significant price difference (1.25%), the high gas fees on the DEX significantly reduce profitability. Since 0.096% is less than the minimum profitability threshold of 0.5%, this trade would not be executed.

Now, let's assume gas prices are lower, with a gas fee of only $5:
- Total sell revenue after fees: 2,025 - 6.075 - 5 = 2,013.925 USDT
- Profit: 2,013.925 - 2,002 = 11.925 USDT
- Profit percentage: 11.925 / 2,002 = 0.596% (0.00596)

With lower gas fees, the profit percentage now exceeds the minimum threshold, and the trade would be executed.

This example demonstrates how the strategy carefully evaluates all costs involved in the arbitrage before making trading decisions.

## Major Risks

The AMM Arb strategy faces several significant risk factors that need to be managed:

### Execution Risk

- **Order Timing**: If orders on both markets aren't executed close to simultaneously, prices may move, reducing or eliminating the expected profit.
  
- **Failed Orders**: If only one side of the arbitrage executes, the strategy is exposed to directional risk. This is particularly problematic for DEX transactions, where blockchain congestion can cause delays or failures.

- **Gas Price Fluctuations**: For DEX transactions, changing gas prices can significantly impact profitability, potentially turning a profitable opportunity into a loss.

### Market Movement Risk

- **Price Convergence**: The price difference may narrow or disappear before both sides of the arbitrage can be executed.

- **Market Impact**: Large orders may move the market, especially on less liquid venues, reducing the expected profit.

- **Flash Crashes/Spikes**: Extreme price movements may create apparent arbitrage opportunities that cannot be executed profitably due to slippage or technical limitations.

### Slippage Risk

- **AMM Price Impact**: On AMM protocols, larger trades have greater price impact due to the constant product formula, potentially reducing profitability.

- **Order Book Depth**: On traditional exchanges, insufficient depth can lead to larger slippage than anticipated.

- **Hidden Costs**: The real execution price may differ from the quoted price due to various market microstructure factors.

### Exchange Risk

- **API Failures**: Exchange API outages or rate limits may prevent timely order placement or cancellation.

- **Blockchain Congestion**: For DEX transactions, network congestion can lead to delays, failed transactions, or higher gas costs.

- **Smart Contract Risks**: DEX interactions involve smart contract risks, including bugs, exploits, or unexpected behavior.

### Risk Mitigation Strategies

The AMM Arb implementation includes several risk management features:

1. **Slippage Buffers**: The `market_1_slippage_buffer` and `market_2_slippage_buffer` parameters add a buffer to order prices to increase the likelihood of execution, especially important for AMM transactions.

2. **Minimum Profitability Threshold**: The `min_profitability` parameter ensures that trades are only executed when they exceed a minimum profit threshold after accounting for all fees.

3. **Budget Checks**: Orders are only placed if there is sufficient balance available on both markets, preventing failed trades due to insufficient funds.

4. **Concurrent vs. Sequential Execution**: The `concurrent_orders_submission` parameter allows users to choose between submitting both orders simultaneously or waiting for the first order to fill before submitting the second.

5. **Transaction Cancellation**: For blockchain-based markets, the strategy includes logic to cancel stale transactions that haven't been included in a block after a configurable time period.

## Implementation Architecture

### Configuration Parameters

The strategy is configured through the following parameters:

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `connector_1` | string | First exchange/connector | N/A |
| `market_1` | string | Trading pair on first connector | N/A |
| `connector_2` | string | Second exchange/connector | N/A |
| `market_2` | string | Trading pair on second connector | N/A |
| `pool_id` | string | Specific pool ID for AMM interactions | "" |
| `order_amount` | decimal | Order amount in base asset | N/A |
| `min_profitability` | decimal | Minimum profit percentage to execute trades | 1.0 |
| `market_1_slippage_buffer` | decimal | Slippage buffer for market 1 (percentage) | 0.0 or 1.0 |
| `market_2_slippage_buffer` | decimal | Slippage buffer for market 2 (percentage) | 0.0 or 1.0 |
| `concurrent_orders_submission` | boolean | Whether to submit both orders simultaneously | False |
| `gateway_transaction_cancel_interval` | integer | Time before canceling unconfirmed blockchain transactions (seconds) | 600 |
| `rate_oracle_enabled` | boolean | Whether to use rate oracle for cross-asset conversions | True |

### Core Functions

The strategy implementation revolves around the following key functions:

1. **`create_arb_proposals()`**: Generates arbitrage proposals by obtaining prices from both markets and calculating potential profitability
2. **`apply_slippage_buffers()`**: Adjusts order prices to account for slippage based on configured buffer values
3. **`apply_budget_constraint()`**: Checks if there's sufficient balance to execute the arbitrage and adjusts orders accordingly
4. **`execute_arb_proposals()`**: Executes the arbitrage by placing orders on both markets
5. **`place_arb_order()`**: Places a single order on a specific market with appropriate parameters
6. **`profit_pct()`**: Calculates the percentage profit of an arbitrage proposal, accounting for all fees

### Execution Flow

The strategy execution follows this sequence:

1. **Initialization**: Set up the markets and configuration parameters
2. **Market Readiness Check**: Ensure both markets are ready for trading
3. **Arbitrage Proposal Creation**: On each tick:
   - Get current prices from both markets
   - Create arbitrage proposals for both directions (buy on market 1/sell on market 2, and vice versa)
   - Filter for proposals that exceed the minimum profitability threshold
4. **Order Adjustment**: For viable proposals:
   - Apply slippage buffers to order prices
   - Check budget constraints and adjust orders accordingly
5. **Order Execution**: For each valid proposal:
   - If concurrent execution is enabled, place orders on both markets simultaneously
   - If sequential execution is preferred, place the first order, wait for it to fill, then place the second order
6. **Order Tracking**: Monitor order status and handle filled, failed, and canceled orders

## Optimization Techniques

### Parameter Tuning

The performance of the AMM Arb strategy can be optimized by tuning these key parameters:

1. **Minimum Profitability**: 
   - Setting `min_profitability` too low may result in unprofitable trades after accounting for slippage and execution risks
   - Setting it too high may result in missed opportunities
   - Optimal setting depends on market volatility, gas prices (for DEX), and trading frequency

2. **Slippage Buffers**:
   - For CEX-to-CEX arbitrage, smaller buffers (0-0.1%) are typically sufficient
   - For DEX interactions, larger buffers (0.5-2%) are recommended to ensure execution
   - Larger buffers reduce profitability but increase fill probability

3. **Order Amount**:
   - Smaller amounts reduce slippage but may not generate significant profit after fixed costs (especially gas fees)
   - Larger amounts increase profit potential but face higher slippage and may not be executable in less liquid markets
   - Finding the optimal balance is crucial for consistent profitability

4. **Concurrent vs. Sequential Execution**:
   - Concurrent execution reduces timing risk but increases the chance of having only one side of the arbitrage execute
   - Sequential execution reduces the risk of half-executed arbitrage but increases timing risk
   - The optimal approach depends on market liquidity and volatility

5. **Gateway Transaction Cancel Interval**:
   - Shorter intervals reduce the time capital is tied up in pending transactions
   - Longer intervals reduce the chance of canceling transactions that might eventually be processed
   - Optimal setting depends on network congestion patterns and gas price strategy

### Risk Management

Advanced risk management techniques include:

1. **Dynamic Parameter Adjustment**:
   - Adjusting slippage buffers based on market volatility
   - Modifying minimum profitability thresholds based on historical execution success rates
   - Varying order amounts based on available liquidity

2. **Exchange-Specific Optimization**:
   - Selecting exchange pairs with complementary fee structures
   - Prioritizing exchanges with higher reliability for critical legs of the arbitrage
   - Accounting for exchange-specific quirks in price calculation and execution

3. **Gas Price Management** (for DEX interactions):
   - Implementing dynamic gas price strategies based on network congestion
   - Setting maximum gas price limits to avoid excessive costs
   - Using gas price oracles to optimize transaction timing

## Performance Metrics

Key metrics to evaluate the strategy's performance include:

1. **Profit per Trade**: Average profit captured per completed arbitrage cycle
2. **Success Rate**: Percentage of attempted arbitrages that complete successfully
3. **Return on Investment (ROI)**: Profit relative to capital deployed
4. **Gas Efficiency** (for DEX): Profit relative to gas costs
5. **Execution Time**: Average time to complete both sides of an arbitrage
6. **Opportunity Utilization**: Percentage of identified opportunities that are acted upon
7. **Capital Efficiency**: Average capital utilization and turnover rate

## Practical Implementation Guidelines

For optimal implementation of the AMM Arb strategy:

1. **Exchange Selection**:
   - Choose exchanges with reliable APIs and good liquidity
   - For CEX-to-DEX arbitrage, select DEXes with reasonable gas costs and fast confirmation times
   - Consider fee structures - choose exchanges with lower taker fees for frequent arbitrage

2. **Initial Parameter Settings**:
   - Start with conservative profitability thresholds (e.g., 1% for CEX-to-CEX, 2-3% for CEX-to-DEX)
   - Use moderate order sizes appropriate for the liquidity of chosen markets
   - Set slippage buffers based on historical slippage observations

3. **Monitoring and Adjustment**:
   - Track success rates of arbitrage attempts and adjust parameters accordingly
   - Monitor gas costs for DEX interactions and adjust thresholds during high congestion
   - Regularly review and rebalance funds across exchanges

4. **Capital Allocation**:
   - Maintain sufficient balances on both exchanges to capitalize on opportunities
   - For DEX interactions, factor in additional funds for gas costs
   - Consider cross-exchange rebalancing strategies to maintain optimal capital distribution

5. **Risk Limits**:
   - Set maximum order sizes relative to market liquidity
   - Implement circuit breakers during extreme market volatility
   - Consider temporarily disabling the strategy during significant market events

## Conclusion

The AMM Arb strategy offers a flexible framework for exploiting price differences across different market types, whether centralized exchanges, decentralized exchanges, or automated market makers. By carefully accounting for all costs involved in arbitrage and implementing robust risk management, the strategy can generate consistent profits from market inefficiencies.

The key to success with this strategy lies in parameter optimization, exchange selection, and risk management. While conceptually simple, effective implementation requires attention to execution details, market conditions, and technical constraints.

For traders looking to capitalize on cross-market inefficiencies, the AMM Arb strategy provides a solid foundation that can be adapted to various market conditions and exchange types. Whether arbitraging between two CEXes, two DEXes, or across different market types, the strategy's flexible design accommodates diverse arbitrage scenarios while maintaining a consistent approach to opportunity identification and execution. 