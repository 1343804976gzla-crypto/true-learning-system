#!/bin/bash
# 重启 True Learning System 服务器

echo "正在停止服务器..."
pkill -f "python.*main.py" 2>/dev/null || echo "没有运行中的服务器"

sleep 2

echo "正在启动服务器..."
cd /c/Users/35456/true-learning-system
nohup python main.py > server.log 2>&1 &

sleep 3

echo "检查服务器状态..."
if curl -s http://localhost:8000/ > /dev/null; then
    echo "✅ 服务器启动成功！"
    echo "访问: http://localhost:8000"
else
    echo "❌ 服务器启动失败，查看 server.log"
fi
