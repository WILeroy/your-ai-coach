"""v2.2: 联网工具 + 会话删除"""
import pytest


# ── SSRF 防护 ──

def test_ssrf_blocked_targets():
    from agent.tools import _assert_public_http_url
    bad = [
        "http://127.0.0.1/x",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/meta",   # 云元数据
        "http://[::1]/",
        "ftp://example.com",
        "http://1.2.3.4:8080/",
        "http://example.com:22/",
        "not-a-url",
    ]
    for u in bad:
        with pytest.raises(ValueError):
            _assert_public_http_url(u)


def test_ssrf_allow_public():
    from agent.tools import _assert_public_http_url
    # 公网域名应通过(会发起DNS)
    try:
        _assert_public_http_url("https://www.baidu.com/")
        _assert_public_http_url("http://example.com/")
    except ValueError as e:
        # 测试环境无外网时跳过
        if "解析" not in str(e):
            raise


def test_resolve_redirect_passthrough():
    from agent.tools import _resolve_redirect
    assert _resolve_redirect("https://www.baidu.com/") == "https://www.baidu.com/"
    assert _resolve_redirect("https://example.org/a?b=1") == "https://example.org/a?b=1"


def test_block_snippet_fallback():
    from agent.tools import _block_snippet
    from bs4 import BeautifulSoup
    html = '<div class="vrwrap"><h3><a>T</a></h3><p>这是一段足够长的摘要文字用来触发兜底逻辑的测试内容长度检查</p></div>'
    s = _block_snippet(BeautifulSoup(html, "html.parser").select_one("div.vrwrap"))
    assert "摘要" in s


# ── 联网工具冒烟(网络可用时) ──

def test_web_search_live():
    from agent.tools import tool_web_search
    r = tool_web_search("健身 蛋白质", max_results=3)
    if "error" in r and "请求失败" in r.get("error", ""):
        pytest.skip("无外网")
    assert "results" in r
    assert len(r["results"]) >= 1
    assert r["results"][0]["url"].startswith("http")
    assert r["view_spec"]["view"] == "search_results"


def test_web_fetch_rejects_ssrf():
    from agent.tools import tool_web_fetch
    r = tool_web_fetch("http://127.0.0.1:5200/api/summary")
    assert "不允许" in r["error"]


# ── 会话删除 API ──

def test_chat_session_delete_api():
    import app as app_mod
    from db import get_db
    from datetime import datetime
    # 造临时会话
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute("INSERT INTO chat_messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                 ["test-del-sess", "user", "hi", now])
    conn.execute("INSERT INTO agent_pending_actions(action_id,session_id,messages_json,tool_name,args_json,tool_call_id,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                 ["test-del-pend", "test-del-sess", "[]", "x", "{}", "", now, now])
    conn.commit(); conn.close()

    app_mod.app.config["TESTING"] = True
    client = app_mod.app.test_client()
    # 未登录 401
    r = client.delete("/api/chat/sessions/test-del-sess")
    assert r.status_code == 401
    # 模拟登录
    with client.session_transaction() as sess:
        sess["authed"] = True
    r = client.delete("/api/chat/sessions/test-del-sess")
    assert r.status_code == 200
    assert r.get_json()["deleted_messages"] >= 1
    # 消息与 pending 均已删除
    conn = get_db()
    n1 = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id='test-del-sess'").fetchone()[0]
    n2 = conn.execute("SELECT COUNT(*) FROM agent_pending_actions WHERE session_id='test-del-sess'").fetchone()[0]
    conn.close()
    assert n1 == 0 and n2 == 0


def test_search_exercises_batch():
    from agent.tools import tool_search_exercises
    r = tool_search_exercises("引体,划船,弯举,不存在的动作xyz")
    assert "groups" in r
    assert len(r["groups"]) == 4
    # 引体应命中 引体/高位下拉
    yinti = next(g for g in r["groups"] if g["keyword"] == "引体")
    assert any("引体" in x["name"] for x in yinti["results"])
    # 单关键词保持旧格式
    r2 = tool_search_exercises("卧推")
    assert "results" in r2 and len(r2["results"]) >= 1


def test_summarize_fallback_never_empty(monkeypatch):
    """兜底总结两次空响应时，必须返回工具结果拼接(绝不空)"""
    import agent.core as core

    class _Msg:
        class message:
            content = ""
    class _Resp:
        choices = [_Msg()]
    def fake_chat(m, tools=None, max_tokens=None, tool_choice=None):
        return _Resp()
    monkeypatch.setattr(core, "chat", fake_chat)
    out = core._summarize_fallback(
        [{"role": "user", "content": "hi"}],
        [{"tool": "get_plan", "args": {}, "result_preview": '{"cycle": {...}}'}])
    assert out and "get_plan" in out
