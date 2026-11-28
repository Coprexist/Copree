"""我给群 / 人起的备注：读写只走这一处

一个对象至多一条（唯一键在库上）。空串 = 删除；写入前校验目标还在，
免得备注指向一个已经删掉的群或账号——那时列表也不会显示它，但脏行会一直躺着。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.user import User
from app.models.user_remark import REMARK_MAX_CHARS, UserRemark

logger = logging.getLogger(__name__)

TARGET_GROUP = "group"
TARGET_USER = "user"
TARGET_TYPES = (TARGET_GROUP, TARGET_USER)


def clean_remark(raw) -> str:
    """备注规范：单行、去首尾空白、截到列宽——渲染层与列表都按这个长度排。"""
    return " ".join(str(raw or "").split())[:REMARK_MAX_CHARS]


def _check_target_type(target_type: str) -> str:
    if target_type not in TARGET_TYPES:
        raise ValueError(f"不支持的备注目标：{target_type}")
    return target_type


async def _ensure_target(db: AsyncSession, target_type: str, target_id: int) -> None:
    if target_type == TARGET_GROUP:
        if await db.get(Group, target_id) is None:
            raise ValueError("群不存在")
    elif await db.get(User, target_id) is None:
        raise ValueError("用户不存在")


async def remark_hit_ids(executor, owner_id: int, query: str, target_type: str) -> list[int]:
    """备注里含关键词的目标 id——搜索用，让用户按自己起的称呼也能搜到人/群。

    只在这个人的备注里匹配：备注本身是私有称呼，别人搜不到，也不会因此看到它。
    executor 只需要一个 execute()（搜索仓库与 db session 都满足）。
    """
    target_type = _check_target_type(target_type)
    col = UserRemark.group_id if target_type == TARGET_GROUP else UserRemark.target_user_id
    rows = await executor.execute(
        select(col).where(
            UserRemark.owner_id == int(owner_id),
            col.isnot(None),
            UserRemark.remark.ilike(f"%{query}%"),
        )
    )
    return [int(value) for (value,) in rows.all()]


def _where(owner_id: int, target_type: str, target_id: int) -> list:
    conds = [UserRemark.owner_id == int(owner_id)]
    conds.append(
        UserRemark.group_id == target_id if target_type == TARGET_GROUP
        else UserRemark.target_user_id == target_id
    )
    return conds


async def list_remarks(db: AsyncSession, owner_id: int) -> list[dict]:
    """我设的全部备注：[{target_type, target_id, remark}]"""
    rows = (await db.execute(
        select(UserRemark).where(UserRemark.owner_id == int(owner_id))
    )).scalars().all()
    return [
        {
            "target_type": TARGET_GROUP if row.group_id is not None else TARGET_USER,
            "target_id": int(row.group_id if row.group_id is not None else row.target_user_id),
            "remark": row.remark,
        }
        for row in rows
    ]


async def remarks_map(db: AsyncSession, owner_id: int) -> dict[tuple[str, int], str]:
    """会话列表用：(target_type, target_id) → 备注（一次查完，别在循环里查）"""
    return {(r["target_type"], r["target_id"]): r["remark"] for r in await list_remarks(db, owner_id)}


async def set_remark(
    db: AsyncSession, owner_id: int, target_type: str, target_id: int, remark,
) -> dict | None:
    """写一条备注；remark 为空 = 删除该条。返回落库后的行，删除时返回 None"""
    target_type = _check_target_type(target_type)
    target_id = int(target_id)
    body = clean_remark(remark)
    row = (await db.execute(
        select(UserRemark).where(*_where(owner_id, target_type, target_id))
    )).scalar_one_or_none()

    if not body:
        if row is not None:
            await db.delete(row)
            await db.commit()
        return None

    if target_type == TARGET_USER and target_id == int(owner_id):
        raise ValueError("不能给自己设备注")
    await _ensure_target(db, target_type, target_id)

    if row is None:
        row = UserRemark(
            owner_id=int(owner_id),
            group_id=target_id if target_type == TARGET_GROUP else None,
            target_user_id=target_id if target_type == TARGET_USER else None,
            remark=body,
        )
        db.add(row)
    else:
        row.remark = body
    await db.commit()
    return {"target_type": target_type, "target_id": target_id, "remark": body}
