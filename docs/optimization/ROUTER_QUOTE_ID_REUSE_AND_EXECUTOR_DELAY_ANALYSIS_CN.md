# `router` 延迟根因、`quote_id` 未复用与 V1/V2 套利策略分析

**日期：** 2026-03-14  
**适用范围：** Hummingbot `amm_arb`、V2 `cex-dex` 套利脚本、Gateway `router` connector  
**分析目标：** 解释当前 `router` 路径的高延迟根因，定位代码修改点，并说明为什么 `quote_id` 长期没有在套利策略中被复用

---

## 1. 结论先行

这次分析的核心结论有 4 条：

1. 当前 `router` 路径的主要延迟，不是单纯 `router` quote 本身慢，而是：
   - 策略层把 `CreateExecutorAction` 延迟到下一轮 tick 才执行，固定多出约 `1s`
   - 执行阶段没有复用 discovery 阶段的 `quote_id`，导致 `router` 在下单前又重新 quote 一次

2. 这个问题不是当前 V2 自定义脚本独有。对 `uniswap/router` 来说，老的 V1 `amm_arb` 也有同一类根因：
   - discovery 阶段重复取 `quote_price / order_price`
   - execution 阶段再次走 quote
   - `quote_id` 没有从 discovery 传到 execution

3. `quote_id` 未复用更像一个**历史演进造成的接口缺口**，不是刻意设计，也不完全像单一 bug。
   下层 `gateway_swap` 和 `gateway swap` 命令已经支持 `execute_quote`，但套利策略栈仍然停留在“只保留价格数字”的旧接口模型里。

4. 截至本次分析，在公开 GitHub 资料里，没有看到一个非常明确、持续被讨论的 issue 专门追踪“`amm_arb` / `ArbitrageExecutor` 没有复用 `quote_id` 导致 router 重复 quote”这件事。

---

## 2. 这次分析的问题是什么

本次分析围绕两个直接问题展开：

1. 当前 V2 `cex-dex` 套利脚本里，为什么从发现机会到真正把 `router` 那条腿发出去，中间会多出明显延迟？
2. 为什么 Gateway 已经支持 `quote_id -> execute_quote`，但套利策略却没有复用这条能力？

为了回答这两个问题，我检查了：

- 当前自定义 V2 脚本
- `StrategyV2Base` 的 tick 和 action 消费链路
- `ArbitrageExecutor`
- `GatewaySwap`
- `uniswap/router` 的 `quoteSwap / executeSwap / executeQuote`
- V1 `amm_arb`
- 本地 git 历史
- 公开 GitHub 搜索结果

---

## 3. 发现一：V2 当前固定多出约 1 秒，不是 router 本身造成的

### 3.1 代码链路

当前 V2 脚本：

- `scripts/v2_cex_dex_aggregator_arb.py`

关键流程是：

1. `on_tick()` 每秒执行一次
2. 没有活跃 executor 时，启动 `_evaluate_and_queue_action()`
3. 发现机会后，不直接创建 executor
4. 而是先把 `CreateExecutorAction` 存到 `_pending_create_action`
5. 下一轮 tick 再由 `create_actions_proposal()` 取出来
6. 再由 `_execute_local_executor_actions()` 真正交给 orchestrator

对应代码位置：

- `scripts/v2_cex_dex_aggregator_arb.py`
  - `on_tick()`
  - `create_actions_proposal()`
  - `_execute_local_executor_actions()`
  - `_evaluate_and_queue_action()`

底层 tick 频率在：

- `hummingbot/strategy/strategy_v2_base.py`
  - `tick()`

### 3.2 这意味着什么

这条链路天然会带来一个固定延迟：

`发现机会 -> 写入 _pending_create_action -> 等下一拍 tick -> 真正 create executor`

因此，即使 discovery 阶段 quote 很快，这里也会稳定多出约 `1s`。

### 3.3 修复点

这一点的修复位置主要在：

- `scripts/v2_cex_dex_aggregator_arb.py`

修复思路是：

- 不再把 `CreateExecutorAction` 先塞进 `_pending_create_action`
- 而是在 `_evaluate_and_queue_action()` 判断通过后，直接执行本地 action

这是最直接、最稳定能拿回来的固定延迟。

---

## 4. 发现二：真正更大的延迟来自执行阶段重复 quote

### 4.1 下层其实已经支持 `quote_id`

当前下层 Gateway 交易执行已经支持 `quote_id`：

- `hummingbot/connector/gateway/gateway_swap.py`

逻辑是：

- 如果下单时传了 `quote_id`
  - 走 `execute_quote`
- 如果没有传
  - 走 `execute_swap`

### 4.2 `uniswap/router` 的 `execute_swap` 会再次 quote

`uniswap/router` 的执行代码在：

- `gateway/src/connectors/uniswap/router-routes/executeSwap.ts`

它的流程非常明确：

1. `quoteSwap(...)`
2. `executeQuote(...)`

也就是说：

`execute_swap = 重新 quote 一次 + 用新 quote 执行`

而 quote 侧本来就已经生成并缓存了 `quoteId`：

- `gateway/src/connectors/uniswap/router-routes/quoteSwap.ts`

因此，在当前架构里，如果上层不传 `quote_id`，就会出现：

`discovery quote 一次 -> execution 又 quote 一次`

### 4.3 这才是 router 这条腿最大的延迟来源

对 `router` 来说，quote 本身就比 `amm/clmm` 更重，因为它会走 AlphaRouter。

所以当前最大的时间浪费不是：

- 链上广播慢
- CEX 下单慢

而是：

- discovery 时 quote
- execution 前又 quote

这部分通常比前面那个固定 `1s` 更重。

### 4.4 修复点

这件事要真修，改动点不在一个文件，而是 3 层：

1. **Gateway quote 返回层**
   - `hummingbot/connector/gateway/gateway_swap.py`
   - 现在 `get_quote_price()` 只返回 `Decimal`
   - 需要把 `quote_id` 和价格一起保留下来

2. **策略发现层**
   - `scripts/v2_cex_dex_aggregator_arb.py`
   - 发现机会时不能只保存 `price`
   - 还要保存对应的 `quote_id`

3. **Executor 执行层**
   - `hummingbot/strategy_v2/executors/arbitrage_executor/data_types.py`
   - `hummingbot/strategy_v2/executors/arbitrage_executor/arbitrage_executor.py`
   - executor config 需要容纳 `quote_id`
   - 下单时需要把 `quote_id` 透传到 `gateway_swap.buy()/sell()`

---

## 5. V1 `amm_arb` 之前的高延迟，是不是同一个原因

对 `uniswap/router` 来说，答案基本是：**是。**

### 5.1 V1 discovery 阶段也有重复请求

V1 `amm_arb` 的 discovery 在：

- `hummingbot/strategy/amm_arb/utils.py`

它每轮会同时请求：

- `get_quote_price()`
- `get_order_price()`

但对 gateway swap connector 来说：

- `get_order_price()` 其实只是再次调用 `get_quote_price()`

对应代码：

- `hummingbot/connector/gateway/gateway_swap.py`

这意味着 V1 在 gateway swap 路径里，本来就有 discovery 阶段的重复 quote。

### 5.2 V1 execution 阶段同样没传 `quote_id`

V1 的下单在：

- `hummingbot/strategy/amm_arb/amm_arb.py`

它调用 `place_arb_order()`，再走：

- `buy_with_specific_market()`
- `sell_with_specific_market()`

这条链只传：

- 市场
- 数量
- 价格

不会传 `quote_id`。

因此对于 `uniswap/router`：

- V1 discovery 阶段重复 quote
- execution 阶段再次 quote
- `quote_id` 同样没有复用

### 5.3 对 `amm/clmm` 也有类似现象，但没有 router 那么重

`amm` 与 `clmm` 的 `executeSwap` 里也会再次取 quote：

- `gateway/src/connectors/uniswap/amm-routes/executeSwap.ts`
- `gateway/src/connectors/uniswap/clmm-routes/executeSwap.ts`

所以严格说，这个“执行前重新 quote”的模式不只存在于 `router`。

但 `router` 更重，因为：

- 它的 quote 本来就更贵
- AlphaRouter 路由计算更复杂

因此同样的问题在 `router` 上表现得最明显。

---

## 6. `quote_id` 不复用：是设计、故意，还是 bug

本次分析的判断是：

**它更像一个架构缺口 / 历史欠账，不像故意设计，也不完全像单点 bug。**

### 6.1 为什么说不是“故意设计”

因为下层能力其实已经存在：

- `gateway_swap.py` 已支持 `execute_quote`
- `gateway swap` 命令已经把 `quote_id` 接通

对应位置：

- `hummingbot/client/command/gateway_swap_command.py`

也就是说，系统下层并没有拒绝这条能力。

### 6.2 为什么又不能简单说是“单一 bug”

因为这不是某一行代码写错，而是中间多层接口都没有保留结构化 quote 元数据：

- connector 层把 quote 压缩成 `price`
- strategy 层只保留 `quote_price / order_price`
- executor config 也没有 `quote_id`
- 下单接口又只暴露 `amount + price`

所以它不是“一处 typo”，而是：

`新能力已经出现在下层，但套利策略栈还停留在旧抽象里`

### 6.3 最准确的说法

最准确的定义是：

`这是一个历史演进造成的 integration gap。`

---

## 7. GitHub 上有没有人公开讨论这个问题

截至本次分析，我没有找到一个公开而明确的 issue，专门在讨论：

`amm_arb / ArbitrageExecutor 没有复用 router quote_id，导致 execution 前重复 quote`

### 7.1 找到的是什么

找到的主要是这些相关但不完全等价的线索：

1. `gateway swap` 命令相关开发历史很多  
   说明维护者在推进 quote + execute 这条能力：

   - `feat: add gateway swap commands with quote and execute functionality`
   - `feat: simplify gateway swap command into unified quote + execute flow`
   - `feat: Add gateway swap command and improve completers`

2. `amm_arb` 自己的修复历史里，主要集中在：
   - side awaiting
   - sync price request
   - gas variables
   - pool id

3. 本地 git 历史还能看到一个不太乐观的信号：
   - `0b9517683 (test) Removes amm_arb from coverage checks as its tests are not ran`

### 7.2 没找到什么

没找到公开而清晰的材料表明：

- 有人系统性地指出过 `quote_id` 在 gateway swap command 已可用，但套利策略没接上
- 也没找到一个明确的公开 issue 持续追这个问题

因此，这件事在公开层面看起来更像：

- 不是完全没人碰到
- 但没有形成被集中跟踪和修复的议题

---

## 8. 为什么这么久都没有修

本次分析给出的判断是 4 个原因叠加。

### 8.1 `amm_arb` 是老策略，早于这套 quote lifecycle

V1 `amm_arb` 的设计时点早于后来的：

- `router`
- `quoteId`
- `executeQuote`

所以它天然沿用了更老的接口模型：

- 发现阶段只拿价格
- 执行阶段重新发单

### 8.2 真正修它需要跨多层改接口，不是一个小 patch

要真修，不是只改一个文件，而是至少要动：

- `GatewaySwap`
- strategy discovery
- executor config
- executor place order path

这比普通 bug fix 重很多。

### 8.3 维护重心更像放在 Gateway 命令和 V2 基础设施

从本地 git 历史看，后续演进更多集中在：

- Gateway swap command
- V2 executor 框架
- connector 和 gateway 稳定性

而不是把老的 `amm_arb` 深度重构成 aggregator-friendly 版本。

### 8.4 这块测试覆盖和关注度都不高

本地历史里直接能看到：

- `amm_arb` 曾经被移出 coverage 检查

这说明它不是一块长期被高强度守护的路径。

---

## 9. 当前最值得修的两个点

如果目标是显著降低当前 V2 `cex-dex` 套利的总延迟，优先级应当是：

### 9.1 第一优先：`quote_id reuse`

目标：

- discovery 阶段拿到的 `quote_id`
- 一路传到 executor
- 下单时直接走 `execute_quote`

收益：

- 去掉 execution 前的重复 quote
- 对 `router` 路径收益最大

### 9.2 第二优先：immediate executor creation

目标：

- 不再等下一拍 tick 再消费 `_pending_create_action`
- 发现机会后立刻 create executor

收益：

- 稳定拿回约 `1s`

---

## 10. 最终判断

这次分析的最终判断可以压缩成一句话：

`当前 router 套利路径的高延迟，核心不是 router 本身，而是策略编排和 quote 生命周期没有打通。`

更具体地说：

1. V2 现在固定多出的约 `1s`，来自 `_pending_create_action -> 下一拍 tick`
2. 更大的额外延迟，来自 execution 前重复 quote
3. 这个问题在 V1 `amm_arb` 的 `router` 路径里也基本存在
4. `quote_id` 没复用，更像历史欠账，不像故意设计
5. 公开资料里没有看到它被长期集中追踪，所以也就一直没被系统性修掉

---

## 11. 后续改造建议

建议按下面顺序推进：

1. 先修 `immediate executor creation`
2. 再修 `quote_id reuse`
3. 然后补结构化指标，验证：
   - `trigger -> executor`
   - `executor -> first leg`
   - `first leg -> second leg`
   - `quote_reuse` 是否真的降低了 wall-clock

如果这两步修完，再继续做：

- custom recovery executor
- event-driven DEX trigger
- 更完整的 multi-venue inventory control

才是合理顺序。

