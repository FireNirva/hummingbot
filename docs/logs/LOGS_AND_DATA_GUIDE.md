# 📊 Hummingbot 日志和数据完全指南

**创建日期**: 2025-10-29  
**适用版本**: Hummingbot V1 & V2 策略

---

## 📁 文件位置总览

```
hummingbot/
├── logs/                           # 日志文件目录
│   ├── logs_[策略配置名].log      # 策略运行日志
│   ├── errors.log                 # 错误日志
│   └── logs_hummingbot.log        # Hummingbot 主程序日志
│
└── data/                           # SQLite 数据库目录
    └── [策略配置名].sqlite        # 交易数据库
```

---

## 📝 一、日志文件 (logs/)

### 1.1 日志文件命名规则

```bash
logs_[策略配置文件名].log

示例：
- 配置文件：conf_amm_arb_IRON.yml
- 日志文件：logs_conf_amm_arb_IRON.log
```

### 1.2 日志文件内容

#### ✅ 包含的信息：

```
1. 策略启动/停止事件
2. 市场连接状态
3. 套利机会发现
4. 订单创建/成交/完成事件
5. 余额不足警告
6. 网络错误
7. 盈利率计算
8. 所有策略日志输出
```

#### 📄 日志示例（真实数据）：

```log
2025-10-25 20:06:30,378 - hummingbot.strategy.amm_arb.amm_arb - INFO - 
Found arbitrage opportunity!: 
First Side - Connector: uniswap/amm  Side: buy  
Quote Price: 0.0000653  Amount: 500.00  
Extra Fees: [TokenAmount(token='ETH', amount=Decimal('0.0000032'))]
Second Side - Connector: gate_io  Side: sell  
Quote Price: 0.2699  Amount: 500.00

2025-10-25 20:06:36,664 - hummingbot.strategy.amm_arb.amm_arb - INFO - 
Buy order completed on uniswap/amm: buy-IRON-WETH-1761422790379569
txHash: 0x2717096164b9b00355f2f07d818b57b9797797b90c69537184ac9c344b3dfc67

2025-10-25 20:06:36,883 - hummingbot.strategy.amm_arb.amm_arb - INFO - 
Sell order completed on gate_io: t-HBOTSINUT64201341845239cdf1c
```

### 1.3 日志轮转机制

根据 `conf/hummingbot_logs.yml` 配置：

```yaml
file_handler:
  class: logging.handlers.TimedRotatingFileHandler
  filename: logs/logs_$STRATEGY_FILE_PATH.log
  when: "D"          # 按天轮转
  interval: 1        # 每 1 天
  backupCount: 7     # 保留 7 天
```

**结果**：
```
logs_conf_amm_arb_IRON.log              # 当前日志
logs_conf_amm_arb_IRON.log.2025-10-22   # 旧日志
logs_conf_amm_arb_IRON.log.2025-10-23
...
```

### 1.4 查看日志的方法

#### 方法 1：实时监控（推荐）

```bash
# 实时查看最新日志
tail -f logs/logs_conf_amm_arb_IRON.log

# 实时查看，过滤关键词
tail -f logs/logs_conf_amm_arb_IRON.log | grep "arbitrage opportunity"
tail -f logs/logs_conf_amm_arb_IRON.log | grep "completed"

# 实时查看，只看错误
tail -f logs/errors.log
```

#### 方法 2：查看历史日志

```bash
# 查看最近 100 行
tail -n 100 logs/logs_conf_amm_arb_IRON.log

# 查看完整文件
cat logs/logs_conf_amm_arb_IRON.log

# 查看特定时间段
grep "2025-10-25 20:06" logs/logs_conf_amm_arb_IRON.log
```

#### 方法 3：使用 Docker 日志

```bash
# 如果在 Docker 中运行
docker logs hummingbot

# 实时查看
docker logs -f hummingbot

# 查看最近 100 行
docker logs --tail 100 hummingbot
```

### 1.5 日志级别

```python
DEBUG    # 详细调试信息（默认不输出到控制台）
INFO     # 一般信息（策略运行状态）
WARNING  # 警告（余额不足、网络问题）
ERROR    # 错误（订单失败、连接断开）
```

---

## 🗄️ 二、交易数据库 (data/)

### 2.1 数据库文件

```bash
data/[策略配置文件名].sqlite

示例：
- 配置文件：conf_amm_arb_IRON.yml
- 数据库：data/conf_amm_arb_IRON.sqlite
```

### 2.2 数据库表结构

#### 核心表列表：

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| **TradeFill** | 🔥 交易成交记录 | order_id, price, amount, trade_fee |
| **Order** | 订单记录 | id, status, order_type, creation_timestamp |
| **OrderStatus** | 订单状态历史 | order_id, status, timestamp |
| **MarketData** | 市场数据快照 | mid_price, best_bid, best_ask |
| **Executors** | 执行器记录（V2 策略）| executor_id, status, config |
| **Controllers** | 控制器记录（V2 策略）| controller_id, status |

#### 🔥 **TradeFill** 表结构（最重要）

```sql
CREATE TABLE IF NOT EXISTS "TradeFill" (
    config_file_path TEXT NOT NULL,    -- 配置文件名
    strategy TEXT NOT NULL,            -- 策略名称
    market TEXT NOT NULL,              -- 市场（gate_io/uniswap）
    symbol TEXT NOT NULL,              -- 交易对（IRON-USDT）
    base_asset TEXT NOT NULL,          -- 基础资产（IRON）
    quote_asset TEXT NOT NULL,         -- 报价资产（USDT/WETH）
    timestamp BIGINT NOT NULL,         -- 时间戳（毫秒）
    order_id TEXT NOT NULL,            -- 订单 ID
    trade_type TEXT NOT NULL,          -- BUY/SELL
    order_type TEXT NOT NULL,          -- MARKET/LIMIT/AMM_SWAP
    price BIGINT NOT NULL,             -- 成交价格（×10^6）
    amount BIGINT NOT NULL,            -- 成交数量（×10^6）
    leverage INTEGER NOT NULL,         -- 杠杆倍数
    trade_fee JSON NOT NULL,           -- 手续费（JSON）
    trade_fee_in_quote BIGINT,         -- 手续费（报价资产）
    exchange_trade_id TEXT NOT NULL,   -- 交易所交易 ID
    position TEXT,                     -- 持仓类型
    PRIMARY KEY (market, order_id, exchange_trade_id)
);
```

**重要**：价格和数量字段是整数（乘以 10^6 或 10^8），需要除以相应倍数才是真实值。

### 2.3 查询交易历史

#### 🔍 基本查询

##### 1. 查看所有交易

```bash
cd /Users/alice/Dropbox/投资/量化交易/hummingbot/data

sqlite3 conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(timestamp/1000, 'unixepoch') as time,
    market,
    trade_type,
    order_type,
    ROUND(price/1000000.0, 6) as price,
    ROUND(amount/1000000.0, 2) as amount,
    ROUND(trade_fee_in_quote/100000.0, 4) as fee
  FROM TradeFill
  ORDER BY timestamp DESC
  LIMIT 20;
"
```

##### 2. 按时间段查询

```bash
sqlite3 conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(timestamp/1000, 'unixepoch') as time,
    trade_type,
    ROUND(price/1000000.0, 6) as price,
    ROUND(amount/1000000.0, 2) as amount
  FROM TradeFill
  WHERE timestamp >= strftime('%s', '2025-10-25') * 1000
    AND timestamp < strftime('%s', '2025-10-26') * 1000
  ORDER BY timestamp;
"
```

##### 3. 按市场统计

```bash
sqlite3 conf_amm_arb_IRON.sqlite "
  SELECT 
    market,
    trade_type,
    COUNT(*) as trade_count,
    ROUND(SUM(amount)/1000000.0, 2) as total_amount,
    ROUND(SUM(trade_fee_in_quote)/100000.0, 4) as total_fee
  FROM TradeFill
  GROUP BY market, trade_type;
"
```

##### 4. 查看最近的套利交易

```bash
sqlite3 conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(timestamp/1000, 'unixepoch') as time,
    order_id,
    market,
    trade_type,
    ROUND(price/1000000.0, 6) as price,
    ROUND(amount/1000000.0, 2) as amount
  FROM TradeFill
  ORDER BY timestamp DESC
  LIMIT 10;
"
```

#### 💰 收益计算查询

##### 1. 计算单次套利收益

```sql
-- 找到配对的买卖订单
SELECT 
    datetime(buy.timestamp/1000, 'unixepoch') as time,
    -- 买入成本
    ROUND((buy.price * buy.amount) / 1000000000000.0, 4) as buy_cost,
    -- 卖出收入
    ROUND((sell.price * sell.amount) / 1000000000000.0, 4) as sell_revenue,
    -- 手续费
    ROUND((buy.trade_fee_in_quote + sell.trade_fee_in_quote) / 100000.0, 4) as total_fee,
    -- 净利润（需要考虑汇率转换）
    ROUND(
        (sell.price * sell.amount) / 1000000000000.0 - 
        (buy.price * buy.amount) / 1000000000000.0 - 
        (buy.trade_fee_in_quote + sell.trade_fee_in_quote) / 100000.0,
        4
    ) as profit
FROM TradeFill buy
INNER JOIN TradeFill sell 
    ON buy.timestamp = sell.timestamp
    AND buy.trade_type = 'BUY' 
    AND sell.trade_type = 'SELL'
ORDER BY buy.timestamp DESC
LIMIT 10;
```

##### 2. 每日收益统计

```sql
SELECT 
    date(timestamp/1000, 'unixepoch') as date,
    COUNT(DISTINCT timestamp) / 2 as arb_count,  -- 除以2因为每次套利2笔交易
    ROUND(SUM(CASE WHEN trade_type = 'BUY' THEN amount ELSE 0 END) / 1000000.0, 2) as total_buy_amount,
    ROUND(SUM(CASE WHEN trade_type = 'SELL' THEN amount ELSE 0 END) / 1000000.0, 2) as total_sell_amount,
    ROUND(SUM(trade_fee_in_quote) / 100000.0, 4) as total_fee
FROM TradeFill
GROUP BY date(timestamp/1000, 'unixepoch')
ORDER BY date DESC;
```

### 2.4 使用 GUI 工具查看数据库

#### 推荐工具：

##### 1. **DB Browser for SQLite** (免费，推荐)

```bash
# macOS 安装
brew install --cask db-browser-for-sqlite

# 打开数据库
open -a "DB Browser for SQLite" data/conf_amm_arb_IRON.sqlite
```

- 官网：https://sqlitebrowser.org/
- 功能：可视化浏览、查询、导出数据

##### 2. **DBeaver** (免费，功能强大)

```bash
# macOS 安装
brew install --cask dbeaver-community
```

- 官网：https://dbeaver.io/
- 功能：专业数据库管理工具

##### 3. **TablePlus** (付费，界面美观)

```bash
# macOS 安装
brew install --cask tableplus
```

- 官网：https://tableplus.com/
- 功能：现代化数据库 GUI

### 2.5 导出数据

#### 导出为 CSV

```bash
sqlite3 -header -csv conf_amm_arb_IRON.sqlite "
  SELECT * FROM TradeFill
" > trades_export.csv
```

#### 导出为 JSON

```bash
sqlite3 conf_amm_arb_IRON.sqlite "
  SELECT json_group_array(json_object(
    'timestamp', timestamp,
    'market', market,
    'trade_type', trade_type,
    'price', price,
    'amount', amount
  )) FROM TradeFill
" > trades_export.json
```

---

## 🔍 三、在 Hummingbot CLI 中查看历史

### 3.1 内置命令

在 Hummingbot CLI 中：

```bash
# 查看当前策略状态
status

# 查看交易历史
history

# 查看余额
balance

# 查看未完成订单
open_orders

# 查看性能指标
performance
```

### 3.2 `history` 命令详解

```bash
# 基本用法
history

# 查看最近 N 笔交易
history --rows 50

# 按时间段查询
history --days 7

# 按交易对过滤
history --market IRON-USDT

# 导出历史
history --export
```

### 3.3 `performance` 命令

显示收益统计：

```
Total Trades: 150
Win Rate: 85%
Total Volume: 75000 IRON
Total Fees: 45.2 USDT
Net Profit: 1250 USDT
ROI: 12.5%
```

---

## 📊 四、收益分析工具

### 4.1 使用 Python 脚本分析

创建 `analyze_trades.py`：

```python
import sqlite3
import pandas as pd
from datetime import datetime

# 连接数据库
db_path = 'data/conf_amm_arb_IRON.sqlite'
conn = sqlite3.connect(db_path)

# 读取交易数据
query = """
SELECT 
    datetime(timestamp/1000, 'unixepoch') as time,
    market,
    trade_type,
    price/1000000.0 as price,
    amount/1000000.0 as amount,
    trade_fee_in_quote/100000.0 as fee
FROM TradeFill
ORDER BY timestamp
"""

df = pd.read_csv(conn, query)

# 计算基本统计
print("=== 交易统计 ===")
print(f"总交易次数: {len(df)}")
print(f"总买入: {df[df['trade_type'] == 'BUY']['amount'].sum():.2f}")
print(f"总卖出: {df[df['trade_type'] == 'SELL']['amount'].sum():.2f}")
print(f"总手续费: {df['fee'].sum():.4f}")

# 按日期统计
df['date'] = pd.to_datetime(df['time']).dt.date
daily = df.groupby('date').agg({
    'amount': 'sum',
    'fee': 'sum'
})
print("\n=== 每日统计 ===")
print(daily)

conn.close()
```

运行：

```bash
cd /Users/alice/Dropbox/投资/量化交易/hummingbot
python analyze_trades.py
```

### 4.2 使用 Jupyter Notebook 分析

```bash
# 安装 Jupyter
pip install jupyter pandas matplotlib

# 启动
jupyter notebook

# 创建新笔记本，导入数据分析
```

---

## 🔔 五、实时监控方案

### 5.1 使用 tmux 多窗口监控

```bash
# 启动 tmux 会话
tmux new -s hummingbot-monitor

# 分屏（Ctrl+B 然后按 %）
# 左窗口：实时日志
tail -f logs/logs_conf_amm_arb_IRON.log

# 右窗口：数据库查询（每 5 秒刷新）
watch -n 5 'sqlite3 data/conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(timestamp/1000, \"unixepoch\") as time,
    trade_type,
    ROUND(price/1000000.0, 6) as price
  FROM TradeFill
  ORDER BY timestamp DESC
  LIMIT 5
"'
```

### 5.2 使用脚本自动监控

创建 `monitor_bot.sh`：

```bash
#!/bin/bash

STRATEGY="conf_amm_arb_IRON"
LOG_FILE="logs/logs_${STRATEGY}.log"
DB_FILE="data/${STRATEGY}.sqlite"

while true; do
    clear
    echo "=========================================="
    echo "  Hummingbot 实时监控 - $(date)"
    echo "=========================================="
    
    echo "\n📊 最近 5 笔交易:"
    sqlite3 "$DB_FILE" "
      SELECT 
        datetime(timestamp/1000, 'unixepoch') as time,
        trade_type,
        ROUND(price/1000000.0, 6) as price,
        ROUND(amount/1000000.0, 2) as amount
      FROM TradeFill
      ORDER BY timestamp DESC
      LIMIT 5
    "
    
    echo "\n📝 最新日志（最近 5 行）:"
    tail -n 5 "$LOG_FILE"
    
    sleep 5
done
```

```bash
chmod +x monitor_bot.sh
./monitor_bot.sh
```

---

## 🎯 六、Docker 环境下的特殊处理

### 6.1 多实例日志管理

如果运行多个 Hummingbot 容器：

```yaml
# docker-compose-multi.yml
services:
  hummingbot1:
    volumes:
      - ./logs-hb1:/home/hummingbot/logs  # 独立日志
      - ./data-hb1:/home/hummingbot/data  # 独立数据

  hummingbot2:
    volumes:
      - ./logs-hb2:/home/hummingbot/logs
      - ./data-hb2:/home/hummingbot/data
```

查看日志：

```bash
# 实例 1
tail -f logs-hb1/logs_conf_amm_arb_GPS.log

# 实例 2
tail -f logs-hb2/logs_conf_amm_arb_IRON.log
```

### 6.2 从容器中复制数据

```bash
# 复制日志到本地
docker cp hummingbot:/home/hummingbot/logs ./logs-backup

# 复制数据库到本地
docker cp hummingbot:/home/hummingbot/data ./data-backup
```

---

## ⚠️ 七、注意事项

### 7.1 数据保护

```bash
# 定期备份数据库
cp data/conf_amm_arb_IRON.sqlite data/backups/conf_amm_arb_IRON_$(date +%Y%m%d).sqlite

# 定期清理旧日志（保留最近 30 天）
find logs/ -name "*.log.*" -mtime +30 -delete
```

### 7.2 磁盘空间管理

```bash
# 查看日志大小
du -sh logs/

# 查看数据库大小
du -sh data/

# 压缩旧日志
gzip logs/logs_*.log.2025-*
```

### 7.3 性能优化

如果数据库很大，创建索引：

```sql
-- 已有的索引（自动创建）
CREATE INDEX tf_market_trading_pair_timestamp_index 
  ON TradeFill (market, symbol, timestamp);

-- 添加自定义索引
CREATE INDEX IF NOT EXISTS idx_tradefill_date 
  ON TradeFill (date(timestamp/1000, 'unixepoch'));
```

---

## 🎓 八、常见查询示例

### 查询 1：查看今天的所有套利机会

```bash
sqlite3 data/conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(timestamp/1000, 'unixepoch', 'localtime') as time,
    market,
    trade_type,
    ROUND(price/1000000.0, 6) as price,
    ROUND(amount/1000000.0, 2) as amount
  FROM TradeFill
  WHERE date(timestamp/1000, 'unixepoch', 'localtime') = date('now', 'localtime')
  ORDER BY timestamp;
"
```

### 查询 2：统计每个市场的交易量

```bash
sqlite3 data/conf_amm_arb_IRON.sqlite "
  SELECT 
    market,
    COUNT(*) as trade_count,
    ROUND(SUM(amount)/1000000.0, 2) as total_volume,
    ROUND(AVG(price)/1000000.0, 6) as avg_price
  FROM TradeFill
  GROUP BY market;
"
```

### 查询 3：查看最高收益的套利

```bash
sqlite3 data/conf_amm_arb_IRON.sqlite "
  SELECT 
    datetime(buy.timestamp/1000, 'unixepoch') as time,
    ROUND((sell.price - buy.price) * sell.amount / 1000000000000.0, 4) as profit
  FROM TradeFill buy
  JOIN TradeFill sell 
    ON buy.timestamp = sell.timestamp
    AND buy.trade_type = 'BUY'
    AND sell.trade_type = 'SELL'
  ORDER BY profit DESC
  LIMIT 10;
"
```

---

## 📚 总结

### 快速参考

| 需求 | 位置 | 工具 |
|------|------|------|
| 实时日志 | `logs/logs_[策略名].log` | `tail -f` |
| 交易历史 | `data/[策略名].sqlite` | SQLite查询 |
| 错误信息 | `logs/errors.log` | `tail -f` |
| 收益统计 | 数据库 `TradeFill` 表 | SQL查询 |
| 可视化 | 数据库 | DB Browser/DBeaver |

### 最常用的命令

```bash
# 1. 实时监控日志
tail -f logs/logs_conf_amm_arb_IRON.log | grep "arbitrage"

# 2. 查看最近交易
sqlite3 data/conf_amm_arb_IRON.sqlite "SELECT * FROM TradeFill ORDER BY timestamp DESC LIMIT 10"

# 3. 统计今日收益
sqlite3 data/conf_amm_arb_IRON.sqlite "
  SELECT 
    COUNT(*) / 2 as arb_count,
    ROUND(SUM(trade_fee_in_quote) / 100000.0, 4) as total_fee
  FROM TradeFill
  WHERE date(timestamp/1000, 'unixepoch') = date('now')
"

# 4. 备份数据
cp data/conf_amm_arb_IRON.sqlite backups/backup_$(date +%Y%m%d).sqlite
```

---

**祝你交易顺利！📈**

