"""v2.4: replace_day_plan 整日课表替换"""
import pytest


def _make_planned_day(date_="2032-01-10", type_="pull"):
    from db import upsert_session, insert_set, exercises_map
    ex_map = exercises_map()
    sid = upsert_session(date_, None, None, None, type_, status="planned")
    if "杠铃划船" in ex_map:
        for i in range(1, 4):
            insert_set(sid, ex_map["杠铃划船"], i, planned_kg=40, planned_reps=10, status="planned")
    return sid


def test_replace_day_plan_preview_shows_diff():
    from agent.tools import tool_replace_day_plan
    _make_planned_day()
    r = tool_replace_day_plan(date="2032-01-10", new_type="legs",
                              sets=[{"exercise": "杠铃深蹲", "groups": [{"kg": 60, "reps": 5}]}],
                              notes="腿日", confirmed=False)
    assert r["status"] == "pending"
    p = r["preview"]
    assert p["old"]["type"] == "pull" and any("划船" in x for x in p["old"]["exercises"])
    assert p["new"]["type"] == "legs"
    assert p["new"]["exercises"][0]["exercise"] == "杠铃深蹲"
    assert p["new"]["exercises"][0]["exists_in_db"] is True


def test_replace_day_plan_execute_replaces_all():
    from agent.tools import tool_replace_day_plan
    from db import get_db
    _make_planned_day()
    r = tool_replace_day_plan(date="2032-01-10", new_type="legs",
                              sets=[{"exercise": "杠铃深蹲", "groups": [{"kg": 60, "reps": 5}] * 4}],
                              notes="改成腿", confirmed=True)
    assert r["status"] == "done" and r["new_type"] == "legs"
    conn = get_db()
    s = conn.execute("SELECT type, planned_notes, actual_notes FROM sessions WHERE id=?", [r["session_id"]]).fetchone()
    assert s["type"] == "legs" and s["planned_notes"] == "改成腿"
    assert s["actual_notes"] is None
    names = [row[0] for row in conn.execute("""
        SELECT e.name FROM sets st JOIN exercises e ON st.exercise_id=e.id
        WHERE st.session_id=?""", [r["session_id"]])]
    assert names == ["杠铃深蹲"] * 4  # 拉动作全部清除
    conn.close()
    # 幂等: 重复替换同内容不翻倍
    r2 = tool_replace_day_plan(date="2032-01-10", new_type="legs",
                               sets=[{"exercise": "杠铃深蹲", "groups": [{"kg": 60, "reps": 5}] * 4}],
                               confirmed=True)
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=?", [r2["session_id"]]).fetchone()[0]
    conn.close()
    assert n == 4


def test_replace_day_plan_rejects_done_session():
    from agent.tools import tool_replace_day_plan
    from db import upsert_session
    upsert_session("2032-01-11", None, None, None, "push", status="done")
    r = tool_replace_day_plan(date="2032-01-11", new_type="legs", sets=[], confirmed=True)
    assert "error" in r and "planned" in r["error"]


def test_replace_day_plan_validates_type():
    from agent.tools import tool_replace_day_plan
    r = tool_replace_day_plan(date="2032-01-10", new_type="yoga", sets=[])
    assert "new_type" in r["error"]
