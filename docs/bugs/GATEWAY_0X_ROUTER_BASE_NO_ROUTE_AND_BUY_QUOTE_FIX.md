# BUG-007: `0x/router` 在 Base 上因旧 API 端点和 BUY 报价语义不兼容导致 `BRETT` 套利报价失败，已修复

**日期：** 2026-03-11  
**严重程度：** High  
**状态：** 已修复并完成运行中验证  
**影响范围：** `Gateway` 的 `0x/router` connector，已在 Base 上 `BRETT-USDC` 路径确认

---

## 问题概述

在 Base 上运行 `BRETT` 的 `0x/router` 套利策略时：

- `gateway approve 0x/router BRETT`
- `gateway approve 0x/router USDC`
- `gateway approve 0x/router WETH`

都能正常成功，但策略日志持续只显示：

- `No arbitrage opportunity.`

而不像 `uniswap/router` 或其他正常路径那样继续打印：

- `buy at ... : -x%`
- `sell at ... : -x%`

这说明问题不在授权，而在报价链路本身。

---

## 现场表现

### 1. 策略侧表现

策略文件：

- `conf/strategies/conf_amm_arb_BRETT_0x_router.yml`

初始失败时，日志 [logs_conf_amm_arb_BRETT_0x_router.log](/Users/alice/Dropbox/投资/量化交易/hummingbot/logs/logs_conf_amm_arb_BRETT_0x_router.log) 只有：

- `2026-03-11 10:08:48 No arbitrage opportunity.`
- `2026-03-11 10:09:09 No arbitrage opportunity.`
- `2026-03-11 10:09:30 No arbitrage opportunity.`

没有任何后续百分比行。

### 2. Gateway 侧表现

`gateway` 日志当时反复出现：

- `Getting indicative price for 5000 BRETT -> USDC`
- `Getting indicative price for 5000 BRETT <- USDC`
- `0x API Error Response: {"message":"no Route matched with those values"}`

这意味着 `amm_arb` 没有拿到完整 quote，因此根本没有生成 proposal。

### 3. 为什么没有百分比

`amm_arb` 的 proposal 构造逻辑在：

- `hummingbot/strategy/amm_arb/utils.py`

其中只要四个报价里的任意一个是 `None`，该 proposal 就会被直接跳过：

```python
if any(p is None for p in (m_1_o_price, m_1_q_price, m_2_o_price, m_2_q_price)):
    continue
```

所以这次“没有百分比”并不等于“只是没机会”，而是：

- `0x/router` 侧报价失败
- `_all_arb_proposals` 为空
- 最终只剩下一句 `No arbitrage opportunity.`

---

## 根本原因

本次问题实际由两个兼容性问题叠加触发。

### 1. Base 的 0x API 端点使用了旧子域名

文件：

- `gateway/src/connectors/0x/0x.config.ts`

修复前，Base 被映射到：

```ts
base: 'base.api.0x.org',
```

但实测在 `2026-03-11`，同样的请求：

- `WETH -> USDC`
- `BRETT -> USDC`

打到 `https://base.api.0x.org` 都返回：

- `no Route matched with those values`

而改用 0x 当前 live v2 入口：

- `https://api.0x.org`
- 并携带 `chainId=8453`

后，`SELL` 路径立刻可以正常返回 route。

结论：

- `base.api.0x.org` 对这套 connector 已经不再是正确入口
- 0x v2 当前应使用全局域名 `api.0x.org`，再通过 `chainId` 选择链

### 2. `BUY` 侧仍按 `buyAmount` exact-output 语义请求 0x

文件：

- `gateway/src/connectors/0x/router-routes/quoteSwap.ts`
- `gateway/src/connectors/0x/0x.ts`

修复前：

- `SELL` 使用 `sellAmount`
- `BUY` 使用 `buyAmount`

但在 `2026-03-11` 的 0x live API 实测中：

- `SELL + sellAmount` 可以正常返回
- `BUY + buyAmount` 在 `permit2/price`
- `BUY + buyAmount` 在 `permit2/quote`
- `BUY + buyAmount` 在 `allowance-holder/price`
- `BUY + buyAmount` 在 `allowance-holder/quote`

都返回：

- `INPUT_INVALID`
- 错误详情里明确指向 `sellAmount`

这说明当前 0x v2 的这条接法下，Hummingbot 原有的“BUY = exact-output / buyAmount”语义不再兼容。

### 3. 修复后出现的次级问题：rate limit

当 API 端点改对后，`SELL` 立即恢复，但很快又出现：

- `Rate limit exceeded`

原因不是新 bug，而是旧 `amm_arb` 调用模式和新 `BUY` 搜索逻辑叠加：

- 同一轮会并发触发重复的 `BUY` / `SELL` indicative quote
- `BUY` 侧为了兼容 0x，需要内部做多次 exact-input 搜索

所以在 0x 端会形成瞬时 burst。

---

## 本次修复

### 1. 将 0x API Base 入口改为全局域名

文件：

- `gateway/src/connectors/0x/0x.config.ts`

修复后不再按网络映射子域名，而是统一：

```ts
return 'https://api.0x.org';
```

由 `chainId` 决定具体链。

### 2. 将 `BUY` 报价改为 exact-input 搜索

文件：

- `gateway/src/connectors/0x/router-routes/quoteSwap.ts`

修复原则：

- Hummingbot 仍然对外保持 `BUY amount(base)` 语义
- 但不再把该数量直接作为 0x 的 `buyAmount`
- 改为用 quote token 做 exact-input 搜索
- 找到“能够买到目标 base 数量”的最小 spend

实现方式：

- 先用同样 base 数量做 sell-side 估算，得到一个起始花费
- 如果买不到目标数量，则逐步放大
- 买到后，再按实际回报比例快速收敛到更接近最小花费的位置

这样可以兼容当前 0x live API，同时保持 `amm_arb` 现有策略接口不变。

### 3. 为 indicative quote 增加短 TTL 去重

同一文件：

- `gateway/src/connectors/0x/router-routes/quoteSwap.ts`

新增：

- 1.5 秒内同参数 indicative quote 缓存
- in-flight request 去重

目的：

- 把同一轮 `amm_arb` 的重复请求压成一次
- 降低 0x 瞬时 burst
- 避免刚修好路由又被限流打断

---

## 修复后验证

### 1. Gateway 本地 quote 已恢复

修复后，在本机直接调用：

- `GET /connectors/0x/router/quote-swap?network=base&baseToken=BRETT&quoteToken=USDC&amount=5000&side=SELL`

返回：

- `amountOut = 35.264097 USDC`

对应：

- `price = 0.0070528194 USDC/BRETT`

同样地：

- `GET /connectors/0x/router/quote-swap?network=base&baseToken=BRETT&quoteToken=USDC&amount=5000&side=BUY`

返回：

- `amountIn = 35.41463 USDC`

对应：

- `price = 0.007082926 USDC/BRETT`

说明买卖双向都已经能正常报价。

### 2. `amm_arb` 百分比日志恢复

修复后，策略日志 [logs_conf_amm_arb_BRETT_0x_router.log](/Users/alice/Dropbox/投资/量化交易/hummingbot/logs/logs_conf_amm_arb_BRETT_0x_router.log) 已重新出现正常百分比输出。

例如：

- `2026-03-11 10:44:05`
  - `buy at 0x/router, sell at gate_io: -0.39%`
  - `sell at 0x/router, buy at gate_io: -0.65%`

以及更晚的稳定运行日志：

- `2026-03-11 16:19:55`
  - `buy at 0x/router, sell at gate_io: -0.56%`
  - `sell at 0x/router, buy at gate_io: -0.94%`

这说明：

- `0x/router` 报价已进入 proposal 计算
- 策略本身没有卡住
- 当前只是市场没有正价差

### 3. 近期运行状态

后续复查时：

- `gateway` 最近运行日志里没有再出现
  - `no Route matched with those values`
  - `INPUT_INVALID`
  - `Rate limit exceeded`
  - `Error getting 0x quote`
- `hummingbot` 侧持续输出 `No arbitrage opportunity.` 加双向百分比

这说明本次修复已经在运行中的系统里稳定生效。

---

## 与授权问题的关系

本次问题容易误判为：

- BRETT 没有 approve
- USDC 没有 approve
- WETH 没有 approve

但实际不是。

原因是：

- `approve` 只决定后续执行是否能 spend token
- 本次失败发生在更前面的 quote 阶段
- 即使 allowance 不足，0x 也依然可以返回 route，只会在响应里标出 `issues.allowance`

所以：

- 这次 `approve` 成功不代表报价链一定正常
- 根因仍然是 connector 的 API 兼容问题

---

## 当前残留与注意事项

### 1. `BUY` 侧是兼容性搜索实现，不是 0x 原生 exact-output

修复后：

- 对 Hummingbot 来说仍然表现为 `BUY 5000 BRETT`
- 但内部实际是基于 exact-input 搜索得到的近似最小 spend

这能正常工作，但相比原生 exact-output：

- 会更复杂一些
- 对流动性曲线更敏感
- 在极端波动时可能略偏保守

### 2. 当前去重是轻量缓解，不是完整 rate limiter

本次只加了：

- 短 TTL 缓存
- in-flight 去重

这已经足以让当前 `BRETT` 策略稳定运行。  
但如果后续再同时增加更多 `0x/router` 策略，仍可能需要更正式的：

- connector-level rate limiter
- 429 backoff

### 3. 容器重建后需要重新使用带补丁的镜像

本次修复已写入本地源码，并热同步到运行中的 `gateway` 容器。  
当前容器继续 `restart` 不会丢。

但如果之后执行：

- `docker compose up --force-recreate`
- 重新拉取旧镜像
- 用未重建的 `firenirva/gateway:latest` 替换当前容器

则该修复会回退。

---

## 结论

本次 `0x/router` 问题已经确认闭环：

1. 不是授权问题  
2. 根因一是 Base 端点写成了旧的 `base.api.0x.org`  
3. 根因二是 `BUY` 报价仍按 `buyAmount` exact-output 调用 0x v2  
4. 修复后 `BRETT-USDC` 的买卖双向 quote 都恢复正常  
5. `amm_arb` 已重新稳定输出双向百分比  
6. 当前仅剩市场层面的“没有正价差”，不再是程序故障
