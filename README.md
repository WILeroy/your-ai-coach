# Fitness Tracker · AI 体能教练

本地训练数据分析与计划系统，支持力量+跑步混合训练的全套运动科学追踪。
现已集成 **AI 对话助手**（LLM Agent），支持自然语言录入训练与智能问答。

## 启动

```bash
cd ~/fitness-tracker
source .venv/bin/activate
python app.py
```

浏览器打开 **http://127.0.0.1:5100**

## 功能

| 页面 | 内容 |
|---|---|
| 仪表盘 | 当日训练卡、周历、三大项 e1RM、ACWR 负荷状态、减载建议 |
| 趋势 | e1RM 历史曲线、周容量堆积图、ACWR 趋势、LSD 配速变化 |
| 周报 | 自动化周度分析（依从性、平台期、负荷风险、下周校准） |
| 周期计划 | 当前周期训练日历与验收目标 |
| **AI助手** ✨ | 对话式训练录入、进展问答、计划查询、分析解读 |

## AI 助手配置

1. 获取 API Key：
   - **Moonshot/Kimi**: https://platform.moonshot.cn（推荐，OpenAI 兼容）
   - **DeepSeek**: https://platform.deepseek.com（更便宜，OpenAI 兼容）
2. 将 `.env.template` 复制为 `.env`，填入 Key：

```bash
cp .env.template .env
# 编辑 .env, 替换 LLM_API_KEY=sk-xxx
```

未配置 Key 时 Chat 页显示配置指引，其余功能不受影响。

### 对话示例

```
你: 今天练什么？
FIT: 今日 7/22 周三 下肢训练: 硬拉 35kg 4×6(首项) / 深蹲 60kg 5×6
     提踵 4×12-15（上次跳过，今天必做）

你: 练完了，硬拉35kg做了4组6次，RPE大概7。深蹲60kg 5组6次最后一组有点沉。
    昨晚睡了7小时。提踵做了4×15。
FIT: 已记录 ✓ 硬拉首次全部完成，W3验收40kg稳了。
     深蹲60全部达标，明天上肢推如果热身感觉沉可以跳过顶组。⚡

你: 我卧推进展怎么样？
FIT: 杠铃卧推当前 e1RM 49kg（7/16 35kg×12），比上次 +5.6kg↑
     上周 40×6 已接近 W3 验收目标 40×5×5，进展良好。
```

## 数据录入

三种方式：

### 1. AI 对话（推荐）
Chat 页直接说人话，Agent 自动解析落库。

### 2. CLI
```bash
python cli.py log 2026-07-22 legs --rpe 7.5 --weight 76 --sleep 7
python cli.py review
```

### 3. API
```bash
curl -X POST http://127.0.0.1:5100/api/log -H "Content-Type: application/json" -d '{...}'
```

## 分析引擎

- **e1RM** (Epley): kg × (1 + reps/30)，追踪三大项最佳组趋势
- **容量吨位**: 按动作模式（蹲/铰链/推/拉/核心）周汇总
- **ACWR**: 急慢性负荷比，sRPE 为统一单位（需 3-4 周数据）
- **平台期**: 连续 3 次同动作 e1RM 未提升
- **依从性**: 被跳过动作的出现模式
- **减载触发**: e1RM 下滑 + RPE 升高 + ACWR 偏高 → 建议减载

## 项目结构

```
fitness-tracker/
├── app.py              # Flask 主应用 (15 API + 5 页面)
├── db.py               # SQLite schema + 查询助手
├── analytics.py        # 分析引擎 (e1RM/ACWR/平台期/依从性)
├── planner.py          # 自动计划器 (渐进规则/周期生成)
├── cli.py              # 命令行工具
├── seed.py             # 种子数据迁移 (W1七天实际 + W2/W3计划)
├── agent/              # AI Agent 模块
│   ├── core.py         # Agent 循环 (理解→工具→回复→落库)
│   ├── tools.py        # 9个确定性工具 + OpenAI schema
│   ├── llm.py          # OpenAI兼容客户端
│   ├── context.py      # 上下文注入 (档案/周期/分析/配重)
│   └── config.py       # .env 配置
├── templates/          # Jinja2 模板 (暗色 UI)
├── static/echarts.min.js  # 离线图表
├── data/fitness.db     # 数据库（单文件，拷贝即备份）
├── .env.template       # LLM 配置模板
└── README.md
```

## 数据备份

拷贝 `data/fitness.db` 一个文件即可。建议每周备份到 iCloud 或外置盘。
