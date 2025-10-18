# CEX-DEX Arbitrage Without Transfers: Mathematical Framework and Implementation Model

**Author:** [Assistant Name]  
**Date:** \today

## Table of Contents
- [Introduction](#introduction)
- [CEX-DEX Arbitrage Model Foundations](#cex-dex-arbitrage-model-foundations)
  - [Mathematical Notation](#mathematical-notation)
  - [Market Structure Differences](#market-structure-differences)
- [Non-Transfer Arbitrage Profit Model](#non-transfer-arbitrage-profit-model)
  - [Basic Profit Equation](#basic-profit-equation)
  - [Execution Cost Analysis](#execution-cost-analysis)
  - [Net Profit Calculation](#net-profit-calculation)
- [Position Management Framework](#position-management-framework)
  - [Net Exposure Calculation](#net-exposure-calculation)
  - [Hedging Strategies](#hedging-strategies)
  - [Rebalancing Thresholds](#rebalancing-thresholds)
- [Risk-Adjusted Framework](#risk-adjusted-framework)
  - [Execution Delay Risk](#execution-delay-risk)
  - [Price Convergence Risk](#price-convergence-risk)
  - [Correlation Analysis](#correlation-analysis)
- [Execution Timing Optimization](#execution-timing-optimization)
  - [CEX-DEX Latency Model](#cex-dex-latency-model)
  - [Optimal Execution Sequence](#optimal-execution-sequence)
  - [Adaptive Timeout Mechanism](#adaptive-timeout-mechanism)
- [Capital Allocation Model](#capital-allocation-model)
  - [Optimal Position Sizing](#optimal-position-sizing)
  - [Multi-Asset Allocation](#multi-asset-allocation)
  - [Reserve Requirements](#reserve-requirements)
- [Market Microstructure Analysis](#market-microstructure-analysis)
  - [CEX Order Book Dynamics](#cex-order-book-dynamics)
  - [DEX Liquidity Pool Mechanics](#dex-liquidity-pool-mechanics)
  - [Cross-Platform Price Discovery](#cross-platform-price-discovery)
- [Implementation Guidelines](#implementation-guidelines)
  - [Parameter Calibration](#parameter-calibration)
  - [Risk Management Strategy](#risk-management-strategy)
  - [Performance Metrics](#performance-metrics)
- [Comparison with Previous Approaches](#comparison-with-previous-approaches)
- [Conclusion](#conclusion)

## Introduction

This document provides a mathematical framework for implementing arbitrage strategies between centralized exchanges (CEX) and decentralized exchanges (DEX) without asset transfers. As the third stage in the arbitrage learning path, this strategy builds upon the understanding of both CEX-to-CEX and DEX-to-DEX arbitrage, introducing the unique challenges of operating across these fundamentally different market structures. The non-transfer approach eliminates the delay, cost, and risk associated with moving assets between platforms, but introduces the complexity of managing positions and exposure across separate environments. By systematically analyzing these elements through mathematical models, this framework aims to guide the development of effective CEX-DEX arbitrage strategies that properly account for the distinct characteristics of hybrid trading approaches.

## CEX-DEX Arbitrage Model Foundations

### Mathematical Notation

- **\( \boldsymbol{P_C} \):** Price of the asset on the CEX
- **\( \boldsymbol{P_D} \):** Price of the asset on the DEX
- **\( \boldsymbol{f_C} \):** Trading fee rate on the CEX
- **\( \boldsymbol{f_D} \):** Trading fee rate on the DEX
- **\( \boldsymbol{G} \):** Gas cost for DEX transactions
- **\( \boldsymbol{Q} \):** Trade quantity
- **\( \boldsymbol{Q_{C,max}} \):** Maximum trade size on CEX
- **\( \boldsymbol{Q_{D,max}} \):** Maximum trade size on DEX (liquidity-constrained)
- **\( \boldsymbol{\Delta P} \):** Price difference \( \Delta P = |P_C - P_D| \)
- **\( \boldsymbol{\sigma_C} \):** Price volatility on CEX
- **\( \boldsymbol{\sigma_D} \):** Price volatility on DEX
- **\( \boldsymbol{\rho} \):** Correlation coefficient between CEX and DEX prices
- **\( \boldsymbol{E_C} \):** Position exposure on CEX
- **\( \boldsymbol{E_D} \):** Position exposure on DEX
- **\( \boldsymbol{E_{net}} \):** Net exposure across platforms \( E_{net} = E_C + E_D \)
- **\( \boldsymbol{\tau_C} \):** Execution delay on CEX
- **\( \boldsymbol{\tau_D} \):** Execution delay on DEX (block confirmation time)
- **\( \boldsymbol{C_{total}} \):** Total capital available for the strategy

### Market Structure Differences

The fundamental structural differences between CEX and DEX affect arbitrage execution:

1. **Price Formation Mechanisms:**
   - CEX: Order book-based with \( P_C(Q) = P_C^{base} \pm \lambda_C \cdot Q \), where \( \lambda_C \) represents market depth
   - DEX: AMM-based with \( P_D(Q) = \frac{y}{x-Q} \) for constant product AMMs

2. **Execution Characteristics:**
   - CEX execution time: \( \tau_C \) typically in milliseconds
   - DEX execution time: \( \tau_D \) typically in seconds/minutes (blockchain-dependent)
   - Timing difference factor: \( \phi = \frac{\tau_D}{\tau_C} \) often in the range of \( 10^2 \) to \( 10^4 \)

3. **Price Discovery Relationship:**
   - Price lead-lag model: \( P_D(t) = \alpha \cdot P_C(t-\delta) + (1-\alpha) \cdot P_D(t-1) + \epsilon_t \)
   - Long-term equilibrium: \( E[P_C] = E[P_D] \) (assuming efficient markets)

## Non-Transfer Arbitrage Profit Model

### Basic Profit Equation

For a simple arbitrage where we buy on the platform with lower price and sell on the platform with higher price, the theoretical profit is:

\[
\Pi_{theoretical} = Q \cdot |P_C - P_D|
\]

Accounting for trading fees and execution costs:

\[
\Pi_{base} = 
\begin{cases}
Q \cdot (P_C \cdot (1 - f_C) - P_D \cdot (1 + f_D)) - G, & \text{if } P_C > P_D \\
Q \cdot (P_D \cdot (1 - f_D) - P_C \cdot (1 + f_C)) - G, & \text{if } P_D > P_C
\end{cases}
\]

### Execution Cost Analysis

The execution costs differ significantly between platforms:

1. **CEX Execution Costs:**
   \[
   C_{exec,C} = Q \cdot P_C \cdot f_C + C_{slip,C}
   \]
   
   Where the slippage cost on CEX is approximately:
   \[
   C_{slip,C} = Q \cdot P_C \cdot \lambda_C \cdot \frac{Q}{2}
   \]

2. **DEX Execution Costs:**
   \[
   C_{exec,D} = Q \cdot P_D \cdot f_D + G + C_{slip,D}
   \]
   
   Where the slippage cost on DEX for constant product AMM is:
   \[
   C_{slip,D} = Q \cdot P_D \cdot \left(\frac{Q}{x-Q}\right)
   \]

### Net Profit Calculation

The minimum profitable price difference threshold accounting for all costs:

\[
\Delta P_{min} = \frac{P_C \cdot f_C + P_D \cdot f_D + \frac{G}{Q} + C_{slip,C} + C_{slip,D}}{1 - \max(f_C, f_D)}
\]

The expected net profit considering execution probability:

\[
E[\Pi_{net}] = p_{exec} \cdot \Pi_{base} - (1 - p_{exec}) \cdot C_{fail}
\]

Where \( p_{exec} \) is the probability of successful execution on both platforms.

## Position Management Framework

### Net Exposure Calculation

In non-transfer arbitrage, positions accumulate on both platforms, creating exposure:

\[
E_{net} = \sum_{i=1}^{n} (E_{C,i} + E_{D,i})
\]

For an individual arbitrage operation:
\[
\Delta E = 
\begin{cases}
(+Q_C, -Q_D), & \text{if buying on CEX, selling on DEX} \\
(-Q_C, +Q_D), & \text{if selling on CEX, buying on DEX}
\end{cases}
\]

The absolute risk exposure:
\[
|E_{net}| = \left|\sum_{i=1}^{n} (E_{C,i} + E_{D,i})\right|
\]

### Hedging Strategies

To maintain balanced exposure, hedging strategies can be employed:

\[
H_i = -\beta \cdot E_i
\]

Where \( \beta \) is the hedge ratio optimally set at:

\[
\beta^* = \frac{\sigma_i}{\sigma_H} \cdot \rho_{i,H}
\]

The cost of hedging should be factored into the profit model:

\[
C_{hedge} = |H| \cdot (f_{hedge} + s_{hedge})
\]

### Rebalancing Thresholds

Position rebalancing should occur when exposure exceeds defined thresholds:

\[
|E_{net}| > \theta_{absolute} \quad \text{or} \quad \frac{|E_{net}|}{C_{total}} > \theta_{relative}
\]

Optimal rebalancing thresholds balance rebalancing costs against exposure risk:

\[
\theta^* = \arg\min_{\theta} \{E[C_{rebalance}(\theta)] + \lambda \cdot E[R_{exposure}(\theta)]\}
\]

Where \( \lambda \) is a risk aversion parameter.

## Risk-Adjusted Framework

### Execution Delay Risk

The probability distribution of execution delays affects profit expectations:

\[
\tau_D \sim \text{Distribution}(\mu_{\tau}, \sigma_{\tau}^2)
\]

The expected profit decay due to delay:

\[
E[\Pi | \tau] = \Pi_0 \cdot e^{-\delta \cdot \tau}
\]

Where \( \delta \) is the decay parameter related to market efficiency.

### Price Convergence Risk

In many cases, CEX-DEX price differences are temporary, with prices converging at rate \( \kappa \):

\[
\frac{d}{dt}(P_C - P_D) = -\kappa \cdot (P_C - P_D) + \eta_t
\]

The expected half-life of an arbitrage opportunity:

\[
t_{1/2} = \frac{\ln(2)}{\kappa}
\]

### Correlation Analysis

The dynamic correlation between CEX and DEX affects risk exposure:

\[
\rho_t = \rho_0 + \alpha \cdot |\Delta P_t| + \beta \cdot \rho_{t-1} + \epsilon_t
\]

The effective net exposure considering correlation:

\[
E_{eff} = |E_C| + |E_D| - 2 \cdot \rho \cdot |E_C| \cdot |E_D|
\]

## Execution Timing Optimization

### CEX-DEX Latency Model

The timing differences between platforms can be modeled as:

\[
\Delta \tau = \tau_D - \tau_C \approx \mu_{\Delta \tau} + \sigma_{\Delta \tau} \cdot Z
\]

Where Z is a standard normal random variable.

### Optimal Execution Sequence

Given the latency differential, optimal execution sequencing is crucial:

\[
S^* = \arg\max_S E[\Pi | \text{Sequence } S]
\]

For most CEX-DEX pairs, the optimal sequence is:
1. Execute on the faster platform (typically CEX) first
2. Execute on the slower platform (typically DEX) second
3. Adjust timing based on historical latency data

### Adaptive Timeout Mechanism

An adaptive timeout mechanism prevents stuck operations:

\[
t_{timeout} = \mu_{\tau} + k \cdot \sigma_{\tau}
\]

Where k is a parameter determining the aggressiveness of the timeout (typically 2-3).

## Capital Allocation Model

### Optimal Position Sizing

The Kelly criterion adapted for CEX-DEX arbitrage suggests:

\[
f^* = \frac{p \cdot \frac{\Pi}{Q} - (1-p) \cdot \frac{L}{Q}}{\frac{\Pi}{Q} \cdot \frac{L}{Q}}
\]

Where:
- p is the probability of profitable arbitrage
- L is the potential loss per unit
- The optimal position size is \( Q^* = f^* \cdot C_{total} \)

### Multi-Asset Allocation

For strategies trading multiple assets, optimal allocation follows:

\[
\vec{w}^* = \arg\max_{\vec{w}} \frac{\vec{w}^T \vec{\mu} - r_f}{\sqrt{\vec{w}^T \Sigma \vec{w}}}
\]

Subject to constraints:
\[
\sum_i w_i = 1, \quad w_i \geq 0
\]

### Reserve Requirements

Reserve capital should be maintained for unexpected events:

\[
R_{min} = max(R_{fixed}, \gamma \cdot E_{net}, \delta \cdot C_{total})
\]

Where:
- \( R_{fixed} \) is a fixed minimum reserve
- \( \gamma \) is a multiplier of net exposure
- \( \delta \) is a fraction of total capital

## Market Microstructure Analysis

### CEX Order Book Dynamics

Order book depth impacts execution quality:

\[
D(p) = \sum_{i: p_i \leq p} q_i \quad \text{for buy side}
\]

\[
D(p) = \sum_{i: p_i \geq p} q_i \quad \text{for sell side}
\]

The price impact function:

\[
PI(Q) = \frac{P(Q) - P(0)}{P(0)}
\]

### DEX Liquidity Pool Mechanics

For constant product AMMs, the price impact is:

\[
PI_{DEX}(Q) = \frac{Q}{x-Q}
\]

The liquidity depth metric:

\[
L = \sqrt{x \cdot y}
\]

### Cross-Platform Price Discovery

The information share of each platform can be calculated as:

\[
IS_i = \frac{\alpha_i^2 \sigma_i^2}{\sum_j \alpha_j^2 \sigma_j^2}
\]

Where \( \alpha_i \) is the platform's contribution to the price discovery process.

## Implementation Guidelines

### Parameter Calibration

Critical parameters to calibrate include:

1. **Minimum profitable price difference**: \( \Delta P_{min} \) considering all costs
2. **Position size limits**: \( Q_{max} \) based on liquidity and risk tolerance
3. **Rebalancing thresholds**: \( \theta_{absolute} \) and \( \theta_{relative} \)
4. **Execution timeout**: \( t_{timeout} \) based on historical latency

### Risk Management Strategy

Comprehensive risk management should include:

1. **Exposure limits**: Maximum allowed net exposure as percentage of capital
2. **Stop-loss triggers**: Conditions to exit positions if losses exceed thresholds
3. **Correlation monitoring**: Adjusting exposure limits based on CEX-DEX correlation
4. **Volatility-adjusted sizing**: Reducing position sizes during high volatility periods

### Performance Metrics

Key performance metrics include:

1. **Profit per trade**: \( \frac{\sum \Pi_i}{n} \)
2. **Success rate**: \( \frac{\text{Profitable trades}}{\text{Total trades}} \)
3. **Return on allocated capital**: \( \frac{\sum \Pi_i}{C_{deployed}} \)
4. **Sharpe ratio**: \( \frac{r_p - r_f}{\sigma_p} \)
5. **Maximum drawdown**: \( \frac{\text{Peak to trough decline}}{\text{Peak value}} \)

## Comparison with Previous Approaches

| Factor | CEX-DEX No-Transfer | CEX-CEX Arbitrage | DEX-DEX Arbitrage |
|--------|---------------------|-------------------|-------------------|
| Execution Speed | Mixed (CEX fast, DEX slow) | Fast (both platforms) | Slow (blockchain-dependent) |
| Cost Structure | Mixed (trading fees + gas) | Low (trading fees only) | High (gas costs dominant) |
| Capital Efficiency | Medium (capital on both platforms) | Low (capital split across CEXs) | High (single wallet) |
| Position Management | Complex (cross-platform exposure) | Simple (easily balanced) | Simple (blockchain-level) |
| Risk Profile | Hybrid (CEX counterparty + smart contract) | Counterparty risk dominant | Smart contract risk dominant |
| Price Differentials | Medium to large | Small | Medium |
| Technical Complexity | High (different platform types) | Medium (similar APIs) | Medium (blockchain focus) |

## Conclusion

CEX-DEX arbitrage without transfers represents a significant advancement in complexity compared to previous arbitrage strategies, incorporating elements of both centralized and decentralized trading environments while eliminating the friction of cross-platform asset movement. The key innovation lies in sophisticated position management across fundamentally different market structures, requiring careful attention to execution timing, correlation analysis, and dynamic exposure management.

This approach offers several advantages over transfer-based strategies, including faster execution, elimination of transfer delays and costs, and reduced counterparty risk from exchange withdrawals. However, it introduces the challenge of maintaining balanced exposure across platforms and requires more sophisticated risk management.

By applying the mathematical framework outlined in this document, traders can systematically identify opportunities, optimize execution parameters, and manage the specific risks of CEX-DEX arbitrage without transfers. As the cryptocurrency market continues to mature, with increasing integration between centralized and decentralized venues, this strategy will remain relevant as both a profitable approach and a foundation for more advanced cross-platform trading strategies. 