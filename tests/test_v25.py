from datetime import date, timedelta

from db import (get_db, upsert_session, insert_body_metric, insert_exercise,
                insert_set, merge_session_sets)


def test_exercise_alias_and_fuzzy_resolution():
    from agent.tools import _resolve_exercise

    exact_alias = _resolve_exercise("胸推")
    assert exact_alias["matched"] == "杠铃卧推"
    assert exact_alias["method"] == "alias"

    dumbbell_alias = _resolve_exercise("哑铃胸推")
    assert dumbbell_alias["matched"] == "哑铃卧推"
    assert dumbbell_alias["confidence"] >= 0.78

    unknown = _resolve_exercise("完全不相关动作xyz")
    assert unknown["matched"] is None
    assert unknown["confidence"] < 0.60


def test_log_training_auto_creates_real_new_exercise():
    from agent.tools import tool_log_training

    preview = tool_log_training(
        date="2032-01-01", stype="legs",
        sets=[{"exercise": "壶铃风车测试", "groups": [{"kg": 12, "reps": 8}]}])
    assert preview["status"] == "pending"
    assert preview["preview"]["new_exercises"][0]["name"] == "壶铃风车测试"

    done = tool_log_training(
        date="2032-01-01", stype="legs",
        sets=[{"exercise": "壶铃风车测试", "groups": [{"kg": 12, "reps": 8}]}],
        confirmed=True)
    assert not done.get("skipped_unknown_exercises")
    assert done["created_exercises"][0]["name"] == "壶铃风车测试"

    conn = get_db()
    row = conn.execute("SELECT id FROM exercises WHERE name='壶铃风车测试'").fetchone()
    assert row
    n = conn.execute("""SELECT COUNT(*) FROM sets st
                        JOIN exercises e ON e.id=st.exercise_id
                        JOIN sessions s ON s.id=st.session_id
                        WHERE s.date='2032-01-01' AND e.name='壶铃风车测试'""").fetchone()[0]
    conn.close()
    assert n == 1


def test_replace_day_plan_auto_creates_exercise_without_locking():
    from agent.tools import tool_replace_day_plan

    upsert_session("2032-01-02", None, None, None, "legs", status="planned")
    result = tool_replace_day_plan(
        date="2032-01-02", new_type="push",
        sets=[{"exercise": "绳索胸前推测试", "groups": [{"kg": 25, "reps": 12}]}],
        confirmed=True)
    assert result["created_exercises"][0]["name"] == "绳索胸前推测试"

    conn = get_db()
    n = conn.execute("""SELECT COUNT(*) FROM sets st
                        JOIN exercises e ON e.id=st.exercise_id
                        JOIN sessions s ON s.id=st.session_id
                        WHERE s.date='2032-01-02' AND e.name='绳索胸前推测试'""").fetchone()[0]
    conn.close()
    assert n == 1


def test_manage_exercise_alias_and_body_metric_panels():
    from agent.tools import tool_get_body_metrics, tool_manage_exercises

    preview = tool_manage_exercises(action="alias", name="杠铃胸口推测试",
                                    target="杠铃卧推")
    assert preview["status"] == "pending"
    assert preview["preview"]["target"] == "杠铃卧推"
    done = tool_manage_exercises(action="alias", name="杠铃胸口推测试",
                                 target="杠铃卧推", confirmed=True)
    assert done["created"] is True

    conn = get_db()
    row = conn.execute("""
        SELECT e.name FROM exercise_aliases a JOIN exercises e ON e.id=a.exercise_id
        WHERE a.alias='杠铃胸口推测试' AND a.source='user'""").fetchone()
    conn.close()
    assert row["name"] == "杠铃卧推"

    body = tool_get_body_metrics(days=90)
    assert body["view_spec"]["view"] == "panels"
    assert {p["name"] for p in body["view_spec"]["panels"]} == {"体重", "睡眠", "静息心率", "HRV"}
    assert "单独坐标轴" in body["view_spec"]["note"]


def test_log_body_metric_hrv_and_readiness_signal():
    from agent.tools import tool_get_today_context, tool_log_body_metric

    preview = tool_log_body_metric(date="2033-01-01", hrv_ms=52)
    assert preview["status"] == "pending"
    assert preview["preview"]["result"]["hrv_ms"] == 52
    assert "hrv_ms" in preview["preview"]["changed_fields"]

    done = tool_log_body_metric(date="2033-01-01", hrv_ms=52, confirmed=True)
    assert done["saved"] is True
    conn = get_db()
    row = conn.execute("SELECT hrv_ms FROM body_metrics WHERE date='2033-01-01'").fetchone()
    assert row["hrv_ms"] == 52
    conn.execute("DELETE FROM body_metrics WHERE date='2033-01-01'")
    conn.commit()
    conn.close()

    today = date.today().isoformat()
    conn = get_db()
    conn.execute("DELETE FROM body_metrics WHERE date=?", [today])
    conn.commit()
    conn.close()
    for i in range(1, 5):
        insert_body_metric((date.today() - timedelta(days=i)).isoformat(),
                           hrv_ms=60 + i)
    result = tool_log_body_metric(hrv_ms=45, confirmed=True)
    assert result["saved"] is True

    ctx = tool_get_today_context()
    hrv_card = next(c for c in ctx["view_spec"]["cards"] if c["label"] == "HRV")
    assert hrv_card["value"] == 45
    assert any("HRV" in x and "下降" in x for x in ctx["view_spec"]["readiness_signals"])


def test_same_day_body_metrics_merge_instead_of_replacing():
    insert_body_metric("2033-02-01", weight=72.8)
    insert_body_metric("2033-02-01", sleep_h=7.5)
    insert_body_metric("2033-02-01", resting_hr=55)
    insert_body_metric("2033-02-01", hrv_ms=52)

    conn = get_db()
    row = conn.execute("SELECT weight,sleep_h,resting_hr,hrv_ms FROM body_metrics WHERE date='2033-02-01'").fetchone()
    conn.close()
    assert tuple(row) == (72.8, 7.5, 55, 52)


def test_session_status_and_planned_actual_notes_stay_separate():
    from agent.tools import tool_get_sessions, tool_update_session

    sid = upsert_session("2033-02-02", None, None, None, "legs",
                         status="planned", planned_notes="原计划：深蹲60kg")
    insert_exercise("状态分离测试深蹲", "squat")
    conn = get_db()
    eid = conn.execute("SELECT id FROM exercises WHERE name='状态分离测试深蹲'").fetchone()["id"]
    conn.close()
    insert_set(sid, eid, 1, planned_kg=60, planned_reps=5)
    insert_set(sid, eid, 2, planned_kg=60, planned_reps=5)
    merge_session_sets(sid, [{"exercise_id": eid, "groups": [
        {"kg": 65, "reps": 5}, {"kg": 65, "reps": 4}]}])

    conn = get_db()
    row = conn.execute("SELECT status,planned_notes,actual_notes FROM sessions WHERE id=?", [sid]).fetchone()
    conn.close()
    assert row["status"] == "done"
    assert row["planned_notes"] == "原计划：深蹲60kg"
    assert row["actual_notes"] is None

    result = tool_update_session(sid, {"actual_notes": "最后一组略吃力"}, confirmed=True)
    assert result["status"] == "done"
    rendered = tool_get_sessions(date_from="2033-02-02", date_to="2033-02-02")
    item = rendered["view_spec"]["sessions"][0]
    assert item["status"] == "done" and item["status_label"] == "已完成"
    assert item["planned_notes"] == "原计划：深蹲60kg"
    assert item["actual_notes"] == "最后一组略吃力"
    assert "状态优先由实际组次" in rendered["view_spec"]["note"]
