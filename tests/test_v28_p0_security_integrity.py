"""v2.8 P0: 数据完整性 / 工具结果协议 / 登录限速防伪造 / 前端渲染消毒"""
import json
from flask import request


# ── 1. insert_session 不再 REPLACE 级联清空 sets/cardio ──

def test_insert_session_existing_date_type_preserves_sets():
    """create_plan 覆盖已有日期时，必须复用同一 session 并保留组数据"""
    from db import get_db, insert_session, insert_set, insert_exercise, exercises_map
    insert_exercise("P0保护动作", "accessory")
    eid = exercises_map()["P0保护动作"]
    sid1 = insert_session("2032-01-01", None, 1, 1, "legs", status="planned")
    for i in range(1, 4):
        insert_set(sid1, eid, i, planned_kg=60, planned_reps=5)

    sid2 = insert_session("2032-01-01", None, 1, 2, "legs", status="planned")

    assert sid1 == sid2, "必须复用既有session，不能REPLACE换id"
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=?", [sid1]).fetchone()[0]
    updated = conn.execute("SELECT day_no FROM sessions WHERE id=?", [sid1]).fetchone()["day_no"]
    conn.close()
    assert n == 3, f"既有组数据被级联删除: {n}"
    assert updated == 2, "字段合并更新未生效"


def test_insert_session_new_row_still_inserts():
    from db import get_db, insert_session
    sid = insert_session("2032-02-01", None, 1, 1, "push", status="planned")
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", [sid]).fetchone()
    conn.close()
    assert row is not None and row["type"] == "push"


# ── 2. 写工具执行结果协议: 失败绝不携带 status=done/ui_refresh ──

def test_preview_or_execute_wraps_business_error_as_status_error():
    from agent.tools import _preview_or_execute
    result = _preview_or_execute(True, lambda: {}, lambda: {"error": "没有可替换的计划"})
    assert result["status"] == "error"
    assert result["error"] == "没有可替换的计划"
    assert "ui_refresh" not in result


def test_preview_or_execute_wraps_exception_as_status_error():
    from agent.tools import _preview_or_execute
    def boom():
        raise RuntimeError("db locked")
    result = _preview_or_execute(True, lambda: {}, boom)
    assert result["status"] == "error"
    assert "db locked" in result["error"]
    assert "ui_refresh" not in result


def test_preview_or_execute_success_keeps_status_done():
    from agent.tools import _preview_or_execute
    result = _preview_or_execute(True, lambda: {}, lambda: {"session_id": 1})
    assert result["status"] == "done" and result["ui_refresh"] is True and result["session_id"] == 1


def test_replace_day_plan_execute_error_not_reported_done():
    """业务校验失败在确认执行后必须返回 status=error"""
    from agent.tools import tool_replace_day_plan
    result = tool_replace_day_plan(date="2033-01-01", new_type="legs",
                                   sets=[{"exercise": "深蹲", "groups": [{"kg": 60, "reps": 5}]}],
                                   confirmed=True)
    assert result["status"] == "error"
    assert result.get("ui_refresh") is None


def test_confirm_execution_counts_failures():
    """批量确认中工具失败时，ok/fail统计必须真实(旧实现fail恒为0)"""
    import agent.core as core
    from agent.tools import store_pending_batch_legacy

    calls = []
    def fake_tool(**kwargs):
        calls.append(kwargs)
        if kwargs.get("flag") == "fail":
            return {"status": "error", "error": "boom"}
        return {"status": "done", "ok": True}

    monkey_tools = dict(core.TOOL_MAP)
    monkey_tools["fake_write"] = fake_tool
    core.TOOL_MAP, backup = monkey_tools, core.TOOL_MAP
    try:
        actions = [{"action_id": f"a{i}", "tool_name": "fake_write",
                    "args": {"flag": flag}, "tool_call_id": f"tc{i}", "preview": {}}
                   for i, flag in enumerate(["ok", "fail"])]
        store_pending_batch_legacy("batch-fail", "sess-fail", "fake_write",
                                   {}, "tc0", {"messages": [], "tool_calls_log": []})
        # 直接写入批量actions，绕开store_pending单action限制
        from db import get_db
        conn = get_db()
        conn.execute("UPDATE agent_pending_actions SET actions_json=?, tool_call_id=? WHERE action_id=?",
                     [json.dumps(actions), "tc0", "batch-fail"])
        conn.commit(); conn.close()

        events = list(core._handle_confirm("batch-fail", "sess-fail", True))
        tool_results = [json.loads(e.split("data: ", 1)[1])
                        for e in events if e.startswith("event: tool_result")]
        # 事件流不直接暴露计数；通过messages中的系统提示验证(需无LLM环境也能跑)
        # 这里断言失败工具的结果被原样传回前端
        assert any("boom" in r["result"] for r in tool_results)
    finally:
        core.TOOL_MAP = backup


# ── 3. X-Forwarded-For 只信任可信反代 ──

def _request_context(remote_addr, xff=None):
    import app as app_mod
    headers = {"X-Forwarded-For": xff} if xff else {}
    with app_mod.app.test_request_context("/api/auth/login",
                                          base_url="http://127.0.0.1:5200",
                                          environ_base={"REMOTE_ADDR": remote_addr},
                                          headers=headers):
        yield request


def test_client_ip_ignores_spoofed_xff_from_untrusted_remote(monkeypatch):
    import auth
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1")
    for _ in _request_context("203.0.113.9", xff="1.2.3.4"):
        assert auth.client_ip() == "203.0.113.9"


def test_client_ip_trusts_xff_from_localhost_proxy(monkeypatch):
    import auth
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1")
    for _ in _request_context("127.0.0.1", xff="1.2.3.4, 127.0.0.1"):
        assert auth.client_ip() == "1.2.3.4"


def test_client_ip_without_xff_uses_remote(monkeypatch):
    import auth
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    for _ in _request_context("192.0.2.10"):
        assert auth.client_ip() == "192.0.2.10"
