"""供应商连通性探针 —— “这个 key + base_url 到底能不能用” 的唯一入口。

**为什么不是一句 GET /models 完事**（2026-09-13 事故复盘）：
- /models 不是所有供应商都实现：小米 MiMo 直接 404（openresty 直出），
  于是“绑定 AK 必失败”，而它的聊天其实是通的；
- 各家 base_url 的版本段写法又不统一（/v1、/v4、兼容模式自带 /v1），
  光看状态码分不清“路径不对”还是“key 不对”。

所以走两段（业界 one-api / new-api 的做法是直接真发一次 chat）：

1. 先 GET {root}/models —— 免费、不需要模型名，多数供应商这条就够；
2. 只有该端点**不存在**（404/405）时，才退化成一次 max_tokens=1 的极小 chat 请求。

认证类错误（401/403）**不退化** —— 路径是通的，退化只会掩盖真正的原因。

返回结构化结果 + 人话文案：把“网络不通 / key 不对 / 路径不对 / 欠费 / 限流”分开报，
而不是把供应商的原始 404 HTML 丢给用户（那是这次事故里最误导人的一点）。
"""
from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass

import httpx

from app.utils.pure.llm_endpoint import chat_completions_url, models_url

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 15.0
PROBE_PROMPT = "hi"          # 只为换来一次 200，内容无所谓
PROBE_MAX_TOKENS = 1         # 极小请求：花几个 token，别真让模型写作文
DETAIL_LIMIT = 200           # 回给前端的供应商原文上限（已脱敏）

# 状态码 → 归类 + 人话。判据是“用户接下来该改什么”，不是 HTTP 语义
_FAILURES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "请求被拒绝：可能是模型名不对，或请求格式不被支持"),
    401: ("auth", "认证失败：API Key 无效、已撤销或填错了"),
    402: ("quota", "余额不足或未开通该服务"),
    403: ("auth", "无权限：API Key 未被授权访问该模型"),
    404: ("not_found", "端点不存在：Base URL 可能少了或多了版本段（/v1、/v4…）"),
    429: ("rate_limited", "触发限流：稍后再试"),
    500: ("server_error", "供应商内部错误"),
    502: ("server_error", "供应商网关错误"),
    503: ("server_error", "供应商服务暂时不可用"),
}


@dataclass(frozen=True)
class ProbeResult:
    """探测结论。ok 之外的 kind 用来给前端分类（现在只用到 message）。"""

    ok: bool
    kind: str            # ok | auth | not_found | quota | rate_limited | bad_request | server_error | http_error | network | timeout | bad_url
    message: str         # 给人看的一句话（已脱敏）
    models: tuple[str, ...] = ()   # 供应商返回的模型 id（拿不到就是空；管理页"获取模型"用）

    @property
    def model_count(self) -> int:
        return len(self.models)


def extract_model_ids(payload) -> tuple[str, ...]:
    """从模型列表响应里抠出模型 id（纯函数）。

    主流是 OpenAI 形状 `{"data":[{"id":...}]}`；有的网关/Ollama 原生形状是
    `{"models":[{"name":...}]}` —— 两种都认，认不出就当没有（不猜）。
    """
    if not isinstance(payload, dict):
        return ()
    for key, id_key in (("data", "id"), ("models", "name")):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        ids = tuple(
            str(r[id_key]) for r in rows
            if isinstance(r, dict) and r.get(id_key)
        )
        if ids:
            return ids
    return ()


def redact_secret(text: str, secret: str | None) -> str:
    """把响应体里可能回显的 API Key 抹掉，并截断。

    不是所有供应商都像 DeepSeek 那样把 key 打码成 ****robe——
    原样回显的会被我们直接展示给浏览器（用户截图发出去就泄露了）。
    """
    out = (text or "").strip()
    if secret:
        out = out.replace(secret, "***")
    return out[:DETAIL_LIMIT]


def resolve_probe_model(base_url: str) -> str:
    """chat 兜底用的模型名：base_url 能对上预设就用预设的 chat_model，否则用平台默认。

    只用来验证“认证 + 路径”通不通，不代表用户实际会选那个模型——
    所以探针不去猜用户选的模型，也就不需要前端把模型名传上来。
    """
    target = (base_url or "").rstrip("/")
    try:
        from app.services.agent.provider_presets import get_all_presets
        for p in get_all_presets():
            if (p.get("base_url") or "").rstrip("/") == target and p.get("chat_model"):
                return str(p["chat_model"])
    except Exception as e:  # 预设读不到不该让整个探测失败，降级到平台默认
        logger.warning(f"探针：读取供应商预设失败，改用默认模型: {e}")
    from app.config import settings
    return settings.default_chat_model


def _headers(api_key: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _failure(resp: httpx.Response, api_key: str | None) -> ProbeResult:
    kind, label = _FAILURES.get(resp.status_code, ("http_error", f"供应商返回 HTTP {resp.status_code}"))
    detail = redact_secret(resp.text, api_key)
    if detail.startswith("<"):
        # nginx/openresty 的 404 错误页：几百字噪声，对用户零诊断价值
        detail = "（供应商返回了 HTML 错误页）"
    message = label if not detail else f"{label}：{detail}"
    return ProbeResult(False, kind, message)


async def _probe_by_chat(
    client: httpx.AsyncClient, base_url: str, api_key: str | None, model: str | None
) -> ProbeResult:
    """GET /models 不存在时的兜底：真发一次极小 chat（业界做法）。

    只有真发一次，才能同时验证 key、base_url 版本段、模型名三者匹配。
    """
    use_model = model or resolve_probe_model(base_url)
    resp = await client.post(
        chat_completions_url(base_url),
        headers=_headers(api_key),
        json={
            "model": use_model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "max_tokens": PROBE_MAX_TOKENS,
            "stream": False,
        },
    )
    if resp.status_code == 200:
        return ProbeResult(True, "ok", f"连接成功（已用 {use_model} 试调用一次）")
    return _failure(resp, api_key)


def _private_hint(base_url: str) -> str:
    """连不上又恰好是本机/内网地址时，把「为什么」直接说出来。

    新手填了自己电脑上跑的中转（localhost:8888），看到的只有 "All connection attempts failed"——
    这句话不会让人想到「服务器和我的电脑不是一台机器」。
    """
    from app.utils.pure.url_guard import is_private_target

    if not is_private_target(base_url):
        return ""
    return ("\n这是本机或局域网地址：服务器访问不到它（localhost/127.0.0.1 指的是你的电脑，"
            "192.168.x.x 是内网）。请填公网能访问的地址，或者把 Base URL 清空、改走平台通道。")


async def probe_provider(
    base_url: str,
    api_key: str | None,
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
    allow_private: bool = False,
    allow_private_hosts: Collection[str] = (),
) -> ProbeResult:
    """探测一个供应商是否可用。

    client 只给测试注入（MockTransport），生产不传。
    私网目标默认拒绝：只有"已登记的地址"（用户/平台保存过的那个 host:port）才放行，
    否则这个接口就是一个内网端口扫描器（见 url_guard / base_url_registry）。
    allow_private=True 是管理员路径：管理员本来就是这台机器的信任根。
    """
    if not (base_url or "").strip():
        return ProbeResult(False, "bad_url", "请先填写 Base URL")

    if not allow_private:
        from app.utils.pure.url_guard import host_port, is_private_target
        if is_private_target(base_url) and host_port(base_url) not in set(allow_private_hosts):
            return ProbeResult(
                False,
                "blocked_private",
                "拒绝请求内网地址：如果这是你自建的 LLM，请先保存这个 Base URL（保存后才允许测试），"
                "平台已配置的地址不受影响",
            )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=PROBE_TIMEOUT)
    try:
        resp = await client.get(models_url(base_url), headers=_headers(api_key))
        if resp.status_code == 200:
            try:
                models = extract_model_ids(resp.json())
            except ValueError:
                models = ()
            return ProbeResult(True, "ok", f"连接成功，{len(models)} 个模型可用", models=models)
        if resp.status_code in (404, 405):
            # 这家没实现 /models（MiMo 就是），退化成真发一次
            return await _probe_by_chat(client, base_url, api_key, model)
        return _failure(resp, api_key)
    except httpx.TimeoutException:
        return ProbeResult(False, "timeout", "连接超时：检查 Base URL 与服务器出网" + _private_hint(base_url))
    except httpx.HTTPError as e:
        # 含 ConnectError / DNS 失败等；异常文本同样要脱敏（可能带 URL/凭据）
        return ProbeResult(False, "network",
                           f"无法连接：{redact_secret(str(e), api_key)}" + _private_hint(base_url))
    finally:
        if owns_client and client is not None:
            await client.aclose()
