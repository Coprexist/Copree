"""没有 SQL 内向量运算的后端（SQLite 安装包）的余弦检索降级。

db_providers/sqlite.py 的 P0 策略是不在 SQL 里做向量运算，所以任何拼 pgvector
`<=>` 的语句在 SQLite 上都报 "no such function"，整条检索链会断。
调用方先判 `get_provider().supports_sql_vector_search`，不支持时改走这里：
候选范围（scope / owner / world_id）仍由调用方用 SQLAlchemy 语句限定，
本模块只负责「算余弦 → 排序 → 截断」，不碰方言。
"""
import logging

from app.utils.embedding import cosine_similarity

logger = logging.getLogger(__name__)


def score_by_cosine(rows, embeddings, query_embedding, top_k):
    """按余弦相似度对候选行排序，返回 [(similarity, row), ...]。

    rows / embeddings 一一对应（调用方从同一个查询里取，embedding 列单独拿出来喂）；
    相似度 <= 0 的行直接丢弃——语义检索不该把不相关的记忆当结果返回。
    """
    scored = []
    for row, emb in zip(rows, embeddings):
        if not emb:
            continue
        sim = cosine_similarity(query_embedding, emb)
        if sim > 0:
            scored.append((sim, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:top_k]
