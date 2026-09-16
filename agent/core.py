"""Agent 核心循环 v2: 流式对话 -> 工具调用 -> 确认机制(SQLite持久化) -> 历史持久化 -> UI事件

关键改进:
- 确认状态存 SQLite(agent_pending_actions)，gunicorn 多 worker 安全
- 工具结果含 view_spec 时自动推送 SSE "ui" 事件驱动前端画布
- 工具轮耗尽仍无文字时，强制一次无工具总结调用，杜绝"(空)"回复
- 确认/取消关键词仅在存在 pending action 时生效，避免误判
"""
import json
import re
from datetime import datetime
from .config import AGENT_MAX_TOOL_ROUNDS, CHAT_HISTORY_LIMIT
from .llm import is_available, chat_stream, chat
from .context import build_system_prompt
from .tools import (TOOL_SCHEMAS, TOOL_MAP, store_pending, get_pending,
                    peek_pending_session, cleanup_expired)

CONFIRM_WORDS = {"确认", "yes", "ok", "好的", "可以", "执行", "confirm", "y",
                 "确定", "确认执行", "确认一下", "确认全部", "全部确认"}
CANCEL_WORDS = {"取消", "no", "否", "算了", "cancel", "n", "不要", "不执行"}

# 确认门控: 这些工具的 confirmed 参数只信任 /api/chat/confirm 内部路径，
# 模型在普通对话轮传入的 confirmed 会被强制剥离(一律按预览处理)
GATED_TOOLS = {"log_training", "log_body_metric", "update_session", "delete_data",
               "create_plan", "adjust_plan", "manage_exercises"}

# 批量确认执行顺序: 先建动作库，再落训练记录，其余按原序
_EXEC_ORDER = {"manage_exercises": 0, "log_training": 1}


def _exec_key(action):
    return _EXEC_ORDER.get(action.get("tool_name", ""), 2)

# 写入意图检测(完成时态/记录类动词)，用于 flash 模型漏调写工具时的纠正
_WRITE_INTENT_RE = re.compile(
    r"练完|做完|做[了完]\d|完成了|记录|记一下|记了|睡了\s*\d|睡\s*\d+\s*小时|体重\s*\d|晨脉\s*\d"
    r"|生成.{0,6}周期|删除|删掉|改成|调整.{0,6}计划")
_WRITE_TOOLS = {"log_training", "log_body_metric", "update_session", "delete_data",
                "create_plan", "adjust_plan", "manage_exercises"}
_WRITE_NUDGE = ("(系统提示: 检测到用户想记录/写入数据，但你尚未调用写入工具。"
                "请立即调用相应的写入工具(不带confirmed参数)生成预览，不要只用文字描述。)")


def handle_message(user_text, session_id=''):
    """处理用户消息，返回 generator yield SSE 事件"""
    if not is_available():
        yield _sse("error", {"message": "LLM 未配置，请在 .env 填写 LLM_API_KEY 后重启服务"})
        return

    cleanup_expired()

    # ── 确认/取消: 仅当该 session 存在 pending action ──
    stripped = user_text.strip()
    lower = stripped.lower()
    if peek_pending_session(session_id):
        if lower.startswith("/confirm"):
            action_id = stripped.split(" ", 1)[1].strip() if " " in stripped else ""
            yield from _handle_confirm(action_id, session_id, True)
            return
        if lower in CONFIRM_WORDS:
            yield from _handle_confirm("", session_id, True)
            return
        if lower in CANCEL_WORDS:
            yield from _handle_confirm("", session_id, False)
            return

    # ── 正常对话流 ──
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages.extend(_load_history(session_id))
    messages.append({"role": "user", "content": user_text})

    yield from _agent_loop(messages, [], session_id, user_text)


# ═══════════════ Agent 主循环 ═══════════════

def _agent_loop(messages, tool_calls_log, session_id, user_text, _retryed=False):
    """流式输出 -> 工具调用 -> 继续循环; 最后一轮禁用工具强制总结"""
    accumulated_content = ""

    for round_n in range(AGENT_MAX_TOOL_ROUNDS + 1):
        use_tools = round_n < AGENT_MAX_TOOL_ROUNDS
        accumulated_content = ""
        tool_call_bufs = {}

        try:
            if use_tools:
                stream = chat_stream(messages, tools=TOOL_SCHEMAS)
            else:
                stream = chat_stream(messages)
        except Exception as e:
            yield _sse("error", {"message": "LLM调用失败: %s" % str(e)[:200]})
            return

        # 收集流式响应 + 工具调用
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta:
                if delta.content:
                    accumulated_content += delta.content
                    yield _sse("delta", {"text": delta.content})
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_bufs:
                            tool_call_bufs[idx] = {"id": tc.id or "", "function": {"name": "", "arguments": ""}}
                        if tc.id:
                            tool_call_bufs[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_call_bufs[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_call_bufs[idx]["function"]["arguments"] += tc.function.arguments

        if not (tool_call_bufs and use_tools):
            break  # 无工具调用，最终回复

        # ── 执行工具 ──
        tool_calls = [tool_call_bufs[idx] for idx in sorted(tool_call_bufs.keys())]
        messages.append({"role": "assistant", "content": accumulated_content or None,
                         "tool_calls": [{"id": tc["id"], "type": "function",
                                         "function": tc["function"]} for tc in tool_calls]})

        pending_actions = []  # 本轮全部待确认操作(批量)
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                fn_args = {}
            # 门控: 剥离模型传入的 confirmed，写工具只能走 预览→用户确认→内部执行
            if fn_name in GATED_TOOLS:
                fn_args.pop("confirmed", None)

            yield _sse("tool_call", {"tool": fn_name, "args": fn_args})

            if fn_name in TOOL_MAP:
                try:
                    result = TOOL_MAP[fn_name](**fn_args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": "未知工具 %s" % fn_name}

            result_str = json.dumps(result, ensure_ascii=False, default=str)
            tool_calls_log.append({"tool": fn_name, "args": fn_args,
                                   "result_preview": result_str[:300]})

            # 需要用户确认 -> 收集(先不持久化)
            if isinstance(result, dict) and result.get("status") == "pending":
                action_id = result.get("action_id", "")
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps({"status": "pending", "action_id": action_id},
                                                       ensure_ascii=False)})
                pending_actions.append({"action_id": action_id, "tool_name": fn_name,
                                        "args": fn_args, "tool_call_id": tc["id"],
                                        "preview": result.get("preview", {})})
                continue  # 继续处理剩余工具，保证全部 tool 消息 append 完

            # 可视化事件
            if isinstance(result, dict):
                if result.get("view_spec"):
                    yield _sse("ui", result["view_spec"])
                if result.get("ui_refresh"):
                    yield _sse("ui", {"view": "refresh"})

            yield _sse("tool_result", {"tool": fn_name, "result": result_str[:300]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

        if pending_actions:
            # 全部工具消息已 append，快照完整；批量持久化为一条 pending
            batch_id = pending_actions[0]["action_id"]
            store_pending(batch_id, session_id, pending_actions,
                          {"messages": messages, "tool_calls_log": tool_calls_log})
            first = pending_actions[0]
            yield _sse("pending_confirm", {
                "action_id": batch_id,
                "tool": first["tool_name"],
                "preview": first.get("preview", {}),
                "actions": [{"tool": a["tool_name"], "preview": a.get("preview", {})}
                            for a in pending_actions],
                "message": "确认执行 %d 项操作?" % len(pending_actions)})
            return  # 等待用户确认

    # ── 空回复兜底 ──
    if not accumulated_content.strip():
        accumulated_content = _summarize_fallback(messages, tool_calls_log)

    # ── 写入意图纠正: 模型漏调写工具时补一轮 ──
    if (not _retryed and accumulated_content.strip()
            and _WRITE_INTENT_RE.search(user_text or "")
            and not any(t.get("tool") in _WRITE_TOOLS for t in tool_calls_log)):
        messages.append({"role": "user", "content": _WRITE_NUDGE})
        yield from _agent_loop(messages, tool_calls_log, session_id, user_text, _retryed=True)
        return

    reply = accumulated_content or "(出错了，请重试)"
    _save_history(session_id, user_text, reply, tool_calls_log)
    yield _sse("done", {"reply": reply, "tool_calls": tool_calls_log})


def _summarize_fallback(messages, tool_calls_log):
    """工具轮耗尽仍无文字时，强制一次无工具总结"""
    if not tool_calls_log:
        return ""
    import copy
    m = copy.deepcopy(messages)
    m.append({"role": "user",
              "content": "(系统提示: 请根据以上工具调用结果，直接给用户一个简洁的中文总结回复，不要再调用工具)"})
    try:
        resp = chat(m, tools=None, max_tokens=512)
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        # 极端情况: 用工具结果拼一个摘要
        parts = []
        for t in tool_calls_log[-3:]:
            parts.append("[%s] %s" % (t["tool"], t["result_preview"][:100]))
        return "查询完成：\n" + "\n".join(parts)


# ═══════════════ 确认流程 ═══════════════

def _handle_confirm(action_id, session_id, confirmed):
    """确认/取消: 从 SQLite 恢复上下文 -> 批量执行(或取消) -> 继续循环"""
    pending = get_pending(action_id or None, session_id)

    if not pending:
        if not confirmed:
            reply = "已取消。还有什么需要帮助的吗？"
            _save_history(session_id, "取消", reply, [])
            yield _sse("delta", {"text": reply})
            yield _sse("done", {"reply": reply, "tool_calls": []})
            return
        yield _sse("error", {"message": "确认操作已过期(10分钟)，请重新描述操作"})
        return

    state = pending["messages"]  # {"messages": [...], "tool_calls_log": [...]}
    messages = state["messages"]
    tool_calls_log = state.get("tool_calls_log", [])
    actions = pending.get("actions", [])
    user_text = next((m["content"] for m in reversed(messages)
                      if m.get("role") == "user"), "")
    summary = _actions_summary(actions)

    if not confirmed:
        for a in actions:
            for m in messages:
                if m.get("role") == "tool" and m.get("tool_call_id") == a.get("tool_call_id"):
                    m["content"] = json.dumps({"status": "cancelled"}, ensure_ascii=False)
                    break
        reply = "已取消 %s。需要调整的话直接告诉我。" % summary
        _save_history(session_id, user_text, reply, tool_calls_log)
        yield _sse("delta", {"text": reply})
        yield _sse("done", {"reply": reply, "tool_calls": tool_calls_log})
        return

    # ── 确认执行: 按依赖排序(manage_exercises → log_training → 其余)，逐个执行并替换 tool 消息 ──
    ordered = sorted(enumerate(actions), key=lambda t: (_exec_key(t[1]), t[0]))
    ok_count, fail_count = 0, 0
    for _, a in ordered:
        fn_name = a.get("tool_name", "")
        fn_args = dict(a.get("args", {}))
        fn_args["confirmed"] = True

        if fn_name in TOOL_MAP:
            try:
                result = TOOL_MAP[fn_name](**fn_args)
            except Exception as e:
                result = {"error": str(e)}
        else:
            result = {"error": "未知工具 %s" % fn_name}

        result_str = json.dumps(result, ensure_ascii=False, default=str)
        tool_calls_log.append({"tool": fn_name, "args": fn_args,
                               "result_preview": result_str[:300]})
        if isinstance(result, dict) and result.get("status") == "error":
            fail_count += 1
        else:
            ok_count += 1

        # 替换 pending 工具消息为真实结果
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id") == a.get("tool_call_id"):
                m["content"] = result_str
                break

        if isinstance(result, dict):
            if result.get("view_spec"):
                yield _sse("ui", result["view_spec"])
            if result.get("ui_refresh"):
                yield _sse("ui", {"view": "refresh"})

        yield _sse("tool_result", {"tool": fn_name, "result": result_str[:300]})

    # 给模型的执行摘要(作为下轮 user 消息，让回复带上下文)
    if len(ordered) > 1:
        exec_note = "（系统提示: 用户已确认，%d 项操作执行完毕(%d 成功/%d 失败: %s)，请据此简短总结）" % (
            len(ordered), ok_count, fail_count, summary)
        messages.append({"role": "user", "content": exec_note})
    yield from _agent_loop(messages, tool_calls_log, session_id, user_text)


_TOOL_SUMMARY = {
    "log_training": "记录训练", "log_body_metric": "记录身体指标",
    "update_session": "修改训练", "delete_data": "删除数据",
    "create_plan": "生成周期", "adjust_plan": "调整计划",
    "manage_exercises": "动作库变更",
}


def _actions_summary(actions):
    """生成操作摘要，如: 记录训练 + 动作库变更×2"""
    if not actions:
        return "操作"
    counts = {}
    order = []
    for a in actions:
        label = _TOOL_SUMMARY.get(a.get("tool_name", ""), a.get("tool_name", "?"))
        if label not in counts:
            order.append(label)
        counts[label] = counts.get(label, 0) + 1
    parts = []
    for label in order:
        n = counts[label]
        parts.append(label if n == 1 else "%s×%d" % (label, n))
    return " + ".join(parts)


# ═══════════════ 历史持久化 ═══════════════

def _load_history(session_id=''):
    if not session_id:
        return []
    from db import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=? AND role IN ('user','assistant') AND content != '(空)' ORDER BY id DESC LIMIT ?",
            [session_id, CHAT_HISTORY_LIMIT]).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{"role": r['role'], "content": r['content']} for r in reversed(rows)]


def _save_history(session_id, user_text, reply, tool_calls_log=None):
    if not session_id:
        return
    from db import get_db
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
        [session_id, "user", user_text, now])
    tc_json = json.dumps(tool_calls_log, ensure_ascii=False) if tool_calls_log else None
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, tool_calls_json, created_at) VALUES(?,?,?,?,?)",
        [session_id, "assistant", reply, tc_json, now])
    conn.commit()
    conn.close()


def _sse(event, data):
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return "event: %s\ndata: %s\n\n" % (event, payload)
