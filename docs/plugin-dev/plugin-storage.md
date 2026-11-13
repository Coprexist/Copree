# 插件存储：申请自己的表

> 插件要持久化业务数据时的唯一做法。目标一句话：**数据归插件，核心不替任何插件长 schema**；
> 卸载默认保数据，重装后接着用。

---

## 1. 什么时候要表、什么时候不要

| 数据 | 放哪 |
|------|------|
| **业务数据**（订阅、规则、统计、账本） | 插件自己的表（本文档） |
| **配置**（密钥、开关、默认值） | `config_schema`（平台加密存 `plugin_configs`，卸载即删） |
| **缓存 / 导出物 / 大体量二进制** | `data/plugin_data/<插件id>/`（不进数据库备份，必须可重建） |

判断标准很简单：**删了插件还想不想留着、重装后要不要接着用**。答案是要，就进表。

不要往核心 `backend/app/models/` 加插件专用的表——那等于让核心替插件长 schema，卸载后主站就脏了。

---

## 2. 申请：写一个迁移

有 `migrations/` 目录就表示「这个插件要表」，没有就当它不要——**清单里不用加任何开关**。

```
backend/plugins/my-plugin/
├── plugin.json
├── plugin.py
└── migrations/
    └── 0001_init.py
```

```python
# migrations/0001_init.py
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    """conn = 同步 Connection（已在本平台的迁移事务里）；t = 带前缀的表名。"""
    meta = sa.MetaData()
    sa.Table(
        t("notes"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "body", name=t("notes_key")),
    )
    meta.create_all(conn)
```

规则：

- **表名一律走 `t("name")`**，不要手写完整表名：前缀是平台的（`plugin_<插件id>__`），前缀化后总长度必须 ≤ 63 字节（PostgreSQL 标识符上限），超了 `t()` 会直接报错并给出预算
- **索引/约束名也走 `t()`**：`t("notes_key")` → `plugin_my_plugin__notes_key`，避免和核心表或别的插件撞名
- **一个文件一个版本**，文件名 `NNNN_描述.py`（四位数字前缀，按文件名排序执行）
- **只前滚**：不写 `downgrade`。卸载默认保数据，需要改表就新增 `0002_xxx.py`
- **SQL 要可移植**：项目支持 PostgreSQL 与 SQLite（`DB_BACKEND`）。能用的写法：`INTEGER/TEXT/VARCHAR/BOOLEAN/DATETIME`、`CREATE INDEX IF NOT EXISTS`、`ALTER TABLE ADD COLUMN`；别用 `SERIAL`、`JSONB`、`GREATEST`、部分索引这类方言专有语法（用 SQLAlchemy 的类型与表达式，它会按方言编译）
- **外键方向**：插件表**可以**引用核心表（`users.id`、`agents.id`）；核心表**绝不**引用插件表
- **迁移要自包含**：已发布的迁移不 import 应用代码（不 `from app.paths import ...` 这类）。迁移是历史，
  发布之后平台怎么重构它都不该变行为；要读环境（如数据根）就自己读环境变量，把当年的事实写进注释

平台没有单独的「schema 声明」概念：**迁移文件就是 DDL 的唯一真相**。查询时你可以自己再定义一份 `sa.Table` 给 SQLAlchemy 用，但它不参与建表。

---

## 3. 平台怎么执行

| 时机 | 行为 |
|------|------|
| 后端启动 | 在插件代码加载**之前**，把所有装了迁移的插件跑到最新（插件的首次查询发生在启动阶段，表必须在） |
| 安装 / 覆盖安装 | 装完立刻跑到最新 |
| 已应用的版本 | 记在核心表 `plugin_migrations(plugin_id, version, checksum, applied_at)`，不会重跑 |
| 文件被改过 | checksum 不符 → **拒绝执行并报错**（改历史迁移会让老库新库分叉），请新增版本 |
| 并发 | PostgreSQL 下按插件取 advisory lock，启动收敛与安装同时发生也不会重跑 |
| 失败 | 该版本事务回滚，错误写进管理台；不影响其他插件与整站启动 |

管理台「已安装」页底部有 **插件数据** 面板：每个插件留了几张表、多少行、占多大，以及一个「清空数据」按钮。

---

## 4. 卸载：默认保数据

- `DELETE /plugins/store/installed/{id}` —— 默认（`purge_data=false`）：删代码、注册、工具、技能段、插件配置；**表和 `plugin_data/<id>/` 保留**，重装后迁移会跳过已应用版本、数据直接续上
- `DELETE ...?purge_data=true` —— 一并删除：`DROP TABLE plugin_<id>__* CASCADE` + 删目录 + 删记账行
- 已卸载但保留数据的插件会出现在「插件数据」面板里（状态「已卸载 · 数据保留」），可单独清空
- 整库备份（PostgreSQL `pg_dump` / SQLite 文件复制）天然包含插件表，不需要额外配置

---

## 5. 在插件代码里用表

```python
from app.services.plugin.storage import table_name


def _t(name: str) -> str:
    return table_name("my-plugin", name)


async def save_note(agent_id: int, body: str) -> None:
    from sqlalchemy import text
    from app.database import async_session

    async with async_session() as db:
        await db.execute(
            text(f'INSERT INTO {_t("notes")} (agent_id, body) VALUES (:a, :b)'),
            {"a": agent_id, "b": body},
        )
        await db.commit()
```

工具类插件执行时已经拿到 `db`，直接复用那个会话即可；服务类插件自己开 `async_session()`。

### 5.1 查询侧：可选的 `tables.py`（推荐）

手写 SQL 时列清单要在迁移与每条语句里各写一遍，加一列就得改好几处，列名拼错要到运行时才炸。
数据多、查询密的插件建议在插件目录里放一份 `tables.py`，**列只声明一次**，查询走 SQLAlchemy 表达式：

```python
# plugins/my-plugin/tables.py
import sqlalchemy as sa
from app.services.plugin.tables import PluginTables

T = PluginTables("my-plugin")
NOTES = T.table(
    "notes",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("agent_id", sa.Integer, nullable=False),
    sa.Column("body", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("agent_id", "body", name=T.name("notes_key")),
)
```

```python
# plugin.py
from .tables import NOTES, T

async with async_session() as db:
    await db.execute(sa.insert(NOTES).values(agent_id=agent_id, body=body))
    rows = (await db.execute(sa.select(NOTES.c.id, NOTES.c.body).where(NOTES.c.agent_id == agent_id))).all()
```

要 upsert 就用 `T.insert(...)`，不要直接 import 某个方言的 insert —— 安装包是 SQLite，
写死 PostgreSQL 的构造器会让插件在那边一执行就报错：

```python
stmt = T.insert(NOTES).values(agent_id=agent_id, body=body)
changed = sa.or_(*(NOTES.c[k] != stmt.excluded[k] for k in ("body",)))
await db.execute(stmt.on_conflict_do_update(
    index_elements=["agent_id"], set_={"body": body}, where=changed,
))
```

`where` 里带上"有列不同"是让同值重存变成零写入：轮询类的写路径（状态、心跳）都该这么写，
空 UPDATE 只会给表添死元组；条件用 `!=`（列 NOT NULL 有默认值），PostgreSQL 与 SQLite 都认。

分工：**DDL 仍只有 `migrations/` 一个来源**（`tables.py` 不建表）；表名与约束名都经 `T.name()/``T.table()` 前缀化。
两份声明是否漂移由测试兜：对每个插件表断言 `tables.py` 的列集合 == 库里实际列集合。

---

## 6. 不做的事

- 不给插件提供独立数据库/schema（SQLite 单机形态没有 schema，前缀是两边都能用的最小约定）
- 不支持 down 迁移：回滚靠新增迁移，卸载默认保数据
- 不允许插件之间互相建表或引用对方的表：前缀是你自己的，别人不能用
- `plugin_data/` 不是可靠存储：它不进数据库备份，只适合缓存与导出物
