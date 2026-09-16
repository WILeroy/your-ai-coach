"""Agent 工具层: 1个开放SQL读工具 + 9个安全写工具(含确认机制)"""
import sys, os, json, uuid, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import (get_db, get_session_detail, exercises_map, upsert_session,
                 merge_session_sets, upsert_cardio, get_latest_cycle)
from analytics import (compute_e1rm_history, compute_acwr, compute_adherence,
                        get_deload_triggers, weekly_summary, epley_e1rm)
from agent.safety import validate_select_sql, add_limit

# ── 确认状态管理(内存) ──
_pending_actions = {}

def store_pending(action_id, tool_name, args, context):
    _pending_actions[action_id] = {
        "tool_name": tool_name, "args": args, "context": context,
        "created_at": time.time()
    }

def get_pending(action_id):
    return _pending_actions.pop(action_id, None)

def cleanup_expired(max_age=300):
    now = time.time()
    expired = [k for k, v in _pending_actions.items() if now - v["created_at"] > max_age]
    for k in expired:
        del _pending_actions[k]


# ═══════════════════════════════════════════════
# 读工具
# ═══════════════════════════════════════════════

def tool_db_query(query, limit=None, **kwargs):
    """执行只读 SQL SELECT 查询"""
    ok, err = validate_select_sql(query)
    if not ok:
        return {"error": err, "hint": "仅允许 SELECT 查询，如需要写入请使用对应工具"}

    limit = limit or 50
    limit = min(max(limit, 1), 200)
    query = add_limit(query, default_limit=limit, max_limit=200)

    try:
        conn = get_db()
        cur = conn.execute(query)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows_raw = cur.fetchall()
        if not rows_raw:
            conn.close()
            return {"columns": columns, "rows": [], "row_count": 0, "truncated": False,
                    "query": query[:200]}

        rows = [dict(zip(columns, row)) for row in rows_raw]

        # 截断过长结果
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]

        # 移除内部字段
        for row in rows:
            for k in list(row.keys()):
                if k.startswith('_'):
                    del row[k]

        conn.close()
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "query": query[:200]
        }
    except Exception as e:
        return {"error": str(e), "hint": "SQL 执行出错，请检查语法"}


def tool_get_db_schema(**kwargs):
    """获取数据库完整 schema 供 LLM 理解数据结构"""
    conn = get_db()
    tables = []
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for tr in table_rows:
        tname = tr['name']
        cols = conn.execute(f"PRAGMA table_info('{tname}')").fetchall()
        columns = [{"name": c['name'], "type": c['type'],
                     "pk": bool(c['pk']), "notnull": bool(c['notnull'])} for c in cols]
        indexes = conn.execute(f"PRAGMA index_list('{tname}')").fetchall()
        idx_list = [{"name": i['name'], "unique": bool(i['unique'])} for i in indexes]
        tables.append({"name": tname, "columns": columns, "indexes": idx_list})

    # 元数据摘要
    stats = {
        "exercises": conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0],
        "cycles": conn.execute("SELECT COUNT(*) FROM cycles").fetchone()[0],
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "sets": conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0],
        "cardio": conn.execute("SELECT COUNT(*) FROM cardio").fetchone()[0],
        "body_metrics": conn.execute("SELECT COUNT(*) FROM body_metrics").fetchone()[0],
    }
    conn.close()
    return {"tables": tables, "stats": stats}


# ═══════════════════════════════════════════════
# 写工具 (含确认机制)
# ═══════════════════════════════════════════════

def _preview_or_execute(confirmed, build_preview, execute):
    """通用确认流程: 未确认返回预览，已确认执行"""
    if not confirmed:
        return {"status": "pending", "action_id": str(uuid.uuid4())[:8],
                "preview": build_preview()}
    try:
        result = execute()
        return {"status": "done", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def tool_log_training(date, stype, sets=None, cardio=None, rpe=None,
                      sleep_h=None, bodyweight=None, notes=None,
                      confirmed=False, **kwargs):
    """记录一次训练完成情况(需确认)"""
    sets = sets or []
    cardio = cardio or {}

    def preview():
        p = {"date": date, "type": stype, "rpe": rpe, "sleep_h": sleep_h,
             "bodyweight": bodyweight, "notes": notes or ""}
        p["sets_summary"] = []
        ex_map = exercises_map()
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            kgs = []
            groups = s.get("groups") or ([s] if "kg" in s else [])
            for g in groups:
                kgs.append(f"{g.get('kg','?')}kg×{g.get('reps','?')}")
            p["sets_summary"].append({
                "exercise": ename,
                "groups": kgs,
                "exists_in_db": ename in ex_map if ename else False
            })
        if cardio and cardio.get("kind"):
            p["cardio_summary"] = {"kind": cardio["kind"],
                                    "distance_km": cardio.get("distance_km"),
                                    "details": cardio.get("actual") and "有详细数据" or "无"}
        return p

    def execute():
        from datetime import datetime as dt
        cycle = _detect_cycle(date)
        cid = cycle['id'] if cycle else None
        sid = upsert_session(date, cid, week_no=None, day_no=None, type_=stype,
                             sleep_h=sleep_h, bodyweight=bodyweight, rpe=rpe,
                             status="done", notes=notes or "")
        ex_map = exercises_map()
        logged = []
        payload = []
        for s in sets:
            ename = s.get("exercise", s.get("name", ""))
            if not ename or ename not in ex_map:
                logged.append({"error": f"动作 '{ename}' 不存在，已跳过"})
                continue
            groups = s.get("groups")
            if not groups and "kg" in s:
                groups = [s]
            if not groups:
                continue
            payload.append({"exercise_id": ex_map[ename], "groups": groups})
            for g in groups:
                logged.append({"exercise": ename, "kg": g.get("kg"), "reps": g.get("reps")})
        if payload:
            merge_session_sets(sid, payload)
        if cardio and cardio.get("kind") and (cardio.get("distance_km") or cardio.get("actual")):
            import json as j
            upsert_cardio(sid, cardio["kind"],
                          planned_json=j.dumps(cardio.get("planned", {})) if cardio.get("planned") else None,
                          actual_json=j.dumps(cardio.get("actual", {})) if cardio.get("actual") else None,
                          distance_km=cardio.get("distance_km"),
                          avg_hr=cardio.get("avg_hr"), avg_pace_sec=cardio.get("avg_pace_sec"))
        return {"session_id": sid, "date": date, "type": stype,
                "logged_sets": logged, "cardio_logged": bool(cardio.get("kind"))}

    return _preview_or_execute(confirmed, preview, execute)


def tool_update_session(session_id, changes=None, confirmed=False, **kwargs):
    """修改已记录的训练数据(需确认)"""
    changes = changes or {}

    def preview():
        old = get_session_detail(int(session_id))
        if not old:
            return {"error": f"session {session_id} 不存在"}
        _simplify_session(old)
        preview_data = {"session_id": session_id, "old": old, "changes": changes}
        if "sets" in changes:
            preview_data["sets_count"] = len(changes["sets"])
        if "cardio" in changes:
            preview_data["cardio_change"] = True
        return preview_data

    def execute():
        conn = get_db()
        sid = int(session_id)
        if "date" in changes:
            conn.execute("UPDATE sessions SET date=? WHERE id=?", [changes["date"], sid])
        if "type" in changes:
            conn.execute("UPDATE sessions SET type=? WHERE id=?", [changes["type"], sid])
        if "rpe" in changes:
            conn.execute("UPDATE sessions SET rpe=? WHERE id=?", [float(changes["rpe"]), sid])
        if "sleep_h" in changes:
            conn.execute("UPDATE sessions SET sleep_h=? WHERE id=?", [float(changes["sleep_h"]), sid])
        if "bodyweight" in changes:
            conn.execute("UPDATE sessions SET bodyweight=? WHERE id=?", [float(changes["bodyweight"]), sid])
        if "notes" in changes:
            conn.execute("UPDATE sessions SET notes=? WHERE id=?", [str(changes["notes"]), sid])
        conn.commit()
        conn.close()
        return {"session_id": session_id, "updated": list(changes.keys())}

    return _preview_or_execute(confirmed, preview, execute)


def tool_delete_data(target, target_id, confirmed=False, **kwargs):
    """删除训练数据(需确认)"""
    valid = {"session", "set", "cardio", "exercise"}
    if target not in valid:
        return {"error": f"不可删除的目标类型: {target}，可选: {valid}"}

    def preview():
        conn = get_db()
        if target == "session":
            row = conn.execute("SELECT id, date, type, status FROM sessions WHERE id=?", [target_id]).fetchone()
            conn.close()
            if not row:
                return {"error": f"session {target_id} 不存在"}
            return {"target": "session", "id": target_id, "data": dict(row), "cascade": "同时删除关联的set和cardio记录"}
        elif target == "set":
            row = conn.execute("SELECT st.*, e.name as ex_name FROM sets st JOIN exercises e ON st.exercise_id=e.id WHERE st.id=?", [target_id]).fetchone()
            conn.close()
            if not row:
                return {"error": f"set {target_id} 不存在"}
            return {"target": "set", "id": target_id, "data": dict(row)}
        elif target == "cardio":
            row = conn.execute("SELECT * FROM cardio WHERE id=?", [target_id]).fetchone()
            conn.close()
            if not row:
                return {"error": f"cardio {target_id} 不存在"}
            return {"target": "cardio", "id": target_id, "data": dict(row)}
        elif target == "exercise":
            row = conn.execute("SELECT * FROM exercises WHERE id=?", [target_id]).fetchone()
            conn.close()
            if not row:
                return {"error": f"exercise {target_id} 不存在"}
            return {"target": "exercise", "id": target_id, "data": dict(row), "warning": "删除动作会级联删除所有关联set记录"}

    def execute():
        conn = get_db()
        if target == "session":
            conn.execute("DELETE FROM sessions WHERE id=?", [target_id])
        elif target == "set":
            conn.execute("DELETE FROM sets WHERE id=?", [target_id])
        elif target == "cardio":
            conn.execute("DELETE FROM cardio WHERE id=?", [target_id])
        elif target == "exercise":
            conn.execute("DELETE FROM exercises WHERE id=?", [target_id])
        conn.commit()
        conn.close()
        return {"deleted": target, "id": target_id}

    return _preview_or_execute(confirmed, preview, execute)


def tool_adjust_plan(date, changes, confirmed=False, **kwargs):
    """调整未来某天训练计划(需确认)"""
    changes = changes or []

    def preview():
        conn = get_db()
        row = conn.execute("SELECT id, date, type, status FROM sessions WHERE date=? AND status='planned'", [date]).fetchone()
        ex_map = exercises_map()
        if not row:
            conn.close()
            return {"error": f"{date} 没有可调整的计划"}

        # 显示当前计划
        cur_sets = conn.execute(
            "SELECT st.*, e.name as ename FROM sets st JOIN exercises e ON st.exercise_id=e.id WHERE st.session_id=? ORDER BY st.set_no",
            [row['id']]).fetchall()
        current = [{"exercise": s['ename'], "set_no": s['set_no'],
                     "planned_kg": s['planned_kg'], "planned_reps": s['planned_reps']} for s in cur_sets]
        conn.close()

        planned_changes = []
        for ch in changes:
            ename = ch.get("exercise")
            groups = ch.get("groups", [ch])
            planned_changes.append({
                "exercise": ename,
                "exists": ename in ex_map,
                "groups": [{"kg": g.get("kg"), "reps": g.get("reps")} for g in groups]
            })
        return {"date": date, "current_plan": current, "new_plan": planned_changes}

    def execute():
        conn = get_db()
        row = conn.execute("SELECT id FROM sessions WHERE date=? AND status='planned'", [date]).fetchone()
        if not row:
            conn.close()
            return {"error": f"{date} 没有可调整的计划"}
        sid = row['id']
        ex_map = exercises_map()
        adjusted = []
        for ch in changes:
            ename = ch.get("exercise")
            if ename not in ex_map:
                continue
            eid = ex_map[ename]
            conn.execute("DELETE FROM sets WHERE session_id=? AND exercise_id=?", [sid, eid])
            for i, g in enumerate(ch.get("groups", [ch]), 1):
                conn.execute(
                    "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
                    [sid, eid, i, g.get("kg"), g.get("reps")])
                adjusted.append({"exercise": ename, "kg": g.get("kg"), "reps": g.get("reps"), "set": i})
        conn.commit()
        conn.close()
        return {"date": date, "adjusted": adjusted}

    return _preview_or_execute(confirmed, preview, execute)


def tool_generate_cycle(start_date, weeks=4, template="standard", confirmed=False, **kwargs):
    """生成训练周期计划(需确认落库)"""
    from datetime import datetime, timedelta
    from planner import build_cycle_template

    def preview():
        days = build_cycle_template(start_date, int(weeks), template)
        # 按周分组预览
        weeks_preview = {}
        for d in days:
            wk = d.get('week_no', 1)
            if wk not in weeks_preview:
                weeks_preview[wk] = []
            weeks_preview[wk].append({"date": d['date'], "type": d['type'], "phase": d['phase']})
        return {"start_date": start_date, "weeks": weeks, "template": template,
                "days_count": len(days), "schedule": weeks_preview}

    def execute():
        from db import insert_session, insert_cycle
        days = build_cycle_template(start_date, int(weeks), template)
        if not days:
            return {"error": "生成计划为空"}
        end_date = days[-1]['date']
        cid = insert_cycle(start_date, end_date, template, '[]', '')
        ex_map = exercises_map()
        for d in days:
            sid = insert_session(d['date'], cid, d.get('week_no'), d.get('day_no'),
                                 d['type'], status='planned')
            if d['type'] in ('push', 'pull', 'legs'):
                _create_stock_sets(sid, d['type'], ex_map)
        return {"cycle_id": cid, "start_date": start_date, "end_date": end_date,
                "days_count": len(days), "weeks": weeks}

    return _preview_or_execute(confirmed, preview, execute)


def _create_stock_sets(session_id, session_type, ex_map):
    """为训练日创建默认动作组"""
    stock = {
        "push": ["杠铃卧推", "哑铃推举", "哑铃飞鸟", "三头臂屈伸"],
        "pull": ["传统硬拉", "杠铃划船", "引体向上", "哑铃弯举"],
        "legs": ["杠铃深蹲", "罗马尼亚硬拉", "保加利亚分腿蹲", "腿弯举"],
    }
    exercises = stock.get(session_type, [])
    conn = get_db()
    for i, ename in enumerate(exercises, 1):
        eid = ex_map.get(ename)
        if not eid:
            continue
        conn.execute(
            "INSERT INTO sets(session_id,exercise_id,set_no,planned_kg,planned_reps,status) VALUES(?,?,?,?,?,'planned')",
            [session_id, eid, i, None, None])
    conn.commit()
    conn.close()


def tool_manage_exercises(action, name, pattern=None, equipment=None, is_main=0,
                          notes=None, confirmed=False, **kwargs):
    """管理动作库: 添加/更新动作(需确认)"""
    if action not in ("add", "update"):
        return {"error": "action 必须是 add 或 update"}

    def preview():
        ex_map = exercises_map()
        existing = name in ex_map
        if action == "add" and existing:
            return {"error": f"动作 '{name}' 已存在，请用 update"}
        if action == "update" and not existing:
            return {"error": f"动作 '{name}' 不存在，请用 add"}
        return {"action": action, "name": name, "pattern": pattern, "equipment": equipment,
                "is_main": bool(is_main), "notes": notes}

    def execute():
        if action == "add":
            conn = get_db()
            conn.execute(
                "INSERT INTO exercises(name,pattern,equipment,is_main,notes) VALUES(?,?,?,?,?)",
                [name, pattern or "accessory", equipment, int(is_main) if is_main else 0, notes or ""])
            conn.commit()
            eid = conn.execute("SELECT id FROM exercises WHERE name=?", [name]).fetchone()['id']
            conn.close()
            return {"action": "add", "name": name, "id": eid}
        else:
            conn = get_db()
            sets = []
            vals = []
            if pattern:
                sets.append("pattern=?")
                vals.append(pattern)
            if equipment:
                sets.append("equipment=?")
                vals.append(equipment)
            if is_main is not None:
                sets.append("is_main=?")
                vals.append(int(is_main))
            if notes is not None:
                sets.append("notes=?")
                vals.append(notes)
            if sets:
                vals.append(name)
                conn.execute(f"UPDATE exercises SET {', '.join(sets)} WHERE name=?", vals)
                conn.commit()
            conn.close()
            return {"action": "update", "name": name, "updated_fields": [s.split("=")[0] for s in sets]}

    return _preview_or_execute(confirmed, preview, execute)


def tool_manage_profile(key, value, confirmed=False, **kwargs):
    """修改用户档案/配置(需确认)"""
    if not key:
        return {"error": "key 不能为空"}

    def preview():
        conn = get_db()
        row = conn.execute("SELECT value FROM profile WHERE key=?", [key]).fetchone()
        old_val = row['value'] if row else None
        new_val = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        conn.close()
        return {"key": key, "old_value": old_val, "new_value": str(value)}

    def execute():
        conn = get_db()
        val_str = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        conn.execute("INSERT OR REPLACE INTO profile(key, value) VALUES(?,?)", [key, val_str])
        conn.commit()
        conn.close()
        return {"key": key, "value": str(value)}

    return _preview_or_execute(confirmed, preview, execute)


def tool_log_body_metric(date, weight=None, sleep_h=None, resting_hr=None,
                          notes="", confirmed=False, **kwargs):
    """记录身体指标(需确认)"""
    def preview():
        return {"date": date, "weight": weight, "sleep_h": sleep_h,
                "resting_hr": resting_hr, "notes": notes}

    def execute():
        from db import insert_body_metric
        insert_body_metric(date, weight, sleep_h, resting_hr, notes)
        return {"date": date, "recorded": True}

    return _preview_or_execute(confirmed, preview, execute)


# ═══════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════

def _detect_cycle(date):
    conn = get_db()
    row = conn.execute("SELECT * FROM cycles WHERE ? BETWEEN start_date AND end_date", [date]).fetchone()
    conn.close()
    return dict(row) if row else None

def _simplify_session(s):
    return {
        "id": s.get('id'), "date": s.get('date'), "type": s.get('type'),
        "status": s.get('status'), "rpe": s.get('rpe'), "notes": s.get('notes'),
        "sets": [{"exercise": st.get('exercise_name'), "planned_kg": st.get('planned_kg'),
                   "planned_reps": st.get('planned_reps'), "status": st.get('status')}
                  for st in (s.get('sets') or [])],
        "cardio": s.get('cardio')
    }


# ═══════════════════════════════════════════════
# OpenAI function schemas
# ═══════════════════════════════════════════════

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "执行只读SQL SELECT查询，自由检索训练数据。可查询所有表：sessions(训练课), sets(组数据), exercises(动作库), cycles(周期), cardio(跑步), body_metrics(身体指标), profile(用户配置)",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "SELECT SQL查询语句，需要用表连接时通过 sessions.id = sets.session_id 和 sets.exercise_id = exercises.id 关联"},
                "limit": {"type": "integer", "description": "返回行数上限，默认50，最大200"}
            }, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_db_schema",
            "description": "获取数据库完整表结构和统计信息",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_training",
            "description": "记录一次训练完成情况。需要用户确认后才执行写入。",
            "parameters": {"type": "object", "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "stype": {"type": "string", "description": "训练类型: legs/push/pull/interval/lsd/relax/rest"},
                "sets": {"type": "array", "items": {"type": "object"}, "description": "训练组数组，每项: {exercise:动作名, groups:[{kg, reps, rpe}]}"},
                "cardio": {"type": "object", "description": "跑步数据 {kind, distance_km, actual:{intervals:[{m, pace_sec}]}}"},
                "rpe": {"type": "number", "description": "整体RPE 1-10"},
                "sleep_h": {"type": "number", "description": "前一晚睡眠小时数"},
                "bodyweight": {"type": "number", "description": "体重kg"},
                "notes": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认，默认false"}
            }, "required": ["date", "stype"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_session",
            "description": "修改已记录的训练数据。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "session_id": {"type": "integer", "description": "要修改的session ID"},
                "changes": {"type": "object", "description": "要修改的字段，如 {rpe:7, notes:'...', sleep_h:7.5}"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["session_id", "changes"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_data",
            "description": "删除训练数据(session/set/cardio/exercise)。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "target": {"type": "string", "description": "目标类型: session/set/cardio/exercise"},
                "target_id": {"type": "integer", "description": "要删除的记录ID"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["target", "target_id"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_plan",
            "description": "调整未来某天的训练计划。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "changes": {"type": "array", "items": {"type": "object"}, "description": "修改列表: [{exercise:动作名, groups:[{kg, reps}]}]"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["date", "changes"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_cycle",
            "description": "生成并落库训练周期计划。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "weeks": {"type": "integer", "description": "周期周数，默认4"},
                "template": {"type": "string", "description": "模板名，默认standard"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["start_date"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_exercises",
            "description": "管理动作库。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string", "description": "add或update"},
                "name": {"type": "string", "description": "动作名称"},
                "pattern": {"type": "string", "description": "动作模式: squat/hinge/push/pull/core/accessory/stretch/cardio"},
                "equipment": {"type": "string", "description": "器械: barbell/dumbbell/machine/bodyweight/cable"},
                "is_main": {"type": "integer", "description": "是否为三大项核心动作(0/1)"},
                "notes": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["action", "name"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_profile",
            "description": "修改用户档案配置。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "key": {"type": "string", "description": "配置键名"},
                "value": {"type": "string", "description": "配置值(json可自动序列化)"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["key", "value"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_body_metric",
            "description": "记录身体指标(体重/睡眠/晨脉)。需要用户确认后才执行。",
            "parameters": {"type": "object", "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "weight": {"type": "number", "description": "体重kg"},
                "sleep_h": {"type": "number", "description": "睡眠小时数"},
                "resting_hr": {"type": "integer", "description": "静息心率"},
                "notes": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "用户是否已确认"}
            }, "required": ["date"]}
        }
    },
]

TOOL_MAP = {
    "db_query": tool_db_query,
    "get_db_schema": tool_get_db_schema,
    "log_training": tool_log_training,
    "update_session": tool_update_session,
    "delete_data": tool_delete_data,
    "adjust_plan": tool_adjust_plan,
    "generate_cycle": tool_generate_cycle,
    "manage_exercises": tool_manage_exercises,
    "manage_profile": tool_manage_profile,
    "log_body_metric": tool_log_body_metric,
}
