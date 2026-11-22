"""OpenAI 兼容端点 URL 的唯一入口（纯函数，零 IO）。

各家的 base_url 写法不统一，"无脑补 /v1"和"无脑不补"都会踩坑：

| base_url | 正确做法 | 反例（实测） |
|---|---|---|
| `https://api.deepseek.com` | 补 /v1 | /v1 可有可无，两种都能通 |
| `https://api.xiaomimimo.com` | 补 /v1 | 不补 → `/models` **404**（openresty 直接 404，绑定 AK 必失败）|
| `https://dashscope.aliyuncs.com/compatible-mode/v1` | **不补** | 再补 → `/v1/v1/chat/completions` **404** |
| `https://open.bigmodel.cn/api/paas/v4` | **不补** | 版本段是 v4，不是 v1 |

规则一句话：**base_url 末尾已经有版本段（/vN）就不再补，否则补 /v1**。

改端点规则只改这里——调用方一律不要再手拼 `f"{base}/v1/...".`
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_VERSION_TAIL = re.compile(r"/v\d+$")
# Gemini 的 OpenAI 兼容根是 .../v1beta/openai：本身就是完整根，再补 /v1 会 404
_OPENAI_TAIL = re.compile(r"/openai$")


def api_root(base_url: str) -> str:
    """补出版本段后的 API 根，如 `https://api.deepseek.com/v1`"""
    base = (base_url or "").rstrip("/")
    if _VERSION_TAIL.search(base) or _OPENAI_TAIL.search(base):
        return base
    return f"{base}/v1"


def server_root(base_url: str) -> str:
    """服务根：剥掉版本段（/vN）与 Gemini 的 /openai 尾巴。

    用途只一条：问"服务本身"的端点（llama.cpp 的 `/props`、Ollama 的 `/api/*`）——
    这些挂在根上，不在 /v1 下。聊天端点一律用 `chat_completions_url`，不要用这个。
    """
    root = api_root(base_url)
    root = _VERSION_TAIL.sub("", root)
    return _OPENAI_TAIL.sub("", root)


def chat_completions_url(base_url: str) -> str:
    return f"{api_root(base_url)}/chat/completions"


def models_url(base_url: str) -> str:
    """模型列表端点（连接测试用）——与 chat 同一套 base_url 语义，别再少一个 /v1"""
    return f"{api_root(base_url)}/models"


# 自建推理服务常出现的写法：容器网关、宿主机别名、mDNS
_LOCAL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]",
    "host.docker.internal", "gateway.docker.internal", "host.containers.internal",
}


def is_local_endpoint(base_url: str) -> bool:
    """这个地址是不是本机 / 内网的自建推理服务。纯函数，零 IO。

    用途只有一条：**超时**。云端一轮几秒，本地 CPU 上跑 7B、prompt 几千 token，
    一轮两三分钟是常态。拿云端的 120 秒去卡它，等于把"正在正常思考"判成故障——
    用户看到的现象是 AI 永远不回，日志里只有一句空的 ReadTimeout。
    """
    raw = (base_url or "").strip()
    if not raw:
        return False
    if "://" not in raw:
        raw = f"//{raw}"
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local)


def embeddings_url(base_url: str) -> str:
    """向量端点；base_url 允许直接给完整的 `.../embeddings` 路径。"""
    base = (base_url or "").rstrip("/")
    return base if base.endswith("/embeddings") else f"{api_root(base)}/embeddings"
