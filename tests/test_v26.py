from datetime import date

from db import get_db, upsert_session


def test_week_completion_separates_actual_records_from_plan_adherence(monkeypatch):
    from agent.tools import tool_get_today_context

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 17)

    monkeypatch.setattr("agent.tools.date", FixedDate)
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE date BETWEEN '2026-09-11' AND '2026-09-17'")
    conn.commit()
    conn.close()

    # 休息日不进入实际完成/计划依从分母。
    upsert_session("2026-09-11", None, 1, 7, "rest", status="rest")
    # 周期计划：2课完成、1课未完成。
    upsert_session("2026-09-12", None, 1, 2, "lsd", status="done")
    upsert_session("2026-09-13", None, 1, 3, "interval", status="planned")
    upsert_session("2026-09-14", None, 1, 4, "relax", status="done")
    # 同日替代/补录：计入实际完成，但不冲抵原 interval 计划。
    upsert_session("2026-09-13", None, None, None, "pull", status="done")

    result = tool_get_today_context()
    completion = result["week_completion"]
    assert completion["actual_completed"] == 3
    assert completion["active_days"] == 3
    assert completion["scheduled_due"] == 3
    assert completion["scheduled_completed"] == 2

    cards = {c["label"]: c for c in result["view_spec"]["cards"]}
    assert cards["近7天实际完成"]["value"] == 3
    assert "3个训练日" in cards["近7天实际完成"]["sub"]
    assert cards["计划依从"]["value"] == "2/3"
    assert "替代训练不计为原计划完成" in cards["计划依从"]["sub"]
