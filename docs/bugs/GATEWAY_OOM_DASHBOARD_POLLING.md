# BUG-001: Gateway 实例因 Dashboard 高频轮询导致 OOM 崩溃

**日期：** 2026-03-02
**严重程度：** Critical
**状态：** 已修复
**影响范围：** 所有配置了 `GATEWAY_URL` 的 Dashboard + Gateway 部署

---

## 现象

Gateway 所在的 Lightsail 实例（`small_3_0`, 2GB RAM, 2 CPU）在启动后 5-7 分钟内变得完全不可达：

- SSH 连接超时（公网 IP 和 Tailscale IP 均无法连接）
- Load average 飙升至 **30+**（2 核 CPU 机器正常值应 < 2）
- 实例需要通过 AWS API 强制重启，且重启后几分钟内再次崩溃
- 形成「启动 → 崩溃 → 重启 → 再崩溃」的死循环

同样配置的 bot 节点和 control 节点完全正常（load < 1.5）。

## 误导性假设

最初怀疑是 Gateway 实例的硬件配置不足（nano 规格）。但实际检查发现三台实例配置完全相同：

```
gateway:  small_3_0, 2GB RAM, 2 CPU, 60GB disk
control:  small_3_0, 2GB RAM, 2 CPU, 60GB disk
bot:      small_3_0, 2GB RAM, 2 CPU, 60GB disk
```

硬件不是问题。

## 根本原因

`hummingbot-api`（Dashboard 后端）内置了两个针对 Gateway 的轮询循环，当 `.env` 中配置了 `GATEWAY_URL` 后自动激活：

### 轮询循环 1：`GatewayHttpClient._monitor_loop()`

位置：`hummingbot/core/gateway/gateway_http_client.py`

```python
POLL_INTERVAL = 2.0   # 每 2 秒执行一次
POLL_TIMEOUT = 1.0
```

每 2 秒执行：
1. `ping_gateway()` — 检查 Gateway 是否在线
2. `get_connectors()` — 获取所有 DEX 连接器（8 个：jupiter, meteora, raydium, uniswap, 0x, pancakeswap, pancakeswap-sol, orca）
3. `get_chains()` — 获取所有区块链网络（ethereum 9 个网络 + solana 2 个网络）
4. `get_namespaces()` — 获取 23 个配置命名空间
5. `get_all_configurations()` — 获取全部配置

**每分钟约 30 次请求。**

### 轮询循环 2：`GatewayTransactionPoller`

位置：`/hummingbot-api/services/gateway_transaction_poller.py`

```python
poll_interval: int = 10        # 每 10 秒查交易状态
position_poll_interval: int = 300  # 每 5 分钟扫描钱包仓位
```

每 5 分钟的仓位扫描触发：
1. `get_all_wallet_addresses()` — 获取所有钱包地址
2. 对每个钱包在每条链上调用 `clmm_positions_owned()` — 查询 CLMM 仓位
3. 其中 Meteora 初始化 → Solana RPC 调用 → 使用免费公共 RPC（无 API Key）→ 触发 429 限频 → 重试堆积

### 崩溃链条

```
Dashboard 每 2 秒轮询 Gateway
    → Gateway Node.js 处理请求（8 连接器 × 11 网络）
    → 每 5 分钟触发 Solana RPC 仓位扫描
    → 免费 Solana RPC 429 限频 → 重试
    → Node.js 内存持续增长（无 --max-old-space-size 限制）
    → 无 swap（2GB RAM 无缓冲）
    → 无 Docker 容器内存限制
    → 吃光 2GB 系统内存
    → OOM killer 或 V8 GC 疯狂抖动
    → load 飙到 30+ → SSH/网络全部不可用
    → 重启后 Docker restart policy (unless-stopped) 自动拉起 Gateway
    → Dashboard 立即重新连接 → 循环重复
```

### 加剧因素

| 因素 | 详情 |
|---|---|
| `DEV=true` | Gateway 以开发模式运行（HTTP 无证书），便于 Dashboard 连接 |
| 无 swap | 2GB RAM 无任何缓冲，内存满即 OOM |
| 无容器内存限制 | `HostConfig.Memory: 0`，Node.js 可吃光全部系统 RAM |
| 无 Node.js 堆限制 | 未设置 `--max-old-space-size`，V8 默认使用可用内存的 ~75% |
| `bigint-buffer` 纯 JS 回退 | 原生绑定编译失败，大整数运算效率低 |
| 8 个 DEX 连接器全部加载 | 实际只需 uniswap/base，但加载了所有连接器 |
| Solana 无 API Key | 使用免费公共 RPC `api.mainnet-beta.solana.com`，严格限频 |

## 排查过程

### 1. 确认硬件配置

```bash
aws lightsail get-instance --instance-name hb-e2e-gateway-1772431298 \
  --query 'instance.{bundle:bundleId,ram:hardware.ramSizeInGb,cpus:hardware.cpuCount}'
# 结果: small_3_0, 2GB, 2 CPU — 与其他节点相同
```

### 2. 捕获异常状态

在 SSH 连接成功的短暂窗口中捕获到：

```
load average: 29.99, 33.88, 31.68
```

2 核 CPU 机器 load 30+，意味着约 30 个进程在等待 CPU。

### 3. 重启后对比

重启后 1 分钟内 Gateway 正常：
- load: 1.77 → 0.09
- 内存: 264MB / 1910MB
- Gateway 容器: 37MB

但 5-7 分钟后又变得不可达。

### 4. 隔离测试

停止 Gateway 容器后系统立即恢复正常。将 Gateway 改为 `DEV=false`（HTTPS 模式）后，Dashboard 无法连接（HTTP vs HTTPS），Gateway 20 分钟完全稳定：

```
load: 0.03, Gateway: 210MB, RestartCount: 0
```

**证实 Dashboard 轮询是唯一原因。**

### 5. 连接溯源

```bash
sudo ss -tnp | grep 15888
# ESTAB 100.93.71.65:15888 ← 100.90.164.54 (control node / Dashboard)
# ESTAB 100.93.71.65:15888 ← 100.112.118.126 (bot node)
```

### 6. 代码确认

在 `hummingbot-api` 容器内找到轮询代码：

```
/hummingbot/core/gateway/gateway_http_client.py:30 → POLL_INTERVAL = 2.0
/hummingbot-api/services/gateway_transaction_poller.py:41 → poll_interval = 10
/hummingbot-api/services/gateway_transaction_poller.py:42 → position_poll_interval = 300
```

### 7. 安全排除

确认非外部攻击：
- Lightsail 防火墙仅开放 22, 6677, 51820 — API 端口 8000 未对外
- 所有 Gateway 连接来源均为 Tailscale 内网 IP
- hummingbot-api 日志仅有 Prometheus 的 `/metrics` 请求

## 修复方案

### 已实施修复（E2E 测试环境）

#### 1. 添加 1GB swap

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
```

#### 2. Gateway 容器添加资源限制

```bash
sudo docker run -d \
  --name hummingbot-gateway \
  --network host \
  --restart unless-stopped \
  --memory 1200m \
  --memory-swap 1800m \
  -e GATEWAY_PASSPHRASE="<passphrase>" \
  -e DEV=true \
  -e NODE_OPTIONS="--max-old-space-size=768" \
  -v /home/ubuntu/gateway/conf:/home/gateway/conf \
  -v /home/ubuntu/gateway/certs:/home/gateway/certs \
  -v /home/ubuntu/gateway/logs:/home/gateway/logs \
  firenirva/gateway:latest
```

#### 3. 清除 Dashboard 的 GATEWAY_URL

```bash
# 在 control node 上
cd /home/ubuntu/hummingbot-api
cp .env .env.bak
sed -i "s|^GATEWAY_URL=.*|GATEWAY_URL=|" .env
sudo docker compose up -d hummingbot-api
```

### 修复效果

| 指标 | 修复前 | 修复后 |
|---|---|---|
| Load average | 30+ | 0.00 - 0.16 |
| Gateway 内存 | 持续增长 → OOM | 稳定 204MB |
| SSH 可达性 | 5-7 分钟后不可达 | 持续可达 |
| RestartCount | 持续累加 | 0（20 分钟+） |
| 系统稳定性 | 死循环崩溃 | 完全稳定 |

## 生产环境建议

### 方案 A：Dashboard 不连 Gateway（推荐，适用于 2GB 实例）

Dashboard 不配 `GATEWAY_URL`，仍可管理 bot（启停、策略配置、交易记录、CEX 余额），仅缺少 DEX 钱包余额和仓位显示。

### 方案 B：Dashboard 连 Gateway（需升级实例）

Gateway 实例至少 **4GB RAM**（`medium` 规格），并保留 swap + 容器内存限制作为安全网。

### 方案 C：分离部署

Bot + Gateway 同机（无 Dashboard 轮询，稳定运行），Dashboard 单独部署。

## 相关文件

- Gateway 容器配置：`/home/ubuntu/gateway/conf/`
- Dashboard 环境变量：`/home/ubuntu/hummingbot-api/.env`
- Docker Compose：`/home/ubuntu/hummingbot-api/docker-compose.yml`
- 轮询代码：`hummingbot/core/gateway/gateway_http_client.py` (POLL_INTERVAL)
- 仓位扫描代码：`/hummingbot-api/services/gateway_transaction_poller.py`
