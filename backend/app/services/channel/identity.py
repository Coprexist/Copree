"""通道身份目录 —— 平台给所有通道的一张表（external_identities）

一行 = 某个通道上的某个人：从哪来（kind + owner_scope + origin）、是谁（display_name /
avatar_url / union_id）、接本实例哪个账号（user_id）、放不放行（status + code）。

为什么这张表归平台、不归任何一个插件：
- 读它的是平台自己：通道卡片要列出待批 / 已放行 / 拉黑名单，批准、拉黑、解除配对都在
  平台上做；插件卸载之后这张表也还在，重装时同一个人接回原来那个账号，不会在群里变
  成两个"同一个人"。
- 每行的 user_id 指向 users 里的锚点账号（见 anchor.py），消息、群成员、私信会话都挂
  users.id：表跟着插件走就是把"谁是谁"切成两半，卸载清数据之后消息仍指向那个锚点。
- kind 是分区键，不是所有权：新通道只是多一个 kind（manifest 的 channel.kind），联邦
  和以后任何来源都落同一张表，平台也只有一套放行规则。

插件侧只经这里读写，不直接查这张表（表结构、放行状态机、锚点建号与合并都属于平台契约）。
对应 OpenClaw 的 DM pairing（docs/channels/pairing.md）：默认不处理陌生人的消息，只回
一个短码，批准权在主人手里；区别只是「批准」这一步搬到了 Copree 界面上。

键是 (kind, owner_scope, origin)：
- kind        来源类别，由插件自己在 manifest 里声明（plugin.json 的 channel.kind，如 "qq"）
- owner_scope 该类别下的归属实例（QQ 是 agent-<agentId>，联邦是对端公网 ID）
- origin      通道侧的稳定标识（QQ 是 openid，联邦是远端实体 ID）

三条不变量：
- 一条 (kind, owner_scope, origin) 只有一行，重复私聊复用同一个码（用户看的是同一个码，不会刷屏）
- 状态只有三种：pending（领了码）/ approved（放行）/ blocked（拉黑）
- 配对是 per 实例的：同一个人的 openid 在另一个机器人（另一个实例）下是另一个人
"""
from __future__ import annotations

import logging
import secrets
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.external import APPROVED, BLOCKED, PENDING, ExternalIdentity
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)

# 去掉 0/O/1/I 这些看着像的字符：这个码要用户从 QQ 里抄回来，认错一个字符就是白折腾
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _conds(kind: str, owner_scope: str, origin: str):
    return (
        ExternalIdentity.kind == kind,
        ExternalIdentity.owner_scope == (owner_scope or ""),
        ExternalIdentity.origin == origin,
    )


async def ensure(
    db: AsyncSession, *, kind: str, owner_scope: str, origin: str,
    display_name: str = "", avatar_url: str | None = None, commit: bool = True,
) -> ExternalIdentity:
    """拿到这个外部身份的那一行，没有就建（默认 pending，先不置码）。

    每条消息都会走一次，所以这里顺手更新展示名与 last_seen_at —— 通道侧改名换头像
    不用额外同步任务，下一次他说话就自动对上了。
    """
    row = (await db.execute(
        select(ExternalIdentity).where(*_conds(kind, owner_scope, origin))
    )).scalar_one_or_none()
    if row is None:
        row = ExternalIdentity(kind=kind, owner_scope=owner_scope or "", origin=origin, status=PENDING)
        db.add(row)
    if display_name and display_name[:120] != (row.display_name or ""):
        row.display_name = display_name[:120]
    if avatar_url:
        row.avatar_url = avatar_url
    row.last_seen_at = utc_now()
    if commit:
        await db.commit()
    else:
        await db.flush()
    return row


async def get(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> ExternalIdentity | None:
    return (await db.execute(
        select(ExternalIdentity).where(*_conds(kind, owner_scope, origin))
    )).scalar_one_or_none()


async def status_of(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> str | None:
    """没记录返回 None（= 陌生人）；这在每条私聊上都会调一次，所以只查状态列"""
    return (await db.execute(
        select(ExternalIdentity.status).where(*_conds(kind, owner_scope, origin))
    )).scalar_one_or_none()


async def has(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> bool:
    """这个地址以前出现过没有

    只取 id 不取整行：调用方要的是"第一次见"这一个判断（第一次才尝试同名认亲），
    老地址每次都重新认亲会把已经稳定的人再算一遍。
    """
    return (await db.execute(
        select(ExternalIdentity.id).where(*_conds(kind, owner_scope, origin)).limit(1)
    )).first() is not None


async def user_id_of(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> int:
    """这个地址接的本地账号 id；没这一行、或还没接上，都是 0"""
    value = (await db.execute(
        select(ExternalIdentity.user_id).where(*_conds(kind, owner_scope, origin))
    )).scalar_one_or_none()
    return int(value or 0)


async def union_id_of(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> str:
    """这个地址记下的跨应用统一标识；没记过就是空串"""
    value = (await db.execute(
        select(ExternalIdentity.union_id).where(*_conds(kind, owner_scope, origin))
    )).scalar_one_or_none()
    return str(value or "")


async def contacts(
    db: AsyncSession, *, kind: str, owner_scope: str, user_ids: Iterable[int]
) -> dict[int, str]:
    """这批本地账号里哪些在这条通道上有号：users.id → 通道侧标识（QQ 是 openid）

    出站要把 <@!平台id> 翻成通道侧的真 @（QQ 官方的 <@!openid>、NapCat 的 [CQ:at,qq=…]），
    靠的就是这张表：只有走过这条通道的人，在那条通道上才有 id 可 @。

    只查调用方点名的那几个 id：一次回复里 @ 的人通常 1-3 个，而通道身份会随配对人数一直涨，
    按通道全表拉出来再在 Python 里筛是拿 O(通道历史) 换 O(@数)。
    一次 where 就够：身份行直接记着锚点账号（user_id），(kind, owner_scope) 限定"哪台
    机器人"，所以同一个 QQ 号接了两个实例时，只有走过**这个实例**的那条地址算数。
    """
    ids = sorted({int(uid) for uid in user_ids if uid})
    if not ids:
        return {}
    rows = (await db.execute(
        select(ExternalIdentity.user_id, ExternalIdentity.origin).where(
            ExternalIdentity.kind == kind,
            ExternalIdentity.owner_scope == owner_scope,
            ExternalIdentity.user_id.in_(ids),
        )
    )).all()
    return {int(uid): str(origin) for uid, origin in rows if uid is not None}


async def upsert_pending(
    db: AsyncSession, *, kind: str, owner_scope: str, origin: str, display_name: str = ""
) -> ExternalIdentity:
    """陌生人第一次私聊：没有记录就发一个码；已有待批记录就复用（省得用户收到一串不同的码）"""
    row = await get(db, kind=kind, owner_scope=owner_scope, origin=origin)
    if row is None:
        row = ExternalIdentity(
            kind=kind, owner_scope=owner_scope or "", origin=origin,
            display_name=display_name[:120], code=new_code(), status=PENDING,
        )
        db.add(row)
    else:
        if display_name and display_name[:120] != (row.display_name or ""):
            row.display_name = display_name[:120]
        if row.status == APPROVED:
            await db.commit()
            return row
        if not row.code:
            row.code = new_code()
        if row.status != PENDING:
            # blocked 的人又来了：不改状态（拉黑是主人的决定），但也别把码发出去
            await db.commit()
            return row
    await db.commit()
    return row


async def list_rows(
    db: AsyncSession, *, kind: str, owner_scope: str, status: str | None = None
) -> list[ExternalIdentity]:
    stmt = select(ExternalIdentity).where(
        ExternalIdentity.kind == kind, ExternalIdentity.owner_scope == (owner_scope or "")
    )
    if status:
        stmt = stmt.where(ExternalIdentity.status == status)
    return list((await db.execute(stmt.order_by(ExternalIdentity.id.desc()))).scalars().all())


def pairing_reply(code: str, card: str) -> str:
    """陌生人私聊收到的配对码回复（两条通道共用一份：改一次两条都改）。

    card = 用户要去填码的那张卡片名（QQ 通道 / QQ 通道（NapCat））。
    末尾两句是为了别让人以为在跟"某个人的 AI"说话，
    以及给出开源地址（陌生人看到机器人会想知道这是什么）。
    """
    return (
        f"配对码：{code}\n"
        f"把它填到 Copree 里此 AI 的「{card}」卡片上，我才会回话。\n"
        f"如果你不是我的创建者，请联系我的创建者。\n"
        f"Copree开源地址：github.com/Coprexist/Copree"
    )


async def approve(
    db: AsyncSession, *, kind: str, owner_scope: str, pairing_id: int | None = None,
    origin: str | None = None, code: str | None = None
) -> ExternalIdentity:
    """批准一条待配对：按 id、按 origin、或按用户抄回来的配对码都行

    三种入口都收敛到这里，是因为「批准」要保证的只有一件事：pending → approved 且记时间。
    """
    stmt = select(ExternalIdentity).where(ExternalIdentity.kind == kind,
                                          ExternalIdentity.owner_scope == (owner_scope or ""))
    if pairing_id is not None:
        stmt = stmt.where(ExternalIdentity.id == pairing_id)
    elif origin:
        stmt = stmt.where(ExternalIdentity.origin == origin)
    elif code:
        stmt = stmt.where(ExternalIdentity.code == code.strip().upper())
    else:
        raise ValueError("缺少待配对标识")
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ValueError("找不到这条待配对申请")
    if row.status == BLOCKED:
        raise ValueError("这条申请已被拉黑，先解除拉黑再批准")
    row.status = APPROVED
    row.approved_at = utc_now()
    await db.commit()
    logger.info("通道配对已批准：%s/%s → %s（%s）", kind, owner_scope, row.display_name or "?", row.origin[-6:])
    return row


async def set_status(
    db: AsyncSession, *, kind: str, owner_scope: str, origin: str, status: str
) -> ExternalIdentity | None:
    if status not in (PENDING, APPROVED, BLOCKED):
        raise ValueError("未知状态：" + status)
    row = await get(db, kind=kind, owner_scope=owner_scope, origin=origin)
    if row is None:
        return None
    row.status = status
    if status == APPROVED:
        row.approved_at = utc_now()
    await db.commit()
    return row


async def forget(db: AsyncSession, *, kind: str, owner_scope: str, origin: str) -> bool:
    """解除配对：删掉记录 → 对方重新变回陌生人（下次私聊重新领码）"""
    result = await db.execute(delete(ExternalIdentity).where(*_conds(kind, owner_scope, origin)))
    await db.commit()
    return bool(result.rowcount)
