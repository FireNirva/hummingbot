#!/bin/bash
set -e

# ===================================================================
# 构建并推送多架构镜像到 Docker Hub
# 支持 AMD64 (x86_64) 和 ARM64 (Apple Silicon)
# ===================================================================

# 🔧 配置：替换为你的 Docker Hub 用户名
DOCKERHUB_USERNAME="firenirva"  # ⚠️ 请替换为你的实际用户名

# 镜像版本标签
VERSION="v2.12.0"
DATE_TAG="custom-$(date +%Y%m%d)"
LATEST="latest"

echo "🏗️  准备构建多架构镜像..."
echo "用户名: $DOCKERHUB_USERNAME"
echo "版本: $VERSION"
echo "架构: linux/amd64, linux/arm64"
echo ""

# ===================================================================
# 设置 buildx
# ===================================================================
echo "🔧 配置 Docker Buildx..."

# 创建并使用新的 builder（如果不存在）
if ! docker buildx ls | grep -q multiarch-builder; then
  echo "创建新的 builder: multiarch-builder"
  docker buildx create --name multiarch-builder --use
else
  echo "使用现有的 builder: multiarch-builder"
  docker buildx use multiarch-builder
fi

# 启动 builder
docker buildx inspect --bootstrap

echo "✅ Buildx 配置完成"
echo ""

# ===================================================================
# 1. 构建并推送 Hummingbot 多架构镜像
# ===================================================================
echo "🔨 构建 Hummingbot 多架构镜像..."
echo "⚠️  这可能需要 20-40 分钟（首次构建）..."

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag $DOCKERHUB_USERNAME/hummingbot:$VERSION \
  --tag $DOCKERHUB_USERNAME/hummingbot:$DATE_TAG \
  --tag $DOCKERHUB_USERNAME/hummingbot:$LATEST \
  --push \
  --file Dockerfile \
  .

echo "✅ Hummingbot 多架构镜像推送完成！"
echo ""

# ===================================================================
# 2. 构建并推送 Gateway 多架构镜像
# ===================================================================
echo "⛽ 构建 Gateway 多架构镜像..."
echo "⚠️  这可能需要 10-15 分钟（首次构建）..."

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag $DOCKERHUB_USERNAME/gateway:$VERSION \
  --tag $DOCKERHUB_USERNAME/gateway:$DATE_TAG \
  --tag $DOCKERHUB_USERNAME/gateway:$LATEST \
  --push \
  --file gateway/Dockerfile \
  gateway/

echo "✅ Gateway 多架构镜像推送完成！"
echo ""

# ===================================================================
# 完成
# ===================================================================
echo "🎉 所有多架构镜像推送成功！"
echo ""
echo "📋 推送的镜像："
echo "  - $DOCKERHUB_USERNAME/hummingbot:$VERSION (amd64, arm64)"
echo "  - $DOCKERHUB_USERNAME/hummingbot:$DATE_TAG (amd64, arm64)"
echo "  - $DOCKERHUB_USERNAME/hummingbot:latest (amd64, arm64)"
echo "  - $DOCKERHUB_USERNAME/gateway:$VERSION (amd64, arm64)"
echo "  - $DOCKERHUB_USERNAME/gateway:$DATE_TAG (amd64, arm64)"
echo "  - $DOCKERHUB_USERNAME/gateway:latest (amd64, arm64)"
echo ""
echo "🔗 查看你的镜像："
echo "  Hummingbot: https://hub.docker.com/r/$DOCKERHUB_USERNAME/hummingbot"
echo "  Gateway:    https://hub.docker.com/r/$DOCKERHUB_USERNAME/gateway"
echo ""
echo "📝 在 AWS (AMD64) 和 Mac (ARM64) 上都可以使用："
echo "  docker pull $DOCKERHUB_USERNAME/hummingbot:latest"
echo "  docker pull $DOCKERHUB_USERNAME/gateway:latest"
echo ""
echo "💡 验证多架构支持："
echo "  docker buildx imagetools inspect $DOCKERHUB_USERNAME/hummingbot:latest"
echo "  docker buildx imagetools inspect $DOCKERHUB_USERNAME/gateway:latest"

