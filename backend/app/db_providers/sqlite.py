"""
SQLite 存储后端（免数据库服务，适合本地开发 / exe 打包）

策略：
  - 引擎：sqlite+aiosqlite（SQLAlchemy 原生支持，模型层零改动）
  - 向量列：JSON 文本存储（TypeDecorator 封装，读写为 list[float]）
  - 向量检索：P0 阶段降级为文本检索（复用 Copree 现有降级逻辑）；
    P1 阶段可接入 sqlite-vec 扩展做真向量检索（接口已预留）
"""

import json
import logging
import os

from sqlalchemy import TypeDecorator, Text

from app.config import settings
from app.db_providers.base import BaseProvider

logger = logging.getLogger(__name__)


class JsonVectorType(TypeDecorator):
    """把 list[float] 存成 JSON 文本的向量列类型（SQLite 用）。

    行为对齐 pgvector.Vector：
      - 写入: list[float] -> JSON 字符串
      - 读出: JSON 字符串 -> list[float]
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None


class SQLiteProvider(BaseProvider):
    name = "sqlite"
    requires_server = False

    #: SQLite 原生 SQL 是否支持向量距离运算（P0 不支持，P1 接 sqlite-vec 后置 True）
    supports_sql_vector_search = False

    def async_engine_url(self) -> str:
        path = self._db_path()
        return f"sqlite+aiosqlite:///{path}"

    def sync_engine_url(self) -> str:
        path = self._db_path()
        return f"sqlite:///{path}"

    def vector_column(self, dimensions: int):
        return JsonVectorType()

    def vector_similarity_expr(self, column: str, param_name: str) -> str:
        # P0: SQLite 不在 SQL 内做向量运算，由调用方降级
        raise NotImplementedError(
            "SQLite P0 阶段不支持 SQL 内向量检索，请使用文本搜索降级"
        )

    def json_text_expr(self, column: str, key: str) -> str:
        return f"json_extract({column}, '$.{key}')"

    def hybrid_search_sql(self) -> str:
        # P0: 降级——返回 None 表示调用方走文本搜索
        return None

    def _db_path(self) -> str:
        path = settings.sqlite_db_path
        # 确保目录存在
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                logger.warning(f"创建 SQLite 数据目录失败: {e}")
        return path

    def is_available(self) -> bool:
        return True

    def on_startup(self) -> None:
        logger.info(f"🗄️  SQLite 存储后端就绪: {self._db_path()}")


sqlite_provider = SQLiteProvider()