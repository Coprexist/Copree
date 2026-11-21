"""把一段文字合成语音（TTS）——各家请求形状不同，产出统一成 mp3 字节

为什么统一要 mp3：QQ 语音消息实测 mp3 直发就能播（2026-11-14 私聊验证），
平台侧不必背 silk 编码器；站内播放器也吃 mp3。哪家默认吐别的格式，就在这里要求它吐 mp3。

只做"文字 → 音频"这一件事：配置读写在 voice_config.py，连通性在 voice_probe.py，
发到哪个渠道由工具（chat_social/send_voice.py）决定。
"""
from __future__ import annotations

import logging

import httpx

from app.utils.outbound_http import request_pinned

logger = logging.getLogger(__name__)

SYNTH_TIMEOUT = 120.0          # 长句合成慢，但要有个上限：阻塞的回复比没有语音更糟
DETAIL_LIMIT = 200
MINIMAX_DEFAULT_MODEL = "speech-02-hd"


class VoiceSynthError(RuntimeError):
    """合成失败：kind 与 voice_probe 的归类同口径，message 是给人看的一句话"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _redact(text: str, secret: str | None) -> str:
    out = (text or "").strip()
    if secret:
        out = out.replace(secret, "***")
    return out[:DETAIL_LIMIT]


def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _http_error(resp: httpx.Response, secret: str | None, what: str) -> VoiceSynthError:
    if resp.status_code in (401, 403):
        return VoiceSynthError("auth", f"{what}：认证失败（API Key 无效或已撤销）")
    if resp.status_code == 429:
        return VoiceSynthError("rate_limited", f"{what}：触发限流，稍后再试")
    if resp.status_code >= 500:
        return VoiceSynthError("server_error", f"{what}：供应商内部错误（HTTP {resp.status_code}）")
    return VoiceSynthError(
        "http_error", f"{what}：HTTP {resp.status_code} {_redact(resp.text, secret)}",
    )


def _check_minimax_body(payload, secret: str | None) -> None:
    """MiniMax 的业务错误也是 HTTP 200，必须解 base_resp"""
    if not isinstance(payload, dict):
        raise VoiceSynthError("http_error", "MiniMax 返回的不是 JSON")
    base = payload.get("base_resp") or {}
    try:
        code = int(base.get("status_code") or 0)
    except (TypeError, ValueError):
        code = 0
    if code == 0:
        return
    msg = str(base.get("status_msg") or "")
    kind = "quota" if code in (1008, 2053, 2056, 2067) else (
        "auth" if code in (1004, 2049) else (
            "rate_limited" if code in (1002, 2045, 2046, 2047) else "http_error"))
    raise VoiceSynthError(kind, f"MiniMax 合成失败（{code}）：{_redact(msg, secret)}")


async def synthesize(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    voice_id: str,
    text: str,
    style: str = "",
    model: str = "",
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """合成一段语音，返回 mp3 字节；失败抛 VoiceSynthError（kind 与探针同口径）"""
    body_text = (text or "").strip()
    if not body_text:
        raise VoiceSynthError("bad_request", "没有要念的内容")
    if not _base(base_url):
        raise VoiceSynthError("bad_url", "这套语音配置没有 Base URL")
    if not voice_id.strip():
        raise VoiceSynthError("bad_request", "这套语音配置没有填音色（voice_id）")

    key = str(provider or "").strip().lower()
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=SYNTH_TIMEOUT)
    try:
        if key == "breeze":
            return await _synth_breeze(client, base_url, api_key, voice_id, body_text, style)
        if key.startswith("minimax"):
            return await _synth_minimax(client, base_url, api_key, voice_id, body_text, model)
        return await _synth_openai(client, base_url, api_key, voice_id, body_text, model)
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def _synth_breeze(
    client: httpx.AsyncClient, base_url: str, api_key: str, voice_id: str,
    text: str, style: str,
) -> bytes:
    """Breeze 沿用 ElevenLabs 那套：返回裸 mp3，instructions 就是"怎么说" """
    body: dict = {"text": text}
    if style.strip():
        body["instructions"] = style.strip()
    resp = await request_pinned(
        client, "POST",
        f"{_base(base_url)}/text-to-speech/{voice_id}?output_format=mp3",
        headers={"Content-Type": "application/json", "xi-api-key": api_key},
        json=body,
    )
    if resp.status_code != 200:
        raise _http_error(resp, api_key, "Breeze 合成失败")
    data = resp.content
    if len(data) < 512:
        raise VoiceSynthError("http_error", f"Breeze 只返回了 {len(data)} 字节，不像音频")
    return data


async def _synth_minimax(
    client: httpx.AsyncClient, base_url: str, api_key: str, voice_id: str,
    text: str, model: str,
) -> bytes:
    """MiniMax t2a_v2：成功时音频在 data.audio（hex 字符串），不在 HTTP body 里"""
    resp = await request_pinned(
        client, "POST", f"{_base(base_url)}/t2a_v2",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": (model or "").strip() or MINIMAX_DEFAULT_MODEL,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": 24000, "bitrate": 128000,
                              "format": "mp3", "channel": 1},
        },
    )
    if resp.status_code != 200:
        raise _http_error(resp, api_key, "MiniMax 合成失败")
    try:
        payload = resp.json()
    except ValueError as e:
        raise VoiceSynthError("http_error", f"MiniMax 返回的不是 JSON：{e}") from e
    _check_minimax_body(payload, api_key)
    hex_audio = str(((payload.get("data") or {}) or {}).get("audio") or "")
    if not hex_audio:
        raise VoiceSynthError("http_error", "MiniMax 没有返回音频")
    try:
        return bytes.fromhex(hex_audio)
    except ValueError as e:
        raise VoiceSynthError("http_error", f"MiniMax 音频解码失败：{e}") from e


async def _synth_openai(
    client: httpx.AsyncClient, base_url: str, api_key: str, voice_id: str,
    text: str, model: str,
) -> bytes:
    """OpenAI 兼容 /audio/speech：直接回音频字节"""
    resp = await request_pinned(
        client, "POST", f"{_base(base_url)}/audio/speech",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": (model or "").strip() or "tts-1",
            "input": text,
            "voice": voice_id,
            "response_format": "mp3",
        },
    )
    if resp.status_code != 200:
        raise _http_error(resp, api_key, "语音合成失败")
    data = resp.content
    if len(data) < 512:
        raise VoiceSynthError("http_error", f"供应商只返回了 {len(data)} 字节，不像音频")
    return data
