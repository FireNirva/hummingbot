# CEX-DEX 基础套利策略 - 问题解决指南

## 🔴 当前问题总结

### 1. **余额不足导致执行器快速失败**
```
Gate.io IRON 余额: 49.89
配置的订单量: 50
结果: 执行器立即失败
```

### 2. **timestamp 验证错误**
```
ExecutorInfo.timestamp: None (应该是浮点数)
原因: 执行器快速失败时状态管理异常
```

### 3. **错误日志爆炸**
```
日志文件: 7000+ 行错误
原因: 策略每秒创建新执行器，未正确去重
```

---

## ✅ 已修复的代码问题

### 修复 1: 异步任务去重
```python
# 添加任务追踪
self._evaluation_task = None
self._evaluation_lock = False

# 在 on_tick 中检查
if self._evaluation_task is not None:
    if not self._evaluation_task.done():
        return  # 跳过重复任务
```

### 修复 2: 锁机制
```python
async def _evaluate_and_prepare_action(self):
    if self._evaluation_lock:
        return
    
    self._evaluation_lock = True
    try:
        # ... 评估逻辑 ...
    except Exception as e:
        self.logger().error(f"错误: {e}")
    finally:
        self._evaluation_lock = False  # 确保释放
```

### 修复 3: 完整异常处理
所有异步操作现在都包裹在 try-except-finally 中。

---

## 🚨 立即停止策略的方法

### 方法 1: 在 Hummingbot 中（推荐）

#### 选项 A: 优雅停止
```bash
# 在 Hummingbot 控制台中输入:
stop

# 等待 3-5 秒观察是否停止
```

#### 选项 B: 强制停止
```bash
# 在 Hummingbot 控制台中连续按:
Ctrl+C    # 第一次
# 等待 2 秒
Ctrl+C    # 第二次（强制）
```

#### 选项 C: 强制退出
```bash
# 在 Hummingbot 控制台中输入:
exit --force
```

### 方法 2: Docker 容器（如果使用 Docker）

#### 停止容器
```bash
# 在系统终端中:
cd /Users/alice/Dropbox/投资/量化交易/hummingbot

# 查看运行的容器
docker ps | grep hummingbot

# 停止容器 (使用容器 ID 或名称)
docker stop <container_id>

# 或者强制停止
docker kill <container_id>
```

#### 重启容器
```bash
# 停止并删除
docker-compose down

# 重新启动
docker-compose up -d
```

### 方法 3: 直接杀死进程（最后手段）

#### 查找进程
```bash
# 在系统终端中:
ps aux | grep python | grep hummingbot

# 或者
pgrep -f hummingbot
```

#### 杀死进程
```bash
# 使用 PID (从上面的命令获取)
kill <PID>

# 如果不行，强制杀死
kill -9 <PID>

# 或者一次性杀死所有
pkill -9 -f hummingbot
```

---

## 🔧 重新启动前的检查清单

### 1. 确认配置文件已更新
```bash
cat conf/scripts/conf_cex_dex_basic_arb.yml | grep order_amount
# 应该显示: order_amount: 40
```

### 2. 确认余额充足
登录 Gate.io 检查：
- IRON 余额 ≥ 40（用于卖出方向）
- USDT 余额 ≥ 40 × 价格（用于买入方向，如果需要）

### 3. 清理旧日志（可选）
```bash
# 备份旧日志
cd /Users/alice/Dropbox/投资/量化交易/hummingbot/logs
mv logs_conf_cex_dex_basic_arb.log logs_conf_cex_dex_basic_arb.log.backup

# 或者直接删除
rm logs_conf_cex_dex_basic_arb.log
```

---

## 🚀 正确的重启流程

### 步骤 1: 停止策略
使用上述任一方法停止 Hummingbot。

### 步骤 2: 验证代码更新
```bash
cd /Users/alice/Dropbox/投资/量化交易/hummingbot

# 检查文件最后修改时间
ls -lh scripts/cex_dex_basic_arb.py
# 应该显示最新的修改时间

# 检查文件行数（应该约 860 行）
wc -l scripts/cex_dex_basic_arb.py
```

### 步骤 3: 启动 Hummingbot
```bash
# 如果使用 Docker
docker-compose up -d
docker attach hummingbot

# 如果直接运行
./start
# 或
python bin/hummingbot.py
```

### 步骤 4: 加载策略
```bash
# 在 Hummingbot 控制台中:
start --script conf_cex_dex_basic_arb.yml
```

### 步骤 5: 观察日志
观察以下正常日志（应该每秒最多出现一次）：
```
✅ 正常日志:
- "初始化 CEX-DEX 基础套利策略..."
- "等待连接器就绪..."
- "CEX买入+DEX卖出方向: 净收益率 X.XX%"
- "DEX买入+CEX卖出方向: 净收益率 X.XX%"

❌ 异常日志（不应该出现）:
- "Error updating controller reports"
- "ExecutorInfo timestamp validation error"
- "Insufficient balance" (每秒重复)
```

---

## 📊 预期行为（修复后）

### 正常运行状态

#### 1. 无套利机会时
```
2025-10-26 XX:XX:XX - INFO - CEX买入+DEX卖出方向: 净收益率 -2.50%
2025-10-26 XX:XX:XX - INFO - DEX买入+CEX卖出方向: 净收益率 0.80%
2025-10-26 XX:XX:XX - INFO - 最佳方向 (DEX买入+CEX卖出) 净收益率 0.80% 低于阈值 1.50%，暂不执行套利
```

#### 2. 有套利机会但余额不足时
```
2025-10-26 XX:XX:XX - INFO - CEX买入+DEX卖出方向: 净收益率 -1.00%
2025-10-26 XX:XX:XX - INFO - DEX买入+CEX卖出方向: 净收益率 2.50%
2025-10-26 XX:XX:XX - INFO - 选择方向: DEX买入+CEX卖出，订单量: 40 IRON
2025-10-26 XX:XX:XX - INFO - 预估净收益率: 2.50%，满足阈值 1.50%
2025-10-26 XX:XX:XX - ERROR - CEX (gate_io) IRON 余额不足：需要 40，可用 35
```
**重要**: 这个错误应该只出现一次，然后策略会跳过本次套利。

#### 3. 成功触发套利时
```
2025-10-26 XX:XX:XX - INFO - CEX买入+DEX卖出方向: 净收益率 -1.00%
2025-10-26 XX:XX:XX - INFO - DEX买入+CEX卖出方向: 净收益率 2.50%
2025-10-26 XX:XX:XX - INFO - 选择方向: DEX买入+CEX卖出，订单量: 40 IRON
2025-10-26 XX:XX:XX - INFO - 预估净收益率: 2.50%，满足阈值 1.50%
2025-10-26 XX:XX:XX - INFO - 已准备套利执行器，等待执行...
2025-10-26 XX:XX:XX - INFO - Arbitrage executor created
2025-10-26 XX:XX:XX - INFO - Placing buy order on uniswap/amm...
2025-10-26 XX:XX:XX - INFO - Placing sell order on gate_io...
```

### 错误情况

#### ❌ 不应该出现的错误（如果还出现，说明有问题）
```
1. timestamp 验证错误 (每秒重复)
   → 如果仍然出现，说明代码未更新成功

2. 执行器不断创建 (日志疯狂增长)
   → 如果仍然出现，说明异步任务去重未生效

3. 余额不足每秒重复
   → 如果仍然出现，说明 _has_active_executor 逻辑有问题
```

---

## 🔍 故障排查

### 问题 1: 策略仍然无法停止

#### 诊断
```bash
# 检查日志最后 50 行
tail -50 logs/logs_conf_cex_dex_basic_arb.log
```

#### 解决
```bash
# 强制杀死进程
pkill -9 -f hummingbot

# 或者重启 Docker 容器
docker-compose restart
```

### 问题 2: 修复后仍出现 timestamp 错误

#### 诊断
```bash
# 确认代码是否更新
grep "_evaluation_lock" scripts/cex_dex_basic_arb.py
# 应该能找到多处匹配
```

#### 解决
```bash
# 如果代码未更新，重新应用修改
# (通过 Cursor 重新打开文件并保存)

# 确保重启 Hummingbot 以加载新代码
```

### 问题 3: 订单量已改为 40 但仍提示 50

#### 诊断
```bash
# 确认配置文件
cat conf/scripts/conf_cex_dex_basic_arb.yml | grep order_amount
```

#### 解决
```bash
# 如果显示 50，手动修改
vim conf/scripts/conf_cex_dex_basic_arb.yml
# 或使用其他编辑器

# 修改后重启策略
```

---

## 📝 后续优化建议

### 1. 更智能的余额检查
```python
# 在 on_tick 开始时就检查余额
if not self._check_basic_balance():
    return  # 跳过整个评估流程
```

### 2. 添加执行器数量监控
```python
active_count = len(self.filter_executors(
    executors=self.get_all_executors(),
    filter_func=lambda e: e.is_active
))
self.logger().info(f"活跃执行器数量: {active_count}/{self.config.max_concurrent_executors}")
```

### 3. 添加日志节流
```python
# 限制 "暂无套利机会" 的日志频率
if now - self._last_no_opportunity_log > 30:  # 30秒一次
    self.logger().info("暂无套利机会")
    self._last_no_opportunity_log = now
```

### 4. 清理失败的执行器
```python
# 定期清理已失败的执行器
failed_executors = self.filter_executors(
    executors=self.get_all_executors(),
    filter_func=lambda e: e.is_failed and not e.is_active
)
for executor in failed_executors:
    self.logger().info(f"清理失败执行器: {executor.id}")
    # 从列表中移除
```

---

## ✅ 验证修复成功的标志

1. ✅ **日志增长正常**: logs 文件大小稳定增长，不会爆炸
2. ✅ **无重复错误**: 同样的错误不会每秒重复
3. ✅ **策略可停止**: `stop` 命令能正常工作
4. ✅ **执行器单次创建**: 同一时间只有 1 个活跃执行器
5. ✅ **余额检查生效**: 余额不足时正确跳过，不会重复尝试

---

## 📞 如果问题仍未解决

1. **收集诊断信息**:
```bash
# 导出最后 200 行日志
tail -200 logs/logs_conf_cex_dex_basic_arb.log > debug_log.txt

# 检查策略进程
ps aux | grep hummingbot > debug_process.txt

# 检查 Docker 状态（如果适用）
docker ps -a > debug_docker.txt
```

2. **提供以下信息**:
   - debug_log.txt 内容
   - 当前 order_amount 配置
   - Gate.io IRON 实际余额
   - Hummingbot 运行方式（Docker / 直接运行）
   - 停止策略的具体尝试方法和结果

---

**最后更新**: 2025-10-26  
**修复版本**: cex_dex_basic_arb.py v1.1

