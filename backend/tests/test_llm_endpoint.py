"""LLM 端点拼接（app/utils/pure/llm_endpoint.py）。纯函数。

对应两起线上事故：
- 小米 MiMo 的 /models 返回 404（缺少 /v1），绑定 API Key 必然失败；
- 通义千问预设的 base_url 已含 /v1，再补一次得到 /v1/v1/chat/completions，404。

规则：base_url 末尾已含版本段（/vN）则不再补，否则补 /v1。

除固定该规则外，本文件遍历真实 PRESETS 校验全部预设：新增预设若写错 base_url，会立即失败。
"""
from app.services.agent.provider_presets import PRESETS
from app.utils.pure.llm_endpoint import (
    api_root,
    chat_completions_url,
    embeddings_url,
    models_url,
)


def test_appends_v1_when_missing():
    """缺少 /v1 时 /models 返回 404（MiMo 事故）。"""
    assert api_root("https://api.xiaomimimo.com") == "https://api.xiaomimimo.com/v1"
    assert models_url("https://api.xiaomimimo.com") == "https://api.xiaomimimo.com/v1/models"


def test_does_not_append_when_version_present():
    """base_url 已含 /v1 时不得再补（通义千问事故：此前聊天一直是 404）。"""
    dashscope = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert api_root(dashscope) == dashscope, "自带 /v1 的 base_url 不能再补一次"
    assert chat_completions_url(dashscope) == dashscope + "/chat/completions"


def test_keeps_non_v1_version_segment():
    """版本段可以是 v4，不能假定为 v1。"""
    zhipu = "https://open.bigmodel.cn/api/paas/v4"
    assert chat_completions_url(zhipu) == zhipu + "/chat/completions"


def test_strips_trailing_slash():
    assert api_root("https://api.deepseek.com/") == "https://api.deepseek.com/v1"
    assert api_root("https://api.deepseek.com///") == "https://api.deepseek.com/v1"


def test_empty_or_none_base_url_does_not_crash():
    assert api_root("") == "/v1"
    assert api_root(None) == "/v1"
    assert chat_completions_url(None) == "/v1/chat/completions"


def test_server_root_strips_version_segment():
    """服务根端点（/props、/api/*）挂在根上，不在 /v1 下。"""
    from app.utils.pure.llm_endpoint import server_root

    assert server_root("http://localhost:11434/v1") == "http://localhost:11434"
    assert server_root("http://localhost:11434") == "http://localhost:11434"
    assert server_root("https://api.deepseek.com") == "https://api.deepseek.com"
    assert server_root("https://open.bigmodel.cn/api/paas/v4") == "https://open.bigmodel.cn/api/paas"
    assert server_root("https://generativelanguage.googleapis.com/v1beta/openai") == (
        "https://generativelanguage.googleapis.com/v1beta"
    )


def test_embeddings_accepts_a_full_path():
    """embedding 的 base_url 允许直接给到 /embeddings，此时不再拼接。"""
    full = "http://localhost:11434/v1/embeddings"
    assert embeddings_url(full) == full
    assert embeddings_url("http://localhost:11434/v1") == full
    assert embeddings_url("http://localhost:11434") == full


def test_is_local_endpoint_covers_self_hosted_shapes():
    """自建推理的地址形态：本机、内网段、容器网关、mDNS。

    判错这一项的代价是超时口径：120 秒卡本地 CPU 推理 = 每一轮都被判成超时，
    用户看到的是"AI 永远不回"，日志里只有一句空 ReadTimeout（2026-11-22 实测）。
    """
    from app.utils.pure.llm_endpoint import is_local_endpoint

    for local in (
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434",
        "http://172.17.0.1:11434/v1",          # docker 默认网关（ollama 容器在宿主机上）
        "http://192.168.1.9:8000/v1",
        "http://10.0.0.5/v1",
        "http://host.docker.internal:11434/v1",
        "http://ollama.local/v1",
        "127.0.0.1:11434",
    ):
        assert is_local_endpoint(local), local

    for remote in (
        "https://api.deepseek.com",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://8.8.8.8/v1",                  # 公网 IP 不是内网
        "",
    ):
        assert not is_local_endpoint(remote), remote


def test_every_preset_produces_a_wellformed_endpoint():
    """遍历真实预设，校验 chat / models 端点不含重复版本段。"""
    assert PRESETS, "预设列表为空"
    for key, preset in PRESETS.items():
        base = preset["base_url"]
        assert base.startswith(("http://", "https://")), key
        for url in (chat_completions_url(base), models_url(base)):
            assert "/v1/v1" not in url, f"{key} 重复版本段: {url}"
            assert url.count("/v1/") <= 1, f"{key} 多个版本段: {url}"

        chat = chat_completions_url(base)
        assert chat.endswith("/chat/completions"), f"{key}: {chat}"
        assert chat.count("/chat/completions") == 1, f"{key}: {chat}"
