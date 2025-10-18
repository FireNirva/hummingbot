# CEX Arbitrage Mathematical Framework

## Introduction

This document establishes a mathematical framework for the CEX-to-CEX (Centralized Exchange to Centralized Exchange) arbitrage strategy. We aim to formalize the key components, relationships, and constraints that govern this trading strategy, providing a solid foundation for strategy development, optimization, and risk management.

## 1. Market Structure and Notation

Let's define the basic notation:

- $E = \{E_1, E_2, ..., E_n\}$: Set of centralized exchanges
- $A = \{A_1, A_2, ..., A_m\}$: Set of assets (cryptocurrencies)
- $P_{i,j,t}$: Price of asset $A_j$ on exchange $E_i$ at time $t$
- $V_{i,j,t}$: Available trading volume for asset $A_j$ on exchange $E_i$ at time $t$
- $F_{i,j}^{buy}$: Fee rate for buying asset $A_j$ on exchange $E_i$
- $F_{i,j}^{sell}$: Fee rate for selling asset $A_j$ on exchange $E_i$
- $W_{i,j}^{withdraw}$: Withdrawal fee for asset $A_j$ from exchange $E_i$
- $W_{i,j}^{deposit}$: Deposit fee for asset $A_j$ to exchange $E_i$ (usually zero)
- $T_{i,j}^{withdraw}$: Withdrawal time for asset $A_j$ from exchange $E_i$
- $T_{i,j}^{deposit}$: Deposit time for asset $A_j$ to exchange $E_i$

## 2. Basic Arbitrage Profit Model

For a simple arbitrage opportunity between exchanges $E_a$ and $E_b$ for asset $A_j$:

1. Buy asset $A_j$ on exchange $E_a$ at price $P_{a,j,t}$
2. Sell asset $A_j$ on exchange $E_b$ at price $P_{b,j,t}$

The potential profit (before fees) for trading volume $V$ is:

$$\Pi_{raw} = V \times (P_{b,j,t} - P_{a,j,t})$$

Accounting for trading fees:

$$\Pi_{net} = V \times P_{b,j,t} \times (1 - F_{b,j}^{sell}) - V \times P_{a,j,t} \times (1 + F_{a,j}^{buy})$$

The arbitrage is profitable when $\Pi_{net} > 0$, which occurs when:

$$\frac{P_{b,j,t}}{P_{a,j,t}} > \frac{1 + F_{a,j}^{buy}}{1 - F_{b,j}^{sell}}$$

## 3. Trading Volume Constraints

The maximum profitable trading volume $V_{max}$ is constrained by:

$$V_{max} = \min(V_{a,j,t}^{buy}, V_{b,j,t}^{sell}, B_a / P_{a,j,t}, B_b)$$

Where:
- $V_{a,j,t}^{buy}$: Maximum buy volume on exchange $E_a$
- $V_{b,j,t}^{sell}$: Maximum sell volume on exchange $E_b$
- $B_a$: Available balance in base currency on exchange $E_a$
- $B_b$: Available balance in asset $A_j$ on exchange $E_b$

## 4. Price Slippage Model

For larger orders, we must account for price slippage:

$$P_{effective}^{buy} = P_{a,j,t} \times (1 + \alpha_a \times V)$$
$$P_{effective}^{sell} = P_{b,j,t} \times (1 - \alpha_b \times V)$$

Where $\alpha_a$ and $\alpha_b$ are slippage coefficients specific to each exchange and trading pair.

With slippage, the net profit becomes:

$$\Pi_{net}^{slippage} = V \times P_{b,j,t} \times (1 - \alpha_b \times V) \times (1 - F_{b,j}^{sell}) - V \times P_{a,j,t} \times (1 + \alpha_a \times V) \times (1 + F_{a,j}^{buy})$$

## 5. Optimal Trading Volume

To maximize profit, we can derive the optimal trading volume by differentiating $\Pi_{net}^{slippage}$ with respect to $V$ and setting it to zero:

$$V_{optimal} = \frac{P_{b,j,t} \times (1 - F_{b,j}^{sell}) - P_{a,j,t} \times (1 + F_{a,j}^{buy})}{2 \times (P_{b,j,t} \times \alpha_b \times (1 - F_{b,j}^{sell}) + P_{a,j,t} \times \alpha_a \times (1 + F_{a,j}^{buy}))}$$

The actual trading volume should be:

$$V_{trade} = \min(V_{optimal}, V_{max})$$

## 6. Execution Time Risk

Price differentials between exchanges may not persist for long. Let's define:

- $\Delta t$: Time required to execute the arbitrage
- $\sigma_{a,b,j}$: Volatility of the price difference between exchanges $E_a$ and $E_b$ for asset $A_j$
- $\lambda_{a,b,j}$: Mean-reversion rate of the price difference

The probability of successful arbitrage can be modeled as:

$$P(success) = \Phi\left(\frac{\Pi_{net} - k \times \sigma_{a,b,j} \times \sqrt{\Delta t} \times V \times P_{avg}}{\sigma_{a,b,j} \times \sqrt{\Delta t} \times V \times P_{avg}}\right)$$

Where:
- $\Phi$ is the cumulative distribution function of the standard normal distribution
- $k$ is a risk parameter
- $P_{avg} = \frac{P_{a,j,t} + P_{b,j,t}}{2}$

## 7. Capital Efficiency Metrics

Define the capital efficiency as:

$$\eta = \frac{\Pi_{net}}{V \times P_{avg}} \times \frac{365 \times 24 \times 60}{\Delta t} \times 100\%$$

This represents the annualized percentage return on capital employed in the arbitrage.

## 8. Multi-Exchange Arbitrage

For arbitrage across multiple exchanges, we can define a directed graph where:
- Nodes represent (Exchange, Asset) pairs
- Edges represent possible trades with associated costs and profits

The goal is to find a cycle in this graph that maximizes profit while respecting all constraints.

## 9. Implementation Considerations

For practical implementation, several factors must be considered:

1. **Exchange API Limitations**:
   - Rate limits
   - Order placement latency
   - Order type restrictions

2. **Risk Management**:
   - Position limits per exchange
   - Maximum capital allocation
   - Stop-loss mechanisms

3. **Monitoring Metrics**:
   - Success rate
   - Average profit per trade
   - Capital efficiency
   - Drawdown statistics

## Conclusion

This mathematical framework provides a foundation for implementing, analyzing, and optimizing CEX-to-CEX arbitrage strategies. By understanding the relationships between prices, fees, volumes, and risks, traders can develop more robust and profitable arbitrage systems. 