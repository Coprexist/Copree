"""通道能力判定与短记忆（app/utils/pure/llm_capability.py）。纯函数 + 进程内集合。

背景：response_worker 无条件给 payload 塞 tools，本地推理模型（deepseek-r1:7b）返回
`400 ... does not support tools`，整轮回复失败。这里的判定决定要不要降级重试，
所以"误判"和"漏判"都必须有用例固定：

- 漏判 → 用户彻底收不到回复（不可接受）；
- 误判 → 这一轮少一组工具，仍能回话（可接受，所以判定宽松）。
"""
from app.utils.pure import llm_capability as cap


def test_matches_real_ollama_wording():
    text = (
        'LLM API 错误 (400): {"error":{"message":"registry.ollama.ai/library/'
        'deepseek-r1:7b does not support tools","type":"api_error","param":null,'
        '"code":null}}'
    )
    assert cap.is_tools_unsupported_error(text)


def test_matches_common_rejection_phrasings():
    for text in (
        "unsupported parameter: tool_choice",
        "tools is not supported by this model",
        "this model does not support function calling",
        "invalid request: function calling not available",
        "当前模型不支持工具调用",
    ):
        assert cap.is_tools_unsupported_error(text), text


def test_ignores_unrelated_failures():
    # 工具这个词出现了、但说的是别的事：不该降级（降级会白摘工具）
    for text in (
        "LLM API 错误 (401): invalid api key",
        "LLM API 错误 (429): rate limit exceeded, retry later",
        "this model's maximum context length is 8192 tokens",
        "",
        "LLM API 错误 (500): upstream timeout",
        # 提到了 tool、但说的是请求形状（历史里的畸形 tool_calls），不是模型能力：
        # 摘掉 tools 修不好这个问题，还会白丢整轮工具能力 —— 所以判定必须要求拒绝词。
        "LLM API 错误 (400): tool_calls[0].function.name: field required",
    ):
        assert not cap.is_tools_unsupported_error(text), repr(text)


def test_memory_is_scoped_by_base_url_and_model():
    cap.forget_no_tools()
    base, model = "http://172.17.0.1:11434/v1", "deepseek-r1:7b"
    assert cap.supports_tools(base, model)
    cap.remember_no_tools(base, model)
    assert not cap.supports_tools(base, model)
    # 大小写/尾斜杠不同仍是同一个通道
    assert not cap.supports_tools("HTTP://172.17.0.1:11434/v1/", "DeepSeek-R1:7B")
    # 换模型 / 换通道不受影响
    assert cap.supports_tools(base, "qwen3:4b")
    assert cap.supports_tools("https://api.deepseek.com/v1", model)
    cap.forget_no_tools()


def test_memory_expires_so_capability_is_reprobed():
    # 运行器没有 monkeypatch fixture：直接换掉模块里的 time 引用（不动全局 time 模块），
    # 用完在 finally 里换回来。
    import time as real_time
    from types import SimpleNamespace

    cap.forget_no_tools()
    base, model = "http://172.17.0.1:11434/v1", "deepseek-r1:7b"
    clock = [1000.0]
    cap.time = SimpleNamespace(monotonic=lambda: clock[0])
    try:
        cap.remember_no_tools(base, model)
        assert not cap.supports_tools(base, model)
        clock[0] += cap._NO_TOOLS_TTL_SECONDS + 1
        # 过期后必须重新试探，否则用户换了模型还一直被摘掉工具
        assert cap.supports_tools(base, model)
        assert cap.supports_tools(base, model)  # 过期清理只做一次，之后保持接受
    finally:
        cap.time = real_time
        cap.forget_no_tools()
