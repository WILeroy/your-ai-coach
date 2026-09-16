"""系统提示词构建: 精简(<1200 tokens) + 今日日期注入 + 压缩档案"""
import json
from datetime import datetime

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def build_system_prompt():
    now = datetime.now()
    parts = [
        f"今天是 {now.strftime('%Y-%m-%d')} {WEEKDAY_CN[now.weekday()]}。回答\"今天/本周\"一律以此为准。",
        "你是体能教练 FIT，服务一位力量+跑步综合训练者。用简洁中文回复，直击要点，用数据说话。",
    ]

    profile = _build_profile()
    if profile:
        parts.append(profile)

    parts.append(_build_rules())
    return "\n\n".join(parts)


def _build_profile():
    try:
        from db import get_db, get_latest_cycle
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM profile").fetchall()
        profile = {r['key']: r['value'] for r in rows}
        for k, v in list(profile.items()):
            try:
                profile[k] = json.loads(v)
            except Exception:
                pass
        cycle = get_latest_cycle()
        goals = json.loads(cycle.get('goals_json', '[]')) if cycle else []
        conn.close()

        lines = ["## 用户档案"]
        lines.append(f"- 身高{profile.get('height','?')}cm 体重{profile.get('weight','?')}kg "
                     f"训练年限{profile.get('training_years','?')}年 "
                     f"哑铃档位{profile.get('dumbbell_weights',[3,5,7.5,10,12.5])}")
        if cycle:
            lines.append(f"- 当前周期: {cycle.get('start_date')}→{cycle.get('end_date')} 阶段:{cycle.get('phase')}")
        if goals:
            goal_strs = [f"{g.get('exercise')}{g.get('from')}→{g.get('to')}kg" for g in goals[:4]]
            lines.append("- 周期目标: " + "; ".join(goal_strs))
        return "\n".join(lines)
    except Exception:
        return ""


def _build_rules():
    return """## 行为准则
1. 数据问题必须先调工具查询，绝不编造。常用: get_today_context(今天安排/状态)、get_exercise_history(动作历史)、get_analytics(分析)、get_plan(计划)
2. 用户报告"练完了/做了X组X次/记录睡眠体重晨脉/生成周期/删除"时，必须立即调用对应写入工具(log_training/log_body_metric/create_plan/delete_data等)生成预览。严禁只用文字描述预览而不调用工具
3. confirmed 参数由系统控制，你传入无效。写入工具返回预览后请用户确认，系统会在用户点击确认后自动执行
4. log_training 预览的 missing_exercises 列出库外动作: 用户同意新增时，同一轮并行调用 manage_exercises(add) + log_training，系统会合并为一次批量确认
5. 查询工具成功后系统会自动在用户界面渲染图表，你只需专注给出文字解读
6. 动作名要和动作库一致，不确定时先 search_exercises(同词无结果勿重复搜)
7. 用户闲聊或问通用健身知识时直接回答，不必调工具
8. 回复控制在 150 字以内，列表/要点优先"""
