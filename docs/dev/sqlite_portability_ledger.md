# SQLite 兼容性账本（跨库 SQL 技术债）

一份用于清债的清单：后端必须同时跑在 PostgreSQL（容器/源码部署）与 SQLite（安装包）上，
所以裸 SQL 里不能出现只有一种库认的写法。

## 运行环境事实

| 部署形态 | 数据库 | SQLite 版本 | schema 来源 |
|---|---|---|---|
| docker compose / 源码 | PostgreSQL 17 | — | Alembic 迁移 |
| 安装包（exe / dmg / tar.gz） | SQLite | 由 CPython 3.11 自带（CI 上实测 3.46.1） | `Base.metadata.create_all` |

关键推论：**迁移文件只服务 PG**，SQLite 的表结构完全来自模型定义。因此
「模型里能用、迁移里写 PG 语法」是允许的；反过来「模型里写了 PG 专属类型/默认值」会直接炸。

## 已修（本次，提交 8f880288）

| 位置 | 原写法 | SQLite 后果 |
|---|---|---|
| `repositories/friend_repo.py` `add_friendship` | `ON CONFLICT (uq_friendship)` | `no such column: uq_friendship`，创建 AI 500 |
| `services/content/conversation_log_service.py` usage_daily upsert | `now()` ×2 | `no such function: now`，用量统计全丢 |
| 同上 `get_session_token_usage` | `token_usage->>'x'::int` | `::` 转换语法报错 |
| `services/infrastructure/tool_usage.py` | `now()` ×2、`current_date - interval 'N days'` | 工具用量统计与汇总都坏 |
| `services/agent/state_stack_service.py` | `updated_at = now()` | 状态栈 touched 失败 |
| `services/channel/token_quota.py` | `now()` ×12 | 通道额度记账全失败 |
| `routers/admin.py` 运营概览疑似来源 | `GROUP BY 2` | SQLite 当常量，聚合静默算错 |
| `config.py` `avatars_dir` | 写死 `/app/uploads/avatars` | Windows 落到盘符根，无权限 → 头像 500 |
| `services/docs/library.py` `candidates()` | 只认容器与源码路径 | 安装包里 `read_manual` 找不到文档 |

## 待核实清单的核实结果（逐条查过代码）

| # | 位置 | 核实结论 |
|---|---|---|
| 1 | `file_service.py:216` | **无债**。已按方言分支：`provider.name == "postgres"` 走 `@>`，否则走 `json_each/json_extract`。账本里原来的疑问可以划掉。 |
| 2 | pgvector `<=>` | **真债，已修**（见下一节）。 |
| 3 | `admin.py` 的 `FILTER (WHERE …)` / `->>` | **已消除**（第三批）。`->>` 改走 `provider.json_text_expr()`，`FILTER` 改 `CASE WHEN`，不再要求 SQLite 版本。 |
| 4 | 全文检索 `to_tsvector`/`tsquery` | **无债**。只出现在 `db_providers/postgres.py`（方言层，且 `hybrid_search_sql()` 在 SQLite provider 里返回 None 表示降级），业务层没有直接用。 |
| 5 | 序列 `nextval`/`setval` | **无**。全仓 grep 为空，自增靠模型 `autoincrement`。 |
| 6 | `UPDATE … FROM` / `DELETE … USING` / `TRUNCATE` | **无**。唯一命中是 `sandbox_isolate.py` 的 `LANDLOCK_ACCESS_FS_TRUNCATE` 常量，不是 SQL。 |
| 7 | `pg_advisory_lock` | **无**。全仓 grep 为空。 |

## 已修（第二批：向量检索的静默降级）

| 位置 | 问题 | 修法 |
|---|---|---|
| `routers/memories.py` `/memories/search` | 裸 `1 - (embedding <=> :embedding)`，**没有任何兜底** → SQLite 上这个接口直接 500。 | 判 `get_provider().supports_sql_vector_search`：PG 走原 SQL；否则 `_python_vector_search()` 取候选在 Python 侧算余弦（上限 500 条） |
| `tools/memory/recall_memory.py` | 靠 `except` 兜住：不崩，但每次调用都打 warning，语义检索在安装包里等于没有 | 同上分支，新增 `_python_vector_memories()`（上限 300 条，含 detail_memories 的 content） |
| `tools/world/recall_memory.py` | `embedding.op("<=>")` 直接进 order_by，靠 `except` 掉到文本回退 | 判能力后：PG 走 `<=>`，否则取候选 Python 算余弦（上限 300 条） |
| `utils/embedding.py` | 没有纯 Python 相似度函数 | 新增 `cosine_similarity()`（维度不一致返回 0，避免换过 embedding 模型的历史数据被误算） |
| `services/memory/vector_fallback.py` | — | 新增 `score_by_cosine(rows, embeddings, query_embedding, top_k)`：向量降级的公共实现，方言无关 |

验证：容器内用 SQLite（3.46）+ 真实模型 `Base.metadata.create_all` 建库，插入 4 条记忆
（近邻 / 正交 / 别人的 / 群），跑真实 `_python_vector_search`：
private→`[('近邻', 0.995)]`（正交的 0 分被丢弃）、group(NULL)→`[('群记忆', 0.8944)]`、group(123)→`[]`。

## 已修（第三批：把裸写 SQL 收口到方言层）

上一批只修了「当场报错」的那几处，剩下的还是手写转义。这一批按「方言语法只许出现在
`db_providers/`」的原则收口：

| 位置 | 原写法 | 修法 |
|---|---|---|
| `conversation_log_service.py` 6 处用量聚合 | `COALESCE(SUM(ud.total_tokens), 0)::bigint` ×36 | `CAST(COALESCE(SUM(...), 0) AS BIGINT)`：两种方言都认（SQLite 上 `::` 报 `unrecognized token: ":"`） |
| `routers/admin.py` 运营总览 3 条 SQL | `details->>'kind'`、`count(*) FILTER (WHERE …)` | `provider.json_text_expr()` 取 JSON；聚合改 `sum(CASE WHEN … THEN 1 ELSE 0 END)` / `count(DISTINCT CASE … END)`，不再依赖 SQLite ≥ 3.30 |
| `tools/network/bilibili_summary.py` | `system_prompt_overrides->'bilibili'->>'sessdata'` | SQLAlchemy JSON 下标 `col["bilibili"]["sessdata"].as_string()`（PG 编译成 `->>`，SQLite 编译成 `json_extract`） |
| `services/infrastructure/embedding_config_service.py` | `pg_attribute` 自检无条件跑 | 只在 `provider.name == "postgres"` 时跑；SQLite 的向量列是 JSON 文本，没有「定长列维度」可言 |
| `db_providers/base.py` + 两个 provider | 业务层各写各的转义 | 新增 `json_text_expr(column, key)`：方言片段统一由 provider 生成 |

**并新增守卫测试** `backend/tests/test_sql_dialect_guard.py`：用 AST 遍历 `app/` 下所有 SQL
字符串字面量（跳过注释与 docstring），命中 10 类 PG 专有写法即失败——`::type`、`->`/`->>`、
`now()`、`interval '`、`GROUP BY 序号`、`nextval`、`advisory_lock`、`<=>`、`FILTER (`、
`to_tsvector`。方言层 `db_providers/` 豁免；其余例外必须在 `GUARDED_FILES` 里写清「靠什么保证
SQLite 不会走到这段 SQL」。第二个用例断言两个 provider 给出的 JSON 取值片段确实不同，
防止哪天被顺手改成一样的。

## 写跨库 SQL 的规矩

- 时间戳：用 `CURRENT_TIMESTAMP`（两边都认）。禁止 `now()`。
- 冲突处理：`ON CONFLICT (列列表) DO ...`。禁止写约束名。
- 类型转换：走 SQLAlchemy 的 `cast()` / `TypeCoerce` / JSON 列的 `.as_integer()`，禁止手写 `::`。
- 时间窗：在 Python 里算成参数传进 SQL，禁止 `interval` 字面量。
- JSON 取值：SQLAlchemy JSON 下标（`col["k"]`），禁止手写 `->>'k'`（除非已按方言分支）。
- 聚合条件：`count(*) FILTER (WHERE ...)` 可以，但 `GROUP BY`/`ORDER BY` 必须写列名或表达式，禁止序号。
- 方言差异（向量检索、相似度、全文检索）统一放 `db_providers/`，业务层只调抽象接口。

## 审计

日常靠守卫测试（`python tests/run_without_pytest.py sql_dialect_guard`）兜住回归；
下面这组 grep 用来人工复查它覆盖不到的地方（例如 SQL 拼在一层变量里）：

```bash
cd backend
grep -rn "now()" --include=*.py app | grep -v "utc_now()\|datetime\.now()"
grep -rn -- "->>\|::int\|::text\|::date\|::jsonb" --include=*.py app
grep -rn "interval '\|current_date\|date_trunc\|EXTRACT(" --include=*.py app
grep -rn "GROUP BY [0-9]\|ORDER BY [0-9]" --include=*.py app
grep -rn "on_conflict_do_.*(constraint=" --include=*.py app
grep -rn -- "@>\|<->\|to_tsvector\|advisory_lock\|unnest(\|generate_series" --include=*.py app
```