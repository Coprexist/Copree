"""本地通道的思考开关与 reasoning 字段兼容（零网络：httpx.MockTransport）。

2026-11-24 真机实测：qwen3:4b 走 ollama 的 OpenAI 兼容层时**默认先思考**，思考 token
与正文抢同一个 max_tokens——256 token 花 42 秒、800 token 花 109 秒，正文全是空串，
用户看到的现象是「AI 一言不发」。两条修正在这里锁住：

1. 请求侧：本地端点显式发 `reasoning_effort: "none"`（ollama 认，llama.cpp / vLLM
   忽略且不会 400）；公网通道不替它做主，payload 逐字节不变。
2. 响应侧：思考内容有两个名字——DeepSeek 叫 `reasoning_content`，ollama 叫
   `reasoning`。只读一个名字，思考内容就被整段丢掉。
"""
import httpx
import pytest

from app.ai.llm import _build_chat_payload, _chat_completion_non_streaming

LOCAL = "http://172.17.0.1:11434/v1"
PUBLIC = "https://api.example.com/v1"


def _payload(*, api_base_url: str, thinking_enabled=None, provider_supports_thinking=False):
    return _build_chat_payload(
        [{"role": "user", "content": "你好"}], "qwen3:4b", 0.3, 0.9, 800,
        provider_supports_thinking=provider_supports_thinking,
        thinking_enabled=thinking_enabled,
        api_base_url=api_base_url,
    )


def test_local_endpoint_asks_for_no_thinking():
    """本地通道要显式关思考，否则思考会把 max_tokens 吃光、正文空。"""
    assert _payload(api_base_url=LOCAL)["reasoning_effort"] == "none"
    assert _payload(api_base_url="http://localhost:8080/v1")["reasoning_effort"] == "none"
    assert _payload(api_base_url="http://host.docker.internal:11434/v1")["reasoning_effort"] == "none"


def test_public_endpoint_payload_stays_byte_identical():
    """公网通道对未知字段的态度各不相同，不替它做主——今天的 payload 不变。"""
    assert "reasoning_effort" not in _payload(api_base_url=PUBLIC)


def test_compatible_thinking_provider_keeps_deepseek_dialect():
    """支持 thinking 的通道用 DeepSeek 的 thinking 对象，不发 reasoning_effort。"""
    payload = _payload(api_base_url=LOCAL, thinking_enabled=False, provider_supports_thinking=True)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_caller_wants_thinking_on_local_endpoint():
    """调用方明确要思考时不发：那是调用方的决定，不是通道能力的默认值。"""
    assert "reasoning_effort" not in _payload(api_base_url=LOCAL, thinking_enabled=True)


def _client_factory(handler):
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler))

    return make


@pytest.mark.asyncio
async def test_ollama_reasoning_field_is_kept(monkeypatch):
    """ollama 的思考内容在 evidence 字段 `reasoning` 里，不能在解析时被丢掉。"""
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {"role": "assistant", "content": "", "reasoning": "我先想一想"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await _chat_completion_non_streaming(
        [{"role": "user", "content": "你好"}], "qwen3:4b", LOCAL, max_tokens=16,
        provider_supports_thinking=False,
    )
    assert out["reasoning_content"] == "我先想一想"
    assert out["content"] == ""


@pytest.mark.asyncio
async def test_deepseek_reasoning_content_kept(monkeypatch):
    """DeepSeek 那套字段名照旧能读到（不能为了兼容 ollama 把它丢了）。"""
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{
                "message": {"role": "assistant", "content": "", "reasoning_content": "深想"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    out = await _chat_completion_non_streaming(
        [{"role": "user", "content": "你好"}], "deepseek-flash", PUBLIC, max_tokens=16,
        provider_supports_thinking=True,
    )
    assert out["reasoning_content"] == "深想"