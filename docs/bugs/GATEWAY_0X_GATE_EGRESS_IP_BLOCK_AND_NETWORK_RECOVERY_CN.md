# Gateway / 0x / Gate 外网访问异常与旧出口 IP 封禁风险记录

## 现象

在 `2026-03-12` 这一轮排查中，`hummingbot` 与 `gateway` 出现了两类表面上相似、但根因不同的问题：

1. `gateway -> Base RPC` 间歇性失败  
   典型报错：
   - `Failed to get balances`
   - `could not detect network`
   - `eth_getBalance ... ECONNREFUSED`

2. `gateway / hummingbot -> 0x / Gate API` 无法建立 HTTPS 连接  
   典型报错：
   - 宿主机 Python: `[Errno 49] Can't assign requested address`
   - 容器内 Node/Python: `ECONNREFUSED`
   - `0x/router` quote 直接失败

## 关键证据

### 1. 旧网络下，0x 与 Gate 同时失败，但 Base RPC 可恢复

- 在旧网络环境下：
  - `https://api.0x.org/` 无法连通
  - `https://api.gateio.ws/...` 无法连通
  - `0x/router` quote 返回 `500`
- 同时：
  - `Chainstack Base RPC` 在切换配置和网络模式后可以恢复

这说明问题不是单一 connector 代码 bug，而是旧出口网络 / IP 路径对多个外部服务同时异常。

### 2. 切换热点后，三层网络全部恢复

切换热点后，重新测试得到：

- 宿主机：
  - `https://api.0x.org/` -> `200`
  - `https://api.gateio.ws/...` -> `200`
  - `https://base-mainnet.core.chainstack.com/...` -> `200`
- `hummingbot` 容器：
  - `http://127.0.0.1:15888/` -> `200`
  - `https://api.0x.org/` -> `200`
  - `https://api.gateio.ws/...` -> `200`
  - `https://base-mainnet.core.chainstack.com/...` -> `200`
- `gateway` 容器：
  - `0x`、`Gate`、`Chainstack` 全部恢复

同时，`0x/router` quote 重新成功：

- `BRETT-USDC`
  - `SELL 100 BRETT` -> `200`
  - `BUY 100 BRETT` -> `200`
- `MAGIC-USDC`
  - `SELL 1000 MAGIC` -> `200`
  - `BUY 1000 MAGIC` -> `200`

## 原因判断

### 判断 1：旧出口 IP / 网络路径存在被拒绝风险

根据现象，更准确的说法是：

- **高度怀疑旧出口 IP 或其网络路径被 0x / Gate 一侧或中间网络设备拒绝**
- 但**没有直接证据**证明这是两家明确返回的正式“账户封禁”或标准 `429` 限流

原因：

- 如果只是常规 API rate limit，更常见的是 `429` / `403` / `401`
- 这次拿到的是建连前失败：
  - `ECONNREFUSED`
  - `[Errno 49] Can't assign requested address`
- 且 `0x` 与 `Gate` 同时出问题，说明更像“旧出口 IP / 路由路径”问题，而不是单个 API key 逻辑问题

因此，这次应归类为：

- **旧出口 IP / 网络路径被拒绝或风控**
- 而不是单纯的代码 bug

### 判断 2：Docker 网络模式也放大了 Gateway 的 Base RPC 问题

除了旧出口 IP 问题外，还存在一个独立问题：

- `hummingbot` 容器原本使用 `network_mode: host`
- `gateway` 容器原本使用默认 bridge 网络

在这台机器上，bridge 网络下的 `gateway` 更容易出现：

- `eth_getBalance`
- `could not detect network`
- `Failed to get balances`

这部分不是 0x / Gate ban，而是容器网络模式问题。

## 官方文档复核

### 0x

0x 官方公开信息明确说明：

- `Swap API` 更偏向“真实交易 use case”
- 如果持续高比例地请求 quote 而不成交，会触发 throttling 风险
- 如果需要大量价格查询，应考虑自定义方案

参考：

- https://0x.org/pricing
- https://help.0x.org/en/articles/8260681-how-to-query-swap-prices-for-many-asset-pairs-without-exceeding-the-rate-limit
- https://0x.org/docs/0x-swap-api/introduction

结论：

- 0x **确实存在**因 quote-to-trade ratio 过高而被限的商业风控
- 这会提高“旧出口 IP / 账号被重点关注”的风险

### Gate

Gate 官方 API 文档公开了按 IP 的访问限制和 WebSocket 连接限制，说明其公共 API 存在 IP 维度的频控和连接管理。

参考：

- https://www.gate.com/docs/developers/apiv4/

结论：

- Gate 并不是“无限制公共行情源”
- 长时间异常访问模式也可能触发 IP 侧限制或上游拒绝

## 本次已落实的修复

### 1. 修复 Gateway 容器网络模式

已修改：

- [docker-compose.yml](/Users/alice/Dropbox/投资/量化交易/hummingbot/docker-compose.yml)

变更：

- `gateway` 改为 `network_mode: host`
- 之后重建了 `gateway` 容器

结果：

- `gateway -> Base RPC` 恢复正常
- `/chains/ethereum/balances` 已实测返回 `200`

### 2. 收紧 0x 相关 V2 live 策略配置

已修改：

- [conf_v2_cex_dex_aggregator_arb_live.yml](/Users/alice/Dropbox/投资/量化交易/hummingbot/conf/scripts/conf_v2_cex_dex_aggregator_arb_live.yml)
- [conf_v2_cex_dex_aggregator_arb_magic_live.yml](/Users/alice/Dropbox/投资/量化交易/hummingbot/conf/scripts/conf_v2_cex_dex_aggregator_arb_magic_live.yml)

调整方向：

- `aggregator_quote_cooldown_sec`: `5 -> 8`
- `aggregator_quote_ttl_sec`: `20 -> 30`
- `aggregator_quote_budget_per_minute`: `12 -> 6`
- `quote_trigger_buffer_pct`: `0.002 -> 0.0015`
- `cex_price_move_trigger_pct`: `0.0015 -> 0.0025`
- `dex_event_poll_interval_sec`: `3 -> 5`
- `dex_event_*_trigger_pct`: 整体上调一档

目的：

- 降低 0x quote-only 流量
- 降低长期运行时触发 0x 风控和外部 IP 关注的概率

## 后续运行建议

1. 尽量使用当前可工作的热点 / 出口 IP 运行 `0x/router`
2. 观察 `quotes / trades_closed / vendor_429_count`
3. 如果再次出现：
   - `api.0x.org` 与 `api.gateio.ws` 同时建连失败
   - 且切换出口 IP 后恢复
   则可以基本确认是出口 IP / 网络路径问题
4. 对 0x 保持更保守的运行方式：
   - 更低的 quote 预算
   - 更长的 cooldown
   - 避免长时间零成交纯扫价

## 当前结论

这次问题不是单一代码 bug，而是两部分叠加：

1. `gateway` 容器网络模式导致的 Base RPC 不稳定  
2. 旧出口 IP / 网络路径对 `0x` 与 `Gate` 的外网访问异常

其中第 1 部分已经通过 `gateway -> host network` 修复。  
第 2 部分在切换热点后恢复，强烈提示旧出口 IP / 路径被拒绝或风控，但不能仅凭当前证据断言为正式账号封禁。
