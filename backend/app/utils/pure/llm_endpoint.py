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

import re

_VERSION_TAIL = re.compile(r"/v\d+$")
# Gemini 的 OpenAI 兼容根是 .../v1beta/openai：本身就是完整根，再补 /v1 会 404
_OPENAI_TAIL = re.compile(r"/openai$")


def api_root(base_url: str) -> str:
    """补出版本段后的 API 根，如 `https://api.deepseek.com/v1`"""
    base = (base_url or "").rstrip("/")
    if _VERSION_TAIL.search(base) or _OPENAI_TAIL.search(base):
        return base
    return f"{base}/v1"


def chat_completions_url(base_url: str) -> str:
    return f"{api_root(base_url)}/chat/completions"


def models_url(base_url: str) -> str:
    """模型列表端点（连接测试用）——与 chat 同一套 base_url 语义，别再少一个 /v1"""
    return f"{api_root(base_url)}/models"


def embeddings_url(base_url: str) -> str:
    """向量端点；base_url 允许直接给完整的 `.../embeddings` 路径。"""
    base = (base_url or "").rstrip("/")
    return base if base.endswith("/embeddings") else f"{api_root(base)}/embeddings"
