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

默认访问 `http://服务器IP:5200`。公网部署请使用强口令，并建议增加 HTTPS 反向代理。

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
