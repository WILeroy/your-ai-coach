"""Agent 工具层 v2: 结构化读写工具 + view_spec 可视化协议

设计原则:
- 读工具: 窄参数、强类型，返回数据 + view_spec(前端自动渲染图表)
- 写工具: 一律确认门控 (preview -> 用户确认 -> confirmed=true 执行)
- 确认状态持久化到 SQLite (agent_pending_actions)，多 worker 安全
"""
import sys, os, json, uuid
import re
import unicodedata
from difflib import SequenceMatcher
import html as html_mod
import socket
import ipaddress
import urllib.request
import urllib.parse
from html.parser import HTMLParser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, date
from db import (get_db, get_session_detail, exercises_map, upsert_session,
                merge_session_sets, upsert_cardio, get_latest_cycle,
                backup_db_if_new_day, get_body_metric)
from analytics import (compute_e1rm_history, compute_volume_trend, compute_acwr,
                        detect_plateaus, compute_adherence, get_deload_triggers,
                        running_economy_trend, epley_e1rm)

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PENDING_EXPIRE_MINUTES = 1440  # 24小时: 允许用户离开页面后回来确认


# ============================================================
# 确认状态管理 (SQLite 持久化, 多 worker 安全)
# ============================================================

def store_pending(action_id, session_id, actions, messages):
    """批量存储待确认操作。
    actions: [{tool_name, args, tool_call_id, preview}...] — 一轮内全部 pending 操作
    必须在所有 tool 消息 append 完成后调用，保证快照完整。"""
    conn = get_db()
    now = datetime.now()
    first = actions[0]
    conn.execute(
        """INSERT OR REPLACE INTO agent_pending_actions
           (action_id, session_id, messages_json, tool_name, args_json, tool_call_id,
            actions_json, created_at, expires_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        [action_id, session_id, json.dumps(messages, ensure_ascii=False, default=str),
         first["tool_name"], json.dumps(first.get("args", {}), ensure_ascii=False, default=str),
         first.get("tool_call_id", ""),
         json.dumps(actions, ensure_ascii=False, default=str),
         now.isoformat(), (now + timedelta(minutes=PENDING_EXPIRE_MINUTES)).isoformat()])
    conn.commit()
    conn.close()


def get_pending(action_id=None, session_id=None):
    """取出(并删除)一条 pending action; 按 action_id 或 session 最新一条"""
    conn = get_db()
    row = None
    if action_id:
        row = conn.execute("SELECT * FROM agent_pending_actions WHERE action_id=?",
                           [action_id]).fetchone()
    elif session_id:
        row = conn.execute(
            "SELECT * FROM agent_pending_actions WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            [session_id]).fetchone()
    if row:
        conn.execute("DELETE FROM agent_pending_actions WHERE action_id=?", [row['action_id']])
        conn.commit()
    conn.close()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row['expires_at']) < datetime.now():
            return None
    except Exception:
        return None
    d = dict(row)
    d['messages'] = json.loads(d.pop('messages_json'))
    # 批量格式: actions_json 数组; 旧格式: 单 action 包装为数组
    if d.get('actions_json'):
        d.pop('tool_name', None)
        d.pop('args_json', None)
        d.pop('tool_call_id', None)
        d['actions'] = json.loads(d.pop('actions_json'))
    else:
        d['actions'] = [{"tool_name": d.pop('tool_name'),
                         "args": json.loads(d.pop('args_json', '{}')),
                         "tool_call_id": d.pop('tool_call_id', '')}]
    return d


def peek_pending_session(session_id):
    """查看 session 是否有 pending(不删除)"""
    conn = get_db()
    row = conn.execute(
        "SELECT expires_at FROM agent_pending_actions WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        [session_id]).fetchone()
    conn.close()
    if not row:
        return False
    try:
        return datetime.fromisoformat(row['expires_at']) >= datetime.now()
    except Exception:
        return False


def peek_pending_detail(session_id):
    """查看session最新待确认操作(不删除)，用于页面刷新后恢复确认卡。"""
    conn = get_db()
    row = conn.execute(
        """SELECT action_id, tool_name, args_json, actions_json, messages_json, expires_at
           FROM agent_pending_actions WHERE session_id=?
           ORDER BY created_at DESC LIMIT 1""", [session_id]).fetchone()
    conn.close()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            return None
    except Exception:
        return None
    try:
        actions = json.loads(row["actions_json"] or "[]")
    except Exception:
        actions = []
    if not actions:
        try:
            args = json.loads(row["args_json"] or "{}")
        except Exception:
            args = {}
        actions = [{"tool_name": row["tool_name"], "args": args,
                    "preview": {}}]
    try:
        state = json.loads(row["messages_json"] or '{"messages":[]}')
        user_text = next((m.get("content", "") for m in reversed(state.get("messages", []))
                          if m.get("role") == "user"), "")
    except Exception:
        user_text = ""
    return {
        "action_id": row["action_id"],
        "user_text": user_text,
        "actions": [{"tool": a.get("tool_name", a.get("tool", "")),
                     "preview": a.get("preview", {})} for a in actions],
    }


def cleanup_expired():
    conn = get_db()
    conn.execute("DELETE FROM agent_pending_actions WHERE expires_at < ?",
                 [datetime.now().isoformat()])
    conn.commit()
    conn.close()


# ============================================================
# 通用辅助
# ============================================================

def _norm_exercise(name):
    """统一全半角、大小写、分隔符与空白，供别名/模糊匹配使用。"""
    s = unicodedata.normalize("NFKC", str(name or "")).casefold()
    s = re.sub(r"[·/\\|,，、;；:：()（）\[\]【】\-\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_SEMANTIC_TERMS = {
    "movement": [
        (("卧推", "胸推", "bench press", "bench"), "bench"),
        (("深蹲", "蹲", "squat"), "squat"),
        (("硬拉", "deadlift"), "deadlift"),
        (("划船", "row"), "row"),
        (("推肩", "肩推", "推举", "shoulder press", "military press"), "shoulder_press"),
        (("引体", "chin up", "pull up"), "chin_up"),
        (("高位下拉", "lat pulldown", "pulldown"), "pulldown"),
        (("弯举", "curl"), "curl"),
        (("腿屈伸", "leg extension"), "leg_extension"),
        (("腿弯举", "leg curl"), "leg_curl"),
        (("侧平举", "lateral raise"), "lateral_raise"),
        (("飞鸟", "夹胸", "fly"), "fly"),
        (("倒蹬", "腿举", "leg press"), "leg_press"),
        (("卷腹", "crunch"), "core"),
        (("提踵", "calf raise"), "calf"),
    ],
    "equipment": [
        (("哑铃", "dumbbell"), "dumbbell"), (("杠铃", "barbell"), "barbell"),
        (("绳索", "cable"), "cable"), (("器械", "机器", "machine"), "machine"),
        (("自重", "bodyweight"), "bodyweight"), (("史密斯", "smith"), "smith"),
        (("悍马", "hammer"), "hammer"),
    ],
    "position": [
        (("上斜", "incline"), "incline"), (("下斜", "decline"), "decline"),
        (("坐姿", "seated"), "seated"), (("俯身", "bent over"), "bent_over"),
        (("单手", "single arm"), "single_arm"), (("单侧", "unilateral"), "unilateral"),
        (("宽", "wide"), "wide"), (("分腿", "split"), "split"),
    ],
}


def _exercise_signature(name):
    s = _norm_exercise(name)
    sig = {}
    for kind, groups in _SEMANTIC_TERMS.items():
        vals = {token for terms, token in groups if any(term in s for term in terms)}
        if vals:
            sig[kind] = vals
    return sig


def _dice_similarity(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _signature_affinity(input_sig, target_sig):
    in_move = input_sig.get("movement", set())
    target_move = target_sig.get("movement", set())
    move_hit = bool(in_move & target_move) if in_move and target_move else None

    other_i, other_t = [], []
    for kind in ("equipment", "position"):
        other_i.extend(input_sig.get(kind, set()))
        other_t.extend(target_sig.get(kind, set()))
    union = len(set(other_i) | set(other_t))
    inter = len(set(other_i) & set(other_t))
    detail = inter / union if union else 1.0

    # 动作模式是第一身份：胸推/卧推/bench press 可视为同一动作模式。
    # 器械与体位只作为排序辅助，避免让“杠铃/哑铃”这类次要差异阻断录入。
    if move_hit is True:
        return 0.88 + 0.12 * detail
    if move_hit is False:
        return 0.25 * detail
    # 输入没有识别出动作模式时，不能因为目标也没有模式而给予高分。
    return 0.0


def _resolve_exercise(name):
    """解析动作身份: canonical名/别名/标准化精确/语义模糊。"""
    raw = str(name or "").strip()
    if not raw:
        return {"input": "", "matched": None, "candidates": [], "confidence": 0.0, "method": "empty"}
    n = _norm_exercise(raw)
    conn = get_db()
    rows = [dict(r) for r in conn.execute("""
        SELECT e.id, e.name, e.pattern, e.equipment, e.is_main,
               a.alias, a.source
        FROM exercises e LEFT JOIN exercise_aliases a ON a.exercise_id=e.id
    """).fetchall()]
    conn.close()

    # 1) 数据库原名 / 存储别名 / 归一化后的别名
    exact = next((r for r in rows if r["name"] == raw), None)
    method = "exact"
    if not exact:
        alias_rows = [r for r in rows if r["alias"] and _norm_exercise(r["alias"]) == n]
        if len(alias_rows) == 1:
            exact, method = alias_rows[0], "alias"

    scored = []
    input_sig = _exercise_signature(raw)
    grouped = {}
    for r in rows:
        key = r["id"]
        if key not in grouped:
            grouped[key] = r
    for r in grouped.values():
        target_n = _norm_exercise(r["name"])
        affinity = _signature_affinity(input_sig, _exercise_signature(r["name"]))
        seq = _dice_similarity(n, target_n)
        score = 0.72 * affinity + 0.28 * seq
        if n and (n in target_n or target_n in n):
            score += 0.04
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], 0 if x[1]["is_main"] else 1, x[1]["name"]))

    if exact:
        return {"input": raw, "matched": exact["name"], "exercise_id": exact["id"],
                "candidates": [], "confidence": 1.0, "method": method}

    if not scored:
        return {"input": raw, "matched": None, "candidates": [], "confidence": 0.0, "method": "new"}
    top_score, top = scored[0]
    tied = [r for score, r in scored if top_score - score <= 0.025]
    mains = [r for r in tied if r["is_main"]]
    if len(tied) > 1 and len(mains) == 1:
        top = mains[0]
    elif len(tied) > 1:
        top = None
    candidates = [r["name"] for _, r in scored[:5]]
    if top and top_score >= 0.60:
        return {"input": raw, "matched": top["name"], "exercise_id": top["id"],
                "candidates": candidates, "confidence": round(top_score, 3), "method": "fuzzy"}
    return {"input": raw, "matched": None, "candidates": candidates,
            "confidence": round(top_score, 3), "method": "new"}


def _match_exercise(name):
    """兼容旧调用: 返回 (matched_name, candidates)。"""
    r = _resolve_exercise(name)
    return r["matched"], r["candidates"]


def _preview_or_execute(confirmed, build_preview, execute):
    """通用确认流程: 未确认返回预览，已确认执行(先备份)"""
    if not confirmed:
        return {"status": "pending", "action_id": uuid.uuid4().hex[:8],
                "preview": build_preview()}
    try:
        backup_db_if_new_day()
        result = execute()
        return {"status": "done", "ui_refresh": True, **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _weeks_ago(weeks):
    return (date.today() - timedelta(days=weeks * 7)).isoformat()


# 动作名 -> pattern/equipment 推断(用于库外动作新增建议)
_PATTERN_HINTS = [
    (("蹲",), "squat"), (("硬拉", "罗马尼亚", "臀桥"), "hinge"),
    (("推", "卧推", "飞鸟", "屈伸"), "push"), (("划船", "引体", "下拉", "拉"), "pull"),
    (("弯举",), "accessory"), (("卷腹", "平板", "核心"), "core"),
    (("拉伸", "泡沫轴"), "stretch"), (("跑", "间歇", "lsd"), "cardio"),
]
_EQUIPMENT_HINTS = [
    (("杠铃", "直杠"), "barbell"), (("哑铃",), "dumbbell"),
    (("绳索", "拉力器"), "cable"), (("悍马", "机", "器械"), "machine"),
]


def _infer_exercise_meta(name):
    pattern = "accessory"
    for kws, p in _PATTERN_HINTS:
        if any(k in name for k in kws):
            pattern = p
            break
    equipment = None
    for kws, eq in _EQUIPMENT_HINTS:
        if any(k in name for k in kws):
            equipment = eq
            break
    return pattern, equipment


def _resolution_preview(name):
    r = _resolve_exercise(name)
    if r["matched"]:
        return {
            "exercise": r["matched"],
            "input_name": r["input"],
            "resolution": r["method"],
            "confidence": r["confidence"],
            "exists_in_db": True,
        }
    pattern, equipment = _infer_exercise_meta(r["input"])
    return {
        "exercise": r["input"],
        "input_name": r["input"],
        "resolution": "new",
        "confidence": r["confidence"],
        "exists_in_db": False,
        "suggested_pattern": pattern,
        "suggested_equipment": equipment,
    }


def _remember_alias(conn, resolution):
    """把高置信模糊匹配固化为别名，后续同名词不再需要模型猜测。"""
    if (resolution.get("method") != "fuzzy" or
            float(resolution.get("confidence") or 0) < 0.78 or
            not resolution.get("input") or not resolution.get("matched")):
        return False
    cur = conn.execute(
        """INSERT OR IGNORE INTO exercise_aliases(exercise_id, alias, source)
           SELECT id, ?, 'learned' FROM exercises WHERE name=?""",
        [resolution["input"].strip(), resolution["matched"]])
    return bool(cur.rowcount)


def _ensure_exercise_ids(names):
    """写路径的动作身份保障：先解析/学习别名，真正新动作则自动建库。"""
    conn = get_db()
    out, created, learned = [], [], []
    for raw in names:
        raw = str(raw or "").strip()
        if not raw:
            continue
        r = _resolve_exercise(raw)
        if not r["matched"]:
            pattern, equipment = _infer_exercise_meta(raw)
            conn.execute(
                "INSERT OR IGNORE INTO exercises(name,pattern,equipment,is_main,notes) VALUES(?,?,?,?,?)",
                [raw, pattern, equipment, 0, "log_training自动新增"])
            row = conn.execute("SELECT id,name FROM exercises WHERE name=?", [raw]).fetchone()
            r.update({"matched": row["name"], "exercise_id": row["id"], "method": "new"})
            created.append({"name": raw, "pattern": pattern, "equipment": equipment})
        else:
            if _remember_alias(conn, r):
                learned.append({"alias": r["input"], "canonical": r["matched"]})
        out.append(r)
    conn.commit()
    conn.close()
    return out, created, learned


# ============================================================
# 读工具 (免确认, 带 view_spec)
# ============================================================

def tool_get_today_context(**kwargs):
    """今天日期/星期、当日计划配重、近7天实际完成与计划依从、最新身体指标"""
    today = date.today().isoformat()
    conn = get_db()

    today_sessions = conn.execute(
        "SELECT * FROM sessions WHERE date=? ORDER BY type", [today]).fetchall()
    today_list = []
    for s in today_sessions:
        s = dict(s)
        sets_rows = conn.execute("""
            SELECT st.*, e.name as exercise_name FROM sets st
            JOIN exercises e ON st.exercise_id=e.id WHERE st.session_id=?
            ORDER BY st.set_no""", [s['id']]).fetchall()
        by_ex = {}
        for st in sets_rows:
            n = st['exercise_name']
            by_ex.setdefault(n, [])
            by_ex[n].append({"set_no": st["set_no"],
                             "planned": '%skg×%s' % (st["planned_kg"] or "?", st["planned_reps"] or "?"),
                             "actual": ('%skg×%s' % (st["actual_kg"], st["actual_reps"])
                                        if st["actual_kg"] is not None else None),
                             "status": st["status"]})
        s['exercises'] = by_ex
        today_list.append(s)

    week_rows = conn.execute(
        """SELECT date, type, status, rpe, week_no, day_no FROM sessions
           WHERE date >= date(?, '-6 days') AND date <= ? ORDER BY date""",
        [today, today]).fetchall()
    week_summary = [{"date": r['date'], "type": r['type'], "status": r['status'],
                     "rpe": r['rpe'], "week_no": r['week_no'],
                     "day_no": r['day_no']} for r in week_rows]

    metric_rows = [dict(r) for r in conn.execute(
        """SELECT date, weight, sleep_h, resting_hr, hrv_ms FROM body_metrics
           ORDER BY date DESC LIMIT 15""").fetchall()]
    metric = metric_rows[0] if metric_rows else None
    conn.close()

    # 完成统计拆成两个口径，避免把“记录数”误读成日历天或计划依从率：
    # 1) actual: done/partial 的非 rest 记录数，同日补录/替代会分别计数；
    # 2) adherence: 截至今天已到期的非 rest 周期计划，替换训练只计入 actual，不冲抵原计划。
    actual_completed = sum(1 for w in week_summary
                           if w['type'] != 'rest' and w['status'] in ('done', 'partial'))
    active_days = len({w['date'] for w in week_summary
                       if w['type'] != 'rest' and w['status'] in ('done', 'partial')})
    scheduled_due = [w for w in week_summary
                     if w['type'] != 'rest' and w['date'] <= today
                     and (w['week_no'] is not None or w['day_no'] is not None)]
    scheduled_completed = sum(1 for w in scheduled_due
                              if w['status'] in ('done', 'partial'))
    cards = [
        {"label": "近7天实际完成", "value": actual_completed, "unit": "次",
         "sub": "覆盖%d个训练日；同日补录/替代会分别计数" % active_days},
    ]
    if scheduled_due:
        cards.append({"label": "计划依从", "value": "%d/%d" % (scheduled_completed, len(scheduled_due)),
                      "unit": "课", "sub": "截至今天的非休息周期计划；替代训练不计为原计划完成"})

    readiness_signals, caveats = [], []
    if metric:
        cards.append({"label": "体重", "value": metric['weight'], "unit": "kg",
                      "sub": "记录日 %s；单日波动不宜解读为增肌/减脂" % metric['date']})
        cards.append({"label": "睡眠", "value": metric['sleep_h'], "unit": "h",
                      "sub": "参考范围7-9h", "reference": "7-9h"})
        if metric['sleep_h'] is not None and metric['sleep_h'] < 6:
            readiness_signals.append("最新睡眠%.1fh低于6h" % metric['sleep_h'])
        historical_hr = [r['resting_hr'] for r in metric_rows[1:]
                         if r['resting_hr'] is not None]
        if metric['resting_hr'] is not None and historical_hr:
            baseline = sum(historical_hr) / len(historical_hr)
            delta_pct = (metric['resting_hr'] - baseline) / baseline * 100
            cards.append({"label": "静息心率", "value": metric['resting_hr'], "unit": "bpm",
                          "sub": "较近%d次均值%+.0f%%" % (len(historical_hr), delta_pct),
                          "reference": "个人基线"})
            if delta_pct >= 7:
                readiness_signals.append("静息心率较个人基线升高%.0f%%" % delta_pct)
        elif metric['resting_hr'] is not None:
            cards.append({"label": "静息心率", "value": metric['resting_hr'], "unit": "bpm",
                          "sub": "历史样本不足，暂无个人基线"})
        historical_hrv = [r['hrv_ms'] for r in metric_rows[1:]
                          if r['hrv_ms'] is not None]
        if metric['hrv_ms'] is not None and historical_hrv:
            hrv_baseline = sum(historical_hrv) / len(historical_hrv)
            hrv_delta = (metric['hrv_ms'] - hrv_baseline) / hrv_baseline * 100
            cards.append({"label": "HRV", "value": metric['hrv_ms'], "unit": "ms",
                          "sub": "较近%d次均值%+.0f%%" % (len(historical_hrv), hrv_delta),
                          "reference": "个人基线"})
            if hrv_delta <= -15:
                readiness_signals.append("HRV较个人基线下降%.0f%%" % abs(hrv_delta))
        elif metric['hrv_ms'] is not None:
            cards.append({"label": "HRV", "value": metric['hrv_ms'], "unit": "ms",
                          "sub": "历史样本不足，暂无个人基线"})
    else:
        caveats.append("尚未录入体重/睡眠/静息心率/HRV，恢复判断只基于训练记录")

    if not week_summary:
        caveats.append("近7天无训练记录，完成统计不适用")
    elif not scheduled_due:
        caveats.append("近7天没有可识别的周期计划，仅统计实际完成记录")
    acwr = compute_acwr()
    latest_acwr = next((x for x in reversed(acwr)
                        if x.get("status") != "insufficient" and x.get("ratio") is not None), None)
    if latest_acwr:
        cards.append({"label": "ACWR", "value": latest_acwr["ratio"], "unit": "",
                      "sub": "%s；7天急性/28天慢性 sRPE负荷" % latest_acwr["status"],
                      "reference": "0.8-1.3"})
        if latest_acwr["ratio"] > 1.5:
            readiness_signals.append("ACWR %.2f高于1.5" % latest_acwr["ratio"])
    else:
        caveats.append("ACWR需要至少10条28天内记录，当前样本不足")

    if today_list:
        current = today_list[0]
        headline = "今天：%s %s" % (current['type'], current['status'])
    else:
        headline = "今天无训练记录，按恢复日处理"
    if readiness_signals:
        status = "caution"
        headline += "；恢复信号需注意"
    elif len(caveats) >= 2:
        status = "limited"
    else:
        status = "ready"

    evidence = [
        {"label": "训练窗口", "value": "近7天", "detail": "实际完成=done/partial非rest记录；计划依从=截至今天已到期非rest周期计划，替代训练不冲抵原计划"},
        {"label": "恢复输入", "value": "最新1次身体指标",
         "detail": "静息心率/HRV基线=此前近14次可用记录均值；建议固定运动手表来源与时段。静息心率升高≥7%或HRV下降≥15%仅作为注意信号"},
        {"label": "负荷模型", "value": "ACWR",
         "detail": "7天急性/28天慢性 sRPE；训练时长缺失时用组数×3分钟估算，属筛查而非诊断"},
    ]

    return {
        "today": today,
        "weekday": WEEKDAY_CN[date.today().weekday()],
        "today_sessions": today_list,
        "week": week_summary,
        "week_completion": {
            "actual_completed": actual_completed,
            "active_days": active_days,
            "scheduled_due": len(scheduled_due),
            "scheduled_completed": scheduled_completed,
            "definition": "actual counts done/partial non-rest records; adherence counts due scheduled non-rest sessions only",
        },
        "latest_metric": dict(metric) if metric else None,
        "view_spec": {
            "view": "insight", "title": "训练决策简报 · %s %s" % (today, WEEKDAY_CN[date.today().weekday()]),
            "headline": headline, "status": status,
            "status_label": {"ready": "可执行", "caution": "注意恢复",
                             "limited": "证据有限"}[status],
            "cards": cards, "evidence": evidence, "caveats": caveats,
            "readiness_signals": readiness_signals,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def tool_get_exercise_history(exercise, days=90, limit=50, **kwargs):
    """某动作的训练历史 + e1RM 趋势"""
    matched, candidates = _match_exercise(exercise)
    if not matched:
        return {"error": "未找到动作 '%s'" % exercise, "candidates": candidates,
                "hint": "可用 search_exercises 查找正确名称"}
    since = _weeks_ago(max(1, days // 7))
    conn = get_db()
    rows = conn.execute("""
        SELECT s.date, st.actual_kg, st.actual_reps, st.planned_kg, st.planned_reps, st.rpe, st.status
        FROM sets st JOIN sessions s ON st.session_id=s.id
        JOIN exercises e ON st.exercise_id=e.id
        WHERE e.name=? AND s.date>=? AND st.status != 'skipped'
        ORDER BY s.date DESC, st.set_no LIMIT ?""", [matched, since, int(limit)]).fetchall()
    conn.close()

    history = [dict(r) for r in rows]
    best = {}
    for h in history:
        if h['actual_kg'] and h['actual_reps']:
            erm = epley_e1rm(h['actual_kg'], h['actual_reps'])
            if erm and (h['date'] not in best or erm > best[h['date']]['e1rm']):
                best[h['date']] = {"e1rm": erm, "kg": h['actual_kg'], "reps": h['actual_reps']}
    dates = sorted(best.keys())
    series = [{"name": matched, "data": [best[d]['e1rm'] for d in dates]}]

    return {
        "exercise": matched,
        "sets": history[::-1],
        "e1rm_series": {d: best[d] for d in dates},
        "view_spec": {"view": "line", "title": "%s e1RM 趋势" % matched,
                      "x": dates, "series": series, "y_name": "kg",
                      "note": "每个训练日取有效组的最优估算值；e1RM=Epley公式(kg×(1+reps/30))，用于跨日相对比较，不等同1RM实测",
                      "window": "近%d天" % max(1, days), "confidence": "medium"},
    }


def tool_get_sessions(date_from=None, date_to=None, type=None, status=None, limit=50, **kwargs):
    """查询训练课列表；画布使用“计划 vs 实际”双轨卡片，不再暴露混合备注/raw status。"""
    q = "SELECT * FROM sessions WHERE 1=1"
    args = []
    if date_from:
        q += " AND date>=?"; args.append(date_from)
    if date_to:
        q += " AND date<=?"; args.append(date_to)
    if type:
        q += " AND type=?"; args.append(type)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY date DESC LIMIT ?"; args.append(min(max(int(limit or 50), 1), 200))
    conn = get_db()
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    ids = [r["id"] for r in rows]
    set_rows = ([dict(r) for r in conn.execute("""
        SELECT st.*, e.name AS exercise_name
        FROM sets st JOIN exercises e ON e.id=st.exercise_id
        WHERE st.session_id IN (%s) ORDER BY st.session_id, st.set_no, st.id
    """ % ",".join("?" * len(ids)), ids)]
        if ids else [])
    cardio_rows = ([dict(r) for r in conn.execute(
        "SELECT * FROM cardio WHERE session_id IN (%s)" % ",".join("?" * len(ids)), ids)]
        if ids else [])
    conn.close()

    def summarize_strength(items, prefix):
        grouped, order = {}, []
        for st in items:
            kg, reps = st.get(prefix + "_kg"), st.get(prefix + "_reps")
            if kg is None and reps is None:
                continue
            key = (st["exercise_name"], kg, reps)
            if key not in grouped:
                grouped[key] = 0
                order.append(key)
            grouped[key] += 1
        return ["%s %s×%s×%d组" % (
            name, "自重" if kg in (None, 0) else "%gkg" % kg,
            reps if reps is not None else "?", count)
            for (name, kg, reps), count in ((key, grouped[key]) for key in order)]

    sets_by_session = {}
    for st in set_rows:
        sets_by_session.setdefault(st["session_id"], []).append(st)
    cardio_by_session = {}
    for c in cardio_rows:
        cardio_by_session.setdefault(c["session_id"], []).append(c)

    status_label = {"planned": "待执行", "done": "已完成", "partial": "部分完成",
                    "skipped": "已跳过", "rest": "休息"}
    session_views = []
    for s in rows:
        sts = sets_by_session.get(s["id"], [])
        cards = cardio_by_session.get(s["id"], [])
        planned = summarize_strength(
            [st for st in sts if st["status"] != "extra"], "planned")
        actual = summarize_strength(sts, "actual")
        planned_strength_count = sum(
            1 for st in sts if st["status"] != "extra" and
            (st.get("planned_kg") is not None or st.get("planned_reps") is not None))
        actual_strength_count = sum(
            1 for st in sts if st.get("actual_kg") is not None or st.get("actual_reps") is not None)
        cardio_actual = [c for c in cards if c.get("distance_km") or
                         (c.get("actual_json") and c.get("actual_json") != "{}")]
        cardio_planned = [c for c in cards if c.get("planned_json") and c.get("planned_json") != "{}"]
        actual_count = actual_strength_count + len(cardio_actual)
        planned_count = planned_strength_count + len(cardio_planned)

        if actual_count:
            effective_status = "done" if actual_count >= planned_count else "partial"
            record_state = "有实际记录"
        else:
            effective_status = s.get("status") or "planned"
            record_state = "无逐组/实际记录" if effective_status in ("done", "partial") else "待执行"

        if cardio_actual:
            actual.extend("%s %gkm" % (c["kind"], c["distance_km"])
                          for c in cardio_actual if c.get("distance_km"))
        if cardio_planned:
            planned.extend("%s(计划)" % c["kind"] for c in cardio_planned)

        session_views.append({
            "id": s["id"], "date": s["date"], "type": s["type"],
            "status": effective_status,
            "status_label": status_label.get(effective_status, effective_status),
            "tone": {"done": "ok", "partial": "warn", "planned": "info",
                     "skipped": "muted", "rest": "muted"}.get(effective_status, "info"),
            "record_state": record_state,
            "planned": planned, "actual": actual,
            "planned_notes": s.get("planned_notes") or "",
            "actual_notes": s.get("actual_notes") or "",
            "rpe": s.get("rpe"),
        })

    window = "、".join(x for x in (date_from, date_to) if x) or "全部日期"
    for s, rendered in zip(rows, session_views):
        # legacy notes 不再返回给模型，避免把计划备注误读成完成事实。
        s.pop("notes", None)
        s["status"] = rendered["status"]
    return {"sessions": rows,
            "view_spec": {"view": "session_list", "title": "训练课 · 计划 vs 实际",
                          "sessions": session_views,
                          "note": "状态优先由实际组次/跑步记录推断；计划备注与完成备注分开显示。RPE/睡眠不再挤在列表主信息里。",
                          "window": window, "confidence": "high" if ids else "empty"}}


ANALYTICS_KINDS = ("e1rm", "volume", "acwr", "plateau", "adherence", "deload", "running")


def tool_get_analytics(kind, weeks=12, **kwargs):
    """分析引擎: e1rm/volume/acwr/plateau/adherence/deload/running"""
    if kind not in ANALYTICS_KINDS:
        return {"error": "未知 kind: %s" % kind, "available": list(ANALYTICS_KINDS)}
    weeks = min(max(int(weeks or 12), 1), 52)
    since = _weeks_ago(weeks)

    if kind == "e1rm":
        hist = compute_e1rm_history()
        items = sorted(hist.items(), key=lambda kv: -len(kv[1]))[:6]
        all_dates = sorted({e['date'] for _, es in items for e in es if e['date'] >= since})
        series = []
        for name, entries in items:
            m = {e['date']: e['e1rm'] for e in entries if e['date'] >= since}
            series.append({"name": name, "data": [m.get(d) for d in all_dates]})
        return {"data": {n: [e for e in entries if e["date"] >= since] for n, entries in items},
                "view_spec": {"view": "line", "title": "e1RM 趋势", "x": all_dates,
                              "series": series, "y_name": "kg", "connect_nulls": True,
                              "note": "Epley估算：kg×(1+reps/30)。跨线只比较同一动作，不同器械/动作模式不直接比较",
                              "window": "近%d周" % weeks, "confidence": "medium"}}

    if kind == "volume":
        vol = compute_volume_trend()
        weeks_list = list(vol.keys())[-weeks:]
        patterns = ["squat", "hinge", "push", "pull", "core", "other"]
        series = [{"name": p, "data": [vol[w].get(p, 0) for w in weeks_list]}
                  for p in patterns if any(vol[w].get(p, 0) > 0 for w in weeks_list)]
        return {"data": {w: vol[w] for w in weeks_list},
                "view_spec": {"view": "bar", "title": "周容量吨位", "x": weeks_list,
                              "series": series, "y_name": "kg", "stack": True,
                              "note": "容量=实际kg×实际次数；自重/缺少重量的组未计入，跨周期比较需保持动作与记录口径一致",
                              "window": "近%d周" % weeks, "confidence": "medium"}}

    if kind == "acwr":
        hist = [h for h in compute_acwr() if h['date'] >= since]
        return {"data": hist,
                "view_spec": {"view": "line", "title": "ACWR 急慢性负荷比",
                              "x": [h['date'] for h in hist],
                              "series": [{"name": "ACWR", "data": [h['ratio'] for h in hist]}],
                              "y_name": "ratio", "mark_line": 1.5, "mark_area": [0.8, 1.3],
                              "note": "7天急性/28天慢性sRPE负荷比；时长缺失时按组数×3分钟估算，属筛查工具而非损伤预测",
                              "window": "近%d周" % weeks, "confidence": "low"}}

    if kind == "plateau":
        plateaus = detect_plateaus(compute_e1rm_history())
        rows = [{"exercise": k, **v} for k, v in plateaus.items()]
        columns = [{"key": "exercise", "label": "动作"}, {"key": "since_date", "label": "停滞自"},
                   {"key": "current_e1rm", "label": "当前e1RM"}, {"key": "stale_count", "label": "连续无提升"}]
        return {"data": plateaus,
                "view_spec": {"view": "table", "title": "平台期检测", "columns": columns, "rows": rows}}

    if kind == "adherence":
        adh = compute_adherence()
        rows = [{"exercise": k, "skipped": v} for k, v in adh.items()]
        columns = [{"key": "exercise", "label": "动作"}, {"key": "skipped", "label": "跳过次数"}]
        return {"data": adh,
                "view_spec": {"view": "table", "title": "依从性(跳过统计)", "columns": columns, "rows": rows}}

    if kind == "deload":
        triggers = get_deload_triggers()
        rows = [{"type": t['type'], "message": t['message']} for t in triggers]
        columns = [{"key": "type", "label": "类型"}, {"key": "message", "label": "信号"}]
        return {"data": triggers,
                "view_spec": {"view": "table", "title": "减载触发信号", "columns": columns, "rows": rows}}

    if kind == "running":
        trend = [t for t in running_economy_trend() if t['date'] >= since]
        return {"data": trend,
                "view_spec": {"view": "line", "title": "LSD 配速趋势",
                              "x": [t['date'] for t in trend],
                              "series": [{"name": "配速", "data": [t['pace_sec'] for t in trend]}],
                              "y_name": "sec/km"}}
    return {"error": "unreachable"}


def tool_get_plan(cycle="current", **kwargs):
    """当前周期计划: 日历 + 目标 + 进度"""
    conn = get_db()
    if cycle == "current":
        row = conn.execute("SELECT * FROM cycles ORDER BY start_date DESC LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM cycles WHERE id=?", [int(cycle)]).fetchone()
    if not row:
        conn.close()
        return {"error": "没有周期数据，可用 create_plan 生成"}
    c = dict(row)
    sessions = [dict(s) for s in conn.execute(
        "SELECT * FROM sessions WHERE cycle_id=? ORDER BY date", [c['id']]).fetchall()]
    conn.close()
    c['goals'] = json.loads(c.pop('goals_json') or '[]')
    c.pop('notes', None)
    erm = compute_e1rm_history()
    goals = []
    for g in c['goals']:
        name = g.get('exercise')
        latest = erm.get(name, [{}])[-1] if erm.get(name) else {}
        goals.append(dict(g, current_e1rm=latest.get('e1rm')))
    today = date.today().isoformat()
    return {"cycle": c, "goals_progress": goals,
            "sessions": [{"date": s['date'], "type": s['type'], "status": s['status']}
                         for s in sessions],
            "today_index": next((i for i, s in enumerate(sessions) if s['date'] >= today), None)}


def tool_get_body_metrics(days=90, **kwargs):
    """身体指标历史(体重/睡眠/静息心率/HRV)"""
    since = _weeks_ago(max(1, days // 7))
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        """SELECT date, weight, sleep_h, resting_hr, hrv_ms FROM body_metrics
           WHERE date>=? ORDER BY date""",
        [since]).fetchall()]
    conn.close()
    x = [r['date'] for r in rows]
    # 不同量纲不共轴：体重kg、睡眠h、静息心率bpm、HRV ms分成四个独立坐标面板。
    panels = [
        {"title": "体重", "unit": "kg", "data": [r['weight'] for r in rows],
         "reference": "短期波动主要看趋势，不解读单日变化"},
        {"title": "睡眠", "unit": "h", "data": [r['sleep_h'] for r in rows],
         "reference": "成人一般建议7-9h"},
        {"title": "静息心率", "unit": "bpm", "data": [r['resting_hr'] for r in rows],
         "reference": "运动手表静息心率需固定来源/时段解读；较个人基线持续升高≥7%时注意恢复"},
        {"title": "HRV", "unit": "ms", "data": [r['hrv_ms'] for r in rows],
         "reference": "HRV看个人基线和方向，设备算法差异大；较基线持续明显下降时注意恢复压力"},
    ]
    return {"metrics": rows,
            "view_spec": {"view": "panels", "title": "身体指标(独立量纲)",
                          "x": x,
                          "panels": [{"name": p["title"], "unit": p["unit"],
                                      "data": p["data"], "reference": p["reference"]}
                                     for p in panels],
                          "note": "每个指标单独坐标轴，避免kg/h/bpm/ms不同单位混在一条Y轴上造成误读",
                          "window": f"近{max(1, days // 7)}周"}}


def tool_search_exercises(keyword, **kwargs):
    """模糊搜索动作库。支持逗号分隔批量查询多个动作名"""
    keyword = (keyword or "").strip()
    if not keyword:
        return {"error": "keyword 不能为空"}
    conn = get_db()
    # 批量模式: 逗号/顿号分隔
    parts = [p.strip() for p in re.split(r"[,，、]", keyword) if p.strip()]
    if len(parts) > 1:
        groups = []
        for p in parts:
            resolution = _resolve_exercise(p)
            rows = [dict(r) for r in conn.execute(
                "SELECT name, pattern, equipment, is_main FROM exercises WHERE name LIKE ? ORDER BY is_main DESC, name LIMIT 10",
                ["%" + p + "%"]).fetchall()]
            groups.append({"keyword": p, "resolved": resolution["matched"],
                           "resolution": resolution["method"], "confidence": resolution["confidence"],
                           "exact": next((r["name"] for r in rows if r["name"] == p), None),
                           "results": rows})
        conn.close()
        return {"groups": groups}
    resolution = _resolve_exercise(parts[0])
    rows = [dict(r) for r in conn.execute(
        "SELECT name, pattern, equipment, is_main FROM exercises WHERE name LIKE ? ORDER BY is_main DESC, name LIMIT 20",
        ["%" + parts[0] + "%"]).fetchall()]
    conn.close()
    return {"keyword": parts[0], "resolved": resolution["matched"],
            "resolution": resolution["method"], "confidence": resolution["confidence"],
            "results": rows}


# ═══════════════════════════════════════════════
# 联网工具 (免注册: 必应中国版抓取 + 网页正文读取)
# ═══════════════════════════════════════════════

_WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


def _strip_tags(s):
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def tool_web_search(query, max_results=5, **kwargs):
    """联网搜索(搜狗→360→必应 多引擎免注册)。返回标题/链接/摘要，自动在画布渲染来源卡片"""
    query = (query or "").strip()
    if not query:
        return {"error": "query 不能为空"}
    max_results = min(max(int(max_results or 5), 1), 8)

    results = (_search_sogou(query, max_results)
               or _search_so(query, max_results)
               or _search_bing(query, max_results))

    if not results:
        return {"error": "搜索暂不可用或无结果", "hint": "请稍后重试或换个关键词"}
    return {
        "query": query, "results": results,
        "view_spec": {"view": "search_results",
                      "title": "🔍 %s" % query, "results": results},
    }


def _http_get(url, timeout=8, headers=None):
    h = {"User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _resolve_redirect(link, base="https://www.sogou.com"):
    """解析 /link?url=... 跳转链接的真实URL；失败返回原链接"""
    if not link.startswith("/link") and not link.startswith("https://www.so.com/link"):
        return link
    url = base + link if link.startswith("/") else link
    try:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(urllib.request.Request(url, headers={
                "User-Agent": _WEB_UA}), timeout=6)
            page = resp.read(4096).decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            return loc if loc.startswith("http") else link
        m = (re.search(r'url=([^"\'>&\s]+)', page)
             or re.search(r'(https?://[^\s"\'<>]+)', page))
        return html_mod.unescape(m.group(1)) if m else link
    except Exception as e:
        return link


def _block_snippet(blk, min_len=25):
    """从结果块兜底提取摘要"""
    for sel in (".fz-mid", ".str-text-info", ".str_info", ".space-txt",
                ".text-layout", ".res-desc", ".g-link", "p"):
        el = blk.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) >= min_len:
                return t[:160]
    texts = [t.strip() for t in blk.stripped_strings if len(t.strip()) > min_len]
    return texts[0][:160] if texts else ""


def _search_sogou(query, max_results):
    try:
        body = _http_get("https://www.sogou.com/web?query=" + urllib.parse.quote(query))
    except Exception:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    items = []
    for blk in soup.select("div.vrwrap"):
        a = blk.select_one("h3 a[href]")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title:
            continue
        items.append({"title": title[:80], "url": link, "snippet": _block_snippet(blk)})
        if len(items) >= max_results:
            break
    if not items:
        return []
    # 并行解析跳转链接
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as ex:
        real_urls = list(ex.map(lambda it: _resolve_redirect(it["url"]), items))
    for it, u in zip(items, real_urls):
        it["url"] = u
    return [it for it in items if it["url"].startswith("http")]


def _search_so(query, max_results):
    try:
        body = _http_get("https://www.so.com/s?q=" + urllib.parse.quote(query))
    except Exception:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    items = []
    for blk in soup.select("li.res-list, div.res-list, li[class*=res-list]"):
        a = blk.select_one("h3 a[href]")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = a.get("href", "")
        if not title or not link:
            continue
        items.append({"title": title[:80], "url": link, "snippet": _block_snippet(blk)})
        if len(items) >= max_results:
            break
    if not items:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(items), 5)) as ex:
        real_urls = list(ex.map(lambda it: _resolve_redirect(it["url"], "https://www.so.com"), items))
    for it, u in zip(items, real_urls):
        it["url"] = u
    return [it for it in items if it["url"].startswith("http")]


def _search_bing(query, max_results):
    try:
        body = _http_get("https://cn.bing.com/search?q=" + urllib.parse.quote(query))
    except Exception:
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    results = []
    for blk in soup.select("li.b_algo"):
        a = blk.select_one("h2 a[href]") or blk.select_one("a[href^='http']")
        if not a or not a.get("href", "").startswith("http"):
            continue
        title = _strip_tags(a.decode_contents())
        # 清理可能残留的显示URL前缀 (如 "baidu.com › item")
        title = re.sub(r"^[a-z0-9.\-]+\.[a-z]{2,}(\s*›[^ht]*)?(https?://\S+)?\s*", "", title) or title
        p = blk.select_one(".b_caption p") or blk.select_one("p")
        snippet = _strip_tags(p.decode_contents())[:160] if p else ""
        if title:
            results.append({"title": title[:80], "url": a["href"], "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _assert_public_http_url(url):
    """SSRF 防护: 仅允许 http/https + 公网IP + 80/443端口"""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        raise ValueError("URL 无法解析")
    if p.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https")
    if p.port not in (None, 80, 443):
        raise ValueError("仅支持 80/443 端口")
    host = p.hostname
    if not host:
        raise ValueError("缺少主机名")
    # 解析全部IP并检查
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise ValueError("主机名无法解析")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("禁止访问内网/回环地址")


class _TextExtractor(HTMLParser):
    """提取 <title> 与正文可见文本"""
    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        t = data.strip()
        if t:
            self.parts.append(t)


def tool_web_fetch(url, max_chars=4000, **kwargs):
    """读取网页正文文本(trafilatura 抽取主内容)。部分反爬站点(如知乎)会失败"""
    url = (url or "").strip()
    try:
        _assert_public_http_url(url)
    except ValueError as e:
        return {"error": "URL 不允许: %s" % e}

    req = urllib.request.Request(url, headers={
        "User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(2_000_000)  # 上限2MB
            charset = r.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, "ignore")
    except Exception as e:
        return {"error": "网页读取失败: %s" % str(e)[:80],
                "hint": "该站点可能拦截了程序访问，可改用搜索摘要"}

    import trafilatura
    text = trafilatura.extract(body, include_comments=False,
                               include_tables=True, favor_recall=True) or ""
    title = ""
    tm = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    if tm:
        title = _strip_tags(tm.group(1))
    if not text:
        # trafilatura 失败时退回粗提取
        ex = _TextExtractor()
        try:
            ex.feed(body)
        except Exception:
            pass
        text = re.sub(r"\s{2,}", " ", " ".join(ex.parts)).strip()
        title = title or ex.title.strip()
    max_chars = min(max(int(max_chars or 4000), 500), 8000)
    if not text:
        return {"error": "未能提取正文(可能是JS渲染页面)"}
    return {"title": (title or "")[:100], "url": url,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars}




# ============================================================
# 写工具 (确认门控)
# ============================================================

def tool_log_training(date=None, stype=None, sets=None, cardio=None, rpe=None, sleep_h=None,
                      bodyweight=None, notes=None, confirmed=False, **kwargs):
    """记录一次训练"""
    d = date or kwargs.get("date_") or datetime.now().strftime("%Y-%m-%d")
    sets = sets or []
    if not stype:
        return {"error": "stype 必填: legs/push/pull/interval/lsd/relax"}

    def preview():
        p = {"date": d, "type": stype, "rpe": rpe, "sleep_h": sleep_h,
             "bodyweight": bodyweight, "notes": notes or ""}
        p["sets_summary"] = []
        new_exercises = []
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            resolution = _resolution_preview(ename)
            kgs = []
            groups = s.get("groups") or ([s] if "kg" in s else [])
            for g in groups:
                kgs.append("%skg×%s" % (g.get('kg', '?'), g.get('reps', '?')))
            item = {"exercise": resolution["exercise"], "input_name": ename,
                    "groups": kgs, "exists_in_db": resolution["exists_in_db"],
                    "resolution": resolution["resolution"],
                    "confidence": resolution["confidence"]}
            p["sets_summary"].append(item)
            if not resolution["exists_in_db"]:
                new_exercises.append({"name": ename,
                                      "suggested_pattern": resolution["suggested_pattern"],
                                      "suggested_equipment": resolution["suggested_equipment"]})
        if new_exercises:
            p["new_exercises"] = new_exercises
        if cardio and cardio.get("kind"):
            p["cardio_summary"] = {"kind": cardio["kind"], "distance_km": cardio.get("distance_km")}
        return p

    def execute():
        cid = None
        cycle = get_latest_cycle()
        if cycle and cycle['start_date'] <= d <= cycle['end_date']:
            cid = cycle['id']
        sid = upsert_session(d, cid, None, None, stype,
                             sleep_h=sleep_h, bodyweight=bodyweight, rpe=rpe,
                             status="done", actual_notes=notes)
        raw_names = [s.get("exercise", s.get("name", "")) for s in sets]
        resolutions, created, learned = _ensure_exercise_ids(raw_names)
        id_by_name = {r["input"]: r["exercise_id"] for r in resolutions}
        logged, skipped = [], []
        payload = []
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            eid = id_by_name.get(ename)
            if not eid:
                skipped.append(ename)
                continue
            groups = s.get("groups") or ([s] if "kg" in s else [])
            if not groups:
                continue
            matched = next(r["matched"] for r in resolutions if r["input"] == ename)
            payload.append({"exercise_id": eid, "groups": groups})
            for g in groups:
                logged.append({"exercise": matched, "kg": g.get("kg"), "reps": g.get("reps")})
        if payload:
            merge_session_sets(sid, payload)
        if cardio and cardio.get("kind") and (cardio.get("distance_km") or cardio.get("actual")):
            upsert_cardio(sid, cardio["kind"],
                          planned_json=json.dumps(cardio.get("planned", {}), ensure_ascii=False) if cardio.get("planned") else None,
                          actual_json=json.dumps(cardio.get("actual", {}), ensure_ascii=False) if cardio.get("actual") else None,
                          distance_km=cardio.get("distance_km"),
                          avg_hr=cardio.get("avg_hr"), avg_pace_sec=cardio.get("avg_pace_sec"))
        result = {"session_id": sid, "date": d, "type": stype, "logged_sets": logged}
        if created:
            result["created_exercises"] = created
        if learned:
            result["learned_aliases"] = learned
        if skipped:
            result["skipped_unknown_exercises"] = skipped
        return result

    return _preview_or_execute(confirmed, preview, execute)


def tool_update_session(session_id, changes=None, confirmed=False, **kwargs):
    """修改训练课字段(status/rpe/三类备注/sleep_h/date/type)；计划与完成备注分开。"""
    changes = changes or {}

    def preview():
        old = get_session_detail(int(session_id))
        if not old:
            return {"error": "session %s 不存在" % session_id}
        old.pop('sets', None); old.pop('cardio', None)
        old.pop('notes', None)
        old.setdefault('planned_notes', '')
        old.setdefault('actual_notes', '')
        return {"session_id": session_id, "old": old, "changes": changes}

    def execute():
        conn = get_db()
        sid = int(session_id)
        allowed = {"date": "date", "type": "type", "status": "status",
                   "planned_notes": "planned_notes", "actual_notes": "actual_notes"}
        numeric = {"rpe": float, "sleep_h": float, "bodyweight": float}
        if "status" in changes and changes["status"] not in ("planned", "done", "partial", "skipped", "rest"):
            conn.close()
            return {"error": "status 必须是 planned/done/partial/skipped/rest"}
        for k, v in changes.items():
            if k in allowed:
                conn.execute("UPDATE sessions SET %s=? WHERE id=?" % allowed[k], [v, sid])
            elif k in numeric:
                conn.execute("UPDATE sessions SET %s=? WHERE id=?" % k, [numeric[k](v), sid])
            elif k == "notes":
                current = conn.execute("SELECT status FROM sessions WHERE id=?", [sid]).fetchone()["status"]
                target = "actual_notes" if current in ("done", "partial") else "planned_notes"
                conn.execute("UPDATE sessions SET %s=? WHERE id=?" % target, [v, sid])
        conn.commit()
        conn.close()
        return {"session_id": session_id, "updated": list(changes.keys())}

    return _preview_or_execute(confirmed, preview, execute)


def tool_delete_data(target, target_id, confirmed=False, **kwargs):
    """删除训练数据(session/set/cardio/exercise)"""
    valid = {"session", "set", "cardio", "exercise"}
    if target not in valid:
        return {"error": "不可删除: %s，可选: %s" % (target, valid)}

    def preview():
        conn = get_db()
        table = {"session": "sessions", "set": "sets", "cardio": "cardio", "exercise": "exercises"}[target]
        row = conn.execute("SELECT * FROM %s WHERE id=?" % table, [target_id]).fetchone()
        conn.close()
        if not row:
            return {"error": "%s %s 不存在" % (target, target_id)}
        return {"target": target, "target_id": target_id, "data": dict(row)}

    def execute():
        conn = get_db()
        table = {"session": "sessions", "set": "sets", "cardio": "cardio", "exercise": "exercises"}[target]
        conn.execute("DELETE FROM %s WHERE id=?" % table, [target_id])
        conn.commit()
        conn.close()
        return {"deleted": target, "id": target_id}

    return _preview_or_execute(confirmed, preview, execute)


def tool_create_plan(start_date, weeks=4, template="standard", goals=None,
                     confirmed=False, **kwargs):
    """生成训练周期计划"""
    from planner import build_cycle_template
    goals = goals or []

    def preview():
        days = build_cycle_template(start_date, int(weeks), template)
        by_week = {}
        for dd in days:
            by_week.setdefault(dd.get('week_no', 1), []).append(
                {"date": dd['date'], "type": dd['type']})
        return {"start_date": start_date, "weeks": weeks, "template": template,
                "goals": goals, "schedule": by_week}

    def execute():
        from db import insert_session, insert_cycle
        days = build_cycle_template(start_date, int(weeks), template)
        if not days:
            return {"error": "生成计划为空"}
        end_date = days[-1]['date']
        cid = insert_cycle(start_date, end_date, template,
                           json.dumps(goals, ensure_ascii=False), '')
        stock_names = ("杠铃卧推", "哑铃推举", "哑铃飞鸟", "三头臂屈伸",
                       "传统硬拉", "杠铃划船", "引体向上", "哑铃弯举",
                       "杠铃深蹲", "罗马尼亚硬拉", "保加利亚分腿蹲", "腿弯举")
        resolutions, created, learned = _ensure_exercise_ids(stock_names)
        ex_map = {r["input"]: r["exercise_id"] for r in resolutions}
        for dd in days:
            sid = insert_session(dd['date'], cid, dd.get('week_no'), dd.get('day_no'),
                                 dd['type'], status='planned')
            if dd['type'] in ('push', 'pull', 'legs'):
                _create_stock_sets(sid, dd['type'], ex_map)
        result = {"cycle_id": cid, "start_date": start_date, "end_date": end_date,
                  "days_count": len(days)}
        if created:
            result["created_exercises"] = created
        if learned:
            result["learned_aliases"] = learned
        return result

    return _preview_or_execute(confirmed, preview, execute)


def _create_stock_sets(session_id, session_type, ex_map):
    stock = {
        "push": ["杠铃卧推", "哑铃推举", "哑铃飞鸟", "三头臂屈伸"],
        "pull": ["传统硬拉", "杠铃划船", "引体向上", "哑铃弯举"],
        "legs": ["杠铃深蹲", "罗马尼亚硬拉", "保加利亚分腿蹲", "腿弯举"],
    }
    conn = get_db()
    for i, ename in enumerate(stock.get(session_type, []), 1):
        eid = ex_map.get(ename)
        if not eid:
            continue
        conn.execute(
            "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
            [session_id, eid, i, None, None])
    conn.commit()
    conn.close()


def tool_adjust_plan(date=None, changes=None, confirmed=False, **kwargs):
    """调整某天计划的动作组"""
    d = date or kwargs.get("date_")
    changes = changes or []

    # 确定性拦截: 向已有完整课表(≥2个动作)的那天新增动作 = 整日替换意图，重定向
    if d and changes:
        conn = get_db()
        row = conn.execute("SELECT id, type FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if row:
            existing = {r["name"] for r in conn.execute("""
                SELECT e.name FROM sets st JOIN exercises e ON st.exercise_id=e.id
                WHERE st.session_id=?""", [row["id"]]).fetchall()}
            conn.close()
            new_names = []
            for ch in changes:
                m, _ = _match_exercise(ch.get("exercise", ""))
                if m and m not in existing:
                    new_names.append(m)
            if new_names and len(existing) >= 2:
                return {"error": "%s 当天已有 %d 个动作(%s)，新增 %s 属于整日换课。请改用 replace_day_plan(date='%s', new_type=..., sets=[当天应保留+新增的全部动作]) 完成替换"
                        % (d, len(existing), "、".join(sorted(existing))[:60],
                           "、".join(new_names), d)}
        else:
            conn.close()

    def preview():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可调整的计划" % d}
        current = [dict(r) for r in conn.execute("""
            SELECT e.name, st.planned_kg, st.planned_reps FROM sets st
            JOIN exercises e ON st.exercise_id=e.id WHERE st.session_id=?""", [row['id']]).fetchall()]
        conn.close()
        new_items, new_exercises = [], []
        for ch in changes:
            resolution = _resolution_preview(ch.get("exercise", ""))
            new_items.append({**ch, **resolution})
            if not resolution["exists_in_db"]:
                new_exercises.append({"name": ch.get("exercise", ""),
                                      "suggested_pattern": resolution["suggested_pattern"],
                                      "suggested_equipment": resolution["suggested_equipment"]})
        return {"date": d, "current": current, "new": new_items,
                "new_exercises": new_exercises}

    def execute():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可调整的计划" % d}
        sid = row['id']
        raw_names = [ch.get("exercise", "") for ch in changes]
        resolutions, created, learned = _ensure_exercise_ids(raw_names)
        id_by_name = {r["input"]: r["exercise_id"] for r in resolutions}
        adjusted = []
        for ch in changes:
            eid = id_by_name.get(ch.get("exercise", ""))
            if not eid:
                continue
            matched = next(r["matched"] for r in resolutions if r["input"] == ch.get("exercise", ""))
            conn.execute("DELETE FROM sets WHERE session_id=? AND exercise_id=?", [sid, eid])
            for i, g in enumerate(ch.get("groups", [ch]), 1):
                conn.execute(
                    "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
                    [sid, eid, i, g.get("kg"), g.get("reps")])
                adjusted.append({"exercise": matched, "kg": g.get("kg"), "reps": g.get("reps")})
        conn.commit()
        conn.close()
        result = {"date": d, "adjusted": adjusted}
        if created:
            result["created_exercises"] = created
        if learned:
            result["learned_aliases"] = learned
        return result

    return _preview_or_execute(confirmed, preview, execute)


DAY_TYPES = ("legs", "push", "pull", "interval", "lsd", "relax", "rest")


def tool_replace_day_plan(date=None, new_type=None, sets=None, notes=None,
                          confirmed=False, **kwargs):
    """整天替换训练计划: 改类型+清原计划组+写新组(需确认)。已完成训练不可替换"""
    d = date or kwargs.get("date_")
    if not d:
        return {"error": "date 必填 YYYY-MM-DD"}
    if new_type not in DAY_TYPES:
        return {"error": "new_type 必须是: %s" % "/".join(DAY_TYPES)}
    sets = sets or []

    def _old_summary(conn, sid):
        s = conn.execute("SELECT type, status, notes FROM sessions WHERE id=?", [sid]).fetchone()
        rows = conn.execute("""
            SELECT e.name, st.planned_kg, st.planned_reps FROM sets st
            JOIN exercises e ON st.exercise_id=e.id
            WHERE st.session_id=? AND st.status='planned'""", [sid]).fetchall()
        by_ex = {}
        for r in rows:
            by_ex.setdefault(r["name"], {"kg": r["planned_kg"], "reps": r["planned_reps"], "n": 0})
            by_ex[r["name"]]["n"] += 1
        items = ["%s %s×%s×%d组" % (k, v["kg"] if v["kg"] is not None else "自重",
                                     v["reps"] or "?", v["n"]) for k, v in by_ex.items()]
        return {"type": s["type"], "status": s["status"], "notes": s["notes"] or "",
                "exercises": items}

    def preview():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可替换的计划(仅能替换 planned 状态的训练日；已完成训练请用补录)" % d}
        old = _old_summary(conn, row["id"])
        conn.close()
        new_items, new_exercises = [], []
        for s in sets:
            resolution = _resolution_preview(s.get("exercise", s.get("name", "")))
            groups = s.get("groups") or ([s] if "kg" in s else [])
            item = {"exercise": resolution["exercise"],
                    "input_name": s.get("exercise", s.get("name", "")),
                    "exists_in_db": resolution["exists_in_db"],
                    "resolution": resolution["resolution"],
                    "confidence": resolution["confidence"],
                    "groups": ["%skg×%s" % (g.get("kg", "?"), g.get("reps", "?")) for g in groups]}
            new_items.append(item)
            if not resolution["exists_in_db"]:
                new_exercises.append({"name": s.get("exercise", s.get("name", "")),
                                      "suggested_pattern": resolution["suggested_pattern"],
                                      "suggested_equipment": resolution["suggested_equipment"]})
        return {"date": d, "old": old,
                "new": {"type": new_type, "notes": notes or "", "exercises": new_items},
                "new_exercises": new_exercises}

    def execute():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'",
                           [d]).fetchone()
        if not row:
            conn.close()
            return {"error": "%s 没有可替换的计划(仅能替换 planned 状态；已完成训练请用补录)" % d}
        sid = row["id"]
        conn.close()
        # 先解析/创建动作，再写计划，避免替换事务未提交时被第二个写连接锁住。
        raw_names = [s.get("exercise", s.get("name", "")) for s in sets]
        resolutions, created, learned = _ensure_exercise_ids(raw_names)
        id_by_name = {r["input"]: r["exercise_id"] for r in resolutions}
        conn = get_db()
        # 清掉全部 planned 组(不动已有 actual 数据)
        conn.execute("DELETE FROM sets WHERE session_id=? AND status='planned'", [sid])
        conn.execute("""UPDATE sessions SET type=?,
                        planned_notes=COALESCE(?, planned_notes) WHERE id=?""",
                     [new_type, notes, sid])
        replaced = []
        for s in sets:
            eid = id_by_name.get(s.get("exercise", s.get("name", "")))
            if not eid:
                continue
            matched = next(r["matched"] for r in resolutions
                           if r["input"] == s.get("exercise", s.get("name", "")))
            groups = s.get("groups") or ([s] if "kg" in s else [])
            for i, g in enumerate(groups, 1):
                conn.execute(
                    "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
                    [sid, eid, i, g.get("kg"), g.get("reps")])
            replaced.append({"exercise": matched, "sets": len(groups)})
        conn.commit()
        conn.close()
        result = {"date": d, "new_type": new_type, "session_id": sid,
                  "replaced_exercises": replaced, "note": "原计划组已清空并替换"}
        if created:
            result["created_exercises"] = created
        if learned:
            result["learned_aliases"] = learned
        return result

    return _preview_or_execute(confirmed, preview, execute)


def tool_log_body_metric(date=None, weight=None, sleep_h=None, resting_hr=None,
                         hrv_ms=None, notes=None, confirmed=False, **kwargs):
    """记录身体指标"""
    d = date or kwargs.get("date_") or datetime.now().strftime("%Y-%m-%d")
    # 兼容模型偶发传 hrv=52；正式schema仍使用带单位的 hrv_ms。
    if hrv_ms is None:
        hrv_ms = kwargs.get("hrv")

    def same_value(a, b):
        if a is None or b is None:
            return a is b
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return a == b

    old = get_body_metric(d)
    raw_incoming = {"weight": weight, "sleep_h": sleep_h,
                    "resting_hr": resting_hr, "hrv_ms": hrv_ms}
    changed_values = {key: value for key, value in raw_incoming.items()
                      if value is not None and not same_value(old.get(key), value)}
    note_changed = bool(notes and notes != (old.get("notes") or ""))

    # 关键防重复：模型常把已有RHR/HRV一起带回。若没有任何实际变化，
    # 这不是写操作，直接返回done/no_op，不生成确认卡。
    if not changed_values and not note_changed:
        return {"status": "done", "date": d, "saved": False, "no_op": True,
                "changed_fields": [], "record": old,
                "message": "身体指标无变化，已跳过重复录入"}

    def preview():
        result = dict(old)
        for key, value in changed_values.items():
            result[key] = value
        changed = list(changed_values.keys())
        if notes:
            result["notes"] = notes
            if note_changed:
                changed.append("notes")
        return {"date": d, "incoming": changed_values, "existing": old,
                "result": result, "changed_fields": changed,
                "merge_rule": "同一天多次录入按字段合并；仅确认实际变化字段，未变化字段不会重复写入"}

    def execute():
        from db import insert_body_metric
        insert_body_metric(
            d,
            changed_values.get("weight"),
            changed_values.get("sleep_h"),
            changed_values.get("resting_hr"),
            changed_values.get("hrv_ms"),
            notes if note_changed else None,
        )
        return {"date": d, "saved": True, "record": get_body_metric(d),
                "changed_fields": list(changed_values.keys()) +
                                 (["notes"] if note_changed else []),
                "ui_refresh": True}

    return _preview_or_execute(confirmed, preview, execute)


def tool_manage_exercises(action, name, pattern=None, equipment=None, is_main=0,
                          notes=None, target=None, confirmed=False, **kwargs):
    """动作库管理: add/update/alias"""
    if action not in ("add", "update", "alias"):
        return {"error": "action 必须是 add/update/alias"}
    name = str(name or "").strip()
    target = str(target or kwargs.get("canonical") or "").strip()

    def preview():
        resolution = _resolve_exercise(name)
        if action == "alias":
            if not target:
                return {"error": "alias 操作需要 target(标准动作名)"}
            target_res = _resolve_exercise(target)
            if not target_res["matched"]:
                return {"error": "目标动作 '%s' 不存在" % target}
            conn = get_db()
            owner = conn.execute("""
                SELECT e.name FROM exercise_aliases a
                JOIN exercises e ON e.id=a.exercise_id WHERE a.alias=?""", [name]).fetchone()
            conn.close()
            if owner and owner["name"] != target_res["matched"]:
                return {"error": "别名 '%s' 已归属 %s；如需改归属请先删除旧别名" % (name, owner["name"])}
            return {"action": "alias", "name": name, "target": target_res["matched"],
                    "note": "确认后 %s 将作为别名写入，历史会归并到 %s" % (name, target_res["matched"])}
        if action == "add" and resolution["matched"]:
            return {"error": "动作 '%s' 已存在；若它与 %s 是同一动作，请用 action=alias"
                    % (name, resolution["matched"])}
        if action == "update" and not resolution["matched"]:
            return {"error": "动作 '%s' 不存在" % name}
        return {"action": action,
                "name": resolution["matched"] if action == "update" else name,
                "pattern": pattern, "equipment": equipment, "is_main": bool(is_main)}

    def execute():
        conn = get_db()
        action_name = name
        if action == "alias":
            target_res = _resolve_exercise(target)
            if not target_res["matched"]:
                conn.close()
                return {"error": "目标动作 '%s' 不存在" % target}
            cur = conn.execute(
                "INSERT OR IGNORE INTO exercise_aliases(exercise_id,alias,source) VALUES(?,?,'user')",
                [target_res["exercise_id"], name])
            conn.commit()
            conn.close()
            return {"action": "alias", "alias": name, "target": target_res["matched"],
                    "created": bool(cur.rowcount)}
        if action == "add":
            conn.execute("INSERT INTO exercises(name,pattern,equipment,is_main,notes) VALUES(?,?,?,?,?)",
                         [name, pattern or "accessory", equipment, int(bool(is_main)), notes or ""])
        else:
            resolution = _resolve_exercise(name)
            if not resolution["matched"]:
                conn.commit()
                conn.close()
                return {"error": "动作 '%s' 不存在" % name}
            update_name = resolution["matched"]
            action_name = update_name
            conn.execute("""UPDATE exercises SET pattern=COALESCE(?,pattern),
                            equipment=COALESCE(?,equipment), is_main=COALESCE(?,is_main),
                            notes=COALESCE(?,notes) WHERE name=?""",
                         [pattern, equipment, int(bool(is_main)) if is_main else None, notes, update_name])
        conn.commit()
        conn.close()
        return {"action": action, "name": action_name}

    return _preview_or_execute(confirmed, preview, execute)


# ============================================================
# OpenAI 工具 schema
# ============================================================

def _fn(name, desc, params=None, required=None):
    p = {"type": "object", "properties": params or {}}
    if required:
        p["required"] = required
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": p}}


def _str(desc, **kw):
    d = {"type": "string", "description": desc}; d.update(kw); return d


def _num(desc, **kw):
    d = {"type": "number", "description": desc}; d.update(kw); return d


_GROUPS = {"type": "array", "items": {"type": "object", "properties": {
    "kg": _num("重量kg"), "reps": _num("次数")}}}

TOOL_SCHEMAS = [
    _fn("get_today_context", "获取今天日期星期、当日训练计划与配重、近7天实际完成次数/计划依从率、最新身体指标。回答'今天练什么/今天状态/完成次数'用它"),
    _fn("get_exercise_history", "查某动作训练历史与e1RM趋势(自动渲染曲线)",
        {"exercise": _str("动作名，如 杠铃卧推"), "days": _num("回看天数，默认90"), "limit": _num("最多组数，默认50")},
        ["exercise"]),
    _fn("get_sessions", "查询训练课列表",
        {"date_from": _str("起始日期YYYY-MM-DD"), "date_to": _str("结束日期YYYY-MM-DD"),
         "type": _str("legs/push/pull/interval/lsd/relax/rest"),
         "status": _str("planned/done/partial/skipped"), "limit": _num("默认50")}),
    _fn("get_analytics", "分析引擎。kind: e1rm(e1RM趋势) volume(周容量) acwr(负荷比) plateau(平台期) adherence(依从性) deload(减载信号) running(跑步配速)",
        {"kind": _str("分析类型", enum=list(ANALYTICS_KINDS)), "weeks": _num("回看周数，默认12")},
        ["kind"]),
    _fn("get_plan", "查周期计划日历+目标进度", {"cycle": _str("current或周期ID，默认current")}),
    _fn("get_body_metrics", "身体指标历史(体重/睡眠/静息心率/HRV)", {"days": _num("回看天数，默认90")}),
    _fn("search_exercises", "解析/搜索动作库：返回canonical动作、别名解析方法和候选。多个动作名用逗号分隔一次批量查询(如'引体向上,划船,弯举')。无结果时可直接用log_training/replace_day_plan，确认后会自动新增",
        {"keyword": _str("关键词")}, ["keyword"]),
    _fn("web_search", "联网搜索(训练技术/营养/伤病康复/器材/时效性问题用)。返回标题/链接/摘要并自动在画布展示来源",
        {"query": _str("搜索关键词"), "max_results": _num("条数，默认5，最多8")}, ["query"]),
    _fn("web_fetch", "读取指定网页正文纯文本。web_search摘要不够时用；部分反爬站点会失败",
        {"url": _str("完整URL http/https"), "max_chars": _num("正文截取字数，默认4000")}, ["url"]),
    _fn("log_training", "记录一次训练(需确认)。sets每项:{exercise,groups:[{kg,reps}]}。动作名会自动解析别名；真正新动作会在用户确认本次训练时自动入库(new_exercises)，不要再并行调用manage_exercises(add)",
        {"date": _str("日期YYYY-MM-DD，默认今天"), "stype": _str("类型: legs/push/pull/interval/lsd/relax"),
         "sets": {"type": "array", "description": "动作组列表",
                  "items": {"type": "object", "properties": {
                      "exercise": _str("动作名"), "groups": _GROUPS}}},
         "cardio": {"type": "object", "properties": {"kind": _str("interval/lsd"), "distance_km": _num("距离km"), "avg_hr": _num("平均心率"), "avg_pace_sec": _num("平均配速秒/km")}},
         "rpe": _num("整体RPE 1-10"), "sleep_h": _num("睡眠小时"), "notes": _str("备注"),
         "confirmed": {"type": "boolean", "description": "用户确认后为true"}},
        ["stype"]),
    _fn("update_session", "修改训练课字段(需确认)",
        {"session_id": _num("session ID"), "changes": {"type": "object", "description": "{status,rpe,sleep_h,date,type,planned_notes,actual_notes}子集；不要用备注表达完成，直接更新status"},
         "confirmed": {"type": "boolean"}},
        ["session_id", "changes"]),
    _fn("delete_data", "删除数据(需确认)",
        {"target": _str("session/set/cardio/exercise"), "target_id": _num("记录ID"),
         "confirmed": {"type": "boolean"}}, ["target", "target_id"]),
    _fn("create_plan", "生成训练周期计划(需确认)",
        {"start_date": _str("开始日期YYYY-MM-DD"), "weeks": _num("周数，默认4"),
         "template": _str("模板，默认standard"),
         "goals": {"type": "array", "description": "目标列表", "items": {"type": "object", "properties": {
             "exercise": _str("动作"), "from": _num("当前kg"), "to": _num("目标kg"),
             "sets": _num("组数"), "reps": _num("次数")}}},
         "confirmed": {"type": "boolean"}}, ["start_date"]),
    _fn("adjust_plan", "微调某天动作的组数/重量/增删个别动作(需确认)。不能改训练类型！用户要求'改成练X/换课'时禁用本工具，必须用 replace_day_plan",
        {"date": _str("日期YYYY-MM-DD"), "changes": {"type": "array", "items": {"type": "object", "properties": {
            "exercise": _str("动作名"), "groups": _GROUPS}}},
         "confirmed": {"type": "boolean"}}, ["date", "changes"]),
    _fn("replace_day_plan", "整天替换训练计划(需确认)：改训练类型+清掉原动作组+写入新动作组。用户说'把某天改成练腿/练推/跑步'或更换整天的课时必须用本工具(而不是adjust_plan)。仅限 planned 状态",
        {"date": _str("日期YYYY-MM-DD"), "new_type": _str("新类型", enum=list(DAY_TYPES)),
         "sets": {"type": "array", "description": "新动作组列表",
                  "items": {"type": "object", "properties": {
                      "exercise": _str("动作名"), "groups": _GROUPS}}},
         "notes": _str("新备注"), "confirmed": {"type": "boolean"}},
        ["date", "new_type"]),
    _fn("log_body_metric", "记录身体指标(仅实际变化字段需确认)。只传用户本次明确提供的新值，不要携带已有旧值；用户仅说'练/今天练什么'时禁止调用本工具",
        {"date": _str("日期YYYY-MM-DD"), "weight": _num("体重kg"), "sleep_h": _num("睡眠h"),
         "resting_hr": _num("静息心率bpm(可来自运动手表)"),
         "hrv_ms": _num("HRV毫秒ms(可来自运动手表，建议记录同源同时段数据)"),
         "confirmed": {"type": "boolean"}}, ["date"]),
    _fn("manage_exercises", "动作库管理(需确认)。同一动作的不同叫法优先用alias归并到target，不要重复add",
        {"action": _str("add/update/alias"), "name": _str("动作名；alias时表示别名"), "target": _str("alias目标的标准动作名"), "pattern": _str("squat/hinge/push/pull/core/accessory/stretch/cardio"),
         "equipment": _str("barbell/dumbbell/machine/bodyweight/cable"),
         "is_main": _num("是否核心动作0/1"), "confirmed": {"type": "boolean"}},
        ["action", "name"]),
]


TOOL_MAP = {
    "get_today_context": tool_get_today_context,
    "get_exercise_history": tool_get_exercise_history,
    "get_sessions": tool_get_sessions,
    "get_analytics": tool_get_analytics,
    "get_plan": tool_get_plan,
    "get_body_metrics": tool_get_body_metrics,
    "search_exercises": tool_search_exercises,
    "web_search": tool_web_search,
    "web_fetch": tool_web_fetch,
    "log_training": tool_log_training,
    "update_session": tool_update_session,
    "delete_data": tool_delete_data,
    "create_plan": tool_create_plan,
    "adjust_plan": tool_adjust_plan,
    "replace_day_plan": tool_replace_day_plan,
    "log_body_metric": tool_log_body_metric,
    "manage_exercises": tool_manage_exercises,
}


def store_pending_batch_legacy(action_id, session_id, tool_name, args, tool_call_id, messages):
    """以旧格式(单 action, 无 actions_json)写入，仅用于兼容性测试"""
    conn = get_db()
    from datetime import datetime, timedelta
    now = datetime.now()
    conn.execute(
        """INSERT OR REPLACE INTO agent_pending_actions
           (action_id, session_id, messages_json, tool_name, args_json, tool_call_id, created_at, expires_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        [action_id, session_id, json.dumps(messages, ensure_ascii=False, default=str),
         tool_name, json.dumps(args, ensure_ascii=False, default=str), tool_call_id,
         now.isoformat(), (now + timedelta(minutes=PENDING_EXPIRE_MINUTES)).isoformat()])
    conn.commit()
    conn.close()
