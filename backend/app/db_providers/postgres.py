"""
PostgreSQL + pgvector 存储后端

默认后端，保持 Copree 现有行为：
  - 引擎：postgresql+asyncpg / postgresql
  - 向量：pgvector.sqlalchemy.Vector
  - 检索：<=> 余弦距离 + to_tsvector/ts_rank 全文
"""

import logging

from app.config import settings
from app.db_providers.base import BaseProvider

logger = logging.getLogger(__name__)


class PostgresProvider(BaseProvider):
    name = "postgres"
    requires_server = True
    supports_sql_vector_search = True

    def async_engine_url(self) -> str:
        return settings.database_url

    def sync_engine_url(self) -> str:
        return settings.database_url_sync

    def vector_column(self, dimensions: int):
        # 延迟 import：pgvector 只在 postgres 后端需要
        from pgvector.sqlalchemy import Vector
        return Vector(dimensions)

    def vector_similarity_expr(self, column: str, param_name: str) -> str:
        # pgvector: <=> 是余弦距离，1 - distance = 相似度 (0~1)
        return f"(1 - ({column} <=> :{param_name}))"

    def json_text_expr(self, column: str, key: str) -> str:
        return f"{column} ->> '{key}'"

    def hybrid_search_sql(self) -> str:
        # 由调用方组装参数；这里返回纯 SQL 模板
        return """
            SELECT
                gme.message_id,
                gme.content,
                gme.created_at,
                m.sender_type,
                m.sender_id,
                {vector_expr} AS vector_score,
                COALESCE(
                    ts_rank(
                        to_tsvector('simple', gme.content),
                        plainto_tsquery('simple', :query_text)
                    ), 0
                ) AS bm25_score,
                EXTRACT(EPOCH FROM (NOW() - gme.created_at)) / 86400.0 AS age_days,
                (
                    {vector_weight} * ({vector_expr}) +
                    {bm25_weight} * COALESCE(
                        ts_rank(
                            to_tsvector('simple', gme.content),
                            plainto_tsquery('simple', :query_text)
                        ), 0
                    ) +
                    {time_weight} * (1.0 - LEAST(
                        EXTRACT(EPOCH FROM (NOW() - gme.created_at)) / 86400.0 / 30.0, 1.0
                    ))
                ) AS combined_score
            FROM group_message_embeddings gme
            JOIN messages m ON gme.message_id = m.id
            WHERE gme.group_id = :group_id
              AND gme.embedding IS NOT NULL
            ORDER BY combined_score DESC
            LIMIT :top_k
        """

    def is_available(self) -> bool:
        """自检：尝试解析连接串（不实际连接，避免启动阻塞）。"""
        url = settings.database_url
        return url.startswith("postgresql")


postgres_provider = PostgresProvider()