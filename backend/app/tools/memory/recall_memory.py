"""
recall_memory 工具 — AI 检索相关记忆（关键词优先 + 向量补位，对齐 dsh-mneme）
"""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db_providers import get_provider
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


#: 无 SQL 向量运算的后端上，Python 侧算余弦的候选上限
_PY_VECTOR_CANDIDATES = 300


async def _python_vector_memories(db: AsyncSession, query_embedding, agent_id: int,
                                  scope: str, group_id, top_k: int) -> list[dict]:
    """向量补位的降级路径：SQLite 上把候选取回，在 Python 侧算余弦。"""
    from sqlalchemy import and_, or_, select
    from app.models.memory import DetailMemory, RoughMemory
    from app.services.memory.vector_fallback import score_by_cosine

    if scope == "private":
        cond = and_(RoughMemory.owner_type == "ai", RoughMemory.owner_id == agent_id)
    else:
        cond = and_(
            RoughMemory.scope == "group",
            or_(RoughMemory.group_id == group_id, RoughMemory.group_id.is_(None)),
        )
    stmt = (
        select(
            RoughMemory.id, RoughMemory.title, RoughMemory.scope,
            RoughMemory.embedding, RoughMemory.created_at, DetailMemory.content,
        )
        .outerjoin(DetailMemory, DetailMemory.rough_id == RoughMemory.id)
        .where(cond, RoughMemory.embedding.is_not(None))
        .limit(_PY_VECTOR_CANDIDATES)
    )
    rows = (await db.execute(stmt)).all()
    scored = score_by_cosine(rows, [row.embedding for row in rows], query_embedding, top_k)
    return [
        {
            "id": row.id, "title": row.title, "scope": row.scope,
            "similarity": round(float(sim), 4),
            "content": (row.content[:200] + "...") if row.content and len(row.content) > 200
            else (row.content or ""),
            "source": "vector",
        }
        for sim, row in scored
    ]


class RecallMemory(ToolPlugin):
    name = "recall_memory"
    description = "检索相关记忆"
    segment = "memory"
    parameters = {
        "query": {"type": "string", "description": "搜索查询"},
        "scope": {"type": "string", "enum": ["private", "group"], "description": "搜索范围"},
        "top_k": {"type": "integer", "default": 5, "description": "返回条数（1-20）"},
    }
    required = ["query", "scope"]
    states = ["active", "dnd"]
    admin_description = "搜索并检索相关记忆。收到消息时自动触发检索，AI 也可主动调用来获取历史上下文。"
    trigger_condition = "收到消息自动检索 / AI 主动查询记忆"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.memory.memory_service import (
            _text_search_memories,
            merge_keyword_and_vector,
        )
        from app.utils.embedding import get_embedding

        query = arguments["query"]
        scope = arguments["scope"]
        top_k = min(arguments.get("top_k", 5), 20)
        mem_group_id = arguments.get("group_id", group_id)

        api_key = context.get("api_key")
        api_base = context.get("api_base_url", "https://api.deepseek.com")

        # ═══ 第一轮：关键词搜索（总是执行，字面词命中优先） ═══
        keyword_memories = await _text_search_memories(
            db, agent_id, query, top_k=top_k,
            group_id=mem_group_id if scope == "group" else None,
            scope=scope,
        )

        # ═══ 第二轮：向量补位（可用时） ═══
        vector_memories: list[dict] = []
        embedding_failed = False
        if not get_provider().supports_sql_vector_search:
            # SQLite 安装包：没有 SQL 内向量运算，候选取回 Python 侧算余弦
            try:
                query_embedding = await get_embedding(query, api_base_url=api_base, api_key=api_key)
                vector_memories = await _python_vector_memories(
                    db, query_embedding, agent_id=agent_id, scope=scope,
                    group_id=mem_group_id, top_k=top_k,
                )
            except Exception as e:
                logger.warning(f"recall_memory 向量搜索失败（仅用关键词结果）: {e}")
                embedding_failed = True
        else:
            try:
                query_embedding = await get_embedding(query, api_base_url=api_base, api_key=api_key)
                embedding_str = f"[{','.join(map(str, query_embedding))}]"

                if scope == "private":
                    where_clause = "owner_type = 'ai' AND owner_id = :owner_id"
                    params = {"embedding": embedding_str, "owner_id": agent_id, "top_k": top_k}
                else:
                    where_clause = "scope = 'group' AND (group_id = :group_id OR group_id IS NULL)"
                    params = {"embedding": embedding_str, "group_id": mem_group_id, "top_k": top_k}

                sql = text(f"""
                    SELECT rm.id, rm.title, rm.scope,
                           1 - (rm.embedding <=> :embedding) AS similarity,
                           rm.created_at, dm.content
                    FROM rough_memories rm
                    LEFT JOIN detail_memories dm ON dm.rough_id = rm.id
                    WHERE {where_clause}
                      AND rm.embedding IS NOT NULL
                    ORDER BY rm.embedding <=> :embedding
                    LIMIT :top_k
                """)

                result = await db.execute(sql, params)
                for row in result:
                    vector_memories.append({
                        "id": row.id, "title": row.title, "scope": row.scope,
                        "similarity": round(float(row.similarity), 4) if row.similarity else None,
                        "content": (row.content[:200] + "...") if row.content and len(row.content) > 200
                        else (row.content or ""),
                        "source": "vector",
                    })
            except Exception as e:
                logger.warning(f"recall_memory 向量搜索失败（仅用关键词结果）: {e}")
                embedding_failed = True

        # ═══ 合并：关键词优先 + 向量补位（去重、回填向量分） ═══
        memories, mode = merge_keyword_and_vector(keyword_memories, vector_memories, top_k)

        if not memories:
            extra = "（Embedding API 不可用，已尝试关键词搜索）" if embedding_failed else ""
            return {"memories": [], "message": f"未找到相关记忆{extra}"}

        result = {"memories": memories, "mode": mode}
        if mode == "keyword" and embedding_failed:
            result["notice"] = "⚠️ Embedding API 当前不可用，以上结果为关键词文本匹配（非向量语义搜索）。记忆功能正常但精度略降。"
        return result


ToolRegistry.register(RecallMemory)