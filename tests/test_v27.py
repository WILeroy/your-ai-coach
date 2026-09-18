import json
import time


def test_fallback_compresses_tool_context_instead_of_leaking_json(monkeypatch):
    import agent.core as core

    calls = []

    class Resp:
        def __init__(self, content):
            self.choices = [type("Choice", (), {
                "message": type("Message", (), {"content": content})()
            })()]

    def fake_chat(messages, tools=None, max_tokens=None, tool_choice=None):
        calls.append(messages)
        if len(calls) < 3:
            return Resp("")
        assert not any(m.get("role") == "tool" for m in messages)
        prompt = messages[-1]["content"]
        assert "怎样调整训练" in prompt
        assert "view_spec" not in prompt
        return Resp("已根据周期数据给出调整建议")

    monkeypatch.setattr(core, "chat", fake_chat)
    result = core._summarize_fallback(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "怎样调整训练"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "tc1", "type": "function",
                 "function": {"name": "get_plan", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc1", "content": json.dumps({
                "cycle": {"id": 5}, "view_spec": {"view": "table", "rows": []}
            }, ensure_ascii=False)},
        ],
        [{"tool": "get_plan", "args": {}, "result_preview": "{}"}])
    assert result == "已根据周期数据给出调整建议"
    assert len(calls) == 3


def test_sse_heartbeat_and_background_completion():
    import app as app_mod

    events = []

    def slow_source():
        events.append("start")
        yield "event: session\ndata: %s\n\n" % json.dumps({"session_id": "s1"})
        time.sleep(0.04)
        yield "event: done\ndata: %s\n\n" % json.dumps({"reply": "ok"})
        events.append("end")

    response = app_mod._sse_response(slow_source(), heartbeat_interval=0.005)
    body = "".join(response.response)
    assert ": keep-alive" in body
    assert 'event: session' in body and 'event: done' in body
    assert events == ["start", "end"]


def test_sse_producer_finishes_after_client_disconnects():
    import app as app_mod

    events = []

    def slow_source():
        events.append("start")
        yield "event: session\ndata: %s\n\n" % json.dumps({"session_id": "s2"})
        time.sleep(0.04)
        yield "event: done\ndata: %s\n\n" % json.dumps({"reply": "saved"})
        events.append("end")

    response = app_mod._sse_response(slow_source(), heartbeat_interval=0.005)
    iterator = iter(response.response)
    next(iterator)
    iterator.close()

    deadline = time.time() + 1
    while time.time() < deadline and events != ["start", "end"]:
        time.sleep(0.01)
    assert events == ["start", "end"]


def test_pending_confirm_can_be_restored_without_consumption():
    from agent.tools import store_pending, peek_pending_detail
    import app as app_mod

    actions = [{
        "action_id": "recover-test", "tool_name": "log_body_metric",
        "args": {"date": "2033-01-01", "sleep_h": 7.5},
        "tool_call_id": "tc-recover", "preview": {"date": "2033-01-01"},
    }]
    messages = {"messages": [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "记录睡眠7.5小时"},
    ], "tool_calls_log": []}
    store_pending("recover-test", "sess-recover", actions, messages)

    first = peek_pending_detail("sess-recover")
    second = peek_pending_detail("sess-recover")
    assert first["action_id"] == "recover-test"
    assert first["user_text"] == "记录睡眠7.5小时"
    assert first["actions"][0]["tool"] == "log_body_metric"
    assert second == first  # 只读恢复，不能消费确认状态

    app_mod.app.config["TESTING"] = True
    client = app_mod.app.test_client()
    assert client.get("/api/chat/pending?session_id=sess-recover").status_code == 401
    with client.session_transaction() as sess:
        sess["authed"] = True
    response = client.get("/api/chat/pending?session_id=sess-recover")
    assert response.status_code == 200
    assert response.get_json()["action_id"] == "recover-test"


def test_body_metric_noop_and_unchanged_fields_do_not_require_confirmation():
    from db import insert_body_metric
    from agent.tools import tool_log_body_metric

    insert_body_metric("2033-03-01", weight=72.8, resting_hr=50, hrv_ms=73)

    # 模型把已有值全部带回，但没有实际变化：不能生成确认卡。
    duplicate = tool_log_body_metric(
        date="2033-03-01", weight=72.8, resting_hr=50, hrv_ms=73)
    assert duplicate["status"] == "done"
    assert duplicate["no_op"] is True
    assert duplicate["saved"] is False
    assert duplicate["changed_fields"] == []
    assert "action_id" not in duplicate

    # 模型带回旧值 + 一个新值：确认卡只呈现sleep_h这一个实际变化。
    preview = tool_log_body_metric(
        date="2033-03-01", weight=72.8, sleep_h=7.5,
        resting_hr=50, hrv_ms=73)
    assert preview["status"] == "pending"
    assert preview["preview"]["changed_fields"] == ["sleep_h"]
    assert preview["preview"]["incoming"] == {"sleep_h": 7.5}

    done = tool_log_body_metric(
        date="2033-03-01", weight=72.8, sleep_h=7.5,
        resting_hr=50, hrv_ms=73, confirmed=True)
    assert done["status"] == "done"
    assert done["changed_fields"] == ["sleep_h"]
    record = done["record"]
    assert (record["weight"], record["sleep_h"], record["resting_hr"], record["hrv_ms"]) == \
        (72.8, 7.5, 50, 73)
