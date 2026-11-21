"""TTS 合成：各家请求形状不同，产出统一 mp3；"HTTP 200 但业务失败"必须抛出来"""
import json

import httpx
import pytest

from app.services.agent.voice_tts import VoiceSynthError, synthesize

pytestmark = pytest.mark.anyio

AUDIO = b"ID3" + b"\x00" * 2048          # 足够长，过"不像音频"的最小长度检查
BREEZE = "https://api.breeze.blue/v1"
MINIMAX = "https://api.minimaxi.com/v1"
OPENAI = "https://api.openai.com/v1"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_breeze_sends_style_as_instructions():
    """Breeze 的 instructions 就是 AI 自己写的那句"怎么说"，别丢在路上"""
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=AUDIO)

    async with _client(handler) as c:
        audio = await synthesize(
            provider="breeze", base_url=BREEZE, api_key="brz-k", voice_id="voc_1",
            text="晚安", style="轻快、好奇，尾音带一点笑", client=c,
        )

    assert audio == AUDIO
    assert seen["path"] == "/v1/text-to-speech/voc_1"
    assert seen["key"] == "brz-k"
    assert seen["body"]["text"] == "晚安"
    assert seen["body"]["instructions"] == "轻快、好奇，尾音带一点笑"


async def test_empty_style_is_omitted_not_sent_blank():
    seen: dict = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=AUDIO)

    async with _client(handler) as c:
        await synthesize(provider="breeze", base_url=BREEZE, api_key="k",
                         voice_id="voc_1", text="早", style="  ", client=c)

    assert "instructions" not in seen["body"]


async def test_minimax_audio_comes_back_as_hex_and_is_decoded():
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "data": {"audio": AUDIO.hex(), "status": 2},
        })

    async with _client(handler) as c:
        audio = await synthesize(
            provider="minimax-cn", base_url=MINIMAX, api_key="mm-k",
            voice_id="voice-1", text="在的", client=c,
        )

    assert audio == AUDIO, "MiniMax 的音频是 hex 字符串，要解成字节再存"
    assert seen["path"] == "/v1/t2a_v2"
    assert seen["body"]["audio_setting"]["format"] == "mp3", "统一要 mp3，QQ 直接能播"
    assert seen["body"]["voice_setting"]["voice_id"] == "voice-1"


async def test_minimax_business_error_on_http_200_is_raised():
    def handler(request):
        return httpx.Response(200, json={
            "base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})

    async with _client(handler) as c:
        with pytest.raises(VoiceSynthError) as err:
            await synthesize(provider="minimax-cn", base_url=MINIMAX, api_key="mm-k",
                             voice_id="v", text="喂", client=c)

    assert err.value.kind == "quota", "欠费归欠费，别当成普通失败"


async def test_openai_compatible_speech_endpoint():
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=AUDIO)

    async with _client(handler) as c:
        audio = await synthesize(
            provider="openai", base_url=OPENAI, api_key="sk", voice_id="alloy",
            text="hi", model="tts-1-hd", client=c,
        )

    assert audio == AUDIO
    assert seen["path"] == "/v1/audio/speech"
    assert seen["body"]["voice"] == "alloy" and seen["body"]["model"] == "tts-1-hd"


async def test_missing_voice_id_says_what_to_fix():
    def handler(request):  # pragma: no cover - 不该被调用
        raise AssertionError("配置不全时不该发请求")

    async with _client(handler) as c:
        with pytest.raises(VoiceSynthError) as err:
            await synthesize(provider="breeze", base_url=BREEZE, api_key="k",
                             voice_id="  ", text="喂", client=c)

    assert err.value.kind == "bad_request" and "音色" in err.value.message
