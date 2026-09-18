#!/bin/bash
# 一键部署: nginx + 自签HTTPS反代 + gunicorn 绑定 127.0.0.1:5210
# 有域名后可: sudo certbot --nginx -d 你的域名
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="fit-ai-coach"

if ! command -v nginx >/dev/null 2>&1; then
    echo "[1/5] 安装 nginx..."
    sudo apt-get update -qq && sudo apt-get install -y nginx
else
    echo "[1/5] nginx 已安装"
fi

echo "[2/5] 生成自签证书(含公网IP SAN)..."
PUBLIC_IP="${FIT_PUBLIC_IP:-$(curl -fsS --max-time 5 ifconfig.me || hostname -I | awk '{print $1}')}"
sudo mkdir -p /etc/nginx/ssl
if [ ! -f /etc/nginx/ssl/fit-ai-coach.crt ]; then
    sudo openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout /etc/nginx/ssl/fit-ai-coach.key \
        -out /etc/nginx/ssl/fit-ai-coach.crt \
        -subj "/CN=fit-ai-coach" \
        -addext "subjectAltName=IP:${PUBLIC_IP},DNS:localhost"
fi

echo "[3/5] 配置站点..."
sudo cp "$PROJECT_DIR/deploy/nginx-fit-ai-coach.conf" /etc/nginx/sites-available/fit-ai-coach
sudo ln -sf /etc/nginx/sites-available/fit-ai-coach /etc/nginx/sites-enabled/fit-ai-coach
sudo rm -f /etc/nginx/sites-enabled/default

echo "[4/5] gunicorn 改绑 127.0.0.1:5200..."
sudo sed -i 's|--bind [^ ]*|--bind 127.0.0.1:5210|' /etc/systemd/system/${SERVICE}.service
sudo systemctl daemon-reload

echo "[5/5] 重载 nginx 并重启应用..."
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo systemctl restart ${SERVICE}

echo
echo "✅ 完成。HTTPS 访问: https://${PUBLIC_IP}:5200 (复用已放行端口)"
echo "   若云安全组已放行443，也可直接用 https://${PUBLIC_IP}/"
echo "   gunicorn 仅监听 127.0.0.1:5210 (公网无法直连)"
echo "   提醒: 在 .env 打开 COOKIE_SECURE=true 后执行 ./start.sh restart"
