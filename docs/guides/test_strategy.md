# Copree 测试策略文档 / Testing Strategy

> **面向开发者和质量保证人员。** 多层次测试策略、测试规范和质量标准。
> **For developers and QA.** Multi-level testing strategy, test standards, and quality criteria.
>
> 本文档只描述**仓库里真实存在**的东西。尚未落地的能力一律显式标注「目标」/「未落地」，
> 不写成既有事实——目录结构与命令必须能原样粘贴执行。
>
> **Quick start (EN).** The backend suite lives flat in `backend/tests/` — there are no `unit/`,
> `integration/` or `e2e/` subdirectories. Run it with `cd backend && python -m pytest tests/ -q`;
> the shipped container image has no pytest, so `python tests/run_without_pytest.py` is a
> dependency-free runner for it (it refuses to start unless the target database name ends in `_test`).
> Coverage is a report only; diff coverage gates pull requests. **Every new test must be proven to
> fail first** — put the bug back and watch it go red (see §1.2). Frontend has no unit tests:
> the PR gate is `tsc --noEmit` plus `npm run i18n:check`, which fails when a UI string would
> otherwise render as a raw i18n key (see §1.4).
> Commands are written in their official form; China-network mirror variants are marked separately
> (see §9.5).

---

## 目录

1. [当前套件与运行方式](#一当前套件与运行方式)
2. [测试策略概述](#二测试策略概述)
3. [测试金字塔现状](#三测试金字塔现状)
4. [单元测试](#四单元测试)
5. [集成测试](#五集成测试)
6. [端到端测试](#六端到端测试)
7. [性能测试](#七性能测试)
8. [测试环境](#八测试环境)
9. [测试覆盖率](#九测试覆盖率)
10. [CI/CD 集成](#十cicd-集成)

---

## 一、当前套件与运行方式

后端用例全部平铺在 `backend/tests/`，**没有子目录**。

| 文件 | 类型 | 用例数 | 覆盖 |
|------|------|--------|------|
| `backend/tests/test_api_probe.py` | 单元（零网络） | 17 | 供应商探针判定、错误文案、响应脱敏、内网地址围栏 |
| `backend/tests/test_multimodal.py` | 单元（零网络 + 临时真文件） | 16 | 附件 → 多模态 content、便签、视觉降级（**P0 源头**）|
| `backend/tests/test_security_guards.py` | 静态 + 单元（不连库） | 10 | 授权边界：依赖漏挂、维护图片白名单、生产关闭接口文档、联邦出站 TLS |
| `backend/tests/test_llm_endpoint.py` | 单元（零网络） | 7 | 端点拼接 `/vN` 规则 + 全部 9 个预设全覆盖 |
| `backend/tests/test_world_chat_images.py` | 集成（真库 + 真文件） | 5 | 群视界发图链路：真调 `_prepare_world_chat`，零 LLM 消耗 |
| `backend/tests/test_world_tool_summaries.py` | 单元（零网络） | 4 | 世界工具插件契约：自报 `label/segment/summary`、文案可读、注册表覆盖 schema |
| `backend/tests/test_gm_dm_symmetry.py` | 静态（路由表） | 3 | 群/私信接口命名对称，已删除的重复入口不得回归 |
| `backend/tests/test_world_tool_plugins.py` | 静态（`symtable`） | 2 | 插件文件"引用了但没定义"的名字错误 |
| `backend/tests/test_world_ai_guardrails.py` | 单元 + 集成（零网络） | 15 | 世界 AI 安全护栏：禁用后缀创建即拒 + 兜底强删、下载固定落点、违规内容拦截、模式门禁、**审阅弹窗完整闭环**（广播 → 点同意 → 工具放行）、**无人应答策略**（自动档放行 / 审阅档不放行） |
| `backend/tests/test_tool_chain.py` | 单元（零网络） | 5 | 工具调用链校验：悬空 `tool_calls` 补齐（2026-09-14「执行到一半 400」的根因）、不伪造成功、幂等、纯对话不动 |
| `backend/tests/test_session_title.py` | 单元（零网络） | 4 | 对话命名：清洗/限长、只写当前会话、默认会话兜底、空名清除 |
| `backend/tests/test_agent_resolution.py` | 集成（真库） | 2 | 群成员 `member_id` 解析优先级 |

合计 **245 条**（`run_without_pytest.py` 全量约 **19s**；2026-09-25 提速前是 108s，见 §1.5）。

辅助文件：

- `backend/tests/conftest.py` —— 测试库环境变量、`migrated_db` fixture、测试库 GUC
- `backend/tests/run_without_pytest.py` —— 后端容器里**没装 pytest**，这是最小运行器（每例打耗时）
- `backend/tests/db_reset.py` —— `clear(db, *roots)`：按外键闭包 DELETE 清表（替代 `TRUNCATE … CASCADE`，见 §1.5）

### 1.1 两种跑法

CI 与装了 pytest 的机器：

```bash
cd backend && python -m pytest tests/ -q
```

后端容器 / 没装 pytest 的机器（走自带运行器）：

```bash
PROD=$(docker exec ai_group_backend printenv DATABASE_URL)
TEST=${PROD/\/ai_group_chat/\/ai_group_chat_test}
docker exec -w /app \
  -e TEST_DATABASE_URL="$TEST" \
  -e TEST_DATABASE_URL_SYNC="${TEST/+asyncpg/}" \
  ai_group_backend python tests/run_without_pytest.py
```

运行器只实现了 `pytest.fixture` 与 `pytest.mark`。需要参数化、插件、覆盖率就去装 pytest，
不要往运行器里加功能。

运行器接受**选择器**（子串匹配 `文件名::用例名`），用于只跑改动涉及的部分：

```bash
# 只跑发图链路（5 条）
docker exec -w /app -e TEST_DATABASE_URL="$TEST" -e TEST_DATABASE_URL_SYNC="${TEST/+asyncpg/}" \
  ai_group_backend python tests/run_without_pytest.py test_world_chat_images
```

实测差距：单跑一个文件（含进程启动与建表）约 **5s**，全量 245 条 **19s**。
改哪个文件就跑哪个，只在推送前跑全量。

**启动闸**：库名不以 `_test` 结尾直接拒绝启动。运行器会 `drop_all` + 清表，
而生产库与测试库在同一个 PostgreSQL 实例里、只差库名——这个闸不是形式主义。

### 1.2 写完用例要证明「它会红」

只跑到绿不算完成。把 bug 放回去（用 monkeypatch，别改源码，更别改正在被生产容器挂载的目录）
确认用例转红，否则它只是摆设。两批用例都是这么定稿的：

| 放回去的 bug | 结果 |
|---|---|
| `image_attachments` 去掉 `or []`（P0 本体） | ✅ 被抓住：`TypeError: 'NoneType' object is not iterable` |
| `build_content` 忽略图片（静默丢弃） | ✅ 被抓住 |
| `strip_image_parts` 只剥图片、不改写便签 | ✅ 被抓住 |
| 不发「本轮附图」便签 | ✅ 被抓住 |
| 历史图片不降级成 `[图片]` | ✅ 被抓住 |
| `api_root` 退化成无脑补 /v1 | ✅ 被抓住 |

**第一版是假覆盖**：只跑了首轮，而首轮历史是空的、`None` 根本不会出现——
P0 只在「上一轮存过不带附件的消息」时才触发。是变异测试把这个漏洞逼出来的。

### 1.3 排查手法与踩过的坑

下面每一条都是这一轮**真实踩出来的**，写下来的理由是它还会再咬人。

#### ① 校验命令时，别用管道后面的 `$?` 和 `&&`

自查"这个包能不能装"时，我写的是：

```bash
pip download coverage | tail -2 && echo PIP_OK    # ❌ 永远打印 PIP_OK
```

`&&` 绑的是 `tail`（几乎总是成功），**不是** `pip`；`echo $?` 同理，取的是管道**最后一环**的退出码。
这一条让我把"能装"误判了、白跑一轮。正确写法是重定向到文件、紧接着单独取 `$?`：

```bash
cmd > /tmp/out.txt 2>&1
echo "rc=$?"        # 这才是 cmd 的退出码
tail -3 /tmp/out.txt
```

#### ② 不在容器内安装依赖

容器内安装的包不进 git、不进 `requirements.txt`，容器重建即丢失；在生产容器里还会直接改变
正在运行服务的环境。需要某个工具时，走 CI（workflow 里显式声明），或在宿主机/开发机环境做。

若在容器外运行工具，注意工作目录是挂载进来的仓库：工具产物（`.coverage`、`coverage.xml`）
会直接落到仓库里，用完删除（已在 `.gitignore` 中忽略）。

#### ③ 永远为绿的用例是假覆盖

见 1.2。只跑首轮的用例看着在测发图，实际上 `None` 那条分支根本没进去。
**永远绿 + 断言齐全 + 覆盖率好看**，是假覆盖的三个特征。

#### ④ 覆盖率的高分要怀疑

`app/models` 92% 是 import 出来的（见 9.3）。任何"某层覆盖率特别高、却明显没人写过测试"的地方，
先怀疑是导入副作用，而不是质量真的好。

#### ⑤ 文档引用代码，能自动校验就别手抄

第十节的 workflow 是**逐字节引用**真实文件的，并用脚本校验过：

```python
assert doc_yaml_block.rstrip() == open(".github/workflows/test.yml").read().rstrip()
```

手抄一份 workflow 进文档，等于给自己留一张迟早过期的假地图——而看文档的人不会去核对。

#### ⑥ 本地能过不等于 CI 能过

测试把图片写进 `settings.data_dir`（硬编码为 `/app/data`）：本地 `/app` 可写、全绿；
CI runner 上 `/app` 属另一用户不可写，4 条用例直接 `PermissionError`。

只要测试依赖**环境可写性**或**绝对路径**，就必须在 CI 上验证过才算数。
修法是不要让测试碰真实数据目录：`data_dir` 是只读 property，测试临时替换类描述符，退出还原。

### 1.4 前端检查（两条，都在容器里跑，不需要装依赖）

```bash
# 类型检查
docker exec -w /app ai_group_frontend node_modules/.bin/tsc --noEmit

# i18n key 完整性：源码里的 t()/tr() + 后端 CONFIG_GROUPS 下发的 key 一起核对
node frontend/scripts/check-i18n.mjs     # 等价于 cd frontend && npm run i18n:check
```

为什么需要 i18n 这条：`getTranslation()` **找不到 key 时原样返回 key**，界面上就会把
`admin.addProvider`、`adminConfig:sourceDb` 这样的源码串显示给用户——不报错、不崩溃，
只靠肉眼发现。2026-09 就是这么攒出 48 个三语全缺 + 62 处单语缺的 key 的。

### 1.5 清表与提速：为什么不用 TRUNCATE … CASCADE（2026-09-25）

全量从 **108s 降到 19s**，只做了三件事，都在测试侧：

**1) 清表改成「删外键闭包」（`tests/db_reset.py`）。** 此前每个用例的 seed 都跑
`TRUNCATE users CASCADE`，实测一次 **4163ms**——它要给闭包里 51 张表逐张换 relfilenode
（建文件 + WAL + 目录 fsync），跟表里有没有数据无关，在这台机器的存储上就是几百毫秒一张。
同样范围的 `DELETE` 只要 **13ms**：

```python
from db_reset import clear

async with async_session() as db:
    await clear(db, "groups", "users")   # 等价 TRUNCATE groups, users CASCADE
```

`clear()` 用递归查询算出外键闭包，删之前把 `session_replication_role` 设成 `replica`
（`SET LOCAL`，只影响本事务），这样不用关心删除顺序、也不用管那些没写 ON DELETE 的外键；
删完立刻调回 `DEFAULT`，后面的 INSERT 仍然正常做外键检查。测试 id 都是显式写的，
不需要 TRUNCATE 的重置序列。

**2) 测试库关掉每次提交等磁盘。** `conftest.py` 在导入时装一条库级 GUC
`synchronous_commit = off`（提交只写 WAL、不等 flush；崩溃可能丢最后几条，测试库无所谓）。
**强制库名以 `_test` 结尾才允许改**——同一个 PostgreSQL 实例上就是生产库。

**3) 别在测试里等真实时间。** `test_world_ai_guardrails` 里那个门禁用例没有页面，
却按产品行为白等了 `_WAIT_FOR_VIEWER = 20` 秒才走"无人应答"分支；现在它自己把该值压到 0
（同文件另一个用例本来就是这么做的）。

排查手法（下次变慢照这个走）：运行器现在**每个用例都打耗时**，末尾还给最慢 8 名；
还嫌不够就 `python -m cProfile -s tottime tests/run_without_pytest.py`——
这套问题就是靠它定位的：`epoll.poll` 占了 90s（都在等 I/O），CPU 几乎不花。

---

## 二、测试策略概述

### 2.1 测试分层模型

```mermaid
flowchart TD
    subgraph "测试金字塔"
        E2E[端到端测试<br/>E2E Tests]
        Integration[集成测试<br/>Integration Tests]
        Unit[单元测试<br/>Unit Tests]
    end

    subgraph "数量比例"
        E2ERatio[~5%]
        IntegrationRatio[~20%]
        UnitRatio[~75%]
    end

    subgraph "执行速度"
        E2ESpeed[慢 (分钟级)]
        IntegrationSpeed[中等 (秒级)]
        UnitSpeed[快 (毫秒级)]
    end

    subgraph "维护成本"
        E2ECost[高]
        IntegrationCost[中]
        UnitCost[低]
    end

    E2E --> E2ERatio
    Integration --> IntegrationRatio
    Unit --> UnitRatio

    E2E --> E2ESpeed
    Integration --> IntegrationSpeed
    Unit --> UnitSpeed

    E2E --> E2ECost
    Integration --> IntegrationCost
    Unit --> UnitCost

    style E2E fill:#7c3aed,color:#fff
    style Integration fill:#2563eb,color:#fff
    style Unit fill:#059669,color:#fff
```

> 上表是**目标**比例。截至 2026-09-13 的实际构成是 **47 条用例**（单元 40 / 集成 7），
> 清单见第一节；整体**行**覆盖率 8%，见第九节。

### 2.2 测试目标

| 维度 | 目标 | 衡量指标 |
|------|------|---------|
| 功能正确性 | 核心功能 100% 覆盖 | 需求覆盖率 |
| 回归稳定性 | 代码变更不破坏现有功能 | 回归测试通过率 |
| 性能满足 | 关键路径响应可接受 | P95 响应时间 |
| 安全性 | 无已知高危漏洞 | 安全扫描结果 |

---

## 三、测试金字塔现状

```mermaid
graph TD
    subgraph "手工回归（部署后人工执行）"
        E2E1[注册登录]
        E2E2[群聊与消息]
        E2E3[AI 对话与工具调用]
        E2E4[文件上传与发图]
        E2E5[群视界世界]
    end

    subgraph "集成测试（真库 · backend/tests/）"
        INT1[test_agent_resolution.py]
        INT2[test_world_chat_images.py]
    end

    subgraph "单元测试（零网络 · backend/tests/）"
        UNIT1[test_api_probe.py]
    end

    E2E --> INT
    INT --> UNIT

    style E2E1 fill:#7c3aed,color:#fff
    style E2E2 fill:#7c3aed,color:#fff
    style E2E3 fill:#7c3aed,color:#fff
    style E2E4 fill:#7c3aed,color:#fff
    style E2E5 fill:#7c3aed,color:#fff
    style INT1 fill:#2563eb,color:#fff
    style INT2 fill:#2563eb,color:#fff
    style UNIT1 fill:#059669,color:#fff
```

三层的分界在本仓库里的具体含义：

| 层 | 判据 | 例 |
|----|------|----|
| 单元 | 不连数据库、不出网 | `test_api_probe.py` 用 `httpx.MockTransport` 顶掉真实出网 |
| 集成 | 连真库，但**不花 LLM 额度** | `test_world_chat_images.py` 真调业务函数，只断言 LLM payload |
| 端到端 | 真环境、真模型、真浏览器 | 目前**没有自动化**，见第六节 |

---

## 四、单元测试

### 4.1 已有覆盖

| 模块 | 测试重点 | 用例文件 |
|------|---------|---------|
| `app/services/agent/api_probe.py` | 探针判定、错误文案、响应脱敏 | `backend/tests/test_api_probe.py` |
| `app/utils/pure/url_guard.py` | 内网/公网地址判定（含 DNS 解析绕过） | `backend/tests/test_api_probe.py` |
| `app/services/agent/base_url_registry.py` | 「已登记私网地址」的允许清单 | `backend/tests/test_api_probe.py` |
| `app/utils/multimodal.py` | 附件 → 多模态 content、便签与视觉降级 | `backend/tests/test_multimodal.py` |
| `app/utils/pure/llm_endpoint.py` | 端点拼接（`/vN` 规则）+ 全部预设 | `backend/tests/test_llm_endpoint.py` |
| `app/routers/deps.py`、`app/utils/auth.py`、`app/routers/files.py` | 权限依赖：群成员、角色以 DB 为准、匿名文件白名单、生产关闭文档 | `backend/tests/test_security_guards.py` |

### 4.2 尚未覆盖（把缺口写出来，别让它不可见）

| 模块 | 应覆盖 | 现状 |
|------|--------|------|
| `app/ai/decider.py` | 决策逻辑、意愿分计算 | 无用例 |
| `app/ai/executor.py` | 工具调用循环、上下文压缩 | 无用例 |
| `app/ai/llm.py` | API Key 解析、消息构建、视觉降级重试 | 无用例 |
| `app/tools/` | 工具参数校验、执行 | 无用例 |
| `app/services/memory/` | 记忆检索、遗忘机制、压缩阈值 | 无用例 |
| `app/services/brain/` | 状态机转换、心跳 | 无用例 |
| `app/chat/` | 消息管道、可达性 | 无用例 |

### 4.3 示例：零网络单测

单元用例**不许出网**。需要 HTTP 的地方一律用 `httpx.MockTransport` 顶掉传输层，
这样「供应商返回 404 但聊天其实是通的」这类真实事故路径才能便宜地复现：

```python
# backend/tests/test_api_probe.py
def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


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
```

### 4.4 运行

```bash
# 装了 pytest
cd backend && python -m pytest tests/test_api_probe.py -v

# 容器里没装 pytest（完整命令见 1.1）
cd /tmp/zfsv3/sata11/15228874271/data/copree && \
  docker exec -w /app ai_group_backend python tests/run_without_pytest.py
```

---

## 五、集成测试

### 5.1 现有用例

| 场景 | 涉及模块 | 用例文件 |
|------|---------|---------|
| 群成员 ID → Agent 解析 | 群成员表 + Agent 模型 | `backend/tests/test_agent_resolution.py` |
| 世界对话准备 → LLM payload | 路由入参 + ChatItem + 落库 + `multimodal` | `backend/tests/test_world_chat_images.py` |

集成用例连**测试库** `ai_group_chat_test`，但**不花 LLM 额度**：跑的是业务函数，不是真的对话。
`test_world_chat_images.py` 走完 `_prepare_world_chat` 后只断言「送给模型的 messages 长什么样」。

### 5.2 示例：真调业务函数，只断言 payload

```python
# backend/tests/test_world_chat_images.py
async def test_image_turn_injects_multimodal_parts_and_note(migrated_db):
    """带图消息生成多模态 parts，便签数量等于实际注入数。"""
    with _temp_data_dir():
        async with async_session() as db:
            world_id, attachment = await _seed_world(db, with_image=True)
            ctx = await _prepare(
                db, world_id,
                [ChatItem(text="这是什么？", attachments=(attachment,))],
            )

    body = _last_user(ctx["messages"])
    assert isinstance(body["content"], list), "图片被丢弃，content 应为 parts 列表"
    urls = [p["image_url"]["url"] for p in body["content"] if p.get("type") == "image_url"]
    assert urls[0].startswith("data:image/png;base64,")
```

### 5.3 conftest.py

集成用例共享 `backend/tests/conftest.py`。它做三件事：把 `DATABASE_URL` 指向测试库、
提供 `migrated_db` fixture、注册 anyio backend：

```python
# backend/tests/conftest.py（节选）
# 连接串必须由环境变量提供：真实口令不进仓库（历史版本曾把口令写死为默认值）
TEST_DATABASE_URL = _require_test_database_url()
# sync 驱动由 async 连接串推导，少传一个环境变量
TEST_DATABASE_URL_SYNC = (
    os.environ.get("TEST_DATABASE_URL_SYNC") or TEST_DATABASE_URL.replace("+asyncpg", "")
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DATABASE_URL_SYNC"] = TEST_DATABASE_URL_SYNC


@pytest.fixture(scope="session")
async def migrated_db():
    """用模型 metadata 建全量表（不跑 alembic：历史迁移链无法从空库重建，模型即 schema）"""
    ...
    await conn.run_sync(Base.metadata.drop_all)
    await conn.run_sync(Base.metadata.create_all)
```

注意 `migrated_db` 是 **session 级**且会 `drop_all`：用例自己负责播种
（现有集成用例的做法是开头 `await clear(db, ...)` 清掉相关外键闭包，再插入自己需要的最小数据；
不要再用 `TRUNCATE ... CASCADE`，见 §1.5）。

### 5.4 运行

需要 `TEST_DATABASE_URL` 指向测试库（**必填**：conftest 不再有默认值，缺了会直接报错说明跑法）。
`TEST_DATABASE_URL_SYNC` 可省略，由 async 连接串推导。在容器里用 1.1 的运行器（它会替你推导 URL 并加闸）。

### 5.5 发图链路的脚手架：怎么加第 6 条用例

`test_world_chat_images.py` 里的几个私有辅助函数就是全部脚手架，新用例直接复用：

| 辅助函数 | 作用 | 注意 |
|---------|------|------|
| `_temp_data_dir()` | 上下文管理器，把 `settings.data_dir` 指向临时目录 | `data_dir` 是只读 property，实现上替换类描述符并在退出时还原；不做这一步会污染生产数据目录，且在 CI 上不可写 |
| `_seed_world(db, with_image=)` | 清库 → 建临时用户 + 世界 → 可选地落一张真实 1×1 PNG | 开头 `await clear(db, "worlds", "users")`；返回 `(world_id, attachment)` |
| `_prepare(db, world_id, items)` | 走真实链路调 `_prepare_world_chat`（`stream_world_chat` 的准备阶段）| 它会**落库**用户消息，所以多轮用例天然带历史 |
| `_last_user(messages)` | 取最后一条 user 消息 | 尾部还挂着时间/访客等 system 段，**不能取 `messages[-1]`** |
| `_notes(messages)` | 取尾部「本轮附图」便签 | 用 `IMAGE_NOTE_PREFIX` 前缀识别 |


每个用例都必须在 `_temp_data_dir()` 内执行 `_seed_world` 与 `_prepare`。

五条用例各自守住的不变式（加新用例时别测重了）：

| 用例 | 守住的不变式 |
|------|-------------|
| `test_plain_text_turn_survives_the_image_path` | 有历史的普通文字消息不能抛（P0 本体）|
| `test_image_turn_injects_multimodal_parts_and_note` | 最后一条 user 是多模态 parts、真带 `data:` URL、便签数 = 实际注入数 |
| `test_image_turn_persists_attachments` | 附件跟着消息落库（刷新后前端渲染缩略图靠它）|
| `test_history_image_degrades_to_placeholder` | 历史图降级成 `[图片]`、只有最新一条带字节、本轮没图就不许发便签 |
| `test_vision_degrade_strips_images_and_note_together` | `strip_image_parts` 之后图片与便签同时消失 |

**写新用例时最容易踩的坑**：让用例只跑「第一轮」。历史为空时很多分支根本不会进入
（`attachments=None` 只可能来自已落库的旧消息），于是就得到一条永远为绿的**假用例**。
写完记得做第 1.2 节的变异验证。

---

## 六、端到端测试

### 6.1 现状：没有自动化 E2E

仓库里**没有**前端 E2E 套件：没有 Playwright 依赖、没有 `frontend/e2e/` 目录，
`frontend/package.json` 的 scripts 只有 `dev` / `build` / `build:demo` / `preview`。
CI 里有一个前端 job，但它只做**类型检查**（`tsc --noEmit`），不跑任何测试（见第十节）。

因此「端到端」目前 = **部署后人工回归**，按附录清单走一遍。

`test_world_chat_images.py` 是其中能自动化部分的替代品：它咬得住「接线级」回归
（payload 形状、便签、降级路径），咬不住 UI 与真实模型行为。

### 6.2 手工回归清单

见附录。改动核心链路（聊天管道 / 世界 / 模型解析 / 附件）后按清单走。

### 6.3 若要引入自动化（目标，尚未落地）

1. 先补**后端**：真起服务 + 真调一次模型。成本可控，且能验证只有真模型才会暴露的行为
   （例如模型不承认自己能看图）；
2. 再补**前端**：登录 → 发消息 → 发图 → 断言气泡与缩略图。

---

## 七、性能测试

### 7.1 现状：未落地

没有性能测试目录，`backend/requirements.txt` 里没有 locust / k6 之类依赖，也没有压测脚本；
CI 不跑性能。

### 7.2 目标矩阵

| 测试场景 | 工具（目标） | 指标 | 通过标准 |
|---------|-------------|------|---------|
| API 响应时间 | Locust / wrk | P95 响应时间 | < 500ms |
| WebSocket 并发 | k6 | 同时在线用户 | > 1000 |
| AI 回复延迟 | 自定义脚本 | 端到端延迟 | < 5s（受模型侧影响，只作趋势观测）|
| 数据库查询 | pgbench | QPS | > 1000 |
| 文件上传 | curl / wrk | 上传速度 | > 10MB/s |

### 7.3 目前怎么测

手工观测，够用为止：

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://127.0.0.1:5228/health
docker logs --since 10m ai_group_backend 2>&1 | grep -E "🌐"
```

**不要**把手工观测到的单次数字当成基准写进文档 —— 没有固定负载的「基准」是误导。

---

## 八、测试环境

### 8.1 真实环境链

只有三段，没有 staging / pre-production：

```mermaid
flowchart LR
    Dev[开发容器 / 本机] -->|push backend/**| CI[GitHub Actions]
    CI -->|测试通过| Merge[合并]
    Merge -->|/usr/local/bin/dsh-web-restart| Prod[生产：NAS 容器]

    style Dev fill:#6366f1,color:#fff
    style CI fill:#f59e0b,color:#fff
    style Prod fill:#dc2626,color:#fff
```

### 8.2 数据库

生产库与测试库在**同一个 PostgreSQL 实例**里，只差库名：

| 用途 | 库名 | 谁在用 |
|------|------|--------|
| 生产 | `ai_group_chat` | `ai_group_backend` 容器（挂载 `./backend:/app`）|
| 测试 | `ai_group_chat_test` | `conftest.py` 的 `migrated_db` + 集成用例 |

正因为同实例、只差库名，`run_without_pytest.py` 才必须有那个「库名以 `_test` 结尾」的启动闸。

### 8.3 测试数据管理

没有 `setup_test_data` / 脱敏复制之类的脚本，也不需要：

- schema 由 `migrated_db` 从**模型 metadata** 建全量（`drop_all` + `create_all`）。
  历史迁移链无法从空库重建，所以**模型即 schema**，测试库不跑 alembic；
- 数据由每个用例自己播种，开头 `await clear(db, ...)`（`tests/db_reset.py`）保证从干净状态开始；
- 用例造的临时文件（如 `test_world_chat_images.py` 的 1×1 PNG）自己删干净。

---

## 九、测试覆盖率

### 9.1 目标

| 测试类型 | 目标覆盖率 | 最低覆盖率 | 关键模块 |
|---------|-----------|-----------|---------|
| 单元测试 | 80% | 60% | AI 核心: 90%+ |
| 集成测试 | 70% | 50% | API 端点: 100% |
| 端到端测试 | 核心链路 100% | 核心链路 100% | 所有业务链路 |

### 9.2 基线（2026-09-13 实测）

该数字是 2026-09-13 在临时环境里一次性测得的，用于确立起点。
当时的测法与现在允许的做法不同（见 1.3 ②），**已不复现**；此后的数字以 CI 的覆盖率报表为准（9.5 与第十节）。

| 指标 | 值 |
|------|-----|
| 总语句 | 27,726 |
| 未执行 | 25,534 |
| **行覆盖率** | **8%** |
| 覆盖率为 0% 的文件 | 267 个里的 **180 个** |

按目录（语句数 = 分母权重，所以"缺测试的总量"看这一列）：

| 目录 | 语句 | 覆盖 |
|------|------|------|
| `app/services` | 13,059 | 5.3% |
| `app/routers` | 6,334 | **0.0%** |
| `app/ai` | 2,618 | **0.0%** |
| `app/utils` | 1,250 | 13.4% |
| `app/chat` | 788 | **0.0%** |
| `app/schemas` | 669 | **0.0%** |
| `app/models` | 1,122 | **92.2%** ← 见 9.3 |

### 9.3 `app/models` 的 92% 是假象

模型层"覆盖率"高，只是因为它被 **import 过** —— 类定义与字段声明在导入时就执行了，
跟"有没有被测"毫无关系。这是「覆盖率衡量**被执行**、不衡量**被验证**」最干净的标本。

同理：**覆盖率量不出代码优不优秀**。低覆盖 ≠ 代码烂（只说明没人守），
高覆盖也可能是烂代码配一堆没断言的用例。要找"哪里值得改"，得把三个维度交叉看：

| 信号 | 含义 | 动作 |
|------|------|------|
| 高复杂度 × 低覆盖 | 最容易出 bug、又完全没人守 | 最优先：补用例或直接重构 |
| 高改动频率 × 低覆盖 | 一直在改、每次都可能改坏 | 优先补用例 |
| 低复杂度 × 低覆盖 | 简单代码没测试 | 优先级低，可以一直不管 |

按这个口径，当前最该盯的是（语句数 / 圈复杂度 / 覆盖率）：`app/routers/admin.py`（1614 / 533 / 0%）、
`app/ai/llm.py`（747 / 304 / 0%）、`app/ai/executor.py`（589 / 186 / 0%）——
repo 里的 admin 最大但风险最低（管理员专用、输入可信），**最大 ≠ 最急**。

### 9.4 怎么用（已接入 CI）

| 用途 | 形式 | 是否阻断 |
|------|------|---------|
| 整体覆盖率 | CI 跑 `--cov-report=term-missing` + `--sort=cover` 出报表 | ❌ **不设阈值** |
| 增量覆盖率 | `diff-cover coverage.xml --compare-branch=origin/<目标分支>` | ✅ **仅 PR** |

增量当门禁、整体只当报表，是业界通行做法（Codecov/Coveralls 的 patch coverage 同理）：
增量数字只跟本次改动有关，老代码既拖不了后腿，也不会替它虚高。
整体覆盖率一旦设阈值，就会奖励写"调用了但没断言"的假用例——比没有覆盖率更糟。

阈值放在 workflow 顶部的 `env.DIFF_COVER_MIN`（初始 80），**太吵就只调这一个数字**。
注意别因为"整体才 8%"就把增量门槛也降掉：那两件事没关系。

增量门禁只能拦住**新增**的坏味道，拦不住已经烂在那儿的部分——所以 4.2 的缺口仍要单独补。

### 9.5 覆盖率数字从哪来

**常规来源：CI 报表。** workflow 里跑 `pytest --cov=app --cov-report=term-missing`，
日志中直接给出 `TOTAL` 与逐文件明细（见第十节）。CI 在 GitHub 上执行，用官方源，不加镜像。

**本地复现**需要一个已装好依赖的 Python 环境（如开发机上的 venv）。
本项目禁止在容器内安装依赖（见 1.3 ②），因此不在容器里做这件事。
按所在网络选一套安装命令：

#### A. 官方源 / Official source（国际网络）

```bash
pip install coverage
```

#### B. 国内镜像 / China mainland mirror

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple coverage
```

装好后在仓库根目录执行：

```bash
cd backend
coverage run --source=app tests/run_without_pytest.py
coverage report
```

可用的国内镜像：清华 `https://pypi.tuna.tsinghua.edu.cn/simple`、
阿里 `https://mirrors.aliyun.com/pypi/simple/`。

> **为什么需要 B 这一版**（本项目部署环境的实测记录）：该 NAS 上 `pypi.org` DNS 解析超时
> （`curl: (28) Resolving timed out after 15000 ms`），`pip download` 30 秒被杀（rc=124）。
> 这不是"没有外网"：`api.deepseek.com` 100 ms 可达。

---

## 十、CI/CD 集成

### 10.1 真实的 workflow

`.github/workflows/test.yml` 就是全部检查（另有 `deploy-demo.yml` 负责 push 到 main 时构建并发布 Pages）。

下面是该文件的**逐字节原文**——不是节选、不是示意。文档里的 CI 内容一旦与真实文件不一致，
就是一张假地图，所以这里宁可直接贴全文：

```yaml
name: Tests

on:
  push:
    paths:
      - 'backend/**'
      - 'frontend/**'
      - '.github/workflows/test.yml'
  pull_request:
    paths:
      - 'backend/**'
      - 'frontend/**'

env:
  # 增量覆盖率门槛（只作用于 pull_request）。基线是 8%，所以这里盯**本次改动的行**，
  # 不盯整体——整体覆盖率当门禁只会奖励写"调用了但没断言"的用例。
  # 太吵就调低这一个数字；先跑一段时间再决定要不要收紧。
  DIFF_COVER_MIN: 80

jobs:
  # 作业 id 与名称保持原样：改名会让 GitHub 上的 required status check 失效
  pytest:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg17
        env:
          POSTGRES_USER: ai_chat
          POSTGRES_PASSWORD: test-pass
          POSTGRES_DB: ai_group_chat_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U ai_chat"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0        # diff-cover 要与目标分支比对，浅克隆拿不到

      - uses: actions/setup-python@v7
        with:
          python-version: '3.11'
          cache: 'pip'
          cache-dependency-path: backend/requirements.txt

      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt
          pip install pytest pytest-asyncio pytest-timeout pytest-cov diff-cover

      - name: Run backend tests
        env:
          TEST_DATABASE_URL: postgresql+asyncpg://ai_chat:test-pass@localhost:5432/ai_group_chat_test
          TEST_DATABASE_URL_SYNC: postgresql://ai_chat:test-pass@localhost:5432/ai_group_chat_test
        run: |
          cd backend
          python -m pytest tests/ -q --cov=app --cov-report=term-missing --cov-report=xml

      # 只报表不设阈值：整体覆盖率当前约 8%，它的用处是看缺口的**分布与趋势**，
      # 不是当门禁。注意 app/models 那 92% 是假象——只是被 import 过，不是被测过。
      - name: 整体覆盖率报表
        run: |
          cd backend
          python -m coverage report --sort=cover | tail -50

      - name: 增量覆盖率门禁（仅 PR）
        if: github.event_name == 'pull_request'
        run: |
          cd backend
          diff-cover coverage.xml \
            --compare-branch=origin/${{ github.base_ref }} \
            --fail-under=${{ env.DIFF_COVER_MIN }}

  # 前端此前在 PR 阶段零检查：deploy-demo.yml 只在 push 到 main 时跑，
  # 且跑的是 vite build（不含 tsc），类型错误一路裸奔到部署。
  frontend:
    name: Frontend (typecheck)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-node@v7
        with:
          node-version: 22
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: npm ci

      # 必须走 node_modules 里的 tsc：npx 在缺包时会去装一个同名的假 tsc@2.0.3
      - name: Typecheck
        run: ./node_modules/.bin/tsc --noEmit
```

> `frontend` job 不跑任何测试，只做类型检查。它拦得住的东西已用「注入一个类型错误」验证过：
> 有错时 `tsc` 退出码 2 并打印 `error TS2322`，删掉后退出码 0。

注意 CI 里的库名是 `ai_group_chat_test`、跑的是 `pytest tests/`（**平铺**，不是 `tests/unit/`）。

### 10.2 触发条件与缺口

| 项 | 现状 |
|----|------|
| 触发路径 | `backend/**`、`frontend/**` 与 workflow 自身 |
| 前端改动（PR） | 类型检查 `tsc --noEmit` + i18n key 完整性 `npm run i18n:check`；**没有单元测试** |
| 前端改动（push main） | 另有 `deploy-demo.yml` 跑 `vite build --mode demo`（**不含 tsc**）并发布 Pages |
| 文档改动 | 不触发（合理）|
| 依赖安装 | 均已开缓存：后端 `cache: pip`、前端 `cache: npm` |
| 克隆深度 | `fetch-depth: 0` —— diff-cover 要与目标分支比对，浅克隆拿不到 |
| 整体覆盖率报表 | 每次跑（`--sort=cover`），**不阻断** |
| 增量覆盖率门禁 | 仅 PR 阻断，阈值 = workflow 顶部 `env.DIFF_COVER_MIN` |

### 10.3 质量门禁现状

| 门禁 | 现状 |
|------|------|
| 后端测试全绿 | ✅ CI 阻断（`pytest tests/ -q` 非零即红）|
| 前端类型检查 | ✅ CI 阻断（`tsc --noEmit`）——2026-09-13 新增 |
| 前端 i18n key 完整性 | ✅ CI 阻断（`npm run i18n:check`）——2026-09-13 新增 |
| 增量覆盖率 | ✅ CI 阻断（仅 PR）——2026-09-13 新增 |
| 整体覆盖率阈值 | ❌ **故意不设**：当门禁只会奖励写"调用了但没断言"的假用例 |
| 代码风格（Ruff / ESLint）| ❌ 未接入 CI |
| 前端测试 / E2E | ❌ 未接入 |
| 安全扫描 | ❌ 未接入 |

---

## 附录：手工回归清单

### 功能测试清单

| # | 功能点 | 测试用例 | 状态 | 备注 |
|---|--------|---------|------|------|
| 1 | 用户注册登录 | 正常注册 / 重复用户名 / 错误密码 | ✅ | 注册当前开放（`registration_enabled`）|
| 2 | 消息发送 | 文本 / 图片 / 文件 | ✅ | 图片必须**真被模型看见**，不能只看气泡 |
| 3 | AI 回复 | @AI / 主动唤醒 / 工具调用 | ✅ | |
| 4 | 群视界发图 | 点选 / 拖拽 / Ctrl+V 三种入法 | ✅ | 蒙版高亮 + 当前区块加深 |
| 5 | 群视界发图（模型侧）| 换纯文本模型时应如实说「看不到图」，不编造 | ✅ | 降级靠 `strip_image_parts` |
| 6 | 「新对话」 | 不占轮次、不写旧会话 | ✅ | 走 `POST /worlds/{id}/chat/session/new` |
| 7 | 供应商连接测试 | 公网 / 已登记私网 / 未登记私网 | ✅ | 未登记私网必须零请求 |
| 8 | 管理页获取模型 | 填 base_url → 点「获取模型」| ✅ | key 一次性，不保存 |
| 9 | AI 主动 compact | 消息不足时应回「无需压缩」而非「失败」| ✅ | 空操作不是失败 |
| 10 | 记忆管理 | 增删改查 | ✅ | |

### 回归测试清单

| # | 场景 | 关联版本 | 上次测试 | 状态 |
|---|------|---------|---------|------|
| 1 | 创建群聊 | v0.1.0 | 2026-08-01 | ✅ |
| 2 | 添加 AI | v0.1.0 | 2026-08-01 | ✅ |
| 3 | AI 对话 | v0.1.5 | 2026-08-05 | ✅ |
| 4 | 记忆管理 | v0.2.0 | 2026-08-08 | ✅ |
| 5 | 群视界 | v0.3.0 | 2026-08-10 | ✅ |
| 6 | 群视界发图 + 附件交互 | Unreleased | 2026-09-13 | ✅ |
| 7 | 内网地址围栏 | Unreleased | 2026-09-13 | ✅ |

> **文档版本**: v2.0.0 | **更新日期**: 2026-09-13
> v2.0.0 起本文档只写事实：目录、命令、依赖均可原样执行。
