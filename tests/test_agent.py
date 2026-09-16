"""Agent 核心单元测试"""
import json
from datetime import datetime


def test_system_prompt_has_today_date():
    from agent.context import build_system_prompt
    p = build_system_prompt()
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in p
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    assert any(w in p for w in weekdays)


def test_system_prompt_compact():
    from agent.context import build_system_prompt
    p = build_system_prompt()
    # 目标 <1200 tokens (中文约1.6字/token)
    assert len(p) < 1200 * 1.6


def test_today_context_view_spec():
    from agent.tools import tool_get_today_context
    r = tool_get_today_context()
    assert r["today"] == datetime.now().strftime("%Y-%m-%d")
    assert r["view_spec"]["view"] == "metric_cards"
    assert isinstance(r["view_spec"]["cards"], list)


def test_exercise_history_exact():
    from agent.tools import tool_get_exercise_history
    r = tool_get_exercise_history("杠铃卧推")
    assert r.get("exercise") == "杠铃卧推"
    assert r["view_spec"]["view"] == "line"
    assert "series" in r["view_spec"]


def test_exercise_history_candidates():
    from agent.tools import tool_get_exercise_history
    r = tool_get_exercise_history("卧推")
    # 多候选时返回候选列表而非报错崩溃
    assert "candidates" in r or r.get("exercise")


def test_analytics_all_kinds():
    from agent.tools import tool_get_analytics
    for kind in ["e1rm", "volume", "acwr", "plateau", "adherence", "deload", "running"]:
        r = tool_get_analytics(kind, weeks=12)
        assert "view_spec" in r, kind
        assert r["view_spec"]["view"] in ("line", "bar", "table"), kind


def test_analytics_invalid_kind():
    from agent.tools import tool_get_analytics
    r = tool_get_analytics("hack")
    assert "error" in r


def test_get_plan():
    from agent.tools import tool_get_plan
    r = tool_get_plan()
    assert "cycle" in r or "error" in r


def test_log_training_sets_none_no_crash():
    from agent.tools import tool_log_training
    r = tool_log_training(date="2026-01-01", stype="legs", sets=None, confirmed=False)
    assert r["status"] == "pending"
    assert "preview" in r


def test_pending_state_cross_process_simulation():
    """模拟多 worker: 两次独立调用 store/get (各自新连接)"""
    from agent.tools import store_pending, get_pending, peek_pending_session
    store_pending("aid-1", "log_body_metric", {"date": "2026-01-01"},
                  "sess-multi", "tc-1",
                  {"messages": [{"role": "user", "content": "hi"}], "tool_calls_log": []})
    assert peek_pending_session("sess-multi") is True
    p = get_pending("aid-1", "sess-multi")
    assert p is not None
    assert p["tool_name"] == "log_body_metric"
    assert p["args"] == {"date": "2026-01-01"}
    assert p["messages"]["messages"][0]["content"] == "hi"
    # 已取出后不再存在
    assert get_pending("aid-1", "sess-multi") is None


def test_pending_expiry():
    from agent.tools import store_pending, get_pending
    from datetime import datetime, timedelta
    store_pending("aid-exp", "log_body_metric", {}, "sess-exp", "tc", {"messages": [], "tool_calls_log": []})
    # 手动改过期时间
    from db import get_db
    conn = get_db()
    conn.execute("UPDATE agent_pending_actions SET expires_at=? WHERE action_id=?",
                 [(datetime.now() - timedelta(minutes=1)).isoformat(), "aid-exp"])
    conn.commit(); conn.close()
    assert get_pending("aid-exp", "sess-exp") is None


def test_sse_format():
    from agent.core import _sse
    out = _sse("delta", {"text": "你好"})
    assert out.startswith("event: delta\ndata: ")
    assert out.endswith("\n\n")
    payload = out.split("data: ", 1)[1].strip()
    assert json.loads(payload) == {"text": "你好"}


def test_tool_schemas_valid():
    from agent.tools import TOOL_SCHEMAS, TOOL_MAP
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert names == set(TOOL_MAP.keys())
    for s in TOOL_SCHEMAS:
        assert s["type"] == "function"
        assert s["function"]["parameters"]["type"] == "object"
