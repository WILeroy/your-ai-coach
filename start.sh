#!/bin/bash
# Fit AI Coach 一键启动（systemd 服务，开机自启，崩溃自动恢复）
cd "$(dirname "$0")"

SERVICE="fit-ai-coach"
PORT=5200

case "${1:-start}" in
  start)
    if systemctl is-active --quiet $SERVICE; then
      echo "✅ 服务已在运行"
    else
      sudo systemctl start $SERVICE
      sleep 2
      echo "✅ 服务已启动"
    fi
    echo "   本地: http://127.0.0.1:$PORT (仅回环)"
    echo "   公网: https://服务器IP:5200 (nginx TLS反代，见 deploy/setup-https.sh)"
    echo "   日志: $(pwd)/gunicorn-access.log"
    echo "   状态: sudo systemctl status $SERVICE"
    ;;
  stop)
    sudo systemctl stop $SERVICE
    echo "✅ 服务已停止"
    ;;
  restart)
    sudo systemctl restart $SERVICE
    sleep 2
    echo "✅ 服务已重启"
    ;;
  status)
    systemctl status $SERVICE --no-pager
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status}"
    ;;
esac
