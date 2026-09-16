#!/usr/bin/env python3
"""Flask 主应用: 页面路由 + JSON API + SSE流式聊天"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, render_template, request, Response, stream_with_context
from db import (get_db, get_session_detail, get_all_sessions, get_all_exercises,
                 get_latest_cycle, sessions_by_cycle, exercises_map, insert_session,
                 insert_set, insert_cardio)
from analytics import (compute_e1rm_history, compute_volume_trend, compute_acwr,
                        detect_plateaus, compute_adherence, get_deload_triggers,
                        running_economy_trend, weekly_summary)
from agent.config import is_configured as llm_is_configured, mask_key, LLM_MODEL, LLM_BASE_URL
from agent.core import handle_message, cleanup_sessions
import json, os, uuid

app = Flask(__name__)

# ── 页面路由 ──
@app.route("/")
def dashboard():
    return render_template("index.html", page="dashboard")

@app.route("/trends")
def trends():
    return render_template("index.html", page="trends")

@app.route("/review")
def review():
    return render_template("index.html", page="review")

@app.route("/plan")
def plan():
    return render_template("index.html", page="plan")

@app.route("/chat")
def chat_page():
    return render_template("index.html", page="chat",
                           llm_configured=llm_is_configured(),
                           llm_model=LLM_MODEL,
                           llm_key_masked=mask_key(os.environ.get("LLM_API_KEY", "")))

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

@app.route("/api/chat/status")
def api_chat_status():
    return jsonify({
        "configured": llm_is_configured(),
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL
    })

@app.route("/api/chat/history")
def api_chat_history():
    sid = request.args.get('session_id', '')
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id ASC LIMIT 50",
        [sid]
    ).fetchall()
    conn.close()
    return jsonify([{"role": r['role'], "content": r['content']} for r in rows])

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Agent 对话入口 (SSE 流式)"""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message required"}), 400

    user_text = data["message"].strip()
    session_id = data.get("session_id", str(uuid.uuid4())[:12])

    if not user_text:
        return jsonify({"reply": "请告诉我你想咨询什么。"})

    def generate():
        try:
            for sse_event in handle_message(user_text, session_id):
                yield sse_event
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"event: error\ndata: {json.dumps({'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )

@app.route("/api/chat/confirm", methods=["POST"])
def api_chat_confirm():
    """确认操作端点"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400

    action_id = data.get("action_id", "")
    session_id = data.get("session_id", "")
    confirmed = data.get("confirmed", True)

    if not action_id:
        return jsonify({"error": "action_id required"}), 400

    confirm_text = f"/confirm {action_id}" if confirmed else "取消"
    if not confirmed:
        confirm_text = "取消"

    def generate():
        try:
            for sse_event in handle_message(confirm_text, session_id):
                yield sse_event
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"event: error\ndata: {json.dumps({'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )

# ── API ──
@app.route("/api/sessions")
def api_sessions():
    sessions = get_all_sessions()
    return jsonify(sessions)

@app.route("/api/session/<int:sid>")
def api_session_detail(sid):
    det = get_session_detail(sid)
    if not det:
        return jsonify({"error": "not found"}), 404
    return jsonify(det)

@app.route("/api/exercises")
def api_exercises():
    exs = get_all_exercises()
    return jsonify(exs)

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
    e1rm_hist = compute_e1rm_history()
    return jsonify(detect_plateaus(e1rm_hist))

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
        cycles.append(cd)
    conn.close()
    cycles.sort(key=lambda c: (len(c['sessions']) > 0, c['start_date']), reverse=True)
    ws = weekly_summary()
    e1rm_clean = {k: v for k, v in ws['e1rm'].items()}
    return jsonify({
        "e1rm": e1rm_clean,
        "volume": ws['volume'],
        "acwr": ws['acwr'],
        "acwr_history": ws['acwr_history'],
        "plateaus": ws['plateaus'],
        "adherence": ws['adherence'],
        "triggers": ws['triggers'],
        "running_trend": ws['running_trend'],
        "cycles": cycles
    })

@app.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400

    date = data.get("date")
    stype = data.get("type")
    if not date or not stype:
        return jsonify({"error": "date and type required"}), 400

    from db import upsert_session, merge_session_sets, upsert_cardio, exercises_map
    ex_map = exercises_map()

    if not data.get("cycle_id"):
        conn = get_db()
        row = conn.execute("SELECT id FROM cycles WHERE ? BETWEEN start_date AND end_date", [date]).fetchone()
        conn.close()
        if row:
            data["cycle_id"] = row['id']

    sid = upsert_session(
        date, data.get("cycle_id"), data.get("week_no"), data.get("day_no"),
        stype, data.get("sleep_h"), data.get("bodyweight"), data.get("rpe"),
        "done", data.get("notes", "")
    )

    payload = []
    for s in data.get("sets", []):
        eid = ex_map.get(s["exercise"])
        if not eid:
            print(f"WARN: unknown exercise '{s['exercise']}', skipping")
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
        import json as j
        upsert_cardio(sid, c["kind"],
                       planned_json=j.dumps(c.get("planned", {})) if c.get("planned") else None,
                       actual_json=j.dumps(c.get("actual", {})) if c.get("actual") else None,
                       distance_km=c.get("distance_km"), avg_hr=c.get("avg_hr"),
                       max_hr=c.get("max_hr"), duration_s=c.get("duration_s"),
                       avg_pace_sec=c.get("avg_pace_sec"))

    return jsonify({"session_id": sid, "status": "ok"})

@app.route("/api/body-metrics", methods=["POST"])
def api_body_metrics():
    data = request.get_json()
    if not data or not data.get("date"):
        return jsonify({"error": "date required"}), 400
    from db import insert_body_metric
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5200, debug=False)
