"""外部通道的本地锚点账号 —— 每个通道入站时都要做的同一件事

为什么收成一处：官方 QQ、NapCat、以后的微信通道各自写一份"先在 users 里找个锚点、
再更新 external_identities"的逻辑，两份代码一旦漂移，同一个人在不同通道下就会被差别
对待（昵称补写、撞名后缀、群成员身份各写各的）。这里只回答一个问题：
这条外部身份对应本地 users.id 是哪一个。

为什么还要锚点账号（而不是直接用 external_identities.id）：消息、私信会话、群成员
目前还只能挂 users.id；等它们都改成引用外部身份之后，这个锚点就该消失
（取舍见 app/models/external.py）。

同一个人在两台机器人下各有一个 openid（QQ 按 appid 分配），于是各有各的锚点账号；
把它们并到一个账号上的两处就在本文件：ensure_channel_user（认亲之后别再分家）与
merge_channel_accounts（已经分家的并回来）。
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


async def channel_contacts(
    db: Any, *, kind: str, owner_scope: str, user_ids: Iterable[int]
) -> dict[int, str]:
    """这批本地账号里哪些在这条通道上有号：users.id → 通道侧标识（QQ 是 openid）

    出站要把 <@!平台id> 翻成通道侧的真 @（QQ 官方的 <@!openid>、NapCat 的 [CQ:at,qq=…]），
    靠的就是这张表：只有走过这条通道的人，在那条通道上才有 id 可 @。

     只查调用方点名的那几个 id：一次回复里 @ 的人通常 1-3 个，而通道身份会随配对人数一直涨，
    按通道全表拉出来再在 Python 里筛是拿 O(通道历史) 换 O(@数)。
    一次 where 就够：外部身份那一行直接记着锚点账号（user_id），而 (kind, owner_scope)
    限定"哪台机器人"，所以同一个 QQ 号接了两个实例时，只有走过**这个实例**的那条地址算数。
    """
    ids = sorted({int(uid) for uid in user_ids})
    if not ids:
        return {}
    from sqlalchemy import select

    from app.models.external import ExternalIdentity

    rows = (await db.execute(
        select(ExternalIdentity.user_id, ExternalIdentity.origin).where(
            ExternalIdentity.kind == kind,
            ExternalIdentity.owner_scope == owner_scope,
            ExternalIdentity.user_id.in_(ids),
        )
    )).all()
    return {int(uid): str(origin) for uid, origin in rows if uid is not None}


def _same_nickname(current: Any, desired: str) -> bool:
    """这个名字已经是"想要的昵称"（原样或带唯一化后缀 #N）→ 不必再改。

    为什么单列一条判据：改名原来只比 `row.username != nickname`，而昵称被更早的锚点
    占着时改名结果必然带 `#N`，于是**每条消息都判定"要改名"**；几台机器人同时改就撞唯一约束
    （2026-10-05 真机：IntegrityError 把整条入站消息带走，那台机器人因此没有被动凭据，
    AI 的回话被跳过）。同一个人在每台机器人下各有一个锚点账号，这种撞车是常态。
    """
    want = str(desired or "").strip()
    cur = str(current or "")
    return bool(want) and (cur == want or cur.startswith(want + "#"))


async def _unique_username(db: Any, desired: str, fallback: str, *, exclude_id: int = 0) -> str:
    """撞名就加 #2、#3…（username 有唯一约束，通道侧昵称重名很常见）

    exclude_id 给"改名"用：自己那行不算占用，否则永远找不到空位。先查后写在并发下仍可能
    撞车——所以调用方按"尽力而为"处理（见改名分支）。
    """
    from sqlalchemy import select

    from app.models.user import User

    base = (desired or "").strip()[:40] or fallback
    for suffix in range(1, 50):
        candidate = base if suffix == 1 else f"{base}#{suffix}"
        where = [User.username == candidate]
        if exclude_id:
            where.append(User.id != int(exclude_id))
        taken = (await db.execute(select(User.id).where(*where))).first()
        if taken is None:
            return candidate
    return base


async def ensure_channel_user(
    db: Any, *, kind: str, owner_scope: str, origin: str, display_name: str,
    origin_channel: str, join_group: int = 0, commit: bool = True, union_id: str = "",
) -> tuple[int, str] | None:
    """外部通道的本地锚点账号：没有就建（type='external'、一次性随机口令、
    email=origin@<kind>.bridge），拿到昵称就补写 username（撞名加 #2/#3…），
    并顺手更新 external_identities 那一行。返回 (user_id, 显示名)。

    kind 用插件 manifest 里的 channel.kind：它同时决定 email 锚点后缀与外部身份的归类，
    所以 QQ 的 openid 和 NapCat 的 QQ 号天然不会互相撞上。

    union_id 是通道侧的**跨应用统一标识**（QQ 的 union_openid，官方说明"可能为空"）：
    有它就按它认人——同一个开发者名下多个机器人看到的是同一个人，共用一个锚点账号；
    空则退回"这个地址自己一个账号"的老行为。

    为什么先问身份行、再决定建不建号：external_identities.user_id 是"这个地址 = 哪个本地
    账号"的唯一记录。认亲（插件的 _adopt_twin、本文件的 merge_channel_accounts）把它接到
    另一个账号上之后，再按 openid 找回自己那个锚点就等于把绑定覆盖回去——2026-10-18 真机：
    同一个 AN 在群里成了两个成员，AI 一人一半地跟他说话。所以绑定过就以绑定为准。

    commit=True 适合"建号就是这次操作全部"的调用方；投递链路（消息落库前还要 fanout /
    broadcast）必须传 False，把提交留给同一条链路的最后一步。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.external import ExternalIdentity
    from app.models.group import GroupMember
    from app.models.user import User
    from app.services.plugin import pairing
    from app.utils.auth import hash_password

    origin = str(origin or "").strip()
    if not origin:
        return None
    # 通道侧没给昵称时的占位名：前缀取通道类别（qq → QQ用户XXXXXX）。它是"这个人还没起名"
    # 的标记而不是真名 —— 所以用它建号时，之后拿到真昵称才会触发改名分支。
    label = (kind or "").split("-")[0].upper() or "通道"
    placeholder = f"{label}用户{origin[:6]}"
    raw_name = str(display_name or "").strip()
    nickname = raw_name[:40]
    union_id = str(union_id or "").strip()

    identity = await pairing.ensure(
        db, kind=kind, owner_scope=owner_scope, origin=origin,
        display_name=raw_name, commit=False,
    )
    anchor = anchor_email(kind, origin)

    row = (await db.get(User, int(identity.user_id))) if identity.user_id else None
    if row is None:
        # 没绑过：同一个 union 的兄弟 + 这个 openid 以前留下的锚点，谁最早谁是正主
        siblings = []
        if union_id:
            siblings = (await db.execute(
                select(ExternalIdentity).where(
                    ExternalIdentity.kind == kind,
                    ExternalIdentity.union_id == union_id,
                    ExternalIdentity.origin != origin,
                ).order_by(ExternalIdentity.id)
            )).scalars().all()
        candidates = {int(s.user_id) for s in siblings if s.user_id}
        old_anchor = (await db.execute(
            select(User).where(User.email == anchor)
        )).scalar_one_or_none()
        if old_anchor is not None:
            candidates.add(int(old_anchor.id))
        known = []
        if candidates:
            known = (await db.execute(
                select(User).where(User.id.in_(sorted(candidates)))
            )).scalars().all()
        if known:
            row = min(known, key=lambda u: int(u.id))
            merged = False
            for other in known:
                if int(other.id) != int(row.id):
                    # 认亲之前建的账号都并进来：留着空壳，群里就多一个同名的人
                    merged = await merge_channel_accounts(
                        db, from_id=int(other.id), to_id=int(row.id)
                    ) or merged
            if merged:
                logger.info(
                    f"外部通道账号合并：{kind} …{origin[-6:]} → {row.username}"
                    "（union / 老锚点认出是同一个人）"
                )
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
    elif nickname and not _same_nickname(row.username, nickname):
        # 第一次建号时可能还没拿到昵称，别让他永远停在占位名上：之后哪次拿到了就补上 ——
        # Copree 界面、AI 看到的说话人名、AI 回 @ 时用的名字，全都读这一列。
        # 已经是"这个名字（或它的 #N 写法）"就不动：重名规避是留给"同名的另一个人"的。
        old_name = str(row.username or "")
        try:
            # 改名是尽力而为：同一瞬间另一个人也改到这个候选名时保留原名就够，
            # 绝不能让整条消息因它失败——那台机器人连被动凭据都留不下（2026-10-05 真机）
            async with db.begin_nested():
                row.username = await _unique_username(
                    db, nickname, placeholder, exclude_id=int(row.id)
                )
                await db.flush()
            logger.info(f"外部通道账号改名：{row.username}（{kind} …{origin[-6:]}）")
        except IntegrityError:
            # 保存点回滚会把这一行的 ORM 状态过期；先把状态刷回库里的样子再退出——
            # 直接在过期对象上赋值，异步下 flush 会去懒加载而被判"意外的 IO"
            await db.refresh(row)
            logger.warning(f"外部通道账号改名撞车，保留原名 {old_name}（{kind} …{origin[-6:]}）")

    # 身份行是这个地址的唯一事实：指回最终定下的那个账号（认亲定了别人就照认亲的来）
    if not raw_name and not str(identity.display_name or ""):
        identity.display_name = str(row.username or "")
    if union_id and identity.union_id != union_id:
        identity.union_id = union_id
    if int(identity.user_id or 0) != int(row.id):
        identity.user_id = int(row.id)

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


async def merge_channel_accounts(db: Any, *, from_id: int, to_id: int) -> bool:
    """把两个通道锚点账号并成一个：后来的那个并进早先的那个

    什么时候需要：同一个人在几台机器人下各有一个 openid，认亲之后又说过话，于是两边各建了
    一个账号（2026-10-18 真机：群 #77 里一句话明明是同一个 AN 说的，AI 看见两个 AN，一人一半）。
    并的方向由调用方定成**早先那个**：名字、群成员、说过的话都在它身上，后来的只是空壳。

    只动 type='external' 的锚点账号：真人账号一律不并——谁是谁，只有本人能决定，
    不是一条启发式能拍板的。并完把空壳删掉；身份行、消息、群成员、静音、私信会话都跟着走。
    """
    from sqlalchemy import select, update

    from app.chat.dm import generate_dm_session_id
    from app.models.dm import DMMessage, DMSession
    from app.models.external import ExternalIdentity
    from app.models.group import GroupMember, MemberSilence
    from app.models.message import Message
    from app.models.user import User
    from app.models.user_preferences import UserDMPreference, UserGroupPreference

    src, dst = int(from_id), int(to_id)
    if src == dst:
        return False
    drop = await db.get(User, src)
    keep = await db.get(User, dst)
    if drop is None or keep is None:
        return False
    if str(drop.type or "") != "external":
        logger.warning(f"拒绝合并账号 #{src} → #{dst}：不是通道锚点（type={drop.type}）")
        return False

    # 地址的归属：一个地址只属于一个账号，这条记录就是唯一事实
    await db.execute(
        update(ExternalIdentity).where(ExternalIdentity.user_id == src).values(user_id=dst)
    )
    await db.execute(
        update(ExternalIdentity).where(ExternalIdentity.bound_user_id == src).values(bound_user_id=dst)
    )

    # 群消息：说过的话跟着人走，否则 AI 记住的是"另一个 AN"
    await db.execute(update(Message).where(Message.sender_id == src).values(sender_id=dst))
    await db.execute(update(DMMessage).where(DMMessage.sender_id == src).values(sender_id=dst))

    # 群成员：同一个群里已经有他 → 删掉重复的那条；没有就改名（唯一的成员记录只该有一份）
    for member in (await db.execute(
        select(GroupMember).where(GroupMember.member_id == src)
    )).scalars().all():
        twin = (await db.execute(select(GroupMember).where(
            GroupMember.group_id == member.group_id,
            GroupMember.member_type == member.member_type,
            GroupMember.member_id == dst,
        ))).scalar_one_or_none()
        if twin is not None:
            await db.delete(member)
        else:
            member.member_id = dst

    # 静音：同 (AI, 群) 只留一条
    for silence in (await db.execute(
        select(MemberSilence).where(MemberSilence.target_user_id == src)
    )).scalars().all():
        twin = (await db.execute(select(MemberSilence).where(
            MemberSilence.agent_id == silence.agent_id,
            MemberSilence.group_id == silence.group_id,
            MemberSilence.target_user_id == dst,
        ))).scalar_one_or_none()
        if twin is not None:
            await db.delete(silence)
        else:
            silence.target_user_id = dst

    # 偏好：主键是 (人, 群)/(人, 会话)，同一条已经有他就算了
    for pref, field in ((UserGroupPreference, "group_id"), (UserDMPreference, "session_id")):
        column = getattr(pref, field)
        for row in (await db.execute(
            select(pref).where(pref.user_id == src)
        )).scalars().all():
            twin = (await db.execute(select(pref).where(
                pref.user_id == dst, column == getattr(row, field),
            ))).scalar_one_or_none()
            if twin is not None:
                await db.delete(row)
            else:
                row.user_id = dst

    # 私信会话：会话 id 由双方 id 拼出来（min_max），并过来之后要按新的一方重算；
    # 已经有同一个对话对象的会话 → 消息搬过去、删掉这条
    for session in (await db.execute(select(DMSession).where(
        (DMSession.user1_id == src) | (DMSession.user2_id == src)
    ))).scalars().all():
        peer = int(session.user2_id) if int(session.user1_id) == src else int(session.user1_id)
        if peer == dst:
            continue
        target_id = generate_dm_session_id(dst, peer)
        existing = (await db.execute(
            select(DMSession).where(DMSession.session_id == target_id)
        )).scalar_one_or_none()
        if existing is None:
            # 会话 id 由双方 id 拼出来，改了人就得换 id；外键没有 ON UPDATE CASCADE，
            # 所以照着原来那条建一条新 id 的会话，消息搬过去之后再把旧的删掉
            keep_dnd = (session.user1_dnd_until if int(session.user1_id) == src
                        else session.user2_dnd_until)
            peer_dnd = (session.user2_dnd_until if int(session.user1_id) == src
                        else session.user1_dnd_until)
            left, right = sorted((dst, peer))
            db.add(DMSession(
                session_id=target_id, user1_id=left, user2_id=right,
                user1_dnd_until=keep_dnd if left == dst else peer_dnd,
                user2_dnd_until=keep_dnd if right == dst else peer_dnd,
                last_message_id=session.last_message_id,
                last_message_at=session.last_message_at,
                created_at=session.created_at,
            ))
            await db.flush()
        await db.execute(update(DMMessage).where(
            DMMessage.session_id == session.session_id
        ).values(session_id=target_id))
        await db.delete(session)
        if existing is not None:
            # 两条会话并成一条：最后一条消息可能换了，列表顺序靠它
            last = (await db.execute(
                select(DMMessage).where(DMMessage.session_id == target_id)
                .order_by(DMMessage.id.desc()).limit(1)
            )).scalar_one_or_none()
            if last is not None:
                existing.last_message_id = last.id
                existing.last_message_at = last.created_at

    await db.flush()
    await db.delete(drop)
    await db.flush()
    logger.info(f"通道账号合并：#{src}（{drop.username}）并进 #{dst}（{keep.username}）")
    return True
