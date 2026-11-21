"""语音（TTS）供应商检测：三家三种判定口径，零网络（httpx.MockTransport）

钉住三件事：
1. Breeze 只用官方自检端点，且"额度耗尽"仍是合法 key（不能判死）；
2. MiniMax 的 HTTP 200 不等于成功——必须解析 base_resp，2049 要按区域说；
3. 响应体里回显的 key 不能原样回给前端。
"""
import httpx
import pytest

from app.services.agent.voice_probe import probe_voice

pytestmark = pytest.mark.anyio

BREEZE = "https://api.breeze.blue/v1"
MINIMAX_CN = "https://api.minimaxi.com/v1"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_breeze_uses_its_self_check_endpoint_and_checks_the_header():
    seen: dict = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("xi-api-key")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "active", "credit_remaining": 123.5})

    async with _client(handler) as c:
        r = await probe_voice("breeze", BREEZE, "brz-abc", client=c)

    assert r.ok and r.kind == "ok"
    assert seen["path"] == "/v1/api-keys/current", "Breeze 有专门的自检端点，别去猜别的路"
    assert seen["key"] == "brz-abc" and seen["auth"] is None, "认证走 xi-api-key，不是 Bearer"


async def test_breeze_zero_credit_is_still_a_valid_key():
    """"能测通却合成不了"最常见的解释就是额度空了：判 ok，但文案要说出来"""
    def handler(request):
        return httpx.Response(200, json={"status": "active", "credit_remaining": 0})

    async with _client(handler) as c:
        r = await probe_voice("breeze", BREEZE, "brz-abc", client=c)

    assert r.ok is True, "额度耗尽不代表 key 无效"
    assert "额度" in r.message


async def test_breeze_rejects_bad_key():
    def handler(request):
        return httpx.Response(401, json={"detail": "invalid api key"})

    async with _client(handler) as c:
        r = await probe_voice("breeze", BREEZE, "brz-bad", client=c)

    assert not r.ok and r.kind == "auth"


async def test_minimax_http_200_with_error_biz_code_is_a_failure():
    """MiniMax 业务失败时常常还是 HTTP 200，只看状态码会得到虚假的绿灯"""
    def handler(request):
        return httpx.Response(200, json={"base_resp": {
            "status_code": 1008, "status_msg": "insufficient balance"}})

    async with _client(handler) as c:
        r = await probe_voice("minimax-cn", MINIMAX_CN, "mm-key", client=c)

    assert not r.ok and r.kind == "quota", r
    assert "余额" in r.message


async def test_minimax_biz_ok_is_success():
    def handler(request):
        return httpx.Response(200, json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "data": [{"id": "MiniMax-Text-01"}, {"id": "abab6.5s-chat"}],
        })

    async with _client(handler) as c:
        r = await probe_voice("minimax-cn", MINIMAX_CN, "mm-key", client=c)

    assert r.ok and r.kind == "ok"


async def test_minimax_models_404_falls_back_to_a_tiny_chat():
    """/v1/models 在 MiniMax 上不可靠：404 只说明没这条路由，不能把好 key 判死"""
    calls: list[str] = []
    body: dict = {}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(404, text="<html>404</html>")
        import json
        body.update(json.loads(request.content))
        return httpx.Response(200, json={"base_resp": {"status_code": 0}})

    async with _client(handler) as c:
        r = await probe_voice("minimax-cn", MINIMAX_CN, "mm-key", client=c)

    assert r.ok and r.kind == "ok"
    assert calls == ["/v1/models", "/v1/chat/completions"]
    assert body["max_tokens"] == 1 and body["stream"] is False, "探针必须是极小请求"


async def test_minimax_2049_says_region_may_be_wrong():
    """国内站与国际站互不相通：用错站也是 2049，不能只喊 key 无效"""
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(404)
        return httpx.Response(200, json={"base_resp": {
            "status_code": 2049, "status_msg": "invalid api key"}})

    async with _client(handler) as c:
        r = await probe_voice("minimax-intl", MINIMAX_CN, "mm-key", client=c)

    assert not r.ok and r.kind == "region", r
    assert "国内站" in r.message and "国际站" in r.message


async def test_probe_never_echoes_the_key_back():
    secret = "sk-super-secret-123"

    def handler(request):
        return httpx.Response(500, text=f"upstream boom with key={secret}")

    async with _client(handler) as c:
        r = await probe_voice("breeze", BREEZE, secret, client=c)

    assert not r.ok
    assert secret not in r.message, "key 不能原样回给前端"


async def test_empty_base_url_is_reported_before_any_request():
    def handler(request):  # pragma: no cover - 不该被调用
        raise AssertionError("没有 base_url 就不该发请求")

    async with _client(handler) as c:
        r = await probe_voice("breeze", "  ", "k", client=c)

    assert not r.ok and r.kind == "bad_url"
