"""v2.1 修复: 批量确认 / 门控加固 / 写入幂等"""
import json
import pytest


# ── 1. 批量 pending: 快照完整性 ──

def test_batch_pending_roundtrip():
    """一轮 3 个并行写工具: store_pending 批量存取，actions 完整"""
    from agent.tools import store_pending, get_pending
    # 模拟 _agent_loop 构造的完整 messages: assistant(3 tool_calls) + 3 个 tool 响应
    tool_calls = [{"id": f"call_{i}", "type": "function",
                   "function": {"name": "manage_exercises", "arguments": "{}"}}
                  for i in range(3)]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "新增3个动作"},
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
    ]
    for i, tc in enumerate(tool_calls):
        messages.append({"role": "tool", "tool_call_id": tc["id"],
                         "content": json.dumps({"status": "pending", "action_id": f"a{i}"})})
    actions = [{"action_id": f"a{i}", "tool_name": "manage_exercises",
                "args": {"action": "add", "name": f"动作{i}"}, "tool_call_id": tc["id"],
                "preview": {"name": f"动作{i}"}}
               for i, tc in enumerate(tool_calls)]

    store_pending("batch-1", "sess-batch", actions,
                  {"messages": messages, "tool_calls_log": []})
    p = get_pending("batch-1", "sess-batch")
    assert p is not None
    assert len(p["actions"]) == 3
    assert [a["tool_name"] for a in p["actions"]] == ["manage_exercises"] * 3
    assert [a["args"]["name"] for a in p["actions"]] == ["动作0", "动作1", "动作2"]
    # 快照完整性: 每个 tool_call 都有对应 tool 消息 (v2.0 的 400 根因)
    msgs = p["messages"]["messages"]
    tc_ids = {tc["id"] for tc in msgs[2]["tool_calls"]}
    tool_msg_ids = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
    assert tc_ids == tool_msg_ids, "快照中存在未响应的 tool_call"


def test_old_format_single_pending_compat():
    """旧格式(无 actions_json)单 action 兼容读取"""
    from db import get_db
    from agent.tools import store_pending_batch_legacy, get_pending

    store_pending_batch_legacy("old-1", "sess-old", "log_body_metric",
                               {"date": "2026-01-01"}, "tc-old",
                               {"messages": [], "tool_calls_log": []})
    p = get_pending("old-1", "sess-old")
    assert p is not None
    assert len(p["actions"]) == 1
    assert p["actions"][0]["tool_name"] == "log_body_metric"
    assert p["actions"][0]["args"] == {"date": "2026-01-01"}


# ── 2. 门控: 模型传 confirmed=true 无效 ──

class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta=None, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, delta=None, finish=None):
        self.choices = [_Choice(delta, finish)]


class _TC:
    def __init__(self, index, id, name, args):
        self.index = index
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": args})()


def _fake_stream_with_tools(tool_specs):
    """构造模拟 OpenAI 流: 每个工具一个 chunk(完整 arguments)"""
    chunks = []
    for i, (name, args) in enumerate(tool_specs):
        chunks.append(_Chunk(delta=_Delta(tool_calls=[_TC(i, f"call_{i}", name, json.dumps(args))])))
    chunks.append(_Chunk(delta=_Delta(content="正在处理"), finish="tool_calls"))
    chunks.append(_Chunk(delta=_Delta(content="完成")))
    return iter(chunks)


def test_model_confirmed_true_is_stripped(monkeypatch):
    """模型在对话轮传 confirmed=true 必须被剥离 -> 仍返回 pending 预览而非执行"""
    import agent.core as core

    events = []
    def fake_stream(messages, tools=None, max_tokens=None):
        return _fake_stream_with_tools([
            ("log_training", {"date": "2030-01-01", "stype": "legs", "sets": [
                {"exercise": "杠铃深蹲", "groups": [{"kg": 60, "reps": 5}]}], "confirmed": True}),
        ])
    monkeypatch.setattr(core, "chat_stream", fake_stream)

    for sse in core._agent_loop(
            [{"role": "user", "content": "记一下"}], [], "sess-gate", "记一下"):
        lines = sse.strip().split("\n")
        ev = next((l[7:] for l in lines if l.startswith("event: ")), "")
        data = next((l[6:] for l in lines if l.startswith("data: ")), "")
        events.append((ev, data))

    # 必须出现 pending_confirm 而非 done 执行
    pend = [d for e, d in events if e == "pending_confirm"]
    assert pend, "confirmed=true 未被剥离，直接执行了写操作"
    payload = json.loads(pend[0])
    assert payload["actions"][0]["tool"] == "log_training"
    # 未真正落库: 2030-01-01 不应有 session
    from db import get_db
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE date='2030-01-01'").fetchone()[0]
    conn.close()
    assert n == 0


def test_parallel_three_writes_single_batch(monkeypatch):
    """3 个并行写工具 -> 一条批量 pending + actions=3 + 快照完整"""
    import agent.core as core

    def fake_stream(messages, tools=None, max_tokens=None):
        return _fake_stream_with_tools([
            ("manage_exercises", {"action": "add", "name": f"测试动作A{i}",
                                  "pattern": "accessory", "confirmed": True})
            for i in range(3)
        ])
    monkeypatch.setattr(core, "chat_stream", fake_stream)

    events = []
    for sse in core._agent_loop(
            [{"role": "user", "content": "新增3个"}], [], "sess-par", "新增3个"):
        lines = sse.strip().split("\n")
        ev = next((l[7:] for l in lines if l.startswith("event: ")), "")
        data = next((l[6:] for l in lines if l.startswith("data: ")), "")
        events.append((ev, data))

    pend = [json.loads(d) for e, d in events if e == "pending_confirm"]
    assert pend and len(pend[0]["actions"]) == 3
    # 快照中每个 tool_call 均有 tool 响应 (v2.0 400 根因回归测试)
    from agent.tools import get_pending, peek_pending_session
    assert peek_pending_session("sess-par") is True
    p = get_pending(session_id="sess-par")
    msgs = p["messages"]["messages"]
    asst = [m for m in msgs if m.get("tool_calls")]
    assert len(asst) == 1 and len(asst[0]["tool_calls"]) == 3
    tc_ids = {tc["id"] for tc in asst[0]["tool_calls"]}
    tool_ids = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
    assert tc_ids == tool_ids
    # 清理测试动作(未确认不会真的加，但保险)
    from db import get_db
    conn = get_db()
    conn.execute("DELETE FROM exercises WHERE name LIKE '测试动作A%'")
    conn.commit(); conn.close()


# ── 3. 执行顺序 ──

def test_execution_order():
    from agent.core import _exec_key
    actions = [
        {"tool_name": "log_training"},
        {"tool_name": "manage_exercises"},
        {"tool_name": "delete_data"},
    ]
    ordered = sorted(enumerate(actions), key=lambda t: (_exec_key(t[1]), t[0]))
    assert [a["tool_name"] for _, a in ordered] == \
        ["manage_exercises", "log_training", "delete_data"]


# ── 4. merge_session_sets 幂等 ──

def test_merge_session_sets_idempotent():
    from db import (get_db, upsert_session, insert_exercise,
                    exercises_map, merge_session_sets)
    insert_exercise("幂等测试动作", "accessory")
    ex_map = exercises_map()
    eid = ex_map["幂等测试动作"]
    sid = upsert_session("2031-01-01", None, None, None, "legs", status="done")

    payload = [{"exercise_id": eid, "groups": [{"kg": 10, "reps": 12} for _ in range(3)]}]
    merge_session_sets(sid, payload)
    conn = get_db()
    n1 = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=? AND exercise_id=?",
                      [sid, eid]).fetchone()[0]
    conn.close()
    assert n1 == 3

    # 重复调用: 新动作(无计划行)不应翻倍
    merge_session_sets(sid, payload)
    conn = get_db()
    n2 = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=? AND exercise_id=?",
                      [sid, eid]).fetchone()[0]
    conn.close()
    assert n2 == 3, f"重复调用后组数翻倍: {n1} -> {n2}"

    # 有计划行的动作: extra 超编组也不翻倍
    from db import insert_set
    sid2 = upsert_session("2031-01-02", None, None, None, "legs", status="done")
    insert_set(sid2, eid, 1, planned_kg=5, planned_reps=5, status="planned")
    payload2 = [{"exercise_id": eid, "groups": [{"kg": 10, "reps": 12} for _ in range(3)]}]
    merge_session_sets(sid2, payload2)  # 3组: 1 planned + 2 extra
    merge_session_sets(sid2, payload2)  # 再来一遍
    conn = get_db()
    n3 = conn.execute("SELECT COUNT(*) FROM sets WHERE session_id=? AND exercise_id=?",
                      [sid2, eid]).fetchone()[0]
    extra = conn.execute(
        "SELECT COUNT(*) FROM sets WHERE session_id=? AND exercise_id=? AND status='extra'",
        [sid2, eid]).fetchone()[0]
    conn.close()
    assert n3 == 3 and extra == 2, f"planned+extra 场景不幂等: total={n3} extra={extra}"
