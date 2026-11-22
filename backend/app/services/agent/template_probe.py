"""对端 chat 模板探测 —— 只在保存通道 / 测试连接 / 手动「重新识别」时跑一次。

**为什么和 api_probe 分开**：api_probe 回答"这个 key + 地址能不能用"（失败要用户改配置）；
这里回答"它认什么样的消息结构"（失败只说明拿不到结论，**保存照常成功**）。
两件事失败原因不同、节奏不同，混在一起会让保存被连通性问题拖死。

**三个端点**（并行，总预算 5 秒，谁先给出结论用谁）：

| 端点 | 谁 | 给什么 |
|---|---|---|
| `GET {root}/props` | llama.cpp | `chat_template`（Jinja） |
| `GET {root}/api/version` | ollama | 存在性 |
| `POST {root}/api/show` | ollama | `template`（Go 模板） |

`{root}` 由 `llm_endpoint.server_root` 剥掉版本段给出，不手拼。

**对话路径永不探测**：请求侧只读已存的 `system_layout`，一次网络请求都不发。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Collection
from dataclasses import dataclass

import httpx

from app.services.agent.api_probe import allow_internal_policy
from app.utils.outbound_http import request_pinned
from app.utils.pure.chat_template import ANYWHERE, GO, JINJA, layout_from_template
from app.utils.pure.llm_endpoint import server_root

logger = logging.getLogger(__name__)

PROBE_BUDGET = 5.0        # 整个探测的总预算（不是每个请求）
_TEMPLATE_LIMIT = 200_000  # 模板正文上限，防对端返回超大字符串


@dataclass(frozen=True)
class TemplateProbe:
    """探测结论。ok=False 时其余字段无意义，调用方保持现值。"""

    ok: bool
    layout: str = ANYWHERE      # anywhere | leading | none
    dialect: str = ""           # jinja | go
    provider: str = ""          # llama.cpp | ollama
    template_hash: str = ""     # 模板指纹：用来判断"同一台机器换模型了没有"
    message: str = ""           # 给人看的一句话

    @property
    def label(self) -> str:
        return {"llama.cpp": "llama.cpp", "ollama": "Ollama"}.get(self.provider, self.provider)


def template_digest(template: str) -> str:
    """模板指纹（纯函数）。只留前 16 位：比"变了没有"够用，又不至于当敏感信息。"""
    return hashlib.sha256(template.encode("utf-8", "replace")).hexdigest()[:16]


async def probe_chat_template(
    base_url: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
    allow_private: bool = False,
    allow_private_hosts: Collection[str] = (),
) -> TemplateProbe:
    """探测对端的 chat 模板并判定 system 摆放。

    client 只给测试注入（MockTransport），生产不传。
    私网目标默认拒绝，规则与 api_probe 完全一致（同一份政策函数，不各写一份）。
    """
    if not (base_url or "").strip():
        return TemplateProbe(False, message="请先填写 Base URL")

    root = server_root(base_url)
    allow_internal = allow_internal_policy(allow_private, allow_private_hosts)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=PROBE_BUDGET)

    try:
        tasks = {
            asyncio.create_task(_probe_llama_cpp(client, root, allow_internal)),
            asyncio.create_task(_probe_ollama(client, root, model, allow_internal)),
        }
        done, pending = await asyncio.wait(tasks, timeout=PROBE_BUDGET)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            result = task.result() if not task.cancelled() and task.exception() is None else None
            if result is not None:
                return result
        return TemplateProbe(False, message="没拿到 chat 模板（对端可能不是 llama.cpp / Ollama）")
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def _probe_llama_cpp(client, root: str, allow_internal) -> TemplateProbe | None:
    """llama.cpp 的 /props 自带 `chat_template`（PR #8337 起）。"""
    try:
        resp = await request_pinned(client, "GET", f"{root}/props", allow_internal=allow_internal)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    template = payload.get("chat_template") if isinstance(payload, dict) else None
    if not _usable_template(template):
        return None
    return TemplateProbe(
        True,
        layout=layout_from_template(template, JINJA),
        dialect=JINJA,
        provider="llama.cpp",
        template_hash=template_digest(template),
        message="已识别：llama.cpp",
    )


async def _probe_ollama(client, root: str, model: str | None, allow_internal) -> TemplateProbe | None:
    """Ollama：`/api/version` 确认存在，`/api/show` 拿模型自己的模板。"""
    try:
        resp = await request_pinned(client, "GET", f"{root}/api/version", allow_internal=allow_internal)
    except Exception:
        return None
    if resp.status_code != 200:
        return None

    name = model or await _first_ollama_model(client, root, allow_internal)
    if not name:
        return TemplateProbe(False, message="Ollama 在线，但不知道要问哪个模型的模板")
    try:
        resp = await request_pinned(
            client, "POST", f"{root}/api/show",
            json={"model": name}, allow_internal=allow_internal,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    template = payload.get("template") if isinstance(payload, dict) else None
    if not _usable_template(template):
        return None
    return TemplateProbe(
        True,
        layout=layout_from_template(template, GO),
        dialect=GO,
        provider="ollama",
        template_hash=template_digest(template),
        message=f"已识别：Ollama（{name}）",
    )


async def _first_ollama_model(client, root: str, allow_internal) -> str:
    """没给模型名时退一步：问 `/api/tags` 要第一个。只是为了拿到一份模板，不参与路由。"""
    try:
        resp = await request_pinned(client, "GET", f"{root}/api/tags", allow_internal=allow_internal)
        payload = resp.json() if resp.status_code == 200 else None
    except Exception:
        return ""
    rows = payload.get("models") if isinstance(payload, dict) else None
    for row in rows or []:
        name = row.get("name") or row.get("model") if isinstance(row, dict) else None
        if isinstance(name, str) and name:
            return name
    return ""


def _usable_template(template: object) -> bool:
    return isinstance(template, str) and bool(template.strip()) and len(template) <= _TEMPLATE_LIMIT
