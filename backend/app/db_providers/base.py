"""
存储后端抽象基类（BaseProvider）

每个 Provider 负责把"方言相关"的能力封装成统一接口：
  1. 数据库引擎 URL（SQLAlchemy 连接串）
  2. 向量列类型（Postgres 用 pgvector.Vector，SQLite 用 sqlite-vec 的 vec 类型）
  3. 向量相似度检索 SQL
  4. 混合检索（向量 + 全文）SQL
  5. 启动自检（is_available）

设计原则（对齐 DSH seam）：
  - 消费方（memory_service / vector_pipeline）只依赖本接口，
    不 import 任何 pgvector / sqlite 方言代码
  - 注册是"副作用"：新增 Provider 只需在 registry 里登记，
    卸载/切换不触碰业务代码
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """存储后端抽象接口"""

    #: 后端标识，如 "postgres" / "sqlite" / "pglite"
    name: str = "base"

    #: 是否需要独立数据库服务（SQLite 不需要）
    requires_server: bool = True

    #: 是否支持 SQL 内向量相似度运算（pgvector <=> / sqlite-vec）
    #: 为 False 时，调用方（memory_service 等）自动降级为文本检索
    supports_sql_vector_search: bool = False

    @abstractmethod
    def async_engine_url(self) -> str:
        """返回 SQLAlchemy 异步引擎 URL。

        Postgres: postgresql+asyncpg://user:pass@host:5432/db
        SQLite:   sqlite+aiosqlite:///./data/copree.db
        """
        raise NotImplementedError

    @abstractmethod
    def sync_engine_url(self) -> str:
        """返回同步引擎 URL（Alembic / 迁移用）。"""
        raise NotImplementedError

    @abstractmethod
    def vector_column(self, dimensions: int) -> Any:
        """返回方言相关的向量列类型（用于 SQLAlchemy Column）。

        Postgres: pgvector.sqlalchemy.Vector(dimensions)
        SQLite:   sqlite_vec 的 vec 类型或兼容封装
        """
        raise NotImplementedError

    # ────────────────────────────────────────────────
    # 向量检索 SQL（返回完整可执行 SQL 片段 + 参数）
    # ────────────────────────────────────────────────

    @abstractmethod
    def vector_similarity_expr(self, column: str, param_name: str) -> str:
        """返回向量相似度表达式（0~1，越大越相似）。

        Postgres: 1 - ({column} <=> :{param_name})
        SQLite:   1 - vec_distance_cosine({column}, :{param_name})
        """
        raise NotImplementedError

    def vector_search_sql(
        self,
        table_alias: str,
        column: str,
        similarity_expr: str,
        threshold: float,
        where_sql: str,
        order_by_sql: str,
        top_k: int,
    ) -> str:
        """组装向量检索 SQL。

        各 Provider 可覆写以适配方言（LIMIT 语法、参数绑定风格等）。
        默认实现是 Postgres 风格；SQLite 基本一致。
        """
        return f"""
            SELECT * FROM (
                {where_sql}
            ) _vec
            WHERE {similarity_expr} > :threshold
            ORDER BY {order_by_sql}
            LIMIT :top_k
        """

    def json_text_expr(self, column: str, key: str) -> str:
        """返回「按 key 取 JSON 列里的字符串值」的 SQL 片段（原生 SQL 里用）。

        Postgres: {column} ->> '{key}'
        SQLite:   json_extract({column}, '$.{key}')
        """
        raise NotImplementedError

    @abstractmethod
    def hybrid_search_sql(self) -> str:
        """返回混合检索 SQL（向量 + 全文 + 时间衰减）。

        Postgres: pgvector <=> + to_tsvector/ts_rank
        SQLite:   vec_distance_cosine + FTS5（或 LIKE 降级）
        """
        raise NotImplementedError

    # ────────────────────────────────────────────────
    # 生命周期
    # ────────────────────────────────────────────────

    def is_available(self) -> bool:
        """启动自检：返回该后端当前是否可用。"""
        return True

    def on_startup(self) -> None:
        """后端启动钩子（如 SQLite 创建向量扩展、PGlite 初始化）。"""
        pass

    def describe(self) -> dict:
        """返回后端信息（用于诊断/设置界面展示）。"""
        return {
            "name": self.name,
            "requires_server": self.requires_server,
            "async_engine_url": self.async_engine_url(),
        }