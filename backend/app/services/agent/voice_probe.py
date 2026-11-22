"""语音（TTS）供应商检测 —— "这把 key + 这个 base_url 到底能不能合成"的唯一入口

形状与 api_probe 完全一致（ProbeResult / 出站收口 / 脱敏 / 私网政策 / 人话分类），
因为用户看到的是同一个「测试」按钮；只有判定口径按家分开：

- **Breeze**：官方有专门的自检端点 `GET /v1/api-keys/current`，设计意图就是"开工前先问
  这把钥匙还能不能用"。HTTP 200 = key 合法；**额度耗尽的 200 也算合法**（只是
  credit_remaining=0），所以这种情况判 ok 但文案要说清"能连上、暂时合成不了"，
  否则用户会以为"测通了却发不出声"是玄学。
- **MiniMax**：没有自检端点，`/v1/models` 还可能直接 404（拿它判 key 会把好 key 判死），
  只能调一次极小业务请求，并且**同时看 HTTP 码和响应体 base_resp.status_code**——
  它业务失败时经常 HTTP 200。国内站与国际站互不相通，用错站一律 2049，
  所以那条要按 kind="region" 说"区域可能配错"，不能只喊"key 无效"。
- **OpenAI 兼容**：沿用主站那套两段式（先 /models，404/405 才退化成一次极小 chat）。

一个原则：探针只回答"能不能用"，不替用户挑音色/模型，也不消耗一次真实合成。
"""
from __future__ import annotations

import logging

import httpx

from app.services.agent.api_probe import (
    ProbeResult,
    allow_internal_policy,
    _blocked_result,
    probe_provider,
)
from app.utils.outbound_http import BlockedFetch, request_pinned

logger = logging.getLogger(__name__)

VOICE_PROBE_TIMEOUT = 15.0
DETAIL_LIMIT = 200
# MiniMax 没有自检端点时的试调用：模型名只是用来把"认证 + 路径"验通
MINIMAX_PROBE_MODEL = "MiniMax-Text-01"

# base_resp.status_code → (kind, 人话)。判据是"用户接下来该改什么"，不是码本身
_MINIMAX_CODES: dict[int, tuple[str, str]] = {
    1004: ("auth", "认证失败：API Key 无效"),
    2049: ("region", "Key 无效，或者国内站/国际站配错了（两个站互不相通）"),
    1008: ("quota", "余额不足"),
    2053: ("quota", "积分不足"),
    2056: ("quota", "套餐资源限制"),
    2067: ("quota", "套餐额度已用完，且没开自动消耗"),
    2022: ("permission", "模型不存在或无权使用"),
    2043: ("permission", "该模型不支持这个 voice_id"),
    2057: ("permission", "需要 M Plan 订阅 key，当前这把不够"),
    1002: ("rate_limited", "请求频率超限"),
    2045: ("rate_limited", "请求数增长超限"),
    2046: ("rate_limited", "Token 增长超限"),
    2047: ("rate_limited", "连接数增长超限"),
}


def _redact(text: str, secret: str | None) -> str:
    out = (text or "").strip()
    if secret:
        out = out.replace(secret, "***")
    return out[:DETAIL_LIMIT]


def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _minimax_verdict(resp: httpx.Response, api_key: str | None) -> ProbeResult | None:
    """MiniMax 的业务判定：先看 base_resp，再看 HTTP 码。

    返回 None 表示"这不构成一次失败"，交给调用方决定怎么说成功。
    """
    code: int | None = None
    msg = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        base = body.get("base_resp")
        if isinstance(base, dict):
            try:
                code = int(base.get("status_code"))
            except (TypeError, ValueError):
                code = None
            msg = str(base.get("status_msg") or "")

    if code == 0:
        return None                    # 业务成功
    if code in _MINIMAX_CODES:
        kind, label = _MINIMAX_CODES[code]
        return ProbeResult(False, kind, f"{label}（base_resp {code}）")
    if code is not None:
        return ProbeResult(
            False, "http_error",
            f"MiniMax 业务码 {code}：{_redact(msg or resp.text, api_key)}",
        )
    if resp.status_code == 200:
        return None
    if resp.status_code in (401, 403):
        return ProbeResult(False, "auth", "认证失败：API Key 无效或未被授权")
    if resp.status_code >= 500:
        return ProbeResult(False, "server_error", f"供应商内部错误（HTTP {resp.status_code}）")
    return ProbeResult(
        False, "http_error",
        f"供应商返回 HTTP {resp.status_code}：{_redact(resp.text, api_key)}",
    )


async def _probe_breeze(
    client: httpx.AsyncClient, base_url: str, api_key: str | None, allow_internal,
) -> ProbeResult:
    url = _base(base_url) + "/api-keys/current"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["xi-api-key"] = api_key
    resp = await request_pinned(client, "GET", url, headers=headers, allow_internal=allow_internal)
    if resp.status_code != 200:
        if resp.status_code in (401, 403):
            return ProbeResult(False, "auth", "认证失败：Breeze 的 API Key 无效、已撤销或填错了")
        return ProbeResult(
            False, "http_error",
            f"Breeze 返回 HTTP {resp.status_code}：{_redact(resp.text, api_key)}",
        )
    # 200 = key 合法。额度耗尽也是 200，只把额度当提示，不据此判失败
    credit = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            credit = body.get("credit_remaining")
    except ValueError:
        pass
    if credit in (0, 0.0, "0"):
        return ProbeResult(True, "ok", "Key 有效，但额度已用尽：能连上，暂时合成不了")
    return ProbeResult(True, "ok", "Key 有效，可以合成")


async def _probe_minimax(
    client: httpx.AsyncClient, base_url: str, api_key: str | None, model: str | None,
    allow_internal,
) -> ProbeResult:
    base = _base(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 第一段：/models。MiniMax 常常没这个端点，404 只说明"没这条路由"，不说明 key 错
    picked = ""
    models_resp = await request_pinned(
        client, "GET", base + "/models", headers=headers, allow_internal=allow_internal,
    )
    if models_resp.status_code == 200:
        verdict = _minimax_verdict(models_resp, api_key)
        if verdict is not None:
            return verdict
        try:
            data = models_resp.json().get("data") or []
            picked = str((data[0] or {}).get("id") or "") if data else ""
        except (ValueError, AttributeError):
            data, picked = [], ""
        return ProbeResult(True, "ok", f"连接成功，{len(data)} 个模型可用")

    # 第二段：真发一次极小业务请求（认证 + 路径 + 模型名一起验）
    use_model = (model or "").strip() or picked or MINIMAX_PROBE_MODEL
    resp = await request_pinned(
        client, "POST", base + "/chat/completions",
        headers=headers,
        json={
            "model": use_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": False,
        },
        allow_internal=allow_internal,
    )
    verdict = _minimax_verdict(resp, api_key)
    if verdict is not None:
        return verdict
    return ProbeResult(True, "ok", f"连接成功（已用 {use_model} 试调用一次）")


async def probe_voice(
    provider: str,
    base_url: str,
    api_key: str | None,
    *,
    voice_id: str = "",
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
    allow_private: bool = False,
    allow_private_hosts=(),
) -> ProbeResult:
    """探测一套语音配置能不能用。client 只给测试注入（MockTransport）。

    私网政策与主站探针共用同一份（allow_internal_policy）：这个接口不许变成内网扫描器。
    """
    if not _base(base_url):
        return ProbeResult(False, "bad_url", "请先选择或者填写 Base URL")

    allow_internal = allow_internal_policy(allow_private, allow_private_hosts)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=VOICE_PROBE_TIMEOUT)
    try:
        key = str(provider or "").strip().lower()
        try:
            if key == "breeze":
                return await _probe_breeze(client, base_url, api_key, allow_internal)
            if key.startswith("minimax"):
                return await _probe_minimax(client, base_url, api_key, model, allow_internal)
            # 其它（含 openai 兼容、自填 base_url）：沿用主站那套两段式
            return await probe_provider(
                base_url, api_key, client=client,
                allow_private=allow_private, allow_private_hosts=allow_private_hosts,
            )
        except BlockedFetch as e:
            return await _blocked_result(e, base_url, api_key)
    finally:
        if owns_client and client is not None:
            await client.aclose()
