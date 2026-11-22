"""对端 chat 模板探测（零网络，httpx.MockTransport）。

三条必须守住的行为：
1. 能拿到模板 → 判定 + 指纹（llama.cpp 的 Jinja / ollama 的 Go 两种方言）；
2. 拿不到 → ok=False，调用方保持现值，**不阻塞保存**；
3. 5 秒预算：慢端点不能把保存拖住。
"""
import asyncio

import httpx
import pytest

from app.services.agent import template_probe as tp
from app.services.agent.template_probe import probe_chat_template, template_digest

pytestmark = pytest.mark.anyio

BASE = "https://api.example.com/v1"

QWEN_STRICT = (
    "{%- if messages[0].role == 'system' %}"
    "{{- '<|im_start|>system' + messages[0].content }}"
    "{%- endif %}"
    "{%- for message in messages %}"
    "{%- if message.role == 'system' and not loop.first %}"
    "{{- raise_exception('System message must be at the beginning.') }}"
    "{%- endif %}"
    "{%- endfor %}"
)

OLLAMA_GO = (
    "{{- if .System }}{{ .System }}{{ end }}"
    "{{- range $i, $_ := .Messages }}"
    '{{- if eq .Role "user" }}<|User|>{{ .Content }}'
    '{{- else if eq .Role "assistant" }}<|Assistant|>{{ .Content }}'
    "{{- end }}{{- end }}"
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_llama_cpp_props_template_is_leading():
    """/props 挂在服务根上（不在 /v1 下），判定走 Jinja 方言。"""
    def handler(request):
        assert request.url.path == "/props", request.url.path
        return httpx.Response(200, json={"chat_template": QWEN_STRICT})

    async with _client(handler) as c:
        r = await probe_chat_template(BASE, client=c)

    assert r.ok and r.layout == "leading"
    assert r.dialect == "jinja" and r.provider == "llama.cpp"
    assert r.template_hash == template_digest(QWEN_STRICT)


async def test_ollama_show_template_is_leading_in_go_dialect():
    """/api/version 确认存在，/api/show 拿 Go 模板；判定要走 Go 方言而不是 Jinja。"""
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.20.4"})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"template": OLLAMA_GO})
        return httpx.Response(404, text="nope")

    async with _client(handler) as c:
        r = await probe_chat_template(BASE, model="deepseek-r1:7b", client=c)

    assert r.ok and r.layout == "leading"
    assert r.dialect == "go" and r.provider == "ollama"
    assert "deepseek-r1:7b" in r.message


async def test_nothing_found_keeps_current_value():
    """对端两样都不是：不写结论，保存照常（调用方看 ok=False 就不动库）。"""
    def handler(request):
        return httpx.Response(404, text="nope")

    async with _client(handler) as c:
        r = await probe_chat_template(BASE, client=c)

    assert r.ok is False
    assert "没拿到" in r.message


async def test_slow_endpoint_does_not_block_saving():
    """预算到点就返回 ok=False —— 保存不能被一个挂住的端点拖住。

    （用脚本内临时改常量而不是 monkeypatch：本仓库的 run_without_pytest 只实现
    conftest 里那几个 fixture。）
    """
    async def handler(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json={"chat_template": QWEN_STRICT})

    original = tp.PROBE_BUDGET
    tp.PROBE_BUDGET = 0.15
    try:
        async with _client(handler) as c:
            r = await tp.probe_chat_template(BASE, client=c)
    finally:
        tp.PROBE_BUDGET = original

    assert r.ok is False


def test_digest_is_stable_and_short():
    assert template_digest("a") == template_digest("a")
    assert template_digest("a") != template_digest("b")
    assert len(template_digest("x")) == 16
