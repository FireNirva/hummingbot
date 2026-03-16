# Gate Fill Timeout 主动 REST Reconciliation 优化说明

## 背景

在 `gate_io` 路径下，系统近期多次出现以下现象：

- `OrderStatus` 已经先到达并将订单标记为 `FILLED`
- 但成交明细 `TradeUpdate` 没有在短时间内到达
- `ClientOrderTracker` 打出：

`The order fill updates did not arrive on time ...`

这会带来两个直接问题：

- 本地订单会以不完整的成交信息进入完成态
- 后续 `PnL`、库存、`MarketsRecorder` 对账链路容易漂移

这个问题已经在：

- 本地 `V2 cex-dex arb + gate_io`
- AWS 生产环境 `V1 amm_arb + gate_io`

都出现过，因此应视为 `gate_io + fills arrival timing` 的底层稳定性问题，而不是单一策略问题。

## 问题本质

当前 `ClientOrderTracker` 的处理逻辑是：

1. 收到 `OrderUpdate(FILLED)`
2. 等待 `tracked_order.wait_until_completely_filled()`
3. 最多等待 `TRADE_FILLS_WAIT_TIMEOUT = 5s`
4. 若 fills 仍未到达，则直接继续处理完成态

这意味着：

- 系统优先相信订单状态
- 但成交明细如果迟到，就只能以不完整信息完成订单

对于 `gate_io`，这不是理想行为，因为：

- 订单状态接口本身不提供完整 fill 细节
- 真正的成交量、成交价、手续费仍然依赖 trade history / fills 明细

## 本次修复

本次修复没有改下单、撤单、余额或事件状态机，而是在 fill timeout 边界增加了一个可选补偿 hook。

### 代码位置

- `ClientOrderTracker`
  - [client_order_tracker.py](/Users/alice/Dropbox/投资/量化交易/hummingbot/hummingbot/connector/client_order_tracker.py)
- `GateIoExchange`
  - [gate_io_exchange.py](/Users/alice/Dropbox/投资/量化交易/hummingbot/hummingbot/connector/exchange/gate_io/gate_io_exchange.py)
- 测试
  - [test_client_order_tracker.py](/Users/alice/Dropbox/投资/量化交易/hummingbot/test/hummingbot/connector/test_client_order_tracker.py)
  - [test_gate_io_exchange.py](/Users/alice/Dropbox/投资/量化交易/hummingbot/test/hummingbot/connector/exchange/gate_io/test_gate_io_exchange.py)

### 新逻辑

当 `OrderUpdate(FILLED)` 到达，但 `5s` 内还没等到完整 fill updates 时：

1. `ClientOrderTracker` 先检查 connector 是否实现 `reconcile_order_fills_on_timeout(...)`
2. 若实现，则主动触发一次 connector 级补偿
3. `GateIoExchange` 在该 hook 中调用既有的 `my_trades` REST 路径
4. 若这次 REST 拉回的 fills 足以补全订单，则继续正常完成订单
5. 若仍然拿不到，则保留原有 warning 和不完整完成路径

## 为什么这种做法是低风险的

这次优化是一个非常保守的补偿式修复，而不是行为重写。

它具备以下特点：

- 只在异常边界触发  
  仅当 fills 在超时时间内未到达时才执行

- 只对实现该 hook 的交易所生效  
  当前只有 `gate_io` 实现，其他交易所逻辑不变

- 复用已有 REST fills 查询逻辑  
  并没有引入新的私有接口或新的订单状态解释规则

- 仍然走原本的 fill 去重链路  
  重复成交不会因为 REST 补偿被双记

## 这次优化能带来什么

### 能改善的部分

- 降低 `gate_io` fill 迟到后，本地订单信息不完整的概率
- 降低库存、成交量、手续费、PnL 因 fills 迟到而漂移的概率
- 提升 `V1/V2` 两类策略在 `gate_io` 路径下的一致性

### 不能高估的部分

这次优化 **不是一个“显著降低 CEX 延迟”的修复**。

原因很简单：

- 当前补偿触发点是在 `TRADE_FILLS_WAIT_TIMEOUT = 5s` 超时之后
- 也就是说，它改善的是“5 秒后仍未拿到 fills 时的数据完整性”
- 它不会把原本的 `5s` 长尾直接压缩成 `1s`

更准确地说：

- 它提升的是 **准确性和稳定性**
- 不是显著提升 **成交确认速度**

## 为什么这次修复仍然值得进入生产

虽然它不能显著降低 `gate_io` 的长尾延迟，但它有明确的生产价值：

- 不改变核心下单路径
- 不放大安全面
- 只在异常边界补偿
- 可以同时改善 `V1 amm_arb` 和 `V2 cex-dex arb`

因此它适合作为：

`生产级准确性修复`

而不应被理解成：

`CEX 延迟优化主方案`

## 对生产长尾优化的真实启发

如果目标是 **在不牺牲安全性和准确性的前提下减少 `gate_io` 长尾**，更值得做的下一步是：

1. 在 `FILLED` 状态一到达时就并行触发一次主动 reconciliation，而不是等满 `5s`
2. 让 fill timeout 成为 connector 级可配置项，而不是全局固定 `5s`
3. 对 `gate_io` 的 fill update 超时场景做更积极的 REST 对账，而不是完全依赖用户流

本次修复可以视为：

`保守第一步`

后续如果继续推进 `gate_io` 长尾优化，应在此基础上逐步前移 reconciliation 的触发时机。

## 审计结论

本次修改适合进入生产候选，原因如下：

- 对其他 CEX 路径无影响
- 对 `gate_io` 是低侵入修复
- 解决的是已经在线上真实复现的问题
- 不会扩大资金风险面

但上线预期应保持准确：

- 这次会明显改善 fills 迟到后的准确性
- 不会单独显著缩短 `gate_io` 的确认延迟

