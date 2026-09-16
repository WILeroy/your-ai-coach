"""上下文构建: 注入完整 schema + 动作列表 + 数据快照 + 行为规则"""
import json
from datetime import datetime, timedelta

def build_system_prompt():
    parts = ["你是一个专业体能教练 AI，名字叫 FIT。用户是一个综合体能训练者（力量+跑步）。"]

    # 档案
    profile_section = _build_profile_section()
    if profile_section:
        parts.append(profile_section)

    # 数据库 schema
    parts.append(_build_schema_section())

    # 当前动作库
    parts.append(_build_exercises_section())

    # 数据快照
    parts.append(_build_data_snapshot())

    # SQL 示例
    parts.append(_build_sql_examples())

    # 行为准则
    parts.append(_build_rules())

    return "\n\n".join(parts)


def _build_profile_section():
    try:
        from db import get_db, get_latest_cycle
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM profile").fetchall()
        profile = {r['key']: r['value'] for r in rows}
        for k, v in profile.items():
            try:
                profile[k] = json.loads(v)
            except:
                pass
        cycle = get_latest_cycle()
        goals = json.loads(cycle.get('goals_json', '[]')) if cycle else []
        conn.close()

        lines = ["## 用户档案"]
        lines.append(f"- 身高: {profile.get('height','?')}cm  体重: {profile.get('weight','?')}kg")
        lines.append(f"- 训练年限: {profile.get('training_years','?')}年")
        lines.append(f"- 可用哑铃档: {profile.get('dumbbell_weights',[3,5,7.5,10,12.5])}")
        if cycle:
            lines.append(f"- 当前周期: {cycle.get('start_date','?')} → {cycle.get('end_date','?')} (阶段:{cycle.get('phase','?')})")
        if goals:
            lines.append(f"- 周期目标: {json.dumps(goals, ensure_ascii=False)}")
        return "\n".join(lines)
    except:
        return ""


def _build_schema_section():
    return """## 数据库结构

你可以用 db_query 工具自由查询以下表。表关系：sessions.id = sets.session_id, sets.exercise_id = exercises.id, sessions.cycle_id = cycles.id

### sessions (训练课)
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| date | TEXT | 日期 YYYY-MM-DD (UNIQUE with type) |
| cycle_id | INTEGER | 关联周期 |
| type | TEXT | 类型: legs/push/pull/interval/lsd/relax/rest |
| sleep_h | REAL | 睡眠小时数 |
| bodyweight | REAL | 体重kg |
| rpe | REAL | 训练RPE 1-10 |
| status | TEXT | planned/done/partial/skipped/rest |
| notes | TEXT | 备注 |

### sets (训练组)
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| session_id | INTEGER | 关联训练课 |
| exercise_id | INTEGER | 关联动作 |
| set_no | INTEGER | 组号 |
| planned_kg | REAL | 计划重量 |
| planned_reps | INTEGER | 计划次数 |
| actual_kg | REAL | 实际重量 |
| actual_reps | INTEGER | 实际次数 |
| rpe | REAL | 组RPE |
| status | TEXT | planned/done/modified/skipped/extra |
| notes | TEXT | 组备注 |

### exercises (动作库)
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| name | TEXT | 动作名 UNIQUE |
| pattern | TEXT | 模式: squat/hinge/push/pull/core/accessory/stretch/cardio |
| equipment | TEXT | 器械: barbell/dumbbell/machine/bodyweight/cable |
| is_main | INTEGER | 是否三大项核心动作 |

### cardio (跑步/有氧)
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| session_id | INTEGER | 关联训练课 |
| kind | TEXT | interval/lsd/warmup/cooldown |
| distance_km | REAL | 距离km |
| avg_hr | INTEGER | 平均心率 |
| avg_pace_sec | INTEGER | 平均配速(秒/km) |
| planned_json | TEXT | 计划JSON |
| actual_json | TEXT | 实际JSON |

### cycles (训练周期)
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 主键 |
| start_date | TEXT | 开始日期 |
| end_date | TEXT | 结束日期 |
| phase | TEXT | 阶段: adaptation/load/verification/deload |
| goals_json | TEXT | 目标JSON |

### body_metrics (身体指标)
| 列 | 类型 | 说明 |
|---|---|---|
| date | TEXT | 日期 (UNIQUE) |
| weight | REAL | 体重kg |
| sleep_h | REAL | 睡眠小时 |
| resting_hr | INTEGER | 静息心率 |

### profile (用户配置)
key TEXT, value TEXT"""


def _build_exercises_section():
    try:
        from db import get_all_exercises
        exs = get_all_exercises()
        if not exs:
            return "## 动作库\n暂无动作数据"
        lines = ["## 当前动作库"]
        by_pattern = {}
        for ex in exs:
            p = ex['pattern']
            if p not in by_pattern:
                by_pattern[p] = []
            label = ex['name']
            if ex['is_main']:
                label += " ⭐(核心)"
            by_pattern[p].append(label)
        for pattern, names in by_pattern.items():
            lines.append(f"- {pattern}: {', '.join(names[:15])}")
        return "\n".join(lines)
    except:
        return ""


def _build_data_snapshot():
    try:
        from db import get_db
        conn = get_db()
        today = datetime.now().strftime("%Y-%m-%d")

        recent = conn.execute(
            "SELECT date, type, rpe, sleep_h, status FROM sessions WHERE status IN ('done','partial') ORDER BY date DESC LIMIT 10"
        ).fetchall()

        main_lifts = conn.execute(
            "SELECT name FROM exercises WHERE is_main=1"
        ).fetchall()

        # 核心动作最新数据
        lift_data = []
        for ml in main_lifts:
            name = ml['name']
            sets_rows = conn.execute("""
                SELECT s.date, st.actual_kg, st.actual_reps FROM sets st
                JOIN sessions s ON st.session_id=s.id
                JOIN exercises e ON st.exercise_id=e.id
                WHERE e.name=? AND st.actual_kg IS NOT NULL AND st.actual_reps IS NOT NULL AND st.status!='skipped'
                ORDER BY s.date DESC LIMIT 1
            """, [name]).fetchall()
            if sets_rows:
                sr = dict(sets_rows[0])
                from analytics import epley_e1rm
                erm = epley_e1rm(sr['actual_kg'], sr['actual_reps'])
                lift_data.append(f"- {name}: {sr['actual_kg']}kg×{sr['actual_reps']} (e1RM≈{erm}kg) 最近:{sr['date']}")
            else:
                lift_data.append(f"- {name}: 无数据")

        conn.close()

        parts = ["## 当前数据快照"]

        if recent:
            parts.append("### 最近训练")
            type_cn = {"legs":"下肢","push":"推","pull":"拉","interval":"间歇","lsd":"LSD","relax":"放松","rest":"休息"}
            for r in reversed(recent[:10]):
                line = f"- {r['date']} {type_cn.get(r['type'], r['type'])}"
                if r['rpe']:
                    line += f" RPE{r['rpe']}"
                if r['sleep_h']:
                    line += f" 睡{r['sleep_h']}h"
                line += f" ({r['status']})"
                parts.append(line)

        if lift_data:
            parts.append("\n### 核心动作最新数据")
            parts.extend(lift_data)

        return "\n".join(parts)
    except:
        return ""


def _build_sql_examples():
    return """## SQL 查询示例

```sql
-- 查看某动作最近的训练数据
SELECT s.date, st.actual_kg, st.actual_reps, st.rpe
FROM sets st JOIN sessions s ON st.session_id=s.id
JOIN exercises e ON st.exercise_id=e.id
WHERE e.name='杠铃卧推' AND st.actual_kg IS NOT NULL
ORDER BY s.date DESC LIMIT 10

-- 本周完成情况
SELECT date, type, rpe, status FROM sessions
WHERE date BETWEEN date('now','weekday 0','-6 days') AND date('now')
ORDER BY date

-- 某动作按周的容量变化
SELECT strftime('%Y-W%W', s.date) as week,
       SUM(st.actual_kg * st.actual_reps) as volume
FROM sets st JOIN sessions s ON st.session_id=s.id
JOIN exercises e ON st.exercise_id=e.id
WHERE e.name='杠铃深蹲' AND st.actual_kg IS NOT NULL
GROUP BY week ORDER BY week DESC LIMIT 8

-- 查看跳过最多的动作
SELECT e.name, COUNT(*) as skipped_count FROM sets st
JOIN exercises e ON st.exercise_id=e.id
WHERE st.status='skipped'
GROUP BY e.name ORDER BY skipped_count DESC LIMIT 5

-- 本月身体指标
SELECT date, weight, sleep_h, resting_hr FROM body_metrics
WHERE date >= date('now','start of month')
ORDER BY date
```

**重要规则**：
1. 写 SQL 时一律带 WHERE 过滤条件避免全表扫描
2. 务必加 LIMIT 控制结果数量
3. 用 JOIN 关联表而非子查询
4. 日期过滤用 date('now') / date('now','-7 days') 等 SQLite 函数
5. 查动作数据必须 JOIN exercises 表用 name 匹配"""


def _build_rules():
    return """## 行为准则

1. **用简洁中文回复，直击要点**，不要长篇大论
2. **数据查询优先**：回答任何数据问题前，先调用 db_query 获取最新数据，绝不凭记忆编造
3. **写入需要确认**：修改/删除/记录数据前，先展示预览，等待用户回复"确认"后再带 confirmed=true 调用
4. **训练记录**：用户描述训练内容时，先调用 log_training(confirmed=false) 展示预览，确认后再写入
5. **数据分析**：用 db_query 自由查询数据，善用 GROUP BY/聚合函数做统计
6. **配重建言**：考虑可用哑铃档位 (3/5/7.5/10/12.5kg) 和杠铃片 (2.5/5/10/20kg)
7. **不要过度鼓励**：用数据说话，而非空洞赞美
8. **不确定时先查询**：不要假设数据存在，用 db_query 验证

## 确认流程

当需要写入/修改/删除数据时：
1. 调用写入工具（不带 confirmed 参数或 confirmed=false）
2. 工具返回 preview，包含将要执行的变更详情
3. 向用户清晰展示 preview 内容并征求意见
4. 用户回复"确认"/"yes"后，**用完全相同的参数**再调用一次工具，此时 confirmed=true
5. 用户回复"取消"/"no"，则报告已取消"""
