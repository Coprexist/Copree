"""recall_memory — 搜记忆

检索本世界的长期记忆（语义搜索）。用户提到以前的事、或需要历史上下文时调用。
"""
import logging

from app.db_providers import get_provider
from app.services.memory.vector_fallback import score_by_cosine
from app.tools.world.base import WorldToolPlugin, WorldToolContext
from sqlalchemy import select


logger = logging.getLogger(__name__)

#: 无 SQL 向量运算的后端上（SQLite 安装包），Python 侧算余弦的候选上限
_PY_VECTOR_CANDIDATES = 300


class RecallMemoryTool(WorldToolPlugin):
    name = 'recall_memory'
    label = '搜记忆'
    segment = 'memory'

    description = '检索本世界的长期记忆（语义搜索）。用户提到以前的事、或需要历史上下文时调用。'

    parameters = {'query': {'type': 'string', 'description': '搜索查询（描述你想找什么）'},
     'top_k': {'type': 'integer', 'description': '返回条数（1-20，默认 5）'}}

    required = ['query']

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            args = ctx.args
            query = str(args.get("query", "")).strip()
            top_k = max(1, min(int(args.get("top_k") or 5), 20))
            if not query:
                return {"success": False, "error": "query 不能为空"}
            from app.models.world import WorldAIMemory
            from app.utils.embedding import get_embedding
            from app.services.world.world_chat_service import _resolve_world_credentials
            api_key, api_base = await _resolve_world_credentials(ctx.world_repo, ctx.world)
            memories = []
            # 第一轮：向量语义检索（与主站 recall 同款：embedding <=> 余弦距离）
            try:
                vec = await get_embedding(query, api_base_url=api_base, api_key=api_key)
                if get_provider().supports_sql_vector_search:
                    emb_str = "[" + ",".join(str(x) for x in vec) + "]"
                    rows = (await ctx.world_repo.execute(
                        select(WorldAIMemory)
                        .where(WorldAIMemory.world_id == ctx.world.id,
                               WorldAIMemory.embedding.is_not(None))
                        .order_by(WorldAIMemory.embedding.op("<=>")(emb_str))
                        .limit(top_k)
                    )).scalars().all()
                else:
                    # SQLite 安装包没有 `<=>`：候选取回，Python 侧算余弦
                    candidates = (await ctx.world_repo.execute(
                        select(WorldAIMemory)
                        .where(WorldAIMemory.world_id == ctx.world.id,
                               WorldAIMemory.embedding.is_not(None))
                        .limit(_PY_VECTOR_CANDIDATES)
                    )).scalars().all()
                    scored = score_by_cosine(
                        candidates, [m.embedding for m in candidates], vec, top_k,
                    )
                    rows = [m for _sim, m in scored]
                memories = [
                    {"title": m.title, "content": m.content, "created_at": str(m.created_at) if m.created_at else None}
                    for m in rows
                ]
            except Exception as e:
                logger.warning(f"🌐 世界 #{ctx.world.id} 向量检索失败（走文本回退）: {e}")
            # 回退：文本包含匹配（向量不可用或无结果时）
            if not memories:
                rows = (await ctx.world_repo.execute(
                    select(WorldAIMemory)
                    .where(WorldAIMemory.world_id == ctx.world.id)
                    .order_by(WorldAIMemory.created_at.desc())
                    .limit(200)
                )).scalars().all()
                # 分词匹配：query 常是「当前计划 工作状态 图鉴 页面」整串（带空格），
                # 整串子串匹配永远命中不了单条记忆 → 任一关键词命中即算（DeepSeek 无 embedding API，文本回退是主路径）
                import re as _re
                keywords = [k.lower() for k in _re.split(r"[\s,，、;；:：]+", query) if k.strip()]
                memories = [
                    {"title": m.title, "content": m.content, "created_at": str(m.created_at) if m.created_at else None}
                    for m in rows
                    if any(k in (m.title or "").lower() or k in (m.content or "").lower() for k in keywords)
                ][:top_k]
            return {"success": True, "query": query, "memories": memories}
        except (ValueError, TypeError) as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            mems = result.get("memories") or []
            if not mems:
                return "没找到相关记忆"
            return f"检索到 {len(mems)} 条记忆：" + "、".join(m.get('title', '') for m in mems[:5])
        return f"记忆检索失败：{result.get('error', '未知错误')}"