# FIT · AI 体能教练 v2

以 **Agent 为核心**的本地训练助手：自然语言对话 → 工具调用 → 数据落库 → 可视化画布自动渲染。

## 架构

```
fit-ai-coach/
├── app.py              # Flask: 认证 + SPA托管 + API + SSE
├── auth.py             # 口令登录 + IP限速(SQLite持久化)
├── db.py               # SQLite schema + 助手 (含每日自动备份)
├── analytics.py        # 分析引擎 (e1RM/ACWR/平台期/依从性/减载)
├── planner.py          # 周期计划生成
├── agent/              # AI Agent
│   ├── core.py         # Agent循环: 流式→工具→确认(SQLite持久化)→UI事件
│   ├── tools.py        # 15个结构化工具 + view_spec可视化协议
│   ├── context.py      # 精简系统提示词(~350 tokens, 含今日日期)
│   ├── llm.py          # OpenAI兼容客户端(仅首token前重试)
│   └── config.py       # .env 配置
├── web/                # Vue3 + TS + Vite 前端
│   └── src/pages/      # Coach(默认)/Dashboard/Trends/Review/Plan/Login
├── scripts/agent_eval.py  # Agent回归评测
├── tests/              # pytest 单元测试
└── data/fitness.db     # 单文件数据库，拷贝即备份
```

## 部署

```bash
# 1. 配置
cp .env.template .env
# 编辑 .env: LLM_API_KEY / ACCESS_PASSWORD

# 2. 构建前端
cd web && npm install && npm run build && cd ..

# 3. 启动 (systemd, 开机自启)
./start.sh          # http://服务器IP:5200
```

## 配置 (.env)

| 键 | 默认 | 说明 |
|---|---|---|
| LLM_API_KEY | - | DeepSeek 或任意 OpenAI 兼容 Key |
| LLM_BASE_URL | https://api.deepseek.com | |
| LLM_MODEL | deepseek-flash | DeepSeek V4.1 Flash |
| LLM_MAX_TOKENS | 2048 | |
| AGENT_MAX_TOOL_ROUNDS | 6 | |
| ACCESS_PASSWORD | - | Web 登录口令，必填 |

## Agent 工作循环

```
用户: "深蹲趋势如何？"
  → FIT 调用 get_exercise_history(深蹲)
  → 工具返回数据 + view_spec
  → 前端画布自动渲染 e1RM 曲线 (SSE "ui" 事件)
  → FIT 输出文字解读

用户: "练完了，深蹲65kg 5×5"
  → FIT 调用 log_training(预览)
  → 前端弹出确认卡片
  → 用户点"确认" → 落库 → 画布自动刷新
```

- **读工具**(免确认): get_today_context / get_exercise_history / get_sessions / get_analytics / get_plan / get_body_metrics / search_exercises
- **写工具**(确认门控): log_training / update_session / delete_data / create_plan / adjust_plan / log_body_metric / manage_exercises
- 确认状态存 SQLite，gunicorn 多 worker 安全；10分钟过期

## 安全

- 口令登录 + 会话Cookie (HttpOnly, SameSite=Lax)
- 同IP 15分钟内 5 次登录失败锁定
- 每日首次写入自动备份数据库到 data/fitness.db.bak-YYYYMMDD
- 服务监听公网时，务必设置强口令；建议后续再加 HTTPS 反代

## 开发

```bash
# 后端 (5201测试端口)
.venv/bin/python -c "import app; app.app.run(port=5201)"

# 前端热更新 (5173, 代理到5200)
cd web && npm run dev

# 测试
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/agent_eval.py   # 需API Key
```

## CLI 数据录入

```bash
python cli.py log 2026-09-16 legs --rpe 8 --sleep 7
python cli.py review
```

## E2E 测试 (Playwright)

```bash
cd web && npm install playwright-core --no-save && npx playwright install chromium
FIT_PW=你的口令 node web/e2e-check.mjs
```

## v2.2 新增

- **Agent 联网**：`web_search`（搜狗→360→必应多引擎免注册，自动解析真实链接）+ `web_fetch`（trafilatura 正文抽取，含 SSRF 防护）。问动作技术/营养/伤病/时效性问题时自动检索并在画布展示来源卡片
- **会话管理**：历史抽屉支持单条删除（悬浮🗑）与一键清空，连带清理未完成确认

## v2.3 单页极简化

- 只保留 **教练页 + 登录页**，移除仪表盘/趋势/周报/计划页面与全部导航
- 画布空闲时自动显示**今日概览**（今日动作明细/近7天完成/最新指标），点"回到概览"可随时恢复
- 趋势/周报/计划改为对话查询 + 画布渲染（get_analytics/get_plan）
- 移除 show_view 工具与 naive-ui 依赖
