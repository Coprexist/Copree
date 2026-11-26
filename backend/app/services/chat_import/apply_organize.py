"""
把智能整理的产出落进已经建好的 AI：记忆库 + 知识库。

为什么和「导入对话」分开：对话是**历史**（写进私信消息），整理出来的东西是**设定**
（记忆条目、知识库文档）。一次导入可以只要历史不要设定，也可以先看预览再决定要哪几样，
所以两条写入路径各自独立，由调用方决定调不调。

提示词不在这里：它是创建表单里的一个字段，由用户在创建前看着改——写进人设这件事
应该让人看见，而不是整理完就悄悄定死。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 记忆标题：取正文第一句，太长就退到 60 字——标题是索引里给人看的那一行
TITLE_MAX = 60


def _title_of(content: str) -> str:
    first = re.split(r"[。！？\n]", content.strip(), maxsplit=1)[0].strip()
    if not first:
        first = content.strip()
    return first[:TITLE_MAX] or "导入的记忆"


def _doc_path(title: str, index: int) -> str:
    """知识库文件名：稳定、可读、不重复。中文标题直接用，去掉文件系统不认的字符。"""
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "-", (title or "").strip()).strip("-")
    return f"{safe or '导入资料'}-{index}.md"


async def apply(db: AsyncSession, *, agent, organized: dict) -> dict:
    """写入记忆与知识库。调用方负责 commit。"""
    from app.models.memory import DetailMemory, RoughMemory
    from app.services.agent import knowledge_service

    memories = [m for m in (organized.get("memories") or []) if (m.get("content") or "").strip()]
    for m in memories:
        content = str(m["content"]).strip()
        rough = RoughMemory(
            owner_type="ai",
            owner_id=int(agent.id),
            title=_title_of(content),
            scope="private",
            status="active",
            value_score=int(m.get("importance") or 3),
            mem_type="event",
            session_refs=[],
            session_foci=[],
            semantic_foci=[],
        )
        db.add(rough)
        await db.flush()          # 正文要 rough_id
        # 不生成 embedding：与灵魂档案导入同一口径——检索时缺向量的走文本通道，
        # 用的时候自然补上，不在导入链路上依赖 embedding 服务
        db.add(DetailMemory(rough_id=rough.id, content=content))

    written_docs = 0
    for i, doc in enumerate(organized.get("knowledge") or [], start=1):
        title = str(doc.get("title") or "").strip()
        content = str(doc.get("content") or "").strip()
        if not title or not content:
            continue
        await knowledge_service.restore_entry(
            db, int(agent.id),
            path=_doc_path(title, i),
            title=title,
            summary=str(doc.get("summary") or "").strip(),
            content=content,
        )
        written_docs += 1

    await db.flush()
    return {"memories": len(memories), "knowledge": written_docs}
