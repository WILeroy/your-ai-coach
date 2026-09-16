"""LLM 客户端: OpenAI 兼容 + 重试 + 流式支持

重试策略: 仅在「尚未产出任何 token」时重试，避免部分内容重复发送。
"""
import time
from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_TOKENS, LLM_TEMPERATURE

try:
    from openai import OpenAI
    _CLIENT = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL) if LLM_API_KEY else None
except ImportError:
    _CLIENT = None

MAX_RETRIES = 3
RETRY_DELAY = 1.5


def is_available():
    return _CLIENT is not None and bool(LLM_API_KEY)


def _retryable(e):
    err = str(e).lower()
    return any(k in err for k in ("rate", "429", "timeout", "server", "502", "503", "504",
                                  "connection", "overloaded"))


def chat(messages, tools=None, max_tokens=LLM_MAX_TOKENS, tool_choice=None):
    """非流式调用（用于兜底总结/连通性检查）"""
    if not is_available():
        raise RuntimeError("LLM 未配置")
    kwargs = dict(
        model=LLM_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=LLM_TEMPERATURE,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"
    elif tool_choice:
        kwargs["tool_choice"] = tool_choice

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return _CLIENT.chat.completions.create(**kwargs)
        except Exception as e:
            last_error = e
            if _retryable(e) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
            raise
    raise RuntimeError(f"LLM 调用失败(重试{MAX_RETRIES}次): {last_error}")


def chat_stream(messages, tools=None, max_tokens=LLM_MAX_TOKENS):
    """流式调用 LLM，yield 每个 delta chunk。

    只有在第一个 chunk 产出前发生异常才会重试；
    一旦开始输出，异常直接抛出（避免内容重复）。
    """
    if not is_available():
        raise RuntimeError("LLM 未配置")
    kwargs = dict(
        model=LLM_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=LLM_TEMPERATURE,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    for attempt in range(MAX_RETRIES):
        yielded = False
        try:
            stream = _CLIENT.chat.completions.create(**kwargs)
            for chunk in stream:
                yielded = True
                yield chunk
            return
        except Exception as e:
            # 已输出部分内容，不重试
            if yielded:
                raise
            if _retryable(e) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
            raise
    raise RuntimeError(f"LLM 调用失败(重试{MAX_RETRIES}次)")


def check_connectivity():
    """启动时连通性检查，返回 (ok, message)"""
    if not is_available():
        return False, "LLM_API_KEY 未配置"
    try:
        resp = chat([{"role": "user", "content": "ping"}], max_tokens=8)
        return True, "ok"
    except Exception as e:
        return False, str(e)[:200]
