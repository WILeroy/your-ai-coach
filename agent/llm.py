"""LLM 客户端: OpenAI 兼容 + 重试 + 流式支持"""
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

def chat(messages, tools=None, max_tokens=LLM_MAX_TOKENS, stream=False):
    """调用 LLM，支持重试和流式"""
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
        kwargs["tool_choice"] = "auto"
    if stream:
        kwargs["stream"] = True

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return _CLIENT.chat.completions.create(**kwargs)
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str or "timeout" in err_str or "server" in err_str:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
                    continue
            raise
    raise RuntimeError(f"LLM 调用失败(重试{MAX_RETRIES}次): {last_error}")

def chat_stream(messages, tools=None, max_tokens=LLM_MAX_TOKENS):
    """流式调用 LLM，返回生成器 yield 每个 delta chunk"""
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

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            stream = _CLIENT.chat.completions.create(**kwargs)
            for chunk in stream:
                yield chunk
            return
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str or "timeout" in err_str or "server" in err_str:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
                    continue
            raise
    raise RuntimeError(f"LLM 调用失败(重试{MAX_RETRIES}次): {last_error}")
