"""
目录级结构记忆 Service（双重记忆架构的系统2）

数据库版实现，与文件系统版 memory_index.py 互补：
- 文件系统: 适合经常直接编辑的大文档
- 数据库: 适合频繁 CRUD 的结构化记录，百万级无压力

目录结构: {category}/{sub_key}/{field} → value
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.structured_record import StructuredRecord
from app.repositories.memory_repo import MemoryRepository, SQLAlchemyMemoryRepository
from app.utils.pure.memory_weight import clamp_weight, default_weight

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyMemoryRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyMemoryRepository(db_or_repo)
    return db_or_repo


async def sr_set(
    db: AsyncSession,
    agent_id: int,
    category: str,
    sub_key: str,
    field: str,
    value: str,
    mem_type: str | None = None,
    weight: int | None = None,
    session_foci: list | None = None,
    semantic_foci: list | None = None,
) -> dict:
    """写入一个字段（upsert：同路径重复写入自动覆盖）。

    类型、权值与焦段锚点都是可选的：不传保持原值，新建则按类型默认。
    """
    db = _ensure_repo(db)
    try:
        result = await db.execute(
            select(StructuredRecord).where(
                StructuredRecord.agent_id == agent_id,
                StructuredRecord.category == category,
                StructuredRecord.sub_key == sub_key,
                StructuredRecord.field == field,
            )
        )
        existing = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if existing:
            existing.value = value
            if mem_type:
                existing.mem_type = mem_type
            if weight is not None:
                existing.value_score = clamp_weight(weight)
            if session_foci is not None:
                existing.session_foci = list(session_foci)
            if semantic_foci is not None:
                existing.semantic_foci = list(semantic_foci)
            existing.updated_at = now
            await db.commit()
            return {"ok": True, "action": "updated", "id": existing.id}
        else:
            kind = mem_type or "daily"
            record = StructuredRecord(
                agent_id=agent_id,
                category=category,
                sub_key=sub_key,
                field=field,
                value=value,
                mem_type=kind,
                value_score=clamp_weight(weight) if weight is not None else default_weight(kind),
                session_foci=list(session_foci or []),
                semantic_foci=list(semantic_foci or []),
            )
            db.add(record)
            await db.commit()
            return {"ok": True, "action": "created", "id": record.id}
    except Exception as e:
        await db.rollback()
        logger.error(f"sr_set 失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


async def sr_get(
    db: AsyncSession,
    agent_id: int,
    category: str,
    sub_key: str,
    field: str | None = None,
) -> dict:
    """读取一个子目录的所有字段（field=None 则返回全部），或指定单个字段"""
    db = _ensure_repo(db)
    try:
        conditions = [
            StructuredRecord.agent_id == agent_id,
            StructuredRecord.category == category,
            StructuredRecord.sub_key == sub_key,
        ]
        if field:
            conditions.append(StructuredRecord.field == field)

        result = await db.execute(
            select(StructuredRecord).where(*conditions)
        )
        records = result.scalars().all()
        if not records:
            return {"fields": {}}

        fields = {}
        for r in records:
            fields[r.field] = r.value

        return {"fields": fields}
    except Exception as e:
        logger.error(f"sr_get 失败: {e}", exc_info=True)
        return {"fields": {}, "error": str(e)}


async def sr_list(
    db: AsyncSession,
    agent_id: int,
    category: str,
) -> dict:
    """列出某个 category 下的所有 sub_key（子目录）及其字段数"""
    db = _ensure_repo(db)
    try:
        result = await db.execute(
            select(
                StructuredRecord.sub_key,
                func.count(StructuredRecord.id).label("cnt"),
                func.max(StructuredRecord.updated_at).label("last_update"),
            )
            .where(
                StructuredRecord.agent_id == agent_id,
                StructuredRecord.category == category,
            )
            .group_by(StructuredRecord.sub_key)
            .order_by(func.max(StructuredRecord.updated_at).desc())
            .limit(50)
        )
        rows = result.all()
        items = [
            {
                "sub_key": r.sub_key,
                "field_count": r.cnt,
                "last_update": r.last_update.isoformat() if r.last_update else None,
            }
            for r in rows
        ]
        return {"items": items}
    except Exception as e:
        logger.error(f"sr_list 失败: {e}", exc_info=True)
        return {"items": [], "error": str(e)}


async def sr_summary(
    db: AsyncSession,
    agent_id: int,
    category: str,
    sub_key: str,
) -> dict:
    """生成一个子目录的快照摘要（返回字段名 + 简短值预览）"""
    db = _ensure_repo(db)
    try:
        result = await db.execute(
            select(StructuredRecord).where(
                StructuredRecord.agent_id == agent_id,
                StructuredRecord.category == category,
                StructuredRecord.sub_key == sub_key,
            )
        )
        records = result.scalars().all()
        if not records:
            return {"summary": "（空）", "fields": {}, "total": 0}

        fields = {}
        for r in records:
            preview = r.value[:80] + "..." if len(r.value) > 80 else r.value
            fields[r.field] = preview

        total = len(records)
        field_names = ", ".join(fields.keys())
        summary = f"{total} 个字段：{field_names}"

        return {"summary": summary, "fields": fields, "total": total}
    except Exception as e:
        logger.error(f"sr_summary 失败: {e}", exc_info=True)
        return {"summary": "（出错）", "fields": {}, "total": 0, "error": str(e)}


async def sr_categories(
    db: AsyncSession,
    agent_id: int,
) -> dict:
    """列出该 AI 使用的所有 category"""
    db = _ensure_repo(db)
    try:
        result = await db.execute(
            select(
                StructuredRecord.category,
                func.count(StructuredRecord.id).label("record_count"),
                func.count(func.distinct(StructuredRecord.sub_key)).label("sub_count"),
            )
            .where(StructuredRecord.agent_id == agent_id)
            .group_by(StructuredRecord.category)
            .order_by(StructuredRecord.category)
        )
        rows = result.all()
        categories = [
            {
                "name": r.category,
                "record_count": r.record_count,
                "sub_count": r.sub_count,
            }
            for r in rows
        ]
        return {"categories": categories}
    except Exception as e:
        logger.error(f"sr_categories 失败: {e}", exc_info=True)
        return {"categories": [], "error": str(e)}


async def sr_delete(
    db: AsyncSession,
    agent_id: int,
    category: str,
    sub_key: str | None = None,
    field: str | None = None,
) -> dict:
    """删除记录（可按 field 删除单条，或删整个 sub_key，或删整个 category）"""
    db = _ensure_repo(db)
    try:
        conditions = [StructuredRecord.agent_id == agent_id, StructuredRecord.category == category]
        if sub_key:
            conditions.append(StructuredRecord.sub_key == sub_key)
        if field:
            conditions.append(StructuredRecord.field == field)

        stmt = delete(StructuredRecord).where(*conditions)
        result = await db.execute(stmt)
        await db.commit()
        deleted = result.rowcount
        return {"ok": True, "deleted": deleted}
    except Exception as e:
        await db.rollback()
        logger.error(f"sr_delete 失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


async def sr_rename(
    db: AsyncSession,
    agent_id: int,
    category: str,
    new_name: str,
    level: str = "category",
    sub_key: str | None = None,
    field: str | None = None,
) -> dict:
    """改名：category / sub_key / field 任一级（对齐世界版 2026-08-12）"""
    db = _ensure_repo(db)
    try:
        new_name = (new_name or "").strip()
        if not new_name:
            return {"ok": False, "error": "new_name 不能为空"}
        conditions = [StructuredRecord.agent_id == agent_id, StructuredRecord.category == category]
        if level == "sub_key":
            if not sub_key:
                return {"ok": False, "error": "rename sub_key 需要 sub_key 定位"}
            conditions.append(StructuredRecord.sub_key == sub_key)
        elif level == "field":
            if not sub_key or not field:
                return {"ok": False, "error": "rename field 需要 sub_key + field 定位"}
            conditions.append(StructuredRecord.sub_key == sub_key)
            conditions.append(StructuredRecord.field == field)
        elif level != "category":
            return {"ok": False, "error": f"未知 level: {level}（category|sub_key|field）"}

        rows = (await db.execute(select(StructuredRecord).where(*conditions))).scalars().all()
        if not rows:
            return {"ok": False, "error": "没有匹配的记录"}
        for r in rows:
            if level == "category":
                r.category = new_name
            elif level == "sub_key":
                r.sub_key = new_name
            else:
                r.field = new_name
        await db.commit()
        return {"ok": True, "renamed": len(rows), "level": level, "new_name": new_name}
    except Exception as e:
        await db.rollback()
        logger.error(f"sr_rename 失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


async def sr_move(
    db: AsyncSession,
    agent_id: int,
    category: str,
    sub_key: str,
    to_category: str,
    field: str | None = None,
) -> dict:
    """移动：整组 sub_key 或单条 field 跨目录（对齐世界版 2026-08-12）"""
    db = _ensure_repo(db)
    try:
        to_category = (to_category or "").strip()
        if not to_category or not sub_key:
            return {"ok": False, "error": "move 需要 to_category + sub_key"}
        conditions = [
            StructuredRecord.agent_id == agent_id,
            StructuredRecord.category == category,
            StructuredRecord.sub_key == sub_key,
        ]
        if field:
            conditions.append(StructuredRecord.field == field)
        rows = (await db.execute(select(StructuredRecord).where(*conditions))).scalars().all()
        if not rows:
            return {"ok": False, "error": "没有匹配的记录"}
        for r in rows:
            r.category = to_category
        await db.commit()
        return {"ok": True, "moved": len(rows), "to_category": to_category}
    except Exception as e:
        await db.rollback()
        logger.error(f"sr_move 失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


async def format_db_records_for_prompt(db: AsyncSession, agent_id: int) -> str:
    """
    记忆索引（缩进树 + 软锚定，2026-08-12 瘦身对齐世界版）。

    格式（省 token + LLM 解析友好）：
    ## 记忆索引
    project/
      图鉴页面/
        进度
      ⭐当前计划
    user/
      ❗偏好

    规则：只注入有内容的路径（空目录不出现）；⭐=重要记忆 ❗=硬约束（value 前缀标记，软锚定）；
    详细内容用 manage_records get 按需取。
    """
    db = _ensure_repo(db)
    def _mark(value: str) -> str:
        v = (value or "").strip()
        if v.startswith("❗"):
            return "❗"
        if v.startswith("⭐"):
            return "⭐"
        return ""

    try:
        from app.models.structured_record import StructuredRecord
        rows = (await db.execute(
            select(StructuredRecord).where(
                StructuredRecord.agent_id == agent_id
            ).order_by(StructuredRecord.category, StructuredRecord.sub_key, StructuredRecord.field)
        )).scalars().all()
    except Exception as e:
        logger.error(f"记忆索引查询失败: {e}", exc_info=True)
        return ""

    if not rows:
        return (
            "## 记忆索引\n"
            "（空）用 manage_records 记录重要的事：people/ 人、topics/ 事、tasks/ 任务、journal/ 日志"
        )

    cats: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:
        cats.setdefault(r.category, {}).setdefault(r.sub_key, {})[r.field] = r.value
    lines = ["## 记忆索引"]
    for cat in sorted(cats):
        subs = cats[cat]
        if not subs or (len(subs) == 1 and "" in subs):
            fields = subs.get("", {})
            if fields:
                lines.append(f"{cat}/")
                for f in sorted(fields):
                    lines.append(f"  {_mark(fields[f])}{f}")
            continue
        lines.append(f"{cat}/")
        for sk in sorted(subs):
            if sk == "":
                continue
            fields = {f: v for f, v in subs[sk].items() if f}
            if fields:
                lines.append(f"  {sk}/")
                for f in sorted(fields):
                    lines.append(f"    {_mark(fields[f])}{f}")
            else:
                lines.append(f"  {sk}")
    lines.append("⭐重要 ❗硬约束；详情 manage_records get 按需取")
    return "\n".join(lines)
