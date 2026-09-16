#!/usr/bin/env python3
"""Agent 回归评测: 跑典型指令，断言工具调用与回复质量

用法:
    .venv/bin/python scripts/agent_eval.py            # 全量
    .venv/bin/python scripts/agent_eval.py 3          # 只跑前3条
需要 .env 配置可用的 LLM_API_KEY。无 Key 时自动跳过(exit 77)。
"""
import sys, os, json, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm import is_available
from agent.core import handle_message

# (指令, 期望被调用的工具列表[任一命中即算], 是否允许pending确认)
CASES = [
    ("今天练什么？", ["get_today_context"], False),
    ("本周训练完成情况如何？", ["get_sessions", "get_today_context", "get_analytics"], False),
    ("杠铃卧推的进展趋势怎么样？", ["get_exercise_history", "get_analytics"], False),
    ("深蹲最近练了多少？", ["get_exercise_history", "get_today_context"], False),
    ("我的负荷状态怎么样？", ["get_analytics", "get_today_context"], False),
    ("当前周期计划是什么？", ["get_plan"], False),
    ("查一下有没有平板支撑这个动作", ["search_exercises"], False),
    ("我练完了，杠铃深蹲60kg 5组5次", ["log_training"], True),
    ("记录：昨晚睡了7小时，体重76kg", ["log_body_metric"], True),
    ("有没有平台期？", ["get_analytics"], False),
]


def run_case(idx, text, expected_tools, allow_pending):
    """返回 (ok, detail)"""
    session_id = f"eval-{uuid.uuid4().hex[:8]}"
    events = {"tools": [], "reply": "", "pending": None, "error": None}
    for sse in handle_message(text, session_id):
        lines = sse.strip().split("\n")
        ev, data = None, None
        for ln in lines:
            if ln.startswith("event: "):
                ev = ln[7:]
            elif ln.startswith("data: "):
                data = ln[6:]
        if not ev or not data:
            continue
        payload = json.loads(data)
        if ev == "tool_call":
            events["tools"].append(payload["tool"])
        elif ev == "pending_confirm":
            events["pending"] = payload
        elif ev == "done":
            events["reply"] = payload.get("reply", "")
        elif ev == "error":
            events["error"] = payload.get("message")

    if events["error"]:
        return False, f"error: {events['error']}"
    # 写入类: pending_confirm 即为成功(等待用户确认是正确行为)
    if events["pending"] is not None:
        if events["pending"]["tool"] not in expected_tools:
            return False, f"确认的工具不符: {events['pending']['tool']}"
        return True, f"pending={events['pending']['tool']}"
    reply = events["reply"]
    if not reply or reply.strip() in ("(空)", "(出错了，请重试)"):
        return False, "空回复"
    if not allow_pending and not any(t in events["tools"] for t in expected_tools):
        return False, f"未调用期望工具 {expected_tools}, 实际: {events['tools']}"
    return True, f"tools={events['tools']} reply={reply[:40]!r}"


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(CASES)
    if not is_available():
        print("[SKIP] LLM 未配置")
        sys.exit(77)
    passed, failed = 0, 0
    for i, (text, tools, pend) in enumerate(CASES[:limit], 1):
        print(f"[{i}/{min(limit, len(CASES))}] {text}")
        ok, detail = run_case(i, text, tools, pend)
        print(f"    {'✅' if ok else '❌'} {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n结果: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
