# V2 Aggregator Arb 事故复盘：0x 节流、失败重试风暴与 Mac 整机网络崩溃

## 摘要

这次事故不是单一 API 故障，而是一个典型的连锁问题：

1. `0x` 因为长期 `quote >> trade` 开始节流。
2. `v2_cex_dex_aggregator_arb.py` 在失败和空报价场景下没有真正停手。
3. 策略继续在 `heartbeat + stale_snapshot` 路径上高频打外部 quote。
4. `0x`、`Gate`、`Chainstack` 的失败连接一起堆积，最终把这台 Mac 的出站网络也拖死。
5. 结果不是只有 bot 出错，而是整机几乎无法访问外网，只能重启机器恢复。

这次事故的价值不在于“某一家 API 出错了”，而在于暴露了一个更危险的事实：

**外部 aggregator 策略如果只有 budget，没有真正的失败熔断，最终可以把整台机器的网络一起打崩。**

## 事故现象

故障窗口里同时出现了下面几类现象：

- `0x` 返回 `429 Rate limit exceeded`
- `gateway` 对 Base RPC / `Chainstack` 出现 `ECONNREFUSED`
- `hummingbot` 对 `Gate` 出现 `Connection refused` 或 `Temporary failure in name resolution`
- 策略日志仍然继续 heartbeat 刷新 quote
- Mac 本机浏览器、命令行、容器都无法正常访问外网
- 只有重启整台机器后，网络才恢复

这说明问题已经不是“某个 connector 坏了”，而是**整机网络资源或 Docker Desktop 出站链路被失败请求风暴打穿**。

## 关键证据

### 1. 0x 已经明确开始节流

主机直接请求 `0x` 时返回：

- `429 Rate limit exceeded`

这不是推测，而是明确的上游限流。

### 2. 策略真实 quote 数远超预期

`MAGIC` live 策略的 `jsonl` 指标最终显示：

- `expensive_quote_requests = 9167`
- `expensive_quote_success = 814`
- `expensive_quote_failures = 8353`
- `trades_closed = 0`

这意味着：

- 实际外部 quote 非常多
- 大部分 quote 都失败了
- 几乎没有交易闭环

### 3. 预算保护没有真正挡住失败风暴

配置里已经设置了保守限制，例如：

- `aggregator_quote_budget_per_minute: 6`

但最终仍然出现 `9167` 次外部 quote 请求，说明当时的实现存在关键缺陷：

- **只有成功 quote 才进入预算窗口**
- 失败和空报价没有被正确计入 budget/cooldown

这使得策略在失败场景下反而更危险。

### 4. stale snapshot 驱动了持续重试

最后一段 `jsonl` 事件几乎都表现为：

- `trigger_source = heartbeat`
- `trigger_reason = stale_snapshot`
- `quote_refresh.status = empty`

这表示：

- 一旦拿不到有效 DEX quote
- 策略没有进入降级或停机
- 而是继续因为缓存过期反复触发 heartbeat 刷新

### 5. Gate 不是主因，而是连带受害者

日志里 `Gate` 也出现了连接失败，但从根因上看：

- `Gate` 并不是本次事故的发起方
- `0x` 节流和外部失败重试风暴才是主导
- `Gate`、`Chainstack`、DNS 失败是后续整机网络异常的连带后果

## 根因分析

### 根因 1：外部 aggregator 被当成可无限失败重试的数据源

`0x` 不是本地 `uniswap/amm`。

本地 AMM quote 失败通常只是你自己的 RPC 压力问题；  
外部 aggregator quote 失败意味着：

- 上游已经拒绝服务
- 继续打只会更快恶化

之前的策略逻辑没有把这两类失败区别对待。

### 根因 2：budget 只约束成功请求，不约束失败请求

这是这次事故最关键的实现错误之一。

结果就是：

- 成功时 budget 看起来有效
- 一旦开始失败，策略反而可能打得更快

这类问题在外部 API 策略里非常危险，因为最需要限流的往往正是失败时刻。

### 根因 3：缺少真正的熔断器

之前的脚本没有下面这些硬保护：

- `429` 立即停机
- 连续网络错误停机
- 连续空报价停机
- 连续 quote 失败停机

所以策略在“已经明显不该继续请求”的情况下，仍然会继续循环。

### 根因 4：在 macOS + Docker Desktop 上承载失败风暴，容错更差

这次不是严格证明了“Docker Desktop 一定有 bug”，但从现象上看：

- 策略失败风暴
- Docker 容器外联失败
- 主机也失去对外访问能力

这更像是：

- 本机出站连接资源被打爆
- 或 Docker Desktop 网络后端进入异常状态

这也是为什么最后需要重启整机，而不是只重启容器。

## 这次学到的关键经验

### 经验 1：对 aggregator，失败比成功更需要预算控制

不能只在成功 quote 时记预算。  
**失败请求、空报价、超时、429 都必须进入同一套 budget/cooldown。**

### 经验 2：外部 API 策略必须有硬熔断

对 `0x / Kyber / ParaSwap / Odos` 这类外部路由器：

- `429` 必须视为红线
- 连续空报价必须视为红线
- 连续网络错误必须视为红线

不能再指望“过一会就会好”。

### 经验 3：`quote-to-trade ratio` 是实盘风险，不只是商业条款

这次之前 `0x` 已经发过 warning 邮件。  
事实证明，这不只是价格问题，也不只是账户问题，而是**会直接演化成系统稳定性问题**。

### 经验 4：Mac 本机不适合长期承载失控的外部聚合器实验

如果后续还要长期做：

- 高频失败测试
- 外部 aggregator 压测
- 长时间 quote-only 策略

更适合迁到：

- Linux 主机
- 独立 VPS
- 与日常工作网络隔离的机器

## 已做修复

已在 [v2_cex_dex_aggregator_arb.py](/Users/alice/Dropbox/投资/量化交易/hummingbot/scripts/v2_cex_dex_aggregator_arb.py) 中加入熔断器，核心修复包括：

1. **失败请求也计入 budget / cooldown**
2. **出现 `429 / rate limit / throttle` 立即触发熔断**
3. **连续网络错误达到阈值触发熔断**
4. **连续空报价达到阈值触发熔断**
5. **连续 quote 失败达到阈值触发熔断**
6. **熔断后自动停止策略**
7. **结构化事件日志写出 `circuit_breaker_trip`**
8. **metrics summary 输出 breaker 状态与连续失败计数**

这次修复的目标不是“继续跑得更久”，而是：

**一旦再次进入危险状态，优先保护机器和网络，不允许策略继续失控。**

## 推荐运行原则

### 在 0x 上

- 不要继续运行纯 quote-only 策略
- 一旦 `429` 出现，必须停机，不要重试
- 不要把 `aggregator_quote_budget_per_minute` 当成唯一保护

### 在本机上

- 只跑带熔断器的新版本脚本
- 不要并行跑多个外部 aggregator 策略
- 出现 DNS/连接失败时，先停 bot，不要观察性硬跑

### 在架构上

- 外部 aggregator 应优先走“少量高价值 quote”
- 不要再用“无限 heartbeat 刷新 + 空报价重试”的模式
- 如果要继续做长期聚合器套利，建议迁移到 Linux / VPS

## 后续建议

1. 把这次熔断器配置显式写入实际运行的策略配置文件
2. 在 `metrics_summary` 之外，再加一层按分钟的外部请求量监控
3. 若继续使用 `0x`，应准备：
   - custom plan
   - 更低频触发模型
   - 真正可成交的闭环，而不是纯 quote
4. 长期建议把聚合器策略运行环境与日常工作电脑隔离

## 一句话总结

这次事故最重要的经验不是“0x 会限流”，而是：

**外部 aggregator 被限流之后，如果策略没有真正的失败熔断，受害的就不只是策略本身，而可能是整台机器的网络。**
