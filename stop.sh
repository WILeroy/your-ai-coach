#!/bin/bash
# Fitness Tracker 一键停止
PORT=5200

if lsof -ti:$PORT >/dev/null 2>&1; then
    lsof -ti:$PORT | xargs kill 2>/dev/null
    sleep 1
    echo "✅ 服务已停止"
else
    echo "ℹ️  服务未在运行"
fi
