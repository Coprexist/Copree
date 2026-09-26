# 向量检索与 Embedding 配置

> 本文档覆盖 Copree 的向量检索体系：Embedding 提供方插件化、维度配置、
> 前端图形化管理、记忆检索策略、索引优化。适用于部署者与开发者。

---

## 一、架构总览

```
                     ┌─────────────────────────────────────────┐
                     │  EmbeddingProvider 插件（可替换）        │
                     │  disabled | ollama | api | local        │
                     └──────────────┬──────────────────────────┘
                                    │ embed(text) → vector | None
                                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  记忆检索（关键词优先 + 向量补位，对齐 dsh-mneme 哲学）      │
   │  merge_keyword_and_vector():                                 │
   │    ① 关键词命中（ILIKE/ngram）总是排最前                     │
   │    ② 向量结果去重补位剩余槽位                                │
   └─────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  存储（pgvector）                                             │
   │  · 向量列 vector(N)，N = EMBEDDING_DIMENSION（用户自选）      │
   │  · HNSW 索引（vector_cosine_ops）                            │
   │  · 过滤列 btree 索引（owner_id, scope）→ 先缩小范围再检索     │
   └─────────────────────────────────────────────────────────────┘
```

---

## 二、Embedding 提供方插件化

### 2.1 四种后端（`EMBEDDING_BACKEND`）

| 后端 | 说明 | 适用场景 |
|------|------|---------|
| `disabled` | **默认**。不生成向量，记忆走纯文本检索 | 无 embedding 服务时 |
| `ollama` | 复用本地/远程 Ollama 实例（原生 `/api/embeddings`）| 有 Ollama 直接复用，无需额外部署 |
| `api` | 任意 OpenAI 兼容 `/v1/embeddings`（OpenAI/硅基流动/智谱/阿里云）| 用厂商 embedding API |
| `local` | fastembed 本地模型（ONNX CPU，离线可用）| 数据不出本机、不想接外部服务 |

### 2.2 代码结构

```
backend/app/embedding_providers/
├── base.py      # EmbeddingProvider ABC：embed() 失败返回 None（永不打断主流程）
├── registry.py  # get_embedding_provider() 按 settings.embedding_backend 选择
├── ollama.py    # Ollama 实例
├── api.py       # OpenAI 兼容端点
├── local.py     # fastembed 离线模型
└── disabled.py  # 默认降级（纯文本）
```

**关键设计**（对齐 dsh-mneme 向量哲学）：
- 向量是**可选增强**：embed 失败返回 `None`，调用方自动降级文本检索，**功能不受影响**
- 配置与 chat **完全解耦**（不依赖 DeepSeek——其无 embedding API）
- 接口语义对齐 OpenAI 兼容 `/embeddings` 端点，未来转 JS 契约不变

### 2.3 环境变量配置

```bash
# .env（部署时定初始值）
EMBEDDING_BACKEND=disabled        # disabled | ollama | api | local
EMBEDDING_BASE_URL=               # ollama: http://host:11434；api: https://host/v1
EMBEDDING_API_KEY=                # 仅 api 后端需要
EMBEDDING_MODEL=                  # ollama: nomic-embed-text；api: text-embedding-3-small 等
EMBEDDING_DIMENSION=1536          # 见第三节
```

> **注意**：容器内访问宿主 Ollama 用 `http://host.docker.internal:11434`（见 §9.1），
> 不是 `127.0.0.1`（容器内指向自己）。

---

## 三、向量维度配置（速度 vs 质量取舍）

pgvector 列维度**建表时固定**（`vector(N)`），换模型（维度变化）需改列。

### 3.1 用户自选取舍

| 模型 | 维度 | 特点 |
|------|------|------|
| `nomic-embed-text`（Ollama）| 768 | 快、省空间（NAS 推荐）|
| `text-embedding-3-small` | 1536 | 准（需 OpenAI 兼容 API）|
| `bge-large-zh-v1.5` | 1024 | 中文效果好（硅基流动）|
| `bge-small-zh-v1.5`（local）| 512 | 轻量离线 |

### 3.2 自动对齐机制

- **无向量数据**：启动时（prestart）自动 `ALTER COLUMN TYPE vector(N)`，毫秒级零风险
- **已有向量数据**：需用迁移脚本安全迁移（见 3.3）
- **SQLite 后端**：维度无关（JsonVectorType 存 JSON 数组）

### 3.3 有数据时的安全迁移

```bash
# 对齐 pg-raggraph expand/contract 方案
python scripts/migrate_embedding_dimension.py prepare --dim 768
python scripts/migrate_embedding_dimension.py backfill --dim 768   # 可中断重跑
python scripts/migrate_embedding_dimension.py cutover --dim 768    # 守卫检查后切换
# 或一键
python scripts/migrate_embedding_dimension.py all --dim 768
```

**cutover 守卫**：未回填行 > 0 时拒绝切换（避免静默丢向量），可 `--force` 跳过。

### 3.4 回填 NULL 记忆

生产启用向量后，给存量 `embedding IS NULL` 的记忆补向量：

```bash
python scripts/migrate_embedding_dimension.py fill
```

---

## 四、前端图形化配置（部署后零门槛管理）

### 4.1 三层配置优先级

```
DB 覆盖（前端图形化修改） > 环境变量（.env） > 代码默认值（config.py）
```

- **持久**：覆盖值存 `system_settings.embedding_config`（JSONB），重启不丢
- **快**：读路径走内存缓存（pydantic-settings `customise_sources` 官方机制），
  保存时更新缓存 + 重建 settings 实例，**热更新无需重启**
- **兼容**：DB 未覆盖的字段自动落到 env/默认；「恢复默认」一键清除 DB 覆盖

### 4.2 管理界面操作

管理员 → 系统设置 → **Embedding 向量配置**卡片：

- 后端类型下拉（disabled/ollama/api/local，带说明）
- 端点 / 模型 / 维度表单（标注来源：界面改的 or 环境变量）
- API Key（密码框，**Fernet 加密存储**，不回显）
- **测试连接**（实际调一次 embed，返回维度）
- **保存并生效**（热更新）/ **恢复默认**

### 4.3 管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/embedding-config` | 当前生效配置（api_key 脱敏）|
| PUT | `/admin/embedding-config` | 保存（持久化 + 热更新）|
| DELETE | `/admin/embedding-config` | 恢复默认 |
| POST | `/admin/embedding-config/test` | 测试连接 |

### 4.4 通用配置组机制（加配置 = 加声明）

「前端图形化改配置」已泛化为**多配置组机制**（`app_config_service.py`）：

```
CONFIG_GROUPS = {
    "embedding": { "column": "embedding_config", "fields": {...} },  # 已上线
    "runtime":   { "column": "runtime_config",   "fields": {...} },  # 检索参数/时区/摘要 TTL
}
```

- 每个组 = `system_settings` 一个 JSONB 列 + 字段 schema 声明
- **加第三批配置 = 在 CONFIG_GROUPS 加一个声明**，前后端自动支持（零新代码）
- 通用 API：`GET /admin/configs`（schema）/ `GET|PUT|DELETE /admin/configs/{group}`
- 通用组件 `ConfigGroupCard`：读 schema 自动渲染表单，**每个字段带"是什么 + 为什么改"说明**（小白友好）

### 4.5 i18n 规范（新增文案必须遵守）

1. **翻译禁 emoji**：`✅❌⚠️` 等不出现在 i18n 字典——图标由前端统一渲染（lucide Check/X/Loader2 + 状态色）
2. **新增前端文案必须走 i18n**：分区文件（`tool.ts`/`admin_config.ts` 模式）+ 三语 zh/en/ja，禁止硬编码中文
3. **后端 schema 只下发 i18n key**（`label_key`/`hint_key`），前端 `t()` 翻译

---

## 五、记忆检索策略（对齐 dsh-mneme）

### 5.1 合并检索：关键词优先 + 向量补位

```python
merge_keyword_and_vector(keyword_results, vector_results, top_k)
    → ([{id, title, content, similarity, source}], mode)
```

- **关键词命中总是排最前**（字面词 = 用户/AI 的原文，精确性最高）
- **向量结果去重补位**剩余槽位（按相似度排序）
- 重叠条目回填真实向量分（O(n) dict 查找）
- `mode` 返回 `keyword` / `vector`（元信息，可展示"当前是语义还是关键词"）

### 5.2 中文关键词提取（ngram）

```python
extract_keywords(query, max_parts=8)
```

- 标点/空格拆分 → 2-gram 滑窗（中文无分词时也能子串命中）
- 纯英文/数字不 ngram（避免 `BA`/`AA` 碎片噪音）
- 无第三方依赖（不引 jieba），SQLite/PG 通用

### 5.3 检索排序

- `_text_search_memories`：`title` 命中优先（对齐 mneme `CASE WHEN`），再按时间倒序

---

## 六、索引与性能（数据量增长时）

### 6.1 过滤先行（核心原则）

全站记忆量大时，**先按结构化字段缩小范围，再向量检索**——与全站总量解耦。

```
WHERE owner_id=:me AND scope='private'     -- btree 索引定位: O(log N)
  → 只对自己的 M 条记忆做向量排序           -- O(M log M)
```

### 6.2 索引清单（Alembic 版本 `d5e6f7a8b9c1`）

| 表 | 索引 | 用途 |
|----|------|------|
| `rough_memories` | `(owner_id, scope)` | 检索按 owner+scope 过滤 |
| `detail_memories` | `(rough_id)` | 按 rough 归属过滤 |
| `group_message_embeddings` | `(group_id)` | 按群过滤 |
| `world_ai_memories` | `(world_id)` | 已有（ix_world_ai_memories_world_id）|
| 4 张表 | HNSW `(embedding vector_cosine_ops)` | 向量距离索引（已有）|

### 6.3 为什么不用 HNSW 直接过滤

pgvector 官方文档明确：HNSW 过滤是**索引扫描后应用**，默认 `ef_search=40`
时低比例条件可能只命中几行（召回崩）。正确做法（官方推荐）：
> "Exact indexes work well for conditions that match a low percentage of rows"

我们的场景（每 owner 记忆占比极低）正是 **btree 过滤列 + 小集合精确排序**。

### 6.4 实测（5 万行模拟）

| | 无索引（全扫）| 有索引（收窄）|
|---|---|---|
| 扫描方式 | Seq Scan 5 万行 | Bitmap Index Scan 定位 5000 行 |
| 耗时 | 4.0ms | 2.4ms |
| 数据量越大 | 线性恶化 | 几乎不变 |

---

## 七、生产启用清单

### 7.1 启用 Ollama 向量

1. **代码就位**：`git pull origin main`（或在 NAS 直接改）
2. **前端配置**：管理员 → 系统设置 → Embedding 向量配置
   - 后端类型：`Ollama（本地）`
   - API 地址：`http://host.docker.internal:11434`（详见 §9.1 三种部署形态）
   - 模型：`nomic-embed-text`，维度：`768`
   - 保存并生效
3. **回填存量记忆**：
   ```bash
   python backend/scripts/migrate_embedding_dimension.py fill
   ```
4. **重启 backend**（如需对齐列维度）：
   ```bash
   docker compose up -d --force-recreate backend
   ```

### 7.2 验证

```bash
# 向量已回填
SELECT count(*) FILTER (WHERE embedding IS NOT NULL) FROM rough_memories;
# HNSW 索引
SELECT indexname FROM pg_indexes WHERE indexdef ILIKE '%hnsw%';
# 网页测试连接（系统设置 → 测试连接 → 应显示维度）
```

---

## 八、常见问题

**Q: DeepSeek 能用向量吗？**
A: 不能。DeepSeek 官方无 embedding API（调用 404）。需配置独立 embedding 源
（Ollama / OpenAI 兼容 / fastembed），配置与 chat 完全解耦。

**Q: 换模型后维度变了怎么办？**
A: 无向量数据 → 改 `EMBEDDING_DIMENSION` 重启自动对齐；
有向量数据 → 跑迁移脚本（`all --dim N`）。

**Q: 前端改了配置要重启吗？**
A: 不用。保存即热更新（重建 settings + provider 动态读配置）。

**Q: 向量失败会影响记忆功能吗？**
A: 不会。embed 失败返回 None → 自动降级文本检索，记忆功能正常（精度略降）。

---

## 九、实测：中文区分度与模型取舍（2026-09-26）

### 9.1 接入方式（跨平台）

`embedding_base_url` 按部署形态填，三者不要混：

| 部署形态 | 该填什么 |
|---------|---------|
| Ollama 跑在宿主机 | `http://host.docker.internal:11434`（compose 已加 `extra_hosts: host.docker.internal:host-gateway`；Linux 靠它解析网关，Docker Desktop 自带该名字） |
| Ollama 是 compose 里的另一个服务 | 服务名，如 `http://ollama:11434` |
| 后端不在容器里跑（直接 uvicorn） | `http://127.0.0.1:11434` |

不要填 `172.17.0.1` / `172.18.0.1` 这类 docker0 网关地址：每个部署的网段都不一样，
写死只对那一台机器成立（本文档早期版本曾这么写，已改正）。

### 9.2 nomic-embed-text 的区分度实测

同一台 Ollama、两个端点（`/api/embeddings` 与 `/v1/embeddings`）结果一致，768 维：

| 文本对 | 余弦相似度 |
|-------|-----------|
| 「他最喜欢的颜色是靛蓝」 vs 「他最爱的色调是靛蓝」（同义） | 1.00 |
| 「化学老师明天要抽查三样」 vs 「他答应明天抽查热NaOH通氯气」（同主题） | 0.84 |
| 「他最喜欢的颜色是靛蓝」 vs 「今天群里聊了会儿球赛」（无关） | 0.68 |
| 「他最喜欢的颜色是靛蓝」 vs 「量子力学研究微观粒子」（无关） | 0.67 |
| 「cat is a small pet」 vs 「The user prefers indigo」（英文，无关） | 0.34 |
| 中文 vs 英文（跨语言，含同义对） | 0.37~0.42 |

结论：**英文与跨语言正常，中文内部的地板被抬到 0.63~0.68**。同主题（0.84）与无关
（0.68）只差 0.16，`similarity_threshold = 0.35` 对中文形同虚设——凡中文必过，
排序基本靠噪声。加 `search_query:` / `search_document:` 前缀不改变这个结论。

### 9.3 为什么不要「改用英文写记忆」

一个自然的想法是：既然英文向量干净，就让 AI 用英文写记忆。实测**反向**：

    中文查询「他最喜欢的颜色是靛蓝」
      ├─ 同义英文「The user prefers indigo」   → 0.39
      └─ 无关中文「今天群里聊了会儿球赛」       → 0.68

同义的英文排在无关的中文后面。检索的两端必须同一种语言，而这个模型跨语言只有
0.37~0.42（低于它中文内部的地板），所以「英文记忆 + 中文查询」是最差的组合。
要沿这条路走就得两端都英文，也就是在召回前多加一次翻译——为绕开模型的短板引入
一个新失败点，不值得。

### 9.4 什么时候该换模型

关键词检索是当前主力，向量只做补位，中文语义弱一点不影响记忆功能可用。出现下面
情况再换：

- 语义召回开始承担主要检索职责（关键词命中率不足）；
- 出现「换了说法就找不回来」的实际反馈。

换 `bge-m3`（1024 维，中文与跨语言都强不少）的代价：

1. 把模型拉到 Ollama；
2. `python scripts/migrate_embedding_dimension.py all --dim 1024`（幂等、可中断、可重跑）；
3. 改 `.env` 的 `EMBEDDING_DIMENSION=1024`（列类型在 import 时确定，DB 配置管不到它）；
4. 重启 backend，并在管理页把模型名与维度改成新的。

四步，第 2 步可随时重跑——这是项目自带的平滑迁移能力，不是一次性脚本。
