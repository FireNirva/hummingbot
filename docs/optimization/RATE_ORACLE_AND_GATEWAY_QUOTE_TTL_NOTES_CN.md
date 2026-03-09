# Rate Oracle 与 Gateway Quote TTL 记录

**日期：** 2026-03-09  
**适用范围：** Hummingbot `amm_arb` + Gateway `uniswap/clmm` + CEX-DEX 套利

## 当前问题

### 1. `rate_oracle` TTL 过长

- 代码位置：`hummingbot/core/rate_oracle/sources/*.py`
- 原设置：`@async_ttl_cache(ttl=30, maxsize=1)`
- 影响：
  - `WETH-USDT` 等 quote 汇率最多每 `30s` 才刷新一次
  - 在 `BRETT-WETH` vs `BRETT-USDT` 这种跨 quote 套利中，机会判断容易滞后
  - 会出现 `CEX` 与 `DEX` 实时价差已经变化，但套利机会仍未出现或出现偏差

### 2. `GatewaySwap` DEX quote TTL 过长

- 代码位置：`hummingbot/connector/gateway/gateway_swap.py`
- 原设置：`@async_ttl_cache(ttl=5, maxsize=10)`
- 影响：
  - `uniswap/clmm` 的 `quote-swap` 价格最多每 `5s` 才刷新一次
  - 策略主循环是 `1s`，但 DEX quote 仍可能连续 `5s` 使用旧价格
  - 在快节奏套利窗口中，会明显拖慢机会识别

## 当前代码设置

### 1. Rate Oracle

- 已将全部 `rate_oracle source` 的 TTL 从 `30s` 调整为 `1s`
- 当前目标：
  - 更快更新 `WETH-USDT` 等 quote 汇率
  - 减少汇率滞后对套利判断的影响

### 2. Gateway DEX Quote

- 已将 `gateway_swap.py` 中 `get_quote_price()` 的 TTL 从 `5s` 调整为 `2s`
- 当前目标：
  - 比 `5s` 更快刷新 DEX quote
  - 避免直接降到 `1s` 后对 RPC 造成过大压力

## 当前权衡结论

- `rate_oracle = 1s`
  - 值得保留
  - 主要增加交易所价格源请求
  - 不直接增加链上 RPC 压力

- `gateway quote = 2s`
  - 是当前更平衡的设置
  - 比 `5s` 更快
  - 比 `1s` 更节省 RPC

## 速度关系

- 策略主循环：`1s`
- `rate_oracle`：`1s`
- `gate_io` 本地 order book：WebSocket 驱动，订单簿 diff 约 `100ms` 级
- `Gateway uniswap/clmm quote`：`2s`

## 后续观察重点

- 套利机会是否比之前更早出现
- `Gateway` 的 `quote-swap` 调用量是否明显上升
- Base RPC 是否出现更多 `429`、超时、空报价
- 机会是否更容易抖动或闪烁

## 如果后续仍然偏慢

- 优先考虑把 DEX quote 逻辑改成“条件触发式高频报价”
- 不建议默认把所有 DEX quote 直接压到 `1s`
- 若后续 RPC 余量充足，再评估是否从 `2s` 进一步降到 `1s`
