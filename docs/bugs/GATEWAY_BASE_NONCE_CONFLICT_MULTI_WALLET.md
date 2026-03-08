# BUG-002: Base 链同钱包多 Bot 并发交易导致 Nonce 冲突

**日期：** 2026-03-08  
**严重程度：** High  
**状态：** 解决方案已确认，建议按本文落地  
**影响范围：** 使用 `Gateway` 连接 Base/Uniswap/PancakeSwap 等 EVM DEX 的 CEX-DEX 套利策略，尤其是 `amm_arb`

---

## 问题概述

当多个 Hummingbot Bot 通过同一个 Base 钱包并发发起链上交易时，Gateway/EVM 路由会出现典型的 nonce 冲突错误：

- `nonce too low`
- `nonce has already been used`
- `replacement transaction underpriced`
- `replacement fee too low`

在 `amm_arb` 场景里，这通常表现为：

- DEX 首腿提交失败
- 套利机会被直接丢弃
- 盈利机会下降
- 当并发更高时，错误频率明显上升

这个问题的本质不是“Gateway 不能多 Bot 共用”，而是“同一条 EVM 链上的同一个钱包不能被多个活跃执行流无保护地并发使用”。

---

## 根本原因

### 1. Gateway 没有为同钱包并发交易做统一 nonce 协调

Base/Uniswap 交易路由会直接从 provider 读取链上当前 nonce，然后构造并发送交易。例如：

- `hummingbot/gateway/src/connectors/uniswap/amm-routes/executeSwap.ts`
- `hummingbot/gateway/src/chains/ethereum/routes/approve.ts`

核心问题是：当两个 Bot 几乎同时对同一个 Base 钱包发起交易时，它们可能读到同一个链上 nonce，随后同时尝试发送交易，导致：

- 一个交易成功使用该 nonce
- 另一个交易变成 `nonce too low`
- 或者因为 gas/fee 不够而变成 `replacement transaction underpriced`

### 2. Hummingbot 默认会回退到链级默认钱包

当前 Gateway 的默认钱包配置是链级别的：

- `hummingbot/gateway/src/chains/ethereum/ethereum.config.ts`
- `ethereum.defaultWallet`

而 Gateway 连接器如果没有显式传入 `address`，就会回退到默认钱包：

- `hummingbot/hummingbot/connector/gateway/gateway_base.py`

这意味着如果多个 Bot 都没有明确指定自己的 Base 钱包地址，它们很容易全部落到同一个 `ethereum.defaultWallet` 上。

### 3. Base 不是单独的 chain，而是 ethereum 下面的 network

在 Gateway 的配置模型里：

- `ethereum.defaultWallet` 是链级默认钱包
- `base` 是 `ethereum-base` 这个 network

所以不能简单理解成“Base 链会天然有独立默认钱包”。如果不做显式地址绑定，多个 Base 相关 Bot 仍然可能共享同一个默认 EVM 钱包。

---

## 日志证据

历史 Gateway 日志中已经出现大量 nonce/替换冲突，主要集中在：

- `NONCE_EXPIRED`
- `REPLACEMENT_UNDERPRICED`

这说明问题不是理论风险，而是已经实际发生。

在 `amm_arb` 的实际日志中，也能看到如下现象：

1. 发现套利机会
2. 尝试在 `uniswap/amm` 提交 Base 链交易
3. Gateway 返回 `InternalServerError`
4. Hummingbot 标记 `MarketOrderFailureEvent`
5. 策略输出 `Dropping Arbitrage Proposal`

典型相关日志：

- `aws1-logs/hummingbot2-logs/logs_conf_amm_arb_IRON.log.2026-02-14`
- `aws1-logs/hummingbot2-logs/logs_conf_amm_arb_IRON.log.2026-02-15`
- `aws1-logs/gateway-logs/logs/logs_gateway_app.log.2026-02-14`
- `aws1-logs/gateway-logs/logs/logs_gateway_app.log.2026-02-15`

---

## 结论

### 不推荐的默认思路

`一个钱包 = 多个活跃 Base Bot`

这会直接放大 nonce 冲突概率。

### 推荐的目标架构

`多个钱包 + 一个 Gateway`

具体是：

- 一个 Gateway 统一管理多个 Base 钱包
- 每个活跃的 Base 执行 Bot 绑定一个独立的 Base 钱包
- 多个 Bot 共享同一个 Gateway 服务实例
- 但不共享同一个 Base 钱包

---

## 为什么推荐“多个钱包 + 一个 Gateway”

这是本文的核心建议。

### 1. 它解决的是正确的问题

真正冲突的是：

- 同一条链
- 同一个钱包
- 同时发起多笔交易

所以应该拆的是“钱包”，而不是优先拆“Gateway”。

### 2. 单 Gateway 可以复用底层链连接和 DEX 实例

Gateway 进程内部对网络级对象做了单例复用，例如：

- `Ethereum.getInstance(network)`
- `Uniswap.getInstance(network)`

这意味着在同一个 Gateway 进程中：

- Base provider 连接是复用的
- Uniswap/PancakeSwap 等 DEX 实例是复用的
- 一些缓存和对象初始化成本只需要承担一次

如果你改成“一个钱包一个 Gateway”，这些东西会被重复创建。

### 3. 多开 Gateway 会增加服务器和运维开销

一个钱包对应一个 Gateway 并不是不能用，但它会带来额外开销：

- 更多 Node.js 进程
- 更多日志目录
- 更多端口和证书管理
- 更多容器/服务监控
- 更多重复的链连接和 DEX 初始化
- 更多重复的 quote/poll/balance 流量

从工程角度看，这种开销通常不值得作为长期方案。

### 4. 一个 Gateway 统一管理多钱包更容易扩展

当你以后从 1 个 Bot 扩展到 2 个、4 个、8 个时：

- 增加钱包比增加 Gateway 更容易
- 统一监控、统一日志、统一升级更简单
- 资源利用率更高

---

## 详细解决方案

### 总体原则

生产环境请遵循以下规则：

1. 一个活跃 Base 套利 Bot 绑定一个独立 Base 钱包
2. 所有 Base 钱包统一放入同一个 Gateway
3. 不要让多个活跃 Bot 共用同一个 Base 钱包
4. 默认钱包只作为回退/手工维护用途，不要作为多 Bot 生产流量入口

---

## 落地步骤

### Step 1: 为 Base 准备多个独立钱包

为每个活跃的 Base Bot 准备一个钱包，例如：

- `wallet_base_01`
- `wallet_base_02`
- `wallet_base_03`

每个钱包都需要：

- 单独的 Base Gas
- 单独的交易代币余额
- 单独完成首次授权（approve）

不要指望多个 Bot 共用一个钱包去省 Gas 或省资金切分，这通常会在高频交易时转化成 nonce 冲突和机会损失。

---

### Step 2: 将多个钱包导入同一个 Gateway

在同一个 Gateway 中多次导入 EVM 钱包即可。

逻辑上这些钱包会被放在：

- `./conf/wallets/ethereum/<address>.json`

可通过以下方式导入：

- 使用 `gateway connect ethereum`
- 或调用 `POST /wallet/add`

建议：

- 第一个钱包可以先设为默认钱包
- 后续钱包导入时不要覆盖默认钱包，避免误把所有 Bot 都切回同一个地址

---

### Step 3: 不要依赖默认钱包做多 Bot 生产交易

这是最重要的一步。

虽然 Gateway 支持多个钱包，但如果 Bot 没有显式指定自己的钱包地址，它仍然可能回退到：

- `ethereum.defaultWallet`

这会让“多个钱包一个 Gateway”形同虚设。

生产场景应要求：

- Bot A 只使用 `wallet_base_01`
- Bot B 只使用 `wallet_base_02`
- Bot C 只使用 `wallet_base_03`

而不是都走默认钱包。

---

### Step 4: 为每个 Bot 明确绑定自己的 Base 钱包

这是“多个钱包一个 Gateway”真正生效的关键。

需要做到：

- 每个 Bot 在创建 Gateway connector 时，都显式传入自己的 `wallet_address`
- 不让连接器自动回退到 `defaultWallet`

目标效果是：

```text
Bot A -> Gateway -> Base wallet A
Bot B -> Gateway -> Base wallet B
Bot C -> Gateway -> Base wallet C
```

而不是：

```text
Bot A -> Gateway -> defaultWallet
Bot B -> Gateway -> defaultWallet
Bot C -> Gateway -> defaultWallet
```

当前这套 `amm_arb` 已经可以通过策略配置直接绑定钱包地址：

```yaml
wallet_address: "0x164BeADF2adD3A0a0cD091eB210AD255b897970b"
```

实现方式是：

- `amm_arb_config_map.py` 暴露了可选配置项 `wallet_address`
- `start.py` 在 `initialize_markets()` 之后、`start_network()` 取默认钱包之前，将地址绑定到 Gateway connector
- `GatewayBase.start_network()` 仅在 `_wallet_address` 为空时才回退默认钱包

这意味着现在可以直接落地“单 Gateway、多钱包、多 Bot”的目标架构，而不需要再依赖“一个钱包一个 Gateway”的临时隔离方案。

需要注意的是：

- 交易执行
- 余额查询
- 授权交易

这些路径都会使用显式绑定的钱包地址；报价接口仍然主要按 connector/network 维度工作，但这不影响 nonce 隔离，因为 nonce 冲突发生在链上交易发送阶段。

---

### Step 5: 每个钱包分别做授权和余额初始化

多钱包架构下，必须接受一个现实：

- `approve` 不是共享的
- 余额也不是共享的

所以每个 Base 钱包都需要：

1. 充值 Base 原生 Gas
2. 准备对应交易资产
3. 完成 DEX Router/Permit2 等授权
4. 验证链上余额和 allowance 正常

这一步是必须成本，但它换来的是交易稳定性和更低的 nonce 风险。

---

### Step 6: 分配 Bot 与钱包的映射

建议使用固定映射，不要动态切换：

```text
IRON arb bot   -> wallet_base_01
BENJI arb bot  -> wallet_base_02
GPS arb bot    -> wallet_base_03
```

固定映射有几个好处：

- 更容易排查日志
- 更容易核对余额
- 更容易做收益归因
- 不容易误切回默认钱包

---

### Step 7: 验证是否已经真正消除同钱包冲突

验证标准：

1. Gateway 日志中显著减少：
   - `nonce too low`
   - `replacement transaction underpriced`
2. `amm_arb` 日志中显著减少：
   - `MarketOrderFailureEvent`
   - `Dropping Arbitrage Proposal`
3. 不同 Bot 的链上交易哈希分布在不同钱包地址下

如果 Bot 仍然全部打到同一个 Base 地址，说明“多钱包一个 Gateway”只做了一半，绑定没有真正生效。

---

## 不推荐的方案

### 方案：一个钱包对应一个 Gateway

这个方案可以作为临时过渡手段，但不推荐作为长期标准架构。

#### 它的优点

- 不改代码也容易做隔离
- 默认钱包模型下容易理解
- 短期止血有效

#### 它的问题

- 进程数量快速增加
- 重复占用内存和 CPU
- 重复初始化 provider / DEX 对象
- 端口、证书、日志、监控更复杂
- 扩容后运维成本明显上升

#### 适合什么场景

仅适合：

- 紧急止血
- 暂时不能改配置或代码
- 需要快速把冲突 Bot 拆开

#### 不适合什么场景

- 中长期生产部署
- 多 Bot 持续扩容
- 希望降低服务器开销和运维复杂度

---

## 推荐实施顺序

### 阶段 1：短期稳定化

先确保：

- 一个活跃 Base Bot 不再与其他活跃 Bot 共用钱包

如果当前系统还做不到显式钱包绑定，可以先临时隔离。

### 阶段 2：目标架构

最终收敛到：

- 一个 Gateway
- 多个 Base 钱包
- 每个 Bot 在策略配置里显式填写 `wallet_address`

这是本文推荐的长期方案。

---

## 运维建议

1. 给每个钱包建立清晰命名和台账
2. 记录 Bot -> wallet -> CEX account 的固定映射
3. 每个钱包单独监控：
   - Gas 余额
   - 交易代币余额
   - allowance
4. 定期检查 Gateway 日志中的 nonce 相关错误是否下降
5. 不要把“默认钱包”当成多 Bot 的公共入口

---

## 一句话结论

**推荐方案：多个 Base 钱包放在同一个 Gateway 中，并让每个活跃 Bot 明确绑定自己的 Base 钱包。**

**不推荐方案：一个钱包一个 Gateway 作为长期架构。它只是临时隔离手段，不是最优设计。**

---

## 状态

**结论已确认**  
**推荐执行**：`多个钱包 + 一个 Gateway + 每个 Bot 在 amm_arb 配置中显式填写 wallet_address`
