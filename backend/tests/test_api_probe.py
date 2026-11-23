"""供应商连通性探针的判定逻辑 —— 零网络（httpx.MockTransport）。

覆盖 2026-09-13 的两类真实事故：
1. 小米 MiMo 的 /models 是 404，但聊天是通的 → 必须兜底成"连接成功"；
2. 401 不能被兜底掩盖（路径是通的，真因是 key）。
另外守住"响应体里的 key 不能原样回给浏览器"。
"""
import json

import httpx
import pytest

from app.services.agent.api_probe import probe_provider

pytestmark = pytest.mark.anyio

MIMO = "https://api.xiaomimimo.com"


def _preset_chat_model(base_url: str) -> str:
    """预设里的 chat_model —— 探针兜底用的就是这个值，别在这里手抄模型名"""
    from app.utils.pure.provider_presets import get_all_presets

    target = base_url.rstrip("/")
    return next(
        str(p["chat_model"]) for p in get_all_presets()
        if (p.get("base_url") or "").rstrip("/") == target and p.get("chat_model")
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _chat_only_handler(*, calls: list[str] | None = None, body: dict | None = None):
    """「只有 chat 能通」的通道：/models 与 Ollama 原生 /api/tags 都不存在。

    探针的兜底链是 models → 原生 tags → chat（api_probe.probe_provider），所以三条路径
    都得各自应答。只判 /models 的话，chat 的 JSON 会被当成 /api/tags 的 200 响应，
    探针接着按「这不是 ollama」往下走 —— 表面上还能过，但断言会跟真实链路错开。
    """
    def handler(request):
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path == "/api/tags":
            return httpx.Response(404)
        if path.endswith("/models"):
            return httpx.Response(404, text="<html>404 Not Found</html>")
        if body is not None:
            body.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    return handler


async def test_models_ok_reports_model_count():
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}, {"id": "c"}]})

    async with _client(handler) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert r.ok and r.kind == "ok" and r.model_count == 3


async def test_models_404_falls_back_to_tiny_chat():
    """/models 不存在 → 先问一次 Ollama 原生 /api/tags，再退化成真发一次极小 chat"""
    calls: list[str] = []

    async with _client(_chat_only_handler(calls=calls)) as c:
        r = await probe_provider(MIMO, "sk-test", client=c)

    assert r.ok and r.kind == "ok"
    # 兜底链的顺序就是契约：多一步少一步都要能被这条断言发现
    assert calls == ["/v1/models", "/api/tags", "/v1/chat/completions"]
    assert _preset_chat_model(MIMO) in r.message     # 模型名取自预设


async def test_chat_probe_body_is_minimal():
    """兜底请求必须是极小请求：max_tokens=1、不流式，别真让模型写作文"""
    body: dict = {}

    async with _client(_chat_only_handler(body=body)) as c:
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

    assert resolve_probe_model(MIMO) == _preset_chat_model(MIMO)
    assert resolve_probe_model("https://api.deepseek.com") == _preset_chat_model("https://api.deepseek.com")
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


OLLAMA = "http://127.0.0.1:11434"


async def test_ollama_native_tags_gives_model_ids():
    """本地 ollama：/v1/models 不存在时改问原生 /api/tags，直接拿到模型 id"""
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(404)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3:14b", "model": "qwen3:14b"},
                {"name": "deepseek-r1:8b", "model": "deepseek-r1:8b"},
            ]})
        return httpx.Response(500)

    async with _client(handler) as c:
        r = await probe_provider(OLLAMA, None, client=c, allow_private=True)

    assert r.ok and r.kind == "ok"
    assert calls == ["/v1/models", "/api/tags"]
    assert r.models == ("qwen3:14b", "deepseek-r1:8b")


async def test_200_without_models_array_is_not_mistaken_for_ollama():
    """200 但不是 {"models":[...]} 形状（SPA 首页、网关兜底页）→ 不算 ollama，继续退化"""
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(404)
        if request.url.path == "/api/tags":
            return httpx.Response(200, text="<html><body>home</body></html>")
        return httpx.Response(200, json={"choices": []})

    async with _client(handler) as c:
        r = await probe_provider("http://127.0.0.1:9999", None, client=c, allow_private=True)

    assert r.ok
    assert calls == ["/v1/models", "/api/tags", "/v1/chat/completions"]


async def test_ollama_with_no_models_says_so():
    """真 ollama 但一个模型都没 pull：连上是连上，得说清楚没模型，而不是报 0 个可用"""
    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(404)
        return httpx.Response(200, json={"models": []})

    async with _client(handler) as c:
        r = await probe_provider(OLLAMA, None, client=c, allow_private=True)

    assert r.ok and r.models == ()
    assert "pull" in r.message
