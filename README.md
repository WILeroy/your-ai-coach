# FIT · AI 体能教练 v2.5

本地优先的 AI 训练教练：通过自然语言记录训练、查询趋势、调整课表，并自动生成可解释的右侧训练决策画布。数据保存在本机 SQLite，不依赖云数据库。

## 快速开始

```bash
# 1. 配置密钥与登录口令
cp .env.template .env
# 编辑 .env：LLM_API_KEY / ACCESS_PASSWORD

# 2. 构建前端
cd web && npm install && npm run build && cd ..

# 3. 启动服务
./start.sh
```

服务只监听 `127.0.0.1:5200`，公网访问统一走 nginx HTTPS 反向代理：

```bash
sudo apt-get install -y nginx
sudo cp deploy/nginx-fit-ai-coach.conf /etc/nginx/sites-available/fit-ai-coach
sudo ln -sf /etc/nginx/sites-available/fit-ai-coach /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# 然后在 .env 设置 COOKIE_SECURE=true 并 ./start.sh restart
```

无域名时默认使用自签证书（浏览器会提示一次风险确认）；绑定域名后可用
`sudo certbot --nginx -d 你的域名` 换成受信证书。

安全约定：`X-Forwarded-For` 只在请求来自 `TRUSTED_PROXIES`（默认 `127.0.0.1,::1`）时生效，
直连客户端伪造该头无法绕过登录限速。

## 必要配置

| 键 | 说明 |
|---|---|
| `LLM_API_KEY` | OpenAI 兼容 API Key |
| `LLM_BASE_URL` | 默认 DeepSeek API |
| `LLM_MODEL` | 默认 `deepseek-flash` |
| `ACCESS_PASSWORD` | Web 登录口令，必填 |

## 开发与测试

```bash
# 后端测试
.venv/bin/python -m pytest tests -q
# 前端类型检查与构建
cd web && npm run build
# 本地后端开发服务
cd .. && .venv/bin/python -c "import app; app.app.run(port=5201)"
# 前端热更新
cd web && npm run dev
```

## 数据与备份

训练数据保存在 `data/fitness.db` 单文件 SQLite 数据库中，复制该文件即可备份。系统会在每日首次写入时自动创建当日备份。
