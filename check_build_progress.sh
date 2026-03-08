#!/bin/bash

# ===================================================================
# 监控多架构镜像构建进度
# ===================================================================

BUILD_LOG="multiarch_build.log"
PID_FILE="build.pid"

echo "🔍 检查构建状态..."
echo ""

# 检查进程是否还在运行
if ps aux | grep -v grep | grep "push_multiarch_to_dockerhub.sh" > /dev/null; then
    echo "✅ 构建任务正在运行中..."
    
    # 显示最后的日志
    echo ""
    echo "📋 最新日志（最后 20 行）："
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -20 $BUILD_LOG
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # 检查关键进度指标
    echo "📊 进度指标："
    
    if grep -q "配置 Docker Buildx" $BUILD_LOG; then
        echo "  ✅ 1/4 - Buildx 配置完成"
    else
        echo "  ⏳ 1/4 - 配置 Buildx..."
    fi
    
    if grep -q "构建 Hummingbot 多架构镜像" $BUILD_LOG; then
        echo "  ✅ 2/4 - 开始构建 Hummingbot"
        
        # 统计 Hummingbot 构建进度
        hb_layers=$(grep -c "CACHED\|DONE" $BUILD_LOG || echo "0")
        echo "      └─ 已完成层数: $hb_layers"
    else
        echo "  ⏳ 2/4 - 等待构建 Hummingbot..."
    fi
    
    if grep -q "Hummingbot 多架构镜像推送完成" $BUILD_LOG; then
        echo "  ✅ 3/4 - Hummingbot 推送完成"
    elif grep -q "pushing manifest for" $BUILD_LOG | grep -q "hummingbot"; then
        echo "  🚀 3/4 - Hummingbot 推送中..."
    else
        echo "  ⏳ 3/4 - 等待推送 Hummingbot..."
    fi
    
    if grep -q "构建 Gateway 多架构镜像" $BUILD_LOG; then
        echo "  ✅ 4/4 - 开始构建 Gateway"
        
        gw_layers=$(grep "gateway" $BUILD_LOG | grep -c "CACHED\|DONE" || echo "0")
        echo "      └─ 已完成层数: $gw_layers"
    else
        echo "  ⏳ 4/4 - 等待构建 Gateway..."
    fi
    
    echo ""
    echo "💡 提示："
    echo "  - 查看实时日志: tail -f $BUILD_LOG"
    echo "  - 预计总时间: 30-50 分钟（首次构建）"
    echo "  - 再次检查进度: ./check_build_progress.sh"
    
else
    # 进程已结束，检查是否成功
    if [ -f "$BUILD_LOG" ]; then
        if grep -q "所有多架构镜像推送成功" $BUILD_LOG; then
            echo "🎉 构建成功完成！"
            echo ""
            echo "📦 推送的镜像："
            grep "推送的镜像：" -A 5 $BUILD_LOG | tail -5
            echo ""
            echo "✅ 现在可以在 AWS 上使用了！"
            echo ""
            echo "📝 在 AWS 上执行："
            echo "  cd ~/hummingbot"
            echo "  docker compose pull"
            echo "  docker compose up -d"
        else
            echo "❌ 构建可能失败了"
            echo ""
            echo "📋 日志末尾："
            tail -30 $BUILD_LOG
            echo ""
            echo "💡 查看完整日志: cat $BUILD_LOG"
        fi
    else
        echo "⚠️  找不到构建日志文件"
    fi
fi

echo ""

