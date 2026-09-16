"""配置加载: .env 文件极简解析"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(PROJECT_ROOT, '.env')

def _load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.moonshot.cn/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "moonshot-v1-8k")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.3"))

AGENT_MAX_TOOL_ROUNDS = int(os.environ.get("AGENT_MAX_TOOL_ROUNDS", "3"))
CHAT_HISTORY_LIMIT = int(os.environ.get("CHAT_HISTORY_LIMIT", "20"))

def is_configured():
    k = LLM_API_KEY
    return bool(k) and "your-key" not in k and "your_api_key" not in k.lower()

def mask_key(k):
    if not k:
        return ""
    return k[:6] + "****" + k[-4:] if len(k) > 10 else "****"
