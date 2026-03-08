# 🐳 使用 Docker Hub 部署自定义 Hummingbot + Gateway

本指南说明如何将你的自定义 Hummingbot 和 Gateway 镜像推送到 Docker Hub，并在多台 AWS 服务器上快速部署。

---

## 📋 优势

✅ **无需重复构建**：在本地构建一次，所有服务器都能使用  
✅ **快速部署**：AWS 上直接拉取镜像，几分钟完成部署  
✅ **版本管理**：可以管理不同版本的镜像  
✅ **节省资源**：避免在每台服务器上编译 TypeScript  

---

## 🚀 第一步：推送镜像到 Docker Hub（本地执行）

### 1.1 登录 Docker Hub

```bash
docker login
# 输入你的 Docker Hub 用户名和密码
```

> 💡 如果没有账号，先注册：https://hub.docker.com/

### 1.2 修改推送脚本

编辑 `push_to_dockerhub.sh`，将 `DOCKERHUB_USERNAME` 改为你的实际用户名：

```bash
DOCKERHUB_USERNAME="firenirva"  # ⚠️ 改为你的用户名
```

### 1.3 执行推送

```bash
./push_to_dockerhub.sh
```

执行后会推送：
- `你的用户名/hummingbot:latest`
- `你的用户名/hummingbot:custom-20251026`
- `你的用户名/gateway:latest`
- `你的用户名/gateway:custom-20251026`

### 1.4 验证推送成功

访问：
- https://hub.docker.com/r/你的用户名/hummingbot
- https://hub.docker.com/r/你的用户名/gateway

---

## ☁️ 第二步：在 AWS 上部署（每台服务器执行）

### 2.1 上传部署脚本到 AWS

```bash
# 从本地上传到 AWS
scp aws_setup.sh your-aws-server:~/
```

### 2.2 在 AWS 上执行部署

```bash
# SSH 到 AWS
ssh your-aws-server

# 修改脚本中的用户名
nano aws_setup.sh
# 将 DOCKERHUB_USERNAME 改为你的用户名

# 执行部署
chmod +x aws_setup.sh
./aws_setup.sh
```

### 2.3 复制配置文件（从本地到 AWS）

```bash
# 在本地执行
cd /Users/alice/Dropbox/投资/量化交易/hummingbot

# 上传 Hummingbot 配置
scp -r conf/connectors/gate_io.yml your-aws-server:~/hummingbot/conf/connectors/
scp -r conf/strategies/*.yml your-aws-server:~/hummingbot/conf/strategies/

# 上传 Gateway 配置
scp -r gateway-files/conf/* your-aws-server:~/hummingbot/gateway-files/conf/
```

---

## 🔄 第三步：更新镜像（有新代码时）

### 3.1 本地更新并推送

```bash
# 在本地修改代码后
cd /Users/alice/Dropbox/投资/量化交易/hummingbot

# 重新构建镜像
docker compose build

# 推送到 Docker Hub
./push_to_dockerhub.sh
```

### 3.2 AWS 上拉取最新镜像

```bash
# 在 AWS 服务器上
cd ~/hummingbot

# 停止服务
docker compose down

# 拉取最新镜像
docker pull 你的用户名/hummingbot:latest
docker pull 你的用户名/gateway:latest

# 重启服务
docker compose up -d
```

---

## 📊 镜像包含的自定义内容

### Hummingbot 镜像修改：
✅ `gateway_base.py`: 使用 `AMM_SWAP` 订单类型  
✅ `v2_cex_dex_dynamic_arb.py`: V2 CEX-DEX 动态套利脚本  

### Gateway 镜像优化：
✅ `ethereum.ts`: EIP-1559 Gas Fee 优化
- Priority Fee 上限：0.05 gwei
- Base Fee 倍数：2x → 1.2x

✅ `executeSwap.ts`: Gas Limit 优化
- 300,000 → 180,000

**预计节省：60-70% Gas 费用** 🎉

---

## 🛠️ 常用命令

### 查看运行状态
```bash
docker compose ps
docker logs hummingbot
docker logs gateway
```

### 进入容器
```bash
# 进入 Hummingbot
docker attach hummingbot

# 进入 Gateway
docker exec -it gateway bash
```

### 重启服务
```bash
docker compose restart
docker compose restart hummingbot
docker compose restart gateway
```

### 查看镜像信息
```bash
docker images | grep firenirva
```

---

## 🔒 安全建议

1. **使用私有仓库**：如果代码包含敏感信息
   ```bash
   # 在 Docker Hub 上将仓库设置为私有
   ```

2. **不要在镜像中包含**：
   - ❌ API Keys
   - ❌ 钱包私钥
   - ❌ 密码
   - ✅ 这些应该在配置文件中（volume 挂载）

3. **版本标签管理**：
   ```bash
   # 推送特定版本
   docker tag hummingbot-hummingbot:latest firenirva/hummingbot:v1.0.0
   docker push firenirva/hummingbot:v1.0.0
   ```

---

## 🎯 多服务器部署示例

```bash
# 在多台服务器上快速部署
for server in aws-server1 aws-server2 aws-server3; do
  echo "部署到 $server..."
  ssh $server "curl -s https://raw.githubusercontent.com/你的用户名/hummingbot/master/aws_setup.sh | bash"
done
```

---

## 📝 故障排查

### 问题：拉取镜像失败
```bash
# 检查 Docker Hub 是否登录
docker login

# 检查镜像是否存在
docker pull 你的用户名/hummingbot:latest --debug
```

### 问题：容器启动失败
```bash
# 查看详细日志
docker logs hummingbot --tail 100
docker logs gateway --tail 100

# 检查配置文件挂载
docker inspect hummingbot | grep Mounts -A 20
```

### 问题：Gateway 连接失败
```bash
# 确认 Gateway 端口开放
curl http://localhost:15888

# 检查证书
ls -la certs/
```

---

## 🔗 相关链接

- **Docker Hub:** https://hub.docker.com/
- **Hummingbot 官方:** https://github.com/hummingbot/hummingbot
- **Gateway 官方:** https://github.com/hummingbot/gateway
- **你的仓库:**
  - Hummingbot: https://github.com/FireNirva/hummingbot
  - Gateway: https://github.com/FireNirva/gateway

---

## 💡 提示

- 使用 `latest` 标签快速迭代
- 使用版本标签（如 `v1.0.0`）用于生产环境
- 定期清理旧镜像节省空间：`docker image prune -a`

