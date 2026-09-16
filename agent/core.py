"""Agent 核心循环: 流式对话 → 工具调用 → 确认机制 → 历史持久化"""
import json
import uuid
import time
from datetime import datetime
from .config import AGENT_MAX_TOOL_ROUNDS, CHAT_HISTORY_LIMIT
from .llm import is_available, chat_stream
from .context import build_system_prompt
from .tools import TOOL_SCHEMAS, TOOL_MAP, store_pending, get_pending, cleanup_expired


# ── 会话状态管理(内存) ──
SESSION_STATES = {}


def handle_message(user_text, session_id=''):
    """处理用户消息，返回 generator yield SSE 事件"""
    if not is_available():
        yield _sse("error", {"message": "LLM 未配置"})
        return

    cleanup_expired()

    # 检查是否是对确认的回复
    normal_text, confirm_action = _parse_confirm(user_text)
    if confirm_action:
        yield from _handle_confirm(confirm_action, session_id)
        return

    # 正常对话流
    messages = [{"role": "system", "content": build_system_prompt()}]
    history = _load_history(session_id)
    messages.extend(history)
    messages.append({"role": "user", "content": normal_text})

    tool_calls_log = []

    yield from _agent_loop(messages, tool_calls_log, session_id, normal_text)


def _agent_loop(messages, tool_calls_log, session_id, user_text):
    """Agent 主循环: 流式输出 → 工具调用 → 继续"""
    accumulated_content = ""
    current_tool_calls = []

    for round_n in range(AGENT_MAX_TOOL_ROUNDS + 1):
        current_tool_calls = []
        accumulated_content = ""

        try:
            stream = chat_stream(messages, tools=TOOL_SCHEMAS)
        except Exception as e:
            yield _sse("error", {"message": f"LLM调用失败: {str(e)[:200]}"})
            return

        # 收集流式响应 + 检测工具调用
        tool_call_bufs = {}
        finish_reason = None

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None

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

        if tool_call_bufs:
            # 构建完整的 tool_calls 列表
            tool_calls = []
            for idx in sorted(tool_call_bufs.keys()):
                buf = tool_call_bufs[idx]
                tool_calls.append({
                    "id": buf["id"],
                    "type": "function",
                    "function": buf["function"]
                })

            # 创建 assistant message 并追加到 messages
            assistant_msg = {"role": "assistant", "content": accumulated_content or None, "tool_calls": tool_calls}
            # format for OpenAI API
            formatted_tool_calls = []
            for tc in tool_calls:
                formatted_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": tc["function"]
                })
            assistant_msg = {"role": "assistant", "content": accumulated_content or None, "tool_calls": formatted_tool_calls}
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                yield _sse("tool_call", {"tool": fn_name, "args": fn_args})

                if fn_name in TOOL_MAP:
                    try:
                        result = TOOL_MAP[fn_name](**fn_args)
                    except Exception as e:
                        result = {"error": str(e)}
                else:
                    result = {"error": f"未知工具 {fn_name}"}

                result_str = json.dumps(result, ensure_ascii=False, default=str)
                tool_calls_log.append({"tool": fn_name, "args": fn_args, "result_preview": result_str[:300]})

                # 检查是否需要确认
                if isinstance(result, dict) and result.get("status") == "pending":
                    action_id = result.get("action_id", str(uuid.uuid4())[:8])

                    # 保存会话状态
                    SESSION_STATES[session_id] = {
                        "messages": messages,
                        "tool_calls_log": tool_calls_log,
                        "round_n": round_n,
                        "user_text": user_text,
                        "created_at": time.time()
                    }
                    store_pending(action_id, fn_name, fn_args, {
                        "session_id": session_id,
                        "tool_call_id": tc["id"]
                    })

                    yield _sse("pending_confirm", {
                        "action_id": action_id,
                        "tool": fn_name,
                        "preview": result.get("preview", {}),
                        "message": f"确认执行 {fn_name}?"
                    })

                    # 追加工具结果到 messages (pending状态)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps({"status": "pending", "action_id": action_id,
                                               "message": "等待用户确认..."}, ensure_ascii=False)
                    })
                    return  # 暂停循环，等待确认

                yield _sse("tool_result", {"tool": fn_name, "result": result_str[:300]})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str
                })
        else:
            # 没有工具调用，最终回复
            break

    # 循环结束，保存历史
    reply = accumulated_content or "(空)"
    _save_history(session_id, user_text, reply, tool_calls_log)
    yield _sse("done", {"reply": reply, "tool_calls": tool_calls_log})


def _handle_confirm(confirm_action, session_id):
    """处理确认操作: 恢复上下文 + 执行已确认的工具 + 继续循环"""
    action_id = confirm_action.get("action_id", "")
    confirmed = confirm_action.get("confirmed", True)

    state = SESSION_STATES.pop(session_id, None)

    if not state:
        yield _sse("error", {"message": "确认会话已过期，请重新描述操作"})
        return

    # 如果没有 action_id，从 pending_actions 中找属于此 session 的
    if not action_id:
        from .tools import _pending_actions
        for aid, pdata in _pending_actions.items():
            if pdata.get("context", {}).get("session_id") == session_id:
                action_id = aid
                break

    pending = get_pending(action_id)

    if confirmed and (not pending):
        yield _sse("error", {"message": "确认操作已过期，请重新描述操作"})
        return

    messages = state["messages"]
    tool_calls_log = state["tool_calls_log"]
    user_text = state["user_text"]

    # 取消操作
    if not confirmed:
        yield _sse("delta", {"text": "已取消操作。还有什么需要帮助的吗？"})
        _save_history(session_id, user_text, "已取消操作。", tool_calls_log)
        yield _sse("done", {"reply": "已取消操作。", "tool_calls": tool_calls_log})
        return

    # 确认操作
    fn_name = pending["tool_name"]
    fn_args = dict(pending["args"])
    fn_args["confirmed"] = True

    if fn_name in TOOL_MAP:
        try:
            result = TOOL_MAP[fn_name](**fn_args)
        except Exception as e:
            result = {"error": str(e)}
    else:
        result = {"error": f"未知工具 {fn_name}"}

    result_str = json.dumps(result, ensure_ascii=False, default=str)
    yield _sse("tool_result", {"tool": fn_name, "result": result_str[:300]})

    tc_id = pending["context"].get("tool_call_id", "")
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "tool" and messages[i].get("tool_call_id") == tc_id:
            messages[i]["content"] = result_str
            break

    yield from _agent_loop(messages, tool_calls_log, session_id, user_text)


def _parse_confirm(user_text):
    """检测用户输入是否为确认操作"""
    text = user_text.strip().lower()

    confirm_keywords = ["确认", "yes", "是", "ok", "好的", "可以", "执行", "confirm", "y"]
    cancel_keywords = ["取消", "no", "否", "不", "算了", "cancel", "n", "不要", "别"]

    is_confirm = text in confirm_keywords or text.startswith("/confirm")
    is_cancel = text in cancel_keywords

    # 尝试提取 action_id (如 /confirm abc123)
    action_id = ""
    if text.startswith("/confirm "):
        action_id = text.split("/confirm ", 1)[1].strip()
        is_confirm = True

    if is_confirm:
        return "", {"action_id": action_id, "confirmed": True}
    elif is_cancel:
        return "", {"action_id": "", "confirmed": False}

    # 检查是否处于等待确认状态
    # 如果不是确认回复，返回原文本
    return user_text, None


def _load_history(session_id=''):
    from db import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT role, content, tool_calls_json FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            [session_id, CHAT_HISTORY_LIMIT]
        ).fetchall()
    except Exception:
        conn.close()
        return []

    history = []
    for r in reversed(rows):
        history.append({"role": r['role'], "content": r['content']})
    conn.close()
    return history


def _save_history(session_id, user_text, reply, tool_calls_log=None):
    from db import get_db
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
        [session_id, "user", user_text, now]
    )
    tc_json = json.dumps(tool_calls_log, ensure_ascii=False) if tool_calls_log else None
    conn.execute(
        "INSERT INTO chat_messages(session_id, role, content, tool_calls_json, created_at) VALUES(?,?,?,?,?)",
        [session_id, "assistant", reply, tc_json, now]
    )
    conn.commit()
    conn.close()


def cleanup_sessions(max_age=3600):
    """清理过期的会话状态"""
    now = time.time()
    expired = [k for k, v in SESSION_STATES.items() if now - v.get("created_at", 0) > max_age]
    for k in expired:
        del SESSION_STATES[k]


def _sse(event, data):
    """构建 SSE 事件字符串"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
