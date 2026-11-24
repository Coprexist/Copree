"""本地通道的思考开关与 reasoning 字段兼容（零网络：httpx.MockTransport）。

2026-11-24 真机实测，三条事实决定了这里的行为：

1. 思考 token 与正文抢**同一个输出上限**——OpenAI `max_completion_tokens` 的注释原文
   写 including visible output tokens and reasoning tokens；Anthropic thinking 文档写
   thinking tokens count toward your max_tokens；ollama 实测 completion_tokens 恒等于
   max_tokens 满额。所以开关必须能把思考真的关掉，否则短输出调用会被思考吃空。
2. 本地端点认 `reasoning_effort: "none"`（ollama 实测有效；llama.cpp / vLLM 忽略且
   不会 400），不认 DeepSeek 的 `thinking` 对象。
3. 思考型模型（qwen3 / gpt-oss 这类）**默认就思考**，所以"开"不需要发参数；只有显式关
   才发 none。"不表态"（None）同样不发，交给模型默认。

响应侧思考内容有两个名字：DeepSeek 叫 `reasoning_content`，ollama 叫 `reasoning`，
只读一个名字就会把思考整段丢掉。
"""
import httpx
import pytest

from app.ai.llm import _build_chat_payload, _chat_completion_non_streaming
from app.utils.pure.llm_endpoint import thinking_supported_for

LOCAL = "http://172.17.0.1:11434/v1"
PUBLIC = "https://api.example.com/v1"


def _payload(*, api_base_url: str, thinking_enabled=None, provider_supports_thinking=False):
    return _build_chat_payload(
        [{"role": "user", "content": "你好"}], "qwen3:4b", 0.3, 0.9, 800,
        provider_supports_thinking=provider_supports_thinking,
        thinking_enabled=thinking_enabled,
        api_base_url=api_base_url,
    )


def test_local_endpoint_turns_thinking_off_explicitly():
    """显式关：本地通道发 reasoning_effort=none，思考不再抢正文的预算。"""
    for url in (LOCAL, "http://localhost:8080/v1", "http://host.docker.internal:11434/v1"):
        assert _payload(api_base_url=url, thinking_enabled=False)["reasoning_effort"] == "none"


def test_local_endpoint_default_leaves_model_default():
    """不表态：不发参数，思考型模型照它自己的默认思考（"可以有思考"）。"""
    payload = _payload(api_base_url=LOCAL)
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload


def test_local_endpoint_thinking_on_sends_nothing():
    """显式开：本地模型默认就思考，不需要（也没有）统一的"开"参数。"""
    payload = _payload(api_base_url=LOCAL, thinking_enabled=True)
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload


def test_local_endpoint_never_uses_deepseek_dialect():
    """本地通道即使被标成支持思考，也只说 reasoning_effort，不发 thinking 对象。"""
    payload = _payload(api_base_url=LOCAL, thinking_enabled=False, provider_supports_thinking=True)
    assert payload["reasoning_effort"] == "none"
    assert "thinking" not in payload


def test_local_thinking_on_sends_no_output_cap():
    """思考开着时不设输出上限：让它想完，否则截断后下一轮还得从头再想。"""
    assert "max_tokens" not in _payload(api_base_url=LOCAL, thinking_enabled=True)
    assert "max_tokens" not in _payload(api_base_url=LOCAL)  # 不表态也交给模型


def test_local_thinking_off_keeps_output_cap():
    """关思考时正文需要边界，上限照发。"""
    assert _payload(api_base_url=LOCAL, thinking_enabled=False)["max_tokens"] == 800


def test_public_endpoint_keeps_output_cap():
    """公网通道照旧发上限（成本与超时都要有边界）。"""
    assert _payload(api_base_url=PUBLIC)["max_tokens"] == 800
    assert _payload(api_base_url=PUBLIC, thinking_enabled=True)["max_tokens"] == 800


def test_public_endpoint_payload_stays_byte_identical():
    """公网通道对未知字段的态度各不相同，不替它做主——今天的 payload 不变。"""
    payload = _payload(api_base_url=PUBLIC)
    assert "reasoning_effort" not in payload
    assert "thinking" not in payload


def test_compatible_provider_keeps_deepseek_dialect():
    """支持 thinking 的公网通道用 DeepSeek 的 thinking 对象，不发 reasoning_effort。"""
    payload = _payload(api_base_url=PUBLIC, thinking_enabled=False, provider_supports_thinking=True)
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


def test_thinking_supported_for_covers_local_and_deepseek():
    """开关是否显示：地址即事实（DeepSeek / 本地自建），其余看用户勾选。"""
    assert thinking_supported_for("https://api.deepseek.com/v1")
    assert thinking_supported_for(LOCAL)
    assert thinking_supported_for("http://192.168.1.9:8080/v1")
    assert thinking_supported_for(PUBLIC, explicit=True)
    assert not thinking_supported_for(PUBLIC)


def _client_factory(handler):
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler))

    return make


@pytest.mark.asyncio
async def test_ollama_reasoning_field_is_kept(monkeypatch):
    """ollama 的思考内容在 `reasoning` 字段里，不能在解析时被丢掉。"""
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