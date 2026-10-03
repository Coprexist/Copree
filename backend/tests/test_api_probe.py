"""供应商连通性探针的判定逻辑 —— 零网络（httpx.MockTransport）。

覆盖 2026-09-13 的两类真实事故：
1. 小米 MiMo 的 /models 是 404，但聊天是通的 → 必须兜底成"连接成功"；
2. 401 不能被兜底掩盖（路径是通的，真因是 key）。
另外守住"响应体里的 key 不能原样回给浏览器"。
"""
import httpx
import pytest

from app.services.agent.api_probe import probe_provider

pytestmark = pytest.mark.anyio

MIMO = "https://api.xiaomimimo.com"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_models_ok_reports_model_count():
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert r.ok and r.kind == "ok" and r.model_count == 3


async def test_models_404_falls_back_to_tiny_chat():
    """/models 不存在 → 真发一次极小 chat；MiMo 这条路径必须判成功"""
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(404, text="<html>404 Not Found</html>")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert r.ok and r.kind == "ok"
    assert calls == ["/v1/models", "/v1/chat/completions"]
    assert "mimo-v2.5" in r.message          # 模型名取自预设


async def test_chat_probe_body_is_minimal():
    """兜底请求必须是极小请求：max_tokens=1、不流式，别真让模型写作文"""
    body: dict = {}

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(404)
        import json
        body.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": []})

    async with _client(handler) as c:
        await probe_provider(MIMO, "sk-test", client=c)

    assert body["max_tokens"] == 1 and body["stream"] is False
    assert body["messages"][0]["role"] == "user"


async def test_auth_error_is_not_downgraded():
    """401 不能退化成 chat —— 路径是通的，退化只会掩盖真因"""
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert not r.ok and r.kind == "auth"
    assert calls == ["/v1/models"]           # 只有一次请求，没有兜底
    assert "API Key" in r.message            # 文案要指向 key，而不是"路径不对"


async def test_invalid_key_echo_is_redacted():
    """供应商把 key 原样回显在错误里时，不能让它出现在用户界面上"""
    def handler(request):
        return httpx.Response(401, text='{"error":"bad key sk-secret-123"}')

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-secret-123", client=c)

    assert "sk-secret-123" not in r.message
    assert "***" in r.message


async def test_404_on_chat_is_reported_as_endpoint_problem():
    """两条路都 404 → 说"端点/版本段不对"，而不是"key 不对"（事故里最误导人的一点）"""
    def handler(request):
        return httpx.Response(404, text="404 Not Found")

    async with _client(handler) as c:
        r = await probe_provider("https://example.com", "sk-test", client=c)

    assert not r.ok and r.kind == "not_found"
    assert "Base URL" in r.message


async def test_html_error_page_is_not_dumped_on_user():
    """网关的 404 HTML 页对用户零诊断价值，只留一句说明"""
    def handler(request):
        return httpx.Response(404, text="<html><head><title>404 Not Found</title></head></html>")

    async with _client(handler) as c:
        r = await probe_provider("https://example.com", "sk-test", client=c)

    assert "<html>" not in r.message
    assert "HTML 错误页" in r.message


async def test_timeout_and_network_are_distinguished():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def refused(request):
        raise httpx.ConnectError("connection refused")

    async with _client(timeout) as c:
        r1 = await probe_provider(MIMO, "sk", client=c)
    async with _client(refused) as c:
        r2 = await probe_provider(MIMO, "sk", client=c)

    assert r1.kind == "timeout" and "超时" in r1.message
    assert r2.kind == "network" and "无法连接" in r2.message


async def test_empty_base_url_short_circuits():
    r = await probe_provider("", "sk-test")

    assert not r.ok and r.kind == "bad_url"


def test_resolve_probe_model_prefers_preset():
    from app.services.agent.api_probe import resolve_probe_model

    assert resolve_probe_model(MIMO) == "mimo-v2.5"
    assert resolve_probe_model("https://api.deepseek.com") == "deepseek-flash"
    # 不认识的地址 → 平台默认模型（不抛异常）
    assert resolve_probe_model("https://not-a-real-provider.example") != ""


async def test_probe_returns_model_ids_for_fetch_button():
    """管理页「获取模型」按钮靠这个字段填列表"""
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "mimo-v2.5"}, {"id": "mimo-v2-flash"}]})

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert r.models == ("mimo-v2.5", "mimo-v2-flash")
    assert r.model_count == 2


def test_extract_model_ids_accepts_both_shapes():
    from app.services.agent.api_probe import extract_model_ids

    assert extract_model_ids({"data": [{"id": "a"}, {"id": "b"}]}) == ("a", "b")
    assert extract_model_ids({"models": [{"name": "qwen3"}]}) == ("qwen3",)   # 网关/Ollama 原生形状
    assert extract_model_ids({"data": [{"nope": 1}, "junk", None]}) == ()     # 认不出就不猜
    assert extract_model_ids({"data": []}) == ()
    assert extract_model_ids(None) == ()


async def test_private_target_is_refused_without_registration():
    """不给白名单时私网地址必须直接拒——否则这个接口就是个内网端口扫描器"""
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    async with _client(handler) as c:
        r = await probe_provider("http://127.0.0.1:18234", "sk-test", client=c)

    assert not r.ok and r.kind == "blocked_private"
    assert calls == []          # 一个请求都没发出去


async def test_private_target_allowed_once_registered():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    async with _client(handler) as c:
        r = await probe_provider(
            "http://127.0.0.1:18234", "sk-test", client=c,
            allow_private_hosts={"127.0.0.1:18234"},
        )

    assert r.kind != "blocked_private"
    assert calls                       # 真的发出去了


async def test_public_target_needs_no_registration():
    def handler(request):
        return httpx.Response(401, json={"error": "nope"})

    async with _client(handler) as c:
        r = await probe_provider("https://api.deepseek.com", "sk-test", client=c)

    assert r.kind == "auth"            # 公网照旧，一个字节都没变


def test_url_guard_marks_internal_targets():
    from app.utils.pure.url_guard import host_port, is_private_target

    for u in ("http://localhost:11434", "http://127.0.0.1:8000", "http://172.18.0.1:11434",
              "http://192.168.1.5:11434", "http://169.254.10.10/latest/meta-data",
              "http://10.0.0.5", "http://[::1]:8000"):
        assert is_private_target(u), u
    for u in ("https://api.deepseek.com", "https://api.xiaomimimo.com", "http://8.8.8.8:80"):
        assert not is_private_target(u), u
    assert host_port("https://api.deepseek.com") == "api.deepseek.com:443"
    assert host_port("http://192.168.1.5:11434") == "192.168.1.5:11434"


def test_redact_secret_is_idempotent_without_secret():
    from app.services.agent.api_probe import DETAIL_LIMIT, redact_secret

    assert redact_secret("plain text", None) == "plain text"
    assert len(redact_secret("x" * 1000, None)) == DETAIL_LIMIT
