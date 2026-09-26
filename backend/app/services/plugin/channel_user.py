"""外部通道的本地锚点账号 —— 每个通道入站时都要做的同一件事

为什么收成一处：官方 QQ、NapCat、以后的微信通道各自写一份"先在 users 里找个锚点、
再更新 external_identities"的逻辑，两份代码一旦漂移，同一个人在不同通道下就会被差别
对待（昵称补写、撞名后缀、群成员身份各写各的）。这里只回答一个问题：
这条外部身份对应本地 users.id 是哪一个。

为什么还要锚点账号（而不是直接用 external_identities.id）：消息、私信会话、群成员
目前还只能挂 users.id；等它们都改成引用外部身份之后，这个锚点就该消失
（取舍见 app/models/external.py）。
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _anchor_suffix(kind: str) -> str:
    return f"@{kind}.bridge"


def anchor_email(kind: str, origin: str) -> str:
    """通道身份的锚点邮箱（users.email）：建号与反查都只认这一处拼法"""
    return f"{origin}{_anchor_suffix(kind)}"


def origin_from_anchor(kind: str, email: str) -> str | None:
    """锚点邮箱反解回通道侧标识 —— 正写与反解必须同一份拼法，所以摆在一起

    认不出（不是这条通道的锚点）返回 None，而不是猜一个出来。
    """
    suffix = _anchor_suffix(kind)
    text = str(email or "")
    return text[: -len(suffix)] if text.endswith(suffix) else None


async def channel_contacts(
    db: Any, *, kind: str, owner_scope: str, user_ids: Iterable[int]
) -> dict[int, str]:
    """这批本地账号里哪些在这条通道上有号：users.id → 通道侧标识（QQ 是 openid）

    出站要把 <@!平台id> 翻成通道侧的真 @（QQ 官方的 <@!openid>、NapCat 的 [CQ:at,qq=…]），
    靠的就是这张表：只有走过这条通道的人，在那条通道上才有 id 可 @。

    只查调用方点名的那几个 id：一次回复里 @ 的人通常 1-3 个，而通道身份会随配对人数一直涨，
    按通道全表拉出来再在 Python 里筛是拿 O(通道历史) 换 O(@数)。
    两步都只碰这几个 id：先按 id 认人（锚点由 origin_from_anchor 反解，不在这里重写拼法），
    再拿解出来的标识确认它确实属于 (kind, owner_scope) —— 同一个 QQ 号接了两个实例时，
    只有走过**这个实例**的那条身份才算数。
    """
    ids = sorted({int(uid) for uid in user_ids})
    if not ids:
        return {}
    from sqlalchemy import select

    from app.models.external import ExternalIdentity
    from app.models.user import User

    rows = (await db.execute(
        select(User.id, User.email).where(User.id.in_(ids))
    )).all()
    decoded = {
        int(uid): origin for uid, email in rows
        if (origin := origin_from_anchor(kind, str(email)))
    }
    if not decoded:
        return {}
    known = set((await db.execute(
        select(ExternalIdentity.origin).where(
            ExternalIdentity.kind == kind,
            ExternalIdentity.owner_scope == owner_scope,
            ExternalIdentity.origin.in_(list(decoded.values())),
        )
    )).scalars().all())
    return {uid: origin for uid, origin in decoded.items() if origin in known}


async def _unique_username(db: Any, desired: str, fallback: str) -> str:
    """撞名就加 #2、#3…（username 有唯一约束，通道侧昵称重名很常见）"""
    from sqlalchemy import select

    from app.models.user import User

    base = (desired or "").strip()[:40] or fallback
    for suffix in range(1, 50):
        candidate = base if suffix == 1 else f"{base}#{suffix}"
        taken = (await db.execute(select(User.id).where(User.username == candidate))).first()
        if taken is None:
            return candidate
    return base


async def ensure_channel_user(
    db: Any, *, kind: str, owner_scope: str, origin: str, display_name: str,
    origin_channel: str, join_group: int = 0, commit: bool = True,
) -> tuple[int, str] | None:
    """外部通道的本地锚点账号：没有就建（type='external'、一次性随机口令、
    email=origin@<kind>.bridge），拿到昵称就补写 username（撞名加 #2/#3…），
    并顺手更新 external_identities 那一行。返回 (user_id, 显示名)。

    kind 用插件 manifest 里的 channel.kind：它同时决定 email 锚点后缀与外部身份的归类，
    所以 QQ 的 openid 和 NapCat 的 QQ 号天然不会互相撞上。

    commit=True 适合"建号就是这次操作全部"的调用方；投递链路（消息落库前还要 fanout /
    broadcast）必须传 False，把提交留给同一条链路的最后一步。
    """
    from sqlalchemy import select

    from app.models.group import GroupMember
    from app.models.user import User
    from app.utils.auth import hash_password

    origin = str(origin or "").strip()
    if not origin:
        return None
    # 通道侧没给昵称时的占位名：前缀取通道类别（qq → QQ用户XXXXXX）。它是"这个人还没起名"
    # 的标记而不是真名 —— 所以用它建号时，之后拿到真昵称才会触发改名分支。
    # 取类别第一段（qq-napcat → QQ用户XXXXXX）：占位名是"这个人还没起名"的标记，不是真名；
    # 直接用整个 kind 会拼出「QQ-NAPCAT用户…」这种给人看的垃圾
    label = (kind or "").split("-")[0].upper() or "通道"
    placeholder = f"{label}用户{origin[:6]}"
    raw_name = str(display_name or "").strip()
    nickname = raw_name[:40]

    anchor = anchor_email(kind, origin)
    row = (await db.execute(select(User).where(User.email == anchor))).scalar_one_or_none()
    if row is None:
        row = User(
            username=await _unique_username(db, nickname or placeholder, placeholder),
            password_hash=hash_password(secrets.token_urlsafe(32)),
            email=anchor,
            email_verified=False,
            # external 不是本实例的真人：这样他才不会被搜人、注册引导、用户统计当成真人
            # （users.type 的取值见 models/external.py）
            type="external",
            # 标记来源通道：这类账号没有 Key 也没有额度，
            # 平台按"外部通道来的会话一律记在 AI 主人头上"给他记账（见 ai/executor.py）
            origin_channel=origin_channel,
        )
        db.add(row)
        await db.flush()
        logger.info(f"外部通道账号已建号：{row.username}（{kind} …{origin[-6:]}）")
    elif nickname and row.username != nickname:
        # 第一次建号时可能还没拿到昵称，别让他永远停在占位名上：之后哪次拿到了就补上 ——
        # Copree 界面、AI 看到的说话人名、AI 回 @ 时用的名字，全都读这一列。
        row.username = await _unique_username(db, nickname, placeholder)
        await db.flush()
        logger.info(f"外部通道账号改名：{row.username}（{kind} …{origin[-6:]}）")

    # 外部身份那一行：通道侧昵称优先，拿不到就用本地显示名
    from app.services.plugin import pairing

    await pairing.ensure(
        db, kind=kind, owner_scope=owner_scope, origin=origin,
        display_name=raw_name or str(row.username or ""),
        commit=False,
    )

    if join_group:
        member = (await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == join_group,
                GroupMember.member_type == "human",
                GroupMember.member_id == row.id,
            )
        )).scalar_one_or_none()
        if member is None:
            db.add(GroupMember(
                group_id=join_group, member_type="human", member_id=row.id, role="member",
            ))
            await db.flush()

    if commit:
        await db.commit()
    # 名字一并带回去：出站要按它把"回复对象"那个 @ 从正文开头摘掉
    return int(row.id), str(row.username or "")
