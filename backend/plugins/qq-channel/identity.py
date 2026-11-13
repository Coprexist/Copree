"""通道身份：这条 openid 对应 Copree 的哪个人

入站时把 QQ 的 @ 点名物化成平台令牌、把新出现的人建成锚点账号、认亲（union_openid 与
同名成员）之后把影子并回本体。本文件只做"人"这一层；消息怎么落库在 inbound.py，
怎么发回去在 outbound.py。
"""
from __future__ import annotations

import time
from typing import Any

from .common import (
    MIRROR_SCAN,
    MIRROR_WINDOW_SECONDS,
    UNION_CACHE_MAX,
    UNION_TTL_SECONDS,
    logger,
)
from .format import (
    MIRROR_MIN_CHARS,
    _MENTION_TOKEN_RE,
    _name_base,
    _same_channel_text,
    format_channel_origin,
    parse_channel_origin,
)


class IdentityMixin:
    async def _materialize_mentions(self, d: dict, content: str, *, join_group: int = 0) -> str:
        """把这条消息 @ 到的人先建成 Copree 群成员，并在正文里补上他们的 id 令牌。

        为什么：正文里的 @名字 要靠入口归一变成 <@!平台id>，前提是"这人已经是群成员"；
        而被 @ 的人可能还没说过话（还没账号）。QQ 有时还会把 @成员的提及从正文里摘掉
        （只在 mentions 里给）——那就在这里直接补 <@!id>，AI 和界面都认得。
        只有全量模式（官方给 mentions）才做得了；@模式载荷里没有这个字段。

        QQ 写在正文里的原始提及是 `<@openid>`（**没有那个 `!`**，那是它自己的 id）：谁都不认，
        一律按 mentions 里的 origin 精确摘掉。**只有"我自己"那一条只摘不补**——点名由唤醒令牌负责，
        不摘就存成 `<@!40> <@5872…>` 这种"两个令牌、其中一个还是乱码"（2026-09-26 真机）。
        别的**机器人**：认得出是本群某个 AI 的真身（见 _mentioned_bot_member）就补它的真令牌——
        同一个 QQ 群被两台机器人接着时，"谁被 @ 了"AI 和界面都该看得见（2026-10-05：@ 另一个
        机器人的那条在 Copree 侧只剩"你好"）；认不出照旧当外部人建锚点账号，不否决。
        顺带：两边都补令牌，认亲（_same_channel_text 摘令牌后比）也才认得出来是同一条。
        """
        mentions = [m for m in (d.get("mentions") or []) if isinstance(m, dict)]
        if not mentions or not self._copree_group_id:
            return content

        from sqlalchemy import select

        from app.database import async_session
        from app.models.user import User
        from app.services.channel import identity
        from app.services.channel.anchor import ensure_channel_user
        from app.utils.text import mention_token

        landing = int(join_group or self._copree_group_id or 0)
        tokens: list[str] = []

        def strip_origin(origin: str) -> None:
            """认不出 / 不补令牌的提及：原始标记必须摘掉，不然 AI 看到一串 openid。"""
            nonlocal content
            for marker in (f"<@!{origin}>", f"<@{origin}>"):
                content = content.replace(marker + " ", "").replace(marker, "")

        def place(token: str, origin: str) -> None:
            """正文里带着原始提及就**原地**换掉，没有才攒到句首。

            原地换保住 @ 的位置（"欢迎<@!206> 喵~" 而不是 "<@!206> 欢迎喵~"）；
            QQ 有时会把提及从正文里摘掉、只在 mentions 里给，那种只能补在句首。
            """
            nonlocal content
            for marker in (f"<@!{origin}>", f"<@{origin}>"):
                if marker in content:
                    content = content.replace(marker, token)
                    return
            tokens.append(token)

        async with async_session() as db:
            for user in mentions:
                origin = str(user.get("id") or user.get("member_openid") or "")
                if not origin:
                    continue
                if user.get("is_you"):
                    # 我自己：事件直接告诉我"这条点到了我"，用自己的账号 id 补令牌。
                    # 从前这里直接跳过（以为唤醒令牌会补），全量模式里没有那一步，
                    # 跳过就等于丢掉"点到我了"——合并时正文被别台渲染成影子 id，
                    # 被 @ 的这台判成没点到自己，于是沉默（2026-10-31 真机）。
                    place(mention_token(self._target_user_id), origin)
                    continue
                if user.get("bot"):
                    # 另一个机器人：认得出是本群某个 AI 的真身，就补**它的真账号**。
                    # 为什么不照旧直接建外部锚点：QQ 按 appid 发 openid，那样认不出它其实
                    # 是自己人，造出来的是"名字#2"这种影子账号，AI 看到的 @ 就指到假 id 上
                    # （2026-10-05 真机：A 侧补出的那个 id，被 @ 的那台机器人自己根本不认）。
                    if user.get("is_you") is None:
                        strip_origin(origin)   # 没标是不是我：没把握，只摘不补（真机两条标记都给）
                        continue
                    if user.get("is_you") is False:
                        uid = await self._mentioned_bot_member(db, str(user.get("username") or ""))
                        if uid:
                            # 认回真身的同时把旧锚点并掉：绑定一旦指向影子，以后 @ 它
                            # 就一直解析到影子身上（2026-10-31 真机），这里让它收敛回本体
                            uid = await self._adopt_bot_identity(db, origin, uid)
                            place(mention_token(uid), origin)
                            continue
                    # 认不出不是否决：它可能还没被列成 AI 成员（真实 QQ 小号接进来的机器人），
                    # 也可能就是别家的机器人——照外部人建锚点账号，@ 了谁照样看得见。
                    # 本群自己的机器人不会走到这里：点到我时 is_you 给的是我的真账号，
                    # 点到别台时按 agent 名（= 它在 QQ 里的昵称）认得出它的真账号。
                # 这个地址是不是第一次出现：只有第一次才尝试同名认亲，老地址别反复并
                known_origin = await identity.has(
                    db, kind=self.channel_kind, owner_scope=self.instance, origin=origin)
                ensured = await ensure_channel_user(
                    db, kind=self.channel_kind, owner_scope=self.instance, origin=origin,
                    display_name=str(user.get("username") or ""),
                    origin_channel=self.channel_kind[:16], join_group=landing,
                )
                if ensured is None:
                    strip_origin(origin)
                    continue
                uid, name = ensured
                if not known_origin:
                    adopted = await self._adopt_same_name_member(
                        db, uid=uid, name=name, group_id=landing)
                    if adopted != uid:
                        uid = adopted
                        name = str(await db.scalar(
                            select(User.username).where(User.id == int(uid))
                        ) or name)
                # 正文里本来就写着 @名字 且没有原始标记：交给入口归一，不再补令牌
                has_marker = any(m in content for m in (f"<@!{origin}>", f"<@{origin}>"))
                if name and not has_marker and f"@{name}" in content:
                    continue
                place(mention_token(uid), origin)
            await db.commit()
        return ("".join(tokens) + " " + content) if tokens else content

    async def _adopt_same_name_member(self, db, *, uid: int, name: str, group_id: int) -> int:
        """这个地址第一次出现时，若能唯一对上一个同名外部账号，就把新锚点并过去。

        QQ 的 openid 按应用发：同一个人在三台机器人下是三个地址，@ 他一次就多一个「名字#2」。
        这里做窄口径认亲——只在新建账号（还没说过话）、落地群里同名外部账号**恰好一个**、
        来源通道也相同时合并。同名不同人确实存在，宁可多建一个，不按名字批量并。
        """
        from sqlalchemy import func, select

        from app.models.group import GroupMember
        from app.models.message import Message
        from app.models.user import User
        from app.services.channel.anchor import merge_channel_accounts

        base = _name_base(name)
        if not base or not group_id:
            return uid
        said = await db.scalar(
            select(func.count()).select_from(Message).where(Message.sender_id == int(uid))
        )
        if int(said or 0) > 0:
            return uid          # 说过话的账号不并：里面可能是两个人的记录混在一起
        rows = (await db.execute(
            select(User).join(
                GroupMember,
                (GroupMember.member_id == User.id) & (GroupMember.member_type == "human"),
            ).where(
                GroupMember.group_id == int(group_id),
                User.id != int(uid),
                User.type == "external",
                User.origin_channel == self.channel_kind[:16],
            )
        )).scalars().all()
        candidates = [row for row in rows if _name_base(row.username) == base]
        if len(candidates) != 1:
            return uid
        target = candidates[0]
        if not await merge_channel_accounts(db, from_id=int(uid), to_id=int(target.id)):
            return uid
        logger.info(
            f"QQ 通道[{self.instance}] 同名锚点认亲：#{uid} → #{target.id}（{target.username}）"
        )
        return int(target.id)

    async def _adopt_bot_identity(self, db: Any, origin: str, real_uid: int) -> int:
        """把"这台机器人在本实例下的旧锚点"并进它的真身账号（自愈，幂等）。

        影子账号是认不出"这是自己人"的年代留下的：一旦写下 (实例, openid) → external 账号，
        以后 @ 它就一直解析到影子身上（2026-10-31：鲸鱼娘被 @ 到 #177，整轮沉默）。
        认回真身的这一刻顺手合并——身份绑定改指本体、它说过的话跟着走、空壳删掉。
        只并在源头是 external 的账号上：真人账号谁是谁只有本人能决定。
        """
        from app.models.user import User
        from app.services.channel import identity
        from app.services.channel.anchor import merge_channel_accounts

        if not origin or not real_uid:
            return int(real_uid)
        row = await identity.get(
            db, kind=self.channel_kind, owner_scope=self.instance, origin=str(origin)
        )
        if row is None or int(row.user_id or 0) in (0, int(real_uid)):
            return int(real_uid)
        shadow = await db.get(User, int(row.user_id))
        if shadow is None or str(shadow.type or "") != "external":
            return int(real_uid)
        merged = await merge_channel_accounts(db, from_id=int(shadow.id), to_id=int(real_uid))
        if merged:
            logger.info(
                f"通道身份自愈[{self.instance}]：影子账号 #{shadow.id}「{shadow.username}」"
                f"并入 #{int(real_uid)}（openid …{str(origin)[-6:]}）"
            )
        return int(real_uid)

    async def _mentioned_bot_member(self, db: Any, name: str) -> int:
        """被 @ 的机器人是不是本群某个 AI 的真身 → 它的 Copree 账号 id（不是就是 0）

        两个面合起来认，不拿名字单点：
        1. 昵称与成员**一字不差**，名单走入口那一套（resolve_member_ids）——
           和正文里 @名字 的归一认的是同一份，不会两处漂移；
        2. 同名的这个成员本身是 AI 账号，挡掉同名的真人（重名很常见，只看名字会把
           @ 喊到人身上）。

        认不出来不等于它不存在：可能还没被列成 AI 成员（真实 QQ 小号接进来的机器人、
        别家的机器人），调用方回落到"当外部人建锚点账号"那条老路，不在这里否决。
        """
        name = str(name or "").strip()
        gid = int(self._copree_group_id or 0)
        if not name or not gid:
            return 0
        from sqlalchemy import select

        from app.chat.gm import resolve_member_ids
        from app.models.agent import Agent as AgentModel
        from app.models.group import GroupMember

        uid = (await resolve_member_ids(db, gid)).get(name)
        if uid:
            is_ai = (await db.execute(
                select(GroupMember.member_id).where(
                    GroupMember.group_id == gid,
                    GroupMember.member_type == "ai",
                    GroupMember.member_id == int(uid),
                )
            )).first()
            if is_ai:
                return int(uid)
        # 机器人那个昵称是**agent 名**，不是 users.username：两者不同时（2026-10-31 真机：
        # agent「鲸鱼娘 Deepseek」/ 账号「鲸鱼娘 DSH」）按成员名认不出来，@ 就被当成外部人
        # 造了个「鲸鱼娘 Deepseek#2」的影子账号，被 @ 的那台自己不认识这个 id，就此沉默。
        # 所以再按 agent 名认一遍，仍然要求这个成员本身是群的 AI 成员。
        row = (await db.execute(
            select(GroupMember.member_id)
            .join(AgentModel, AgentModel.user_id == GroupMember.member_id)
            .where(
                GroupMember.group_id == gid,
                GroupMember.member_type == "ai",
                AgentModel.name == name,
            )
            .limit(1)
        )).first()
        return int(row[0]) if row else 0

    def _with_mention_prefix(self, content: str) -> str:
        """两边语义对齐：QQ 里 @机器人 = 在 Copree 里 @这个 AI（群自己的唤醒规则仍然生效）。

        用 id 令牌点名而不是名字：名字会改、会重名，绑定出来的 user_id 才是身份；
        万一没绑到 user_id 才退回名字（旧写法仍被识别）。
        """
        from app.utils.text import mention_token

        prefix = mention_token(self._target_user_id) if self._target_user_id else f"@{self._target_agent}"
        if prefix in str(content or ""):
            return str(content)     # 全量事件里已经按 is_you 补过，别补第二个
        return f"{prefix} {content}"

    async def _fill_union(self, qq_group: str, author: dict) -> None:
        """按需把 union_openid 补进 author：事件里为空就走"获取群成员信息"，结果缓存一天。

        为什么不能只信事件：官方只在成员信息接口里承诺返回稳定值，事件载荷里那个字段
        本来就写着"可能为空"（真机也是空的）。成员接口只有 30 QPM，所以补到就缓存，
        **没补到也缓存**——不然每来一条消息都要撞一次同样的失败。
        """
        if author.get("union_openid") or self._member_info_denied:
            return
        member = self._author_openid(author)
        client = self._client
        if not member or client is None:
            return
        key = f"{qq_group}:{member}"
        cached = self._union_cache.get(key)
        if cached is not None and time.time() - cached[0] < UNION_TTL_SECONDS:
            if cached[1]:
                author["union_openid"] = cached[1]
            return
        try:
            info = await client.member_info(qq_group, member)
        except Exception as e:
            text = f"{type(e).__name__}: {e}"
            if "11253" in text or "无接口访问权限" in text:
                # 该接口只对白名单机器人开放：一次就够了，别每条消息都去撞
                self._member_info_denied = True
                logger.warning(
                    f"QQ 通道[{self.instance}] 取不到群成员信息（11253：该接口仅白名单机器人可用），"
                    f"union_openid 只能继续看事件载荷"
                )
            else:
                logger.info(f"QQ 通道[{self.instance}] 取群成员信息失败：{text[:160]}")
            return
        union = str(info.get("union_openid") or "").strip()
        self._remember_union(key, union)
        if union:
            author["union_openid"] = union
            logger.info(f"QQ 通道[{self.instance}] 已补拉到一个 union（群成员信息接口）")

    def _remember_union(self, key: str, union: str) -> None:
        """缓存补拉结果（空也缓存），满了丢最早的那条"""
        self._union_cache[key] = (time.time(), union)
        self._union_order.append(key)
        while len(self._union_order) > UNION_CACHE_MAX:
            self._union_cache.pop(self._union_order.popleft(), None)

    @staticmethod
    def _author_openid(author: dict) -> str:
        """通道侧的会话/成员标识：群里是 member_openid，私聊是 user_openid（官方两套都可能是空）"""
        return str(
            author.get("member_openid") or author.get("user_openid") or author.get("id") or ""
        ).strip()

    async def _short_twin(self, db: Any, rows: list, author: dict, stripped: str) -> Any | None:
        """短句认亲：同一个本地账号、或同一个昵称 + 正文一字不差。

        昵称兜底这条必要：新地址第一次出现时还没有本地账号可对（identity 是落库那一刻才建的），
        "同群 + 3 秒内 + 同名 + 同一句"已经足够排除巧合。
        """
        from sqlalchemy import select

        from app.models.user import User
        from app.services.channel import identity

        origin = self._author_openid(author)
        uid = 0
        if origin:
            uid = await identity.user_id_of(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=origin)
        name = str(author.get("username") or "").strip()
        candidates = [m for m in rows
                      if _MENTION_TOKEN_RE.sub("", str(m.content or "")).strip() == stripped]
        if not candidates:
            return None
        if uid:
            for m in candidates:
                if int(m.sender_id) == uid:
                    return m
        if name:
            ids = {int(m.sender_id) for m in candidates}
            names = dict((await db.execute(
                select(User.id, User.username).where(User.id.in_(sorted(ids)))
            )).all())
            for m in candidates:
                if str(names.get(int(m.sender_id)) or "") == name:
                    return m
        return None

    async def _twin_message(self, db: Any, group_id: int, qq_group: str, author: dict,
                            content: str) -> Any | None:
        """同一条 QQ 消息被两台机器人分别送进来时，认出先落库的那条。

        判据：同群、QQ 进来的人话、**摘掉 @ 令牌后**正文一字不差、几秒之内、来源不是本实例，
        且两边认到的 union 不冲突（都认得出又不同名 = 根本不是同一个人）。
        为什么不能拿渲染后的字节直接比：两台是**不同的 AI**——被 @ 的那台补的是它自己的令牌，
        正文里的成员也各自解析成各自通道下的 id，逐字比会认不出亲，合并后的群里同一句话就出现
        两遍、说话人还是两个账号。

        短句（"！"、"嗯"）另有判据：摘完令牌已经不剩什么可比的，但"同一个人几秒内说同一句"仍然是
        很强的信号——几台机器人都在同一个群里收消息，同一条会被各落一遍。所以短句改比
        「同一个本地账号，或同一个昵称」+ 正文一字不差。
        """
        from datetime import timedelta

        from sqlalchemy import select

        from app.models.message import Message
        from app.services.channel import identity
        from app.utils.pure.timeutil import utc_now

        mine = format_channel_origin(self.channel_kind, self.instance, qq_group)
        # 时间窗内的候选捞出来逐条比（令牌不同，SQL 的 content 等值比不了）；
        # 同群 + 别的来源 + 人话，本来就没几条
        rows = (await db.execute(
            select(Message).where(
                Message.group_id == int(group_id),
                Message.sender_type == "human",
                Message.channel_origin.isnot(None),
                Message.channel_origin != mine,
                Message.created_at >= utc_now() - timedelta(seconds=MIRROR_WINDOW_SECONDS),
            ).order_by(Message.id.desc()).limit(MIRROR_SCAN)
        )).scalars().all()
        stripped = _MENTION_TOKEN_RE.sub("", str(content or "")).strip()
        if len(stripped) < MIRROR_MIN_CHARS:
            twin = await self._short_twin(db, rows, author, stripped)
        else:
            twin = next((m for m in rows if _same_channel_text(m.content, content)), None)
        if twin is None:
            return None
        theirs = parse_channel_origin(self.channel_kind, twin.channel_origin)
        union = str(author.get("union_openid") or "").strip()
        if theirs and union:
            other_union = await identity.union_id_of(
                db, kind=self.channel_kind, owner_scope=theirs[0], origin=theirs[1],
            )
            if other_union and other_union != union:
                return None             # 两边都认得出人，而且不是同一个 → 只是碰巧说了同一句话
        return twin

    async def _adopt_twin(self, db: Any, twin: Any, author: dict) -> tuple[int, str]:
        """另一台机器人已经落下这一条：把这一侧的人接到那个账号上，不再建第二条消息。

        这就是"两侧 AI 都收到了、同时只有这一条"时的捆绑：本地账号从此只有一个，
        这一侧的地址（member_openid）照旧留着——@ 他、回他都要用它。

        两边都已经有账号时留**早的那个**：名字、群成员、说过的话都在它身上。留晚的那个，
        先落库那一侧顺手建的"空壳"就成了正主，AI 那边看见两个同名的人各说一半
        （2026-10-18 真机：一个 AN 成了两个账号 92/161，identity 每来一条消息就被两边
        各写一次，来回跳）。
        """
        from sqlalchemy import select

        from app.models.user import User
        from app.services.channel import identity
        from app.services.channel.anchor import merge_channel_accounts

        openid = self._author_openid(author)
        record = await identity.ensure(
            db, kind=self.channel_kind, owner_scope=self.instance, origin=openid,
            display_name=str(author.get("username") or ""), commit=False,
        )
        union = str(author.get("union_openid") or "").strip()
        if union and record.union_id != union:
            record.union_id = union
        canonical = int(twin.sender_id)
        mine = int(record.user_id or 0)
        if mine and mine != canonical:
            canonical = min(mine, canonical)
            await merge_channel_accounts(
                db, from_id=max(mine, int(twin.sender_id)), to_id=canonical
            )
        record.user_id = canonical
        name = (await db.execute(
            select(User.username).where(User.id == canonical)
        )).scalar()
        logger.info(
            f"QQ 群 {openid[-6:]} 与已落库的那条是同一句（来自 {twin.channel_origin}）："
            f"接到账号 #{canonical}"
        )
        return canonical, str(name or "")

    async def _ensure_qq_user(self, db: Any, author: dict, join_group: int = 0) -> tuple[int, str] | None:
        """QQ 用户 → 外部身份那一行（+ 过渡期的本地锚点账号）；需要时加入绑定的群。

        为什么按人建身份：AI 看到的是「谁在说话」。都塞进一个"QQ 访客"账号，
        群里每个人都长得一样，记忆和关系也就无从谈起。身份住在 external_identities
        （谁、从哪来、放不放行），不在 users 里占号。
        建号/补名这件事与 NapCat 通道共用 ensure_channel_user：kind 取自本插件 manifest
        （qq），所以 email 锚点仍是 {openid}@qq.bridge，已有行不受影响。
        """
        from app.services.channel.anchor import ensure_channel_user

        openid = self._author_openid(author)
        return await ensure_channel_user(
            db,
            kind=self.channel_kind,
            owner_scope=self.instance,
            origin=openid,
            # 跨应用统一标识（官方：可能为空）：多台机器人认同一个人就靠它，
            # 否则同一个人在每台机器人眼里各是一个 openid、各占一个账号
            union_id=str(author.get("union_openid") or "").strip(),
            display_name=str(author.get("username") or "").strip(),
            origin_channel=self.channel_kind[:16],
            join_group=join_group,
            # 投递链路在 fanout/broadcast 之后统一 commit，这里不能提前落盘
            commit=False,
        )
