# BUG-006: `amm_arb` 对执行净报价的 `Gateway DEX` 存在重复 percent fee 计入，已修复

**日期：** 2026-03-09  
**严重程度：** High  
**状态：** 已修复  
**影响范围：** `amm_arb` + `GatewaySwap` + 动态注册的 `Gateway DEX` connectors，已用 `uniswap/clmm` `BRETT-WETH` 路径确认

---

## 问题概述

在 `BRETT` 套利策略运行期间，`amm_arb` 屏幕日志持续显示：

- `buy at uniswap/clmm, sell at gate_io: -1.22%`
- `buy at uniswap/clmm, sell at gate_io: -1.26%`
- `buy at uniswap/clmm, sell at gate_io: -1.28%`

但现场抓取 `Gateway quote`、`Gate.io` 订单簿以及 `ETH_USDT` 汇率后，发现这条路径的真实执行价虽然仍然是负收益，但没有日志里那么差。

根因不是 `BRETT` 池本身额外收了两次费，而是：

- `Gateway` 返回的 DEX quote 已经是执行后的成交报价
- `amm_arb` 在 `profit_pct(account_for_fee=True)` 中又调用了一次 `build_trade_fee()`
- 动态注册的 `Gateway DEX` connector 被统一写死为 `0.3%` maker/taker percent fee

结果就是：

- DEX 一侧又被多扣了一次 percent fee
- `amm_arb` 日志收益被系统性压低

---

## 现场复现与定量判断

### 1. 路径与现场数据

- 策略：`conf_amm_arb_BRETT.yml`
- DEX：`uniswap/clmm` `BRETT-WETH`
- CEX：`gate_io` `BRETT-USDT`
- 网络：`base`
- 池地址：`0xba3f945812a83471d709bce9c3ca699a19fb46f7`

现场抓取到的关键值：

- `BUY 5000 BRETT` quote: `amountIn = 0.017673422056518152 WETH`
- `SELL 5000 BRETT` quote: `amountOut = 0.017308863783860987 WETH`
- `gate_bid = 0.007152`
- `gate_ask = 0.00716`
- `ETH_USDT mid = 2037.065`

### 2. 复算结果

按真实执行价直接复算：

- `gross = -0.6719351970779755230157791464%`

再只加 Gate.io 默认 taker fee `0.2%`：

- `after_gate_fee = -0.8705913266838195719697475881%`

再同时加上 Hummingbot 对 `Gateway DEX` 统一登记的 `0.3%` fee：

- `after_both = -1.167090056514276741744514046%`

这已经非常接近现场日志的 `-1.22% ~ -1.28%` 区间。

结论：

- 这条路径本来就是负收益
- 但日志里额外偏负的一截，确实来自 `Gateway DEX 0.3%` 的重复计入

---

## 根因链路

### 1. `Gateway` 报价本身已经是执行报价

`Gateway` 的 swap quote 返回值直接来自实际路由交易对象：

- `estimatedAmountIn`
- `estimatedAmountOut`
- `priceImpact`

这代表的是该笔交易的真实成交报价，不是未计入池费的静态 `spot`。

### 2. `amm_arb` 会再次构造 trade fee

`amm_arb` 在筛选 proposal 时使用：

- `profit_pct(account_for_fee=True)`

而 `profit_pct()` 会重新调用：

- `build_trade_fee()`

这会再按 connector 的 fee schema 扣一次 percent fee。

### 3. `Gateway DEX` 的 fee schema 被硬编码成 `0.3%`

问题落点不在 `amm_arb` 策略参数，而在 `Gateway` 动态 connector 注册时的元数据：

- 文件：`hummingbot/core/gateway/gateway_http_client.py`
- 函数：`_register_gateway_connectors()`

修复前这里统一写死：

```python
trade_fee_schema=TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.003"),
    taker_percent_fee_decimal=Decimal("0.003"),
)
```

这会让任何依赖 `build_trade_fee()` 的调用路径都把 `Gateway quote` 当成“还没扣过 DEX fee 的价格”，从而重复计入。

---

## 为什么不靠策略配置修

这个问题不能靠 `conf_amm_arb_BRETT.yml` 这类策略文件真正修复。

原因是：

- `min_profitability`
- `order_amount`
- `slippage_buffer`

这些参数只能绕开错误，不能消除那次多扣的 percent fee。

`conf_fee_overrides.yml` 在当前实现里也不是可靠的无代码解法，因为 `Gateway` connectors 是运行时动态注册的，而 fee override 键并不会自动为这些新 connector 刷新生成。

所以正式修复必须落在代码里。

---

## 本次代码修复

### 修复原则

把问题修在 connector 元数据层，而不是只在 `amm_arb` 里做特判。

这样做的原因是：

- 重复扣费的根因是 `Gateway DEX` 的 fee schema 定义错误
- 不只是 `amm_arb`，任何以后依赖 `build_trade_fee()` 的路径都可能受到影响
- `network_transaction_fee` 仍然通过 `extra_flat_fees` 单独计入，不受这次修复影响

### 实际修改

#### 1. 将动态注册的 `Gateway DEX` 默认 percent fee 改为 `0`

文件：

- `hummingbot/core/gateway/gateway_http_client.py`

修复后：

```python
trade_fee_schema=TradeFeeSchema()
```

并增加注释说明：

- `Gateway swap quotes already reflect execution pricing`
- client side 不应再次追加 percent fee

#### 2. 新增回归测试

文件：

- `test/hummingbot/core/test_gateway_http_client.py`

新增测试覆盖：

- 动态注册 `uniswap/clmm` connector 后
- `maker_percent_fee_decimal == 0`
- `taker_percent_fee_decimal == 0`

这样后续如果有人把默认 fee schema 改回非零，测试会直接失败。

---

## 修复后行为

修复完成后，`Gateway` connector 的成本计入方式变成：

- `Gateway quote` 负责反映池费和执行价偏移
- `network_transaction_fee` 继续作为链上 gas 成本单独计入
- Hummingbot 不再额外给 `Gateway DEX` 补一层统一 `0.3%` percent fee

这意味着：

- `amm_arb` 的日志收益会更接近真实可执行收益
- proposal 排序不再被这层假 fee 系统性压低
- 这不是“把亏损变盈利”，而是把利润计算恢复到正确口径

---

## 验证结果

### 1. 逻辑验证

用 `BRETT` 现场数据复算时：

- `gross = -0.6719%`
- `after_gate_fee = -0.8706%`
- `after_both = -1.1671%`

其中 `after_both` 与现场日志非常接近，足以证明那层 `Gateway 0.3%` 在参与利润计算。

修复后，这层额外 percent fee 不应再出现。

### 2. 链上路径验证

此前 `BRETT-WETH` 的 swap 执行还叠加遇到过 `sqrtPriceLimitX96` 的 `SPL` revert 问题，该问题已单独修复并记录在：

- `docs/bugs/GATEWAY_UNISWAP_CLMM_SQRT_PRICE_LIMIT_SPL_REVERT.md`

在两个问题都处理后，链上实际 swap 已成功确认：

- 交易哈希：`0xbb3b87e5f19ea965712ad85935693b5eaa58bf2bfd08df21dc1269267e0344e8`
- Base 区块：`43126995`
- 状态：`status 1`

说明这条 `BRETT-WETH` 实盘路径已经恢复可用。

### 3. 单元测试验证

新增测试文件：

- `test/hummingbot/core/test_gateway_http_client.py`

用于锁定：

- 新注册的 `Gateway` connector 不再自带非零 percent fee

---

## 本次改动文件

- `hummingbot/core/gateway/gateway_http_client.py`
- `test/hummingbot/core/test_gateway_http_client.py`
- `docs/bugs/AMM_ARB_GATEWAY_DEX_FEE_DOUBLE_COUNT_BRETT.md`

---

## 后续注意事项

1. 这次修复依赖你本地代码，而不是容器内热改；后续重建或重启 Hummingbot 时会保留。
2. 如果未来某个新的 `Gateway` connector 返回的是“未计协议费的原始 spot quote”，那它不能复用这套假设，需要单独核对 quote 语义。
3. 如果后面希望通过 `conf_fee_overrides.yml` 覆盖动态 `Gateway` connectors 的 fee，建议再单独补一项增强：在动态注册后刷新 fee override config map。

---

## 最终结论

这个问题是合理且已经落成代码修复的：

- 根因：`Gateway DEX` 默认 fee schema 被错误地当成 `0.3%`
- 影响：`amm_arb` 对执行净报价再次扣 percent fee，导致日志收益偏低
- 修复：把动态注册的 `Gateway DEX` 默认 percent fee 改为 `0`
- 结果：利润计算口径恢复正确，且不会影响 gas 的单独计入
