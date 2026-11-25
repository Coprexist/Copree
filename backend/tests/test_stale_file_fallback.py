"""厂商回 400「这个 file_id 不存在/过期/已删」时的兜底

2026-11-25：图片走 Files API 后，远端文件可能被删/过期/被配额回收。这时 chat 请求会被
整体 400 打回——不兜底就是整轮失败、对方一条回复都收不到。
兜底口径（与 DSH 同款）：把 file 块就地降级成 [图片] 占位，**只重发一次**。
"""
import json

import httpx
import pytest

pytestmark = pytest.mark.anyio

BASE = "https://api.deepseek.com"


def _patch(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _file_message():
    return [{"role": "user", "content": [
        {"type": "text", "text": "看看这张图"},
        {"type": "file", "file_id": "file-api-gone"},
    ]}]


def _ok_response():
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "看到了"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    })


async def test_stale_file_id_degrades_to_placeholder_and_retries_once(monkeypatch):
    from app.ai.llm import _chat_completion_non_streaming

    sent: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) == 1:
            return httpx.Response(400, json={"error": {
                "message": "file-api-gone not found or expired"}})
        return _ok_response()

    _patch(monkeypatch, handler)
    result = await _chat_completion_non_streaming(
        _file_message(), "deepseek-flash", BASE, "sk-test")

    assert result["content"] == "看到了"
    assert len(sent) == 2, "失效只重发一次"
    second = json.dumps(sent[1], ensure_ascii=False)
    assert "file-api-gone" not in second, "第二次不该再引用失效的 file_id"
    assert "看" in second and "图" in second, "正文要保住，只把图换成占位"


async def test_plain_400_is_not_retried(monkeypatch):
    from app.ai.llm import _chat_completion_non_streaming

    sent: list[dict] = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "invalid parameter: temperature"}})

    _patch(monkeypatch, handler)
    with pytest.raises(Exception):
        await _chat_completion_non_streaming(
            _file_message(), "deepseek-flash", BASE, "sk-test")
    assert len(sent) == 1, "与文件无关的 400 不该重试"


async def test_second_stale_failure_propagates(monkeypatch):
    """重试一次仍失效 → 抛出，不进入死循环。"""
    from app.ai.llm import _chat_completion_non_streaming

    sent: list[dict] = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "file not found"}})

    _patch(monkeypatch, handler)
    with pytest.raises(Exception):
        await _chat_completion_non_streaming(
            _file_message(), "deepseek-flash", BASE, "sk-test")
    assert len(sent) == 2, "最多重发一次"


async def test_invalidated_id_is_not_reused(monkeypatch):
    """被标失效的 file_id 不再被复用（下一轮会重新上传）。"""
    from app.services.ai import deepseek_files

    deepseek_files.invalidate(["file-api-x"])
    row = type("R", (), {"expires_at": __import__("datetime").datetime(2099, 1, 1),
                         "remote_file_id": "file-api-x"})()
    assert deepseek_files._reusable(row, __import__("datetime").datetime(2026, 1, 1)) is False
