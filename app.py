#!/usr/bin/env python3
"""Flask 主应用 v2: 口令认证 + Vue SPA 托管 + JSON API + SSE流式聊天"""
import sys, os, json, uuid, secrets
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import (Flask, jsonify, request, Response, stream_with_context,
                   send_from_directory)
from datetime import timedelta

from db import (get_db, get_session_detail, get_all_sessions, get_all_exercises,
                get_latest_cycle, sessions_by_cycle, exercises_map, insert_session,
                insert_set, insert_cardio, init_db, backup_db_if_new_day)
from analytics import (compute_e1rm_history, compute_volume_trend, compute_acwr,
                        detect_plateaus, compute_adherence, get_deload_triggers,
                        running_economy_trend, weekly_summary)
from agent.config import is_configured as llm_is_configured, mask_key, LLM_MODEL, LLM_BASE_URL
from agent.core import handle_message
import auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "web", "dist")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"))


def _load_secret_key():
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

init_db()


# ═══════════════ 认证守卫 ═══════════════

@app.before_request
def auth_guard():
    path = request.path
    # 认证接口与静态资源放行；其余 /api/* 需要登录
    if path.startswith("/api/auth/"):
        return None
    if not path.startswith("/api/"):
        return None  # SPA 壳与静态资源放行(数据全部走 /api)
    if not auth.is_authed():
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    return auth.login_route()


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    return auth.logout_route()


@app.route("/api/auth/check")
def api_auth_check():
    return auth.check_route()


# ═══════════════ Chat / Agent ═══════════════

@app.route("/api/chat/status")
def api_chat_status():
    return jsonify({
        "configured": llm_is_configured(),
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
    })


@app.route("/api/canvas/default")
def api_canvas_default():
    """画布空闲时的默认「今日概览」(纯只读，不过LLM)"""
    from agent.tools import tool_get_today_context, WEEKDAY_CN
    from datetime import date
    ctx = tool_get_today_context()
    views = [ctx["view_spec"]]

    # 今日动作明细表
    rows = []
    for s in ctx.get("today_sessions", []):
        for ex_name, sets in (s.get("exercises") or {}).items():
            for st in sets:
                rows.append({
                    "动作": ex_name, "组": st["set_no"],
                    "计划": st.get("planned") or "—",
                    "实际": st.get("actual") or "—",
                    "状态": st.get("status") or "",
                })
    if rows:
        views.append({"view": "table", "title": "今日训练明细",
                      "columns": [{"key": "动作", "label": "动作"}, {"key": "组", "label": "组"},
                                  {"key": "计划", "label": "计划"}, {"key": "实际", "label": "实际"},
                                  {"key": "状态", "label": "状态"}],
                      "rows": rows})
    else:
        views.append({"view": "table", "title": "今日无训练计划 💤",
                      "columns": [{"key": "提示", "label": "提示"}],
                      "rows": [{"提示": "今天没有安排训练，好好休息"}]})
    return jsonify({"views": views, "today": ctx["today"], "weekday": ctx["weekday"]})


@app.route("/api/chat/sessions")
def api_chat_sessions():
    conn = get_db()
    rows = conn.execute("""
        SELECT c1.session_id,
               MAX(c1.created_at) as last_at,
               COUNT(*) as msg_count,
               (SELECT c2.content FROM chat_messages c2
                WHERE c2.session_id = c1.session_id AND c2.role='user'
                ORDER BY c2.id LIMIT 1) as title
        FROM chat_messages c1
        WHERE c1.session_id != ''
        GROUP BY c1.session_id
        ORDER BY last_at DESC LIMIT 30
    """).fetchall()
    conn.close()
    return jsonify([{"session_id": r["session_id"],
                     "title": (r["title"] or "")[:40],
                     "last_at": r["last_at"],
                     "msg_count": r["msg_count"]} for r in rows])


@app.route("/api/chat/history")
def api_chat_history():
    sid = request.args.get('session_id', '')
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id=? AND role IN ('user','assistant') AND content != '(空)' ORDER BY id ASC LIMIT 100",
        [sid]).fetchall()
    conn.close()
    return jsonify([{"role": r['role'], "content": r['content']} for r in rows])


@app.route("/api/chat/sessions/<sid>", methods=["DELETE"])
def api_chat_delete_session(sid):
    """删除单个会话(消息+关联pending)"""
    conn = get_db()
    n1 = conn.execute("DELETE FROM chat_messages WHERE session_id=?", [sid]).rowcount
    conn.execute("DELETE FROM agent_pending_actions WHERE session_id=?", [sid])
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted_messages": n1})


@app.route("/api/chat/sessions", methods=["DELETE"])
def api_chat_delete_all_sessions():
    """清空全部会话"""
    conn = get_db()
    n1 = conn.execute("DELETE FROM chat_messages").rowcount
    conn.execute("DELETE FROM agent_pending_actions")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted_messages": n1})


def _sse_response(gen):
    return Response(
        stream_with_context(gen),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Agent 对话入口 (SSE 流式)"""
    data = request.get_json(silent=True)
    if not data or not data.get("message"):
        return jsonify({"error": "message required"}), 400

    user_text = str(data["message"]).strip()
    session_id = data.get("session_id") or uuid.uuid4().hex[:12]

    if not user_text:
        return jsonify({"error": "message required"}), 400

    def generate():
        try:
            # 首个事件回传 session_id，前端持久化
            yield "event: session\ndata: %s\n\n" % json.dumps({"session_id": session_id},
                                                               ensure_ascii=False)
            for sse_event in handle_message(user_text, session_id):
                yield sse_event
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield "event: error\ndata: %s\n\n" % json.dumps({"message": str(e)[:200]},
                                                             ensure_ascii=False)

    return _sse_response(generate())


@app.route("/api/chat/confirm", methods=["POST"])
def api_chat_confirm():
    """确认操作入口 (SSE 流式)"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no data"}), 400
    action_id = data.get("action_id", "")
    session_id = data.get("session_id", "")
    confirmed = data.get("confirmed", True)
    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    confirm_text = ("/confirm %s" % action_id) if confirmed else "取消"

    def generate():
        try:
            for sse_event in handle_message(confirm_text, session_id):
                yield sse_event
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield "event: error\ndata: %s\n\n" % json.dumps({"message": str(e)[:200]},
                                                             ensure_ascii=False)

    return _sse_response(generate())


# ═══════════════ 数据 API ═══════════════

@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_all_sessions())


@app.route("/api/session/<int:sid>")
def api_session_detail(sid):
    det = get_session_detail(sid)
    if not det:
        return jsonify({"error": "not found"}), 404
    return jsonify(det)


@app.route("/api/exercises")
def api_exercises():
    return jsonify(get_all_exercises())


@app.route("/api/cycles")
def api_cycles():
    conn = get_db()
    rows = conn.execute("SELECT * FROM cycles ORDER BY start_date DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/cycle/<int:cid>/sessions")
def api_cycle_sessions(cid):
    sessions = sessions_by_cycle(cid)
    for s in sessions:
        s.pop('id', None)
    return jsonify(sessions)


@app.route("/api/e1rm")
def api_e1rm():
    return jsonify(compute_e1rm_history())


@app.route("/api/volume")
def api_volume():
    return jsonify(compute_volume_trend())


@app.route("/api/acwr")
def api_acwr():
    return jsonify(compute_acwr())


@app.route("/api/plateaus")
def api_plateaus():
    return jsonify(detect_plateaus(compute_e1rm_history()))


@app.route("/api/adherence")
def api_adherence():
    return jsonify(compute_adherence())


@app.route("/api/triggers")
def api_triggers():
    return jsonify(get_deload_triggers())


@app.route("/api/summary")
def api_summary():
    cycles = []
    conn = get_db()
    crows = conn.execute("SELECT id, start_date, end_date, phase, goals_json FROM cycles ORDER BY start_date DESC").fetchall()
    for c in crows:
        cd = dict(c)
        cd['sessions'] = sessions_by_cycle(cd['id'])
        cd['goals'] = json.loads(cd['goals_json']) if cd['goals_json'] else []
        cd.pop('goals_json', None)
        cycles.append(cd)
    conn.close()
    cycles.sort(key=lambda c: (len(c['sessions']) > 0, c['start_date']), reverse=True)
    ws = weekly_summary()
    return jsonify({
        "e1rm": ws['e1rm'],
        "volume": ws['volume'],
        "acwr": ws['acwr'],
        "acwr_history": ws['acwr_history'],
        "plateaus": ws['plateaus'],
        "adherence": ws['adherence'],
        "triggers": ws['triggers'],
        "running_trend": ws['running_trend'],
        "cycles": cycles,
    })


@app.route("/api/review")
def api_review():
    from analytics import compute_week_review
    week = request.args.get("week")
    try:
        return jsonify(compute_week_review(week))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no data"}), 400
    date = data.get("date")
    stype = data.get("type")
    if not date or not stype:
        return jsonify({"error": "date and type required"}), 400

    from db import upsert_session, merge_session_sets, upsert_cardio
    ex_map = exercises_map()
    backup_db_if_new_day()

    if not data.get("cycle_id"):
        conn = get_db()
        row = conn.execute("SELECT id FROM cycles WHERE ? BETWEEN start_date AND end_date", [date]).fetchone()
        conn.close()
        if row:
            data["cycle_id"] = row['id']

    sid = upsert_session(
        date, data.get("cycle_id"), data.get("week_no"), data.get("day_no"),
        stype, data.get("sleep_h"), data.get("bodyweight"), data.get("rpe"),
        "done", data.get("notes", ""))

    payload = []
    for s in data.get("sets", []):
        eid = ex_map.get(s["exercise"])
        if not eid:
            continue
        groups = s.get("groups")
        if not groups and "kg" in s:
            groups = [s]
        if groups:
            payload.append({"exercise_id": eid, "groups": groups})
    if payload:
        merge_session_sets(sid, payload)

    if data.get("cardio"):
        c = data["cardio"]
        upsert_cardio(sid, c["kind"],
                      planned_json=json.dumps(c.get("planned", {}), ensure_ascii=False) if c.get("planned") else None,
                      actual_json=json.dumps(c.get("actual", {}), ensure_ascii=False) if c.get("actual") else None,
                      distance_km=c.get("distance_km"), avg_hr=c.get("avg_hr"),
                      max_hr=c.get("max_hr"), duration_s=c.get("duration_s"),
                      avg_pace_sec=c.get("avg_pace_sec"))

    return jsonify({"session_id": sid, "status": "ok"})


@app.route("/api/body-metrics", methods=["POST"])
def api_body_metrics():
    data = request.get_json(silent=True)
    if not data or not data.get("date"):
        return jsonify({"error": "date required"}), 400
    from db import insert_body_metric
    backup_db_if_new_day()
    insert_body_metric(data["date"], data.get("weight"), data.get("sleep_h"),
                       data.get("resting_hr"), data.get("notes", ""))
    return jsonify({"status": "ok"})


@app.route("/api/db-status")
def api_db_status():
    conn = get_db()
    ex = conn.execute("SELECT count(*) FROM exercises").fetchone()[0]
    cy = conn.execute("SELECT count(*) FROM cycles").fetchone()[0]
    se = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    st = conn.execute("SELECT count(*) FROM sets").fetchone()[0]
    ca = conn.execute("SELECT count(*) FROM cardio").fetchone()[0]
    conn.close()
    return jsonify({"exercises": ex, "cycles": cy, "sessions": se, "sets": st, "cardio": ca})


# ═══════════════ SPA 托管 (web/dist) ═══════════════

@app.route("/")
def spa_index():
    if os.path.exists(os.path.join(DIST_DIR, "index.html")):
        return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"error": "frontend not built", "hint": "cd web && npm install && npm run build"}), 503


@app.route("/assets/<path:filename>")
def spa_assets(filename):
    return send_from_directory(os.path.join(DIST_DIR, "assets"), filename)


@app.route("/favicon.ico")
def spa_favicon():
    path = os.path.join(DIST_DIR, "favicon.ico")
    if os.path.exists(path):
        return send_from_directory(DIST_DIR, "favicon.ico")
    return "", 404


@app.errorhandler(404)
def spa_fallback(e):
    """非 API 路径回退到 SPA (客户端路由)"""
    if not request.path.startswith("/api/") and not request.path.startswith("/static/"):
        if os.path.exists(os.path.join(DIST_DIR, "index.html")):
            return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    # 启动时 LLM 连通性检查(仅本地 dev)
    from agent.llm import check_connectivity
    ok, msg = check_connectivity()
    if not ok:
        print("[WARN] LLM 连通性检查失败:", msg)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5200)), debug=False)
