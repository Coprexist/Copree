# LLM 端点 URL 的唯一入口

**一句话**：所有发往供应商的 URL 都由 `app/utils/pure/llm_endpoint.py` 生成，
调用方**不要再手拼** `f"{base}/v1/chat/completions"`。

## 为什么需要它

各家的 `base_url` 写法不统一，靠"无脑补 /v1"或"无脑不补"都会踩坑（下表全部为实测结论）：

| base_url | 正确做法 | 踩坑实录 |
|---|---|---|
| `https://api.deepseek.com` | 补 `/v1` | /v1 可有可无，两种都能通 |
| `https://api.xiaomimimo.com` | 补 `/v1` | **不补 → `/models` 404**（openresty 直出 404，绑定 AK 必失败）|
| `https://token-plan-cn.xiaomimimo.com` | 补 `/v1` | 同上 |
| `https://dashscope.aliyuncs.com/compatible-mode/v1` | **不补** | 再补 → `/v1/v1/chat/completions` **404** |
| `https://open.bigmodel.cn/api/paas/v4` | **不补** | 版本段是 v4，不是 v1 |
| `https://api.openai.com` / `api.moonshot.cn` / `api.siliconflow.cn` | 补 `/v1` |  |

## 规则

> **base_url 末尾已经有版本段（`/vN`）就不再补，否则补 `/v1`。**

```python
from app.utils.pure.llm_endpoint import chat_completions_url, models_url, embeddings_url

chat_completions_url("https://api.xiaomimimo.com")
# -> https://api.xiaomimimo.com/v1/chat/completions
chat_completions_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
# -> https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions（不再叠 v1）
models_url("https://api.xiaomimimo.com")   # -> .../v1/models（连接测试用）
embeddings_url("https://host")             # -> https://host/v1/embeddings（已给完整 .../embeddings 则原样返回）
```

## 谁在用

| 调用方 | 用途 |
|---|---|
| `app/ai/llm.py` | 主站对话（流式 / 非流式共用 `_build_request_url_and_headers`）|
| `app/services/world/world_chat_service.py` | 群视界世界对话（流式 + 工具轮）|
| `app/services/agent/agent_service.py` | 角色生成 |
| `app/routers/user.py` | `POST /user/test-api-connection`（**历史上就是这里漏了 /v1**）|
| `app/utils/embedding.py`、`app/embedding_providers/api.py` | 向量端点 |

## 排查手法

判断"路径对不对"用**无效 key**探测即可，无需真 key：

- `401` / `400` / `405` → 路径存在（只是认证或参数不对）
- `404`（尤其 openresty / nginx 的 HTML）→ 路径不存在

```bash
docker exec ai_group_backend curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer sk-invalid" https://api.xiaomimimo.com/models
# 404 → 少了 /v1
```

## 连接测试（探针）

`POST /user/test-api-connection` 只负责取 key（用户输入 > 库里已存）与转成前端契约；
判定逻辑全在 `app/services/agent/api_probe.py` 的 `probe_provider()`——
"这个 key + base_url 到底能不能用"的**唯一入口**。

**两段式**：

| 步骤 | 请求 | 说明 |
|---|---|---|
| 1 | `GET {root}/models` | 免费、不需要模型名，多数供应商这条就够 |
| 2 | 仅当第 1 步返回 404/405 | `POST {root}/chat/completions`，`max_tokens=1` 的极小请求（业界 one-api / new-api 就是这么测渠道的）|

**401/403 不退化**：路径是通的，退化只会掩盖真因（把"key 不对"伪装成别的问题）。

**为什么不再是"一句 GET /models"**：`/models` 不是所有供应商都实现（小米 MiMo 直接 404），
而各家 base_url 的版本段又不统一——只有真发一次 chat，才能同时验证 key / 版本段 / 模型名三者匹配。

**结构化结论**：`kind` ∈
`ok | auth | not_found | quota | rate_limited | bad_request | server_error | http_error | network | timeout | bad_url`。
把"网络不通 / key 不对 / 路径不对 / 欠费 / 限流"分开报，而不是把供应商的原始 404 HTML 丢给用户。

**两处该有的谨慎**：

- 响应体过 `redact_secret()`：把回显出来的 API Key 抹成 `***`（不是每家都像 DeepSeek 那样打码成 `****robe`），
  并截断到 200 字符；
- HTML 错误页只留一句"（供应商返回了 HTML 错误页）"，不 dump 几百字噪声。

**谁在用**：`POST /user/test-api-connection`（用户绑定 key 时测）与
`POST /admin/provider-presets/fetch-models`（管理页「获取模型」按钮）。
后者靠 `ProbeResult.models` 把模型 id 带回前端填列表——两个入口共用同一套探测与文案，别再写第二份。

## 内网地址策略：私网目标必须"已登记"（2026-09-13）

`api_base_url` 由用户填写的地方共 3 处：`/user/config`（任何用户）、agent 配置（AI 主人）、
世界 AI 配置（世界主人），再加一个不落库的探针接口。**私网目标只有在"已登记"时才允许请求。**

**要解决的问题**（实测确认）：探针拿无效 key 探 200/401/404/302/连接拒绝/超时的差异，
就能画出内网 HTTP 服务地图——等于给每个注册用户一个内网端口扫描器。
（顺带记下它的**上限**：路径后缀固定，读不到内网任意页面；302 不跟；200 分支不回显 body；不附带我们的凭据。）

**为什么不干脆"一律只许公网"**：平台自己就合法使用内网地址
（preset 的 Ollama `http://localhost:11434`、容器部署时的 embedding `http://host.docker.internal:11434`），
用户也合法地指向自己的局域网 LLM。所以分界线不是"URL 长什么样"，而是
**"这个地址是不是用户已经声明过要用的那一个"**。

**已登记 = `base_url_registry.saved_private_hosts()`**：平台预设 + 平台 `provider_config` +
该用户自己的 `user` / `agent` / `world` 配置里保存过的 `host:port`（**无新表、无新 UI**）。

| 场景 | 行为 |
|---|---|
| 公网地址（DeepSeek / OpenAI / 中转站…） | 一律放行，体验零变化 |
| 私网地址 + 已保存过 | 放行（自己的局域网 LLM 正常用） |
| 私网地址 + 没保存过 | 拒绝：`blocked_private`，提示"先保存这个 Base URL 再测"（**一个请求都不发出去**）|
| 管理员（`/admin/...` 那条路径） | 不设限——管理员是这台机器的信任根，否则新加的私网供应商"先测再存"就没法做了 |

于想扫内网就得"每个地址先保存一次再测一次"，那是手点而不是扫描，且每次保存都是正常配置写（有记录）。

`url_guard.is_private_target()` 会**解析域名再判**，所以 `127.0.0.1.nip.io` 这类"域名指向回环"的绕过写法也拦得住。

**注意方向**：dsh-copree 插件访问 Copree 是 **DSH → Copree（入站）**，
`world_push` / `world_pull` 也是 DSH 侧工具调我们的 API——出站守卫碰不到它，别拿它当"必须放开内网"的理由。

**测试**：`backend/tests/test_api_probe.py` 用 `httpx.MockTransport` 零网络覆盖全部分支
（含"MiMo 的 /models 是 404 但聊天通"这条真实事故路径、以及"401 不许被兜底掩盖"）。
容器里没装 pytest 时可以桩掉 pytest 直接跑：

```bash
docker exec -i -w /app ai_group_backend python - <<'PY'
import sys, types, asyncio, inspect
fake = types.ModuleType("pytest")
class _Mark:
    def __getattr__(self, n): return lambda *a, **k: None
fake.mark = _Mark(); sys.modules["pytest"] = fake
sys.path.insert(0, "/app/tests")
import test_api_probe as t
for n in sorted(x for x in dir(t) if x.startswith("test_")):
    r = getattr(t, n)()
    if inspect.isawaitable(r): asyncio.run(r)
    print("PASS", n)
PY
```
