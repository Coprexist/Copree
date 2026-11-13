"""出站：AI 的回复怎么回到 QQ

载体选举（同一条会话由哪台实例发）、引用与 @ 的补写、分片与超长重发、失败上报、
撤回联动、召回额度的占位与释放，都在本文件。入站只负责把消息落库、唤醒 AI；
"发出去"这一半与入站分开，是为了让两边各自能改而不互相牵动。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .common import (
    DM_MAX,
    DM_WINDOW,
    GROUP_MAX,
    GROUP_WINDOW,
    UNREACHABLE_NOTICE_BASE,
    UNREACHABLE_NOTICE_MAX,
    _serialize_same_text,
    logger,
)
from .format import (
    RECALL_PERIOD_DAYS,
    TEXT_CHUNK_MIN,
    _is_dedup_error,
    _is_length_error,
    format_channel_origin,
    parse_channel_origin,
    recall_period,
    split_message,
)


class OutboundMixin:
    async def _deliver_to_group(
        self, group_id: int, qq_group: str, author: dict, content: str, channel_msg_id: str = "",
        channel_ref_idx: str = "", quote_ref: str = "", qq_facts: dict[str, Any] | None = None,
    ) -> tuple[int, str, int] | None:
        """把 QQ 群消息当作一次正常的群发言落库，并走与网页端完全相同的投递链路。

        落点群由调用方按映射表算好（见 _landing_group）：这个函数只认 group_id，
        免得"消息进了 A 群、回复路由却记着 B 群"这种半截错位。
        """
        from app.chat.gm import send_gm_message
        from app.chat.group_delivery import (
            broadcast_group_message,
            fanout_group_message,
            forward_group_message_federated,
            maybe_vectorize_group_message,
            message_view,
            wake_group_ai,
        )
        from app.database import async_session

        async with async_session() as db:
            if not await self._quota_gate().admit(
                db, kind="group", target=group_id,
                label=str((qq_facts or {}).get("name") or ""),
                copree_group_id=group_id,
                notify=lambda text: self._send_quota_notice(
                    kind="group", text=text, qq_group=qq_group, channel_msg_id=channel_msg_id),
            ):
                await db.commit()
                return None
            # 两台机器人同时收到同一句：先按这句排队（见 _serialize_same_text），
            # 否则两边都查不到对方尚未提交的那条，各落一条、各建一个账号
            await _serialize_same_text(db, self.channel_kind, group_id, content)
            twin = await self._twin_message(db, group_id, qq_group, author, content)
            if twin is not None:
                # 另一台机器人已经落下这一条：捆绑身份、不再建第二条（见 _adopt_twin）
                sender_id, peer_name = await self._adopt_twin(db, twin, author)
                # 合并的是"那句话"，不是"点到了谁"：本条事件点到的那个 AI 的令牌得补回
                # 合并后那一行。否则被 @ 的那台看到的是别台渲染的正文（里面只有别人的
                # 影子 id），判定成没点到自己（2026-10-31 真机：鲸鱼娘 @ 后沉默）。
                from app.utils.text import mention_token
                mine = mention_token(self._target_user_id) if self._target_user_id else ""
                if mine and mine in content and mine not in str(twin.content or ""):
                    twin.content = f"{mine} {twin.content}"
                # 合并的是"消息"，不是"通道标识"：这句话在本机器人这里有自己的 id 与 REFIDX，
                # 单独记一份（见 message_channel_refs），否则回话时会拿别人的索引去引用
                await self._record_channel_ref(
                    db, int(twin.id), qq_group,
                    channel_id=channel_msg_id, ref_idx=channel_ref_idx,
                )
                await db.commit()
                return sender_id, peer_name, int(twin.id)
            ensured = await self._ensure_qq_user(db, author, join_group=group_id)
            if ensured is None:
                return None
            sender_id, peer_name = ensured
            await self._sync_group_name(db, group_id, qq_facts)
            reply_to = await self._resolve_quoted(db, group_id, qq_group, quote_ref)
            message = await send_gm_message(
                db,
                group_id=group_id,
                sender_type="human",
                sender_id=sender_id,
                content=content,
                reply_to=reply_to,      # 对方在 QQ 里引用着某条 → 站内也画成引用
                via="qq",          # 群里要能看出这条是从 QQ 来的
            )
            if channel_msg_id:
                # 记下通道侧的消息 id：站内撤回时要请 QQ 一起撤
                # （机器人是群管理员时，还能撤回普通成员的消息）
                message.channel_msg_id = channel_msg_id
            if channel_ref_idx:
                # 记下它的 REFIDX：AI 回复这条时要精准引用（message_reference 用它）
                message.channel_ref_idx = channel_ref_idx
            # 这条来自哪个 QQ 群：出站（含别的实例）靠它把回复送回同一个群
            message.channel_origin = format_channel_origin(self.channel_kind, self.instance, qq_group)
            await db.flush()
            await self._record_channel_ref(
                db, int(message.id), qq_group,
                channel_id=channel_msg_id, ref_idx=channel_ref_idx,
            )
            # 序列化一次，两处共用：AI 成员（fanout）和群里的人（broadcast）
            msg_data = await message_view(db, message)
            await fanout_group_message(db, group_id, message, content, msg_data)
            await broadcast_group_message(group_id, msg_data)
            await db.commit()
            await forward_group_message_federated(group_id, msg_data, db)
            wake_group_ai(group_id, message, content)
            await maybe_vectorize_group_message(db, group_id, message)
            logger.info(f"QQ 群 {qq_group} 的消息已进入 Copree 群 #{group_id}（msg {message.id}）")
            # 连 id 一起带回去：出站摘正文开头的 @ 要用它（入口归一之后是 <@!id>，名字摘不动）；
            # 站内消息 id 给"另一种事件更全时补正文"用
            return sender_id, str(peer_name or ""), int(message.id)

    async def _deliver_to_dm(self, openid: str, author: dict, content: str,
                             channel_msg_id: str = "") -> str | None:
        """QQ 私聊 → 与该 AI 的私信会话（私信落库后同样走共用分发；涉及 AI 免好友校验）"""
        from app.chat.dm import get_or_create_dm_session, send_dm_message
        from app.chat.dm_delivery import fanout_dm_message, forward_dm_federated, wake_dm_ai
        from app.database import async_session

        async with async_session() as db:
            ensured = await self._ensure_qq_user(db, author, join_group=0)
            if ensured is None:
                return None
            sender_id, _peer_name = ensured
            session = await get_or_create_dm_session(db, sender_id, self._target_user_id)
            session_id = str(session["session_id"])
            if not await self._quota_gate().admit(
                db, kind="dm", target=session_id, label=str(_peer_name or ""),
                dm_session_id=session_id,
                notify=lambda text: self._send_quota_notice(
                    kind="dm", text=text, openid=openid, channel_msg_id=channel_msg_id),
            ):
                await db.commit()
                return None
            payload = await send_dm_message(db, session_id, sender_id=sender_id, content=content)
            await fanout_dm_message(session_id, payload, sender_id)
            await db.commit()
            wake_dm_ai(session_id, payload, sender_id=sender_id, sender_type="human")
            await forward_dm_federated(session_id, payload)
            logger.info(f"QQ 私聊已进入会话 {session_id}（AI {self._target_agent}，msg {payload.get('id')}）")
            return session_id

    def _route_for_outbound(self, group_id: int, openid: str) -> dict[str, Any] | None:
        """AI 的这条消息该发到哪个 QQ 群：优先它回的那个会话，其次该群里最近来消息的那个。

        凭据只活在内存里（msg_id/seq 是腾讯按"那条来消息"发的），所以查不到就是发不出去，
        不能另挑一个群顶上——那正是"A 群的消息被回进 B 群"的来源。
        """
        route = self._routes.get(openid) if openid else None
        if route is not None and int(route.get("copree_group_id") or 0) == int(group_id):
            return route
        candidates = [
            r for r in self._routes.values() if int(r.get("copree_group_id") or 0) == int(group_id)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: float(r.get("ts") or 0))

    def _serves_group(self, group_id: int) -> bool:
        """这个实例接不接这个 Copree 群：默认落点，或映射表里任何一个群指向它"""
        from app.utils.pure.channel_landing import serves_group

        return serves_group(
            group_map=self._group_map, default_group_id=self._copree_group_id, group_id=group_id
        )

    @staticmethod
    def _alive(plugin: Any) -> bool:
        task = getattr(plugin, "_task", None)
        return task is not None and not task.done()

    def _siblings(self) -> list[Any]:
        """同插件的其它实例：所有通道实例都跑在同一个进程里，选举不必跨进程协商"""
        from app.services.infrastructure.plugin_registry import PluginRegistry

        # 按插件 id 认亲 + 鸭子类型确认：注册表里躺着所有服务插件，只有这套实现才有
        # "接哪个群、绑哪个 AI、有没有被动凭据"的概念（按类比会在"同一份代码被加载两次"时漏认）
        return [
            p for p in PluginRegistry.get_all()
            if p is not self and getattr(p, "id", "") == self.id and hasattr(p, "_route_for_outbound")
        ]

    def _carrier(self, group_id: int, author_id: int, origin_instance: str = "") -> Any:
        """这条 AI 消息该由哪台机器人发出去（返回的就是那台实例）。

        同一句话只该有一台发（两边都发群里就是两条），判据按优先级：
        1. **作者有主**：这条是某台实例绑的 AI 说的，就只让那台发。判据不看"接不接这个群、
           有没有被动凭据"——会话是两台各自落库的，凭据说没就没，换台顶发就是用别人的
           机器人身份说这句话（2026-10-05 真机：一台机器人说的话，挂着另一台的名进了 QQ 群）。
           只认活着的实例（死掉的那台发不了，别堵着口子）；同一个 AI 被两台接着时按会话
           归属认，都没有就按实例名定序。
        2. 作者不归任何实例（群里别人的 AI 起的话头）：先看来消息的那台，再看谁有可用被动
           凭据，最后按实例名定序——每台各算一次都得到同一个答案，不需要额外的账本。
        """
        origin = str(origin_instance or "")
        owners = [
            p for p in [self] + self._siblings()
            if self._alive(p)
            and int(getattr(p, "_target_user_id", 0) or 0) == int(author_id or 0)
        ]
        if owners:
            # 作者有主：只看它的实例。在线与否要看（死掉的实例发不了，别让它把口子堵住），
            # 但"接不接这个群、有没有被动凭据"不改判据——换台顶发就是用别人的机器人身份
            # 说这句话；发不出去那边会自己留痕提示
            same = [p for p in owners if str(getattr(p, "instance", "") or "") == origin]
            if same:
                return same[0]
            return min(owners, key=lambda p: str(getattr(p, "instance", "") or ""))
        candidates = [
            p for p in [self] + self._siblings()
            if self._alive(p) and p._serves_group(group_id)
        ]
        if not candidates:
            return self
        if origin:
            same = [p for p in candidates if str(getattr(p, "instance", "") or "") == origin]
            if same:
                return same[0]
        ready = [p for p in candidates if p._route_for_outbound(group_id, "") is not None]
        return min(ready or candidates, key=lambda p: str(getattr(p, "instance", "") or ""))

    async def _outbound_sink(self, db: Any, group_id: int, message: Any, source: str) -> None:
        """群消息出口：只转发绑定群里 AI 发的消息。

        谁是发送方由 _carrier 一处定（作者归属优先，其次会话归属与凭据）：两台机器人接着
        同一个 Copree 群时，"这条会话归我"不等于"这句话归我说"——照会话判会让 B 机器人用
        自己的身份把 A 机器人的话发进 QQ 群（2026-10-05 真机）。

        必须 fire-and-forget：这个 sink 在 send_gm_message 里、commit 之前被调用，
        等一次 HTTP 往返会把"发消息"本身拖慢。
        """
        if not self._serves_group(group_id):
            return                          # 不是这个实例接的群，不归它管
        if getattr(message, "sender_type", None) != "ai":
            return
        text = str(getattr(message, "content", "") or "").strip()
        if not text:
            return
        origin = await self._message_origin(db, message)
        carrier = self._carrier(
            group_id, int(getattr(message, "sender_id", 0) or 0), origin[0] if origin else ""
        )
        if carrier is not self:
            # 这条归别的实例发（作者的机器人，或本群另有其人在接）。我们插一手只会把它发进
            # 自己那个群——被动凭据也只在它那边
            return
        route = self._route_for_outbound(group_id, origin[1] if origin else "")
        if not route:
            # 有落点却没有被动凭据：本进程内还没收到过来自该群的入站消息（刚重启、
            # 或这条 AI 消息不是回给 QQ 来消息的）。群里发不出去，留痕别静默丢——
            # 站内提示就近告诉人"去群里 @ 一次就能恢复"，比让他对着 QQ 干等强。
            logger.info(f"QQ 群回复跳过：Copree 群 #{group_id} 还没有可用的被动回复凭据")
            self._replies.spawn(self._report_unreachable(
                "group", {"copree_group_id": group_id},
                "这台机器人在这个群里还没收到过消息（可能没开「接收所有消息」，或没人 @ 过它）："
                "去群里 @ 一次它就能恢复",
            ), f"{self.key} 群回复凭据缺失提示")
            return
        # 只有「@ 事件」的回复才有腾讯自带的 @对方（见路由里 at_event 的注释）。那种情况摘掉开头
        # 对**这次回的那个人**的 @，免得两个 @；全量事件的回复腾讯不补，必须由我们自己把 @ 发出去。
        # 站内内容两处都不动；Markdown 不在这里降级：能不能发 MD 由 QqClient._send_rich 按权限决定
        from app.utils.text import strip_leading_mention

        if route.get("at_event"):
            text = strip_leading_mention(
                text, str(route.get("peer_name") or ""), int(route.get("peer_user_id") or 0) or None
            ).strip()
            if not text:
                return
        # 正文里剩下的 <@!平台id> 翻成 QQ 的真 @（会 @ 到人、会提醒）
        self._enqueue_reply(
            str(route.get("qq") or ""),
            lambda: self._prepare_reply(
                route, text, kind="group", link_mentions=True,
                message_id=getattr(message, "id", None),
                reply_to=getattr(message, "reply_to", None),
            ),
            f"{self.key} 群回复",
        )

    async def _dm_outbound_sink(self, db: Any, session_id: str, msg: dict) -> None:
        """私信出口：这条私信是我们经手的会话、且是 AI 发的，就发回 QQ"""
        route = self._dm_route.get(str(session_id))
        if not route:
            return
        sender_id = msg.get("sender_id")
        if int(sender_id or 0) != self._target_user_id:
            return                      # 只转发这个 AI 的回复
        # 同上：正文格式在 _deliver 里统一施加（群与私聊同一份配置）
        text = str(msg.get("content") or "").strip()
        if not text:
            return
        self._enqueue_reply(
            str(route.get("qq") or ""),
            lambda: self._prepare_reply(route, text, kind="dm", message_id=msg.get("id")),
            f"{self.key} 私信回复",
        )

    async def _prepare_reply(self, route: dict, text: str, kind: str, *, link_mentions: bool = False,
                             message_id: int | None = None, reply_to: int | None = None):
        """发送前的准备（并发跑）→ 返回"真正发出去"的那个协程。

        link_mentions：群回复才翻真 @（要查一次库认人）；私聊没有 @ 这回事，也就不查。
        message_id：站内那条消息的 id——发成功后要把通道侧的 id 记回去，撤回才找得到它。
        """
        if link_mentions:
            try:
                # 这条按纯文本发 → 内联 @ 渲染不了，退成 @名字（见 _translate_mentions）
                text = await self._translate_mentions(text, plain=(self._msg_type == 0))
            except Exception as e:
                # 翻不成真 @ 也要照发：这条回复本身比 @ 的成色重要
                logger.warning(f"QQ 群 @ 映射失败，按原文发送：{type(e).__name__}: {e}")
        text = await self._emoji_to_chars(text)
        reference_id = await self._reply_reference(reply_to, kind, route)
        return lambda: self._post_reply(route, kind, text, reference_id, message_id)

    async def _send_reply(self, route: dict, text: str, kind: str, *, link_mentions: bool = False,
                          message_id: int | None = None, reply_to: int | None = None) -> None:
        """立刻发一条、不排队：正常回复都走 _enqueue_reply（按会话排队），这条给"就现在发"的调用方。

        比如自测与单测：它们要看的是"这条发出去会怎样"，不是它与别人的先后。
        """
        send = await self._prepare_reply(route, text, kind, link_mentions=link_mentions,
                                         message_id=message_id, reply_to=reply_to)
        await send()

    async def _post_reply(self, route: dict, kind: str, text: str, reference_id: str,
                          message_id: int | None) -> None:
        """真正发出去（按会话排队，前一条发完才轮到这条）：失败没人接得住，只能记进状态。"""
        try:
            # 正文格式由 _deliver 统一施加（群与私聊一致；留空 = 先 Markdown、没权限降级纯文本）
            result = await self._deliver(route, kind, text, reference_id=reference_id)
        except Exception as e:
            self.last_error = f"发送失败：{type(e).__name__}: {e}"
            logger.warning(
                f"回复到 QQ {'群' if kind == 'group' else '用户'} "
                f"{str(route.get('qq') or '')[-6:]} 失败：{self.last_error}"
            )
            await self._report_unreachable(kind, route, str(e))
            return
        # 这一段发出去了：这个目标的回避清零，下次再发不出去从 5 分钟重新起算
        # （放在"太长没发完"之前：那算这一条没交代完，照样计数）
        self._fail_notice.pop(self._notice_key(kind, route), None)
        if result.get("unsent"):
            await self._report_unreachable(
                kind, route, f"消息太长，被动回复次数用完了，末尾 {result['unsent']} 字没发出去",
            )
        # 记下通道侧那条消息的 id（撤回要用）与引用索引（引用要用）
        response = result.get("response") or {}
        await self._remember_channel_ids(
            kind, message_id,
            channel_id=str(response.get("id") or ""),
            ref_idx=str((response.get("ext_info") or {}).get("ref_idx") or ""),
            # 会话标识也写回这条 AI 消息：线程里的下一条（回复它）还认得出该回哪个群
            origin=str(route.get("origin") or "") if kind == "group" else "",
        )

    @staticmethod
    def _notice_key(kind: str, route: dict) -> str:
        """"发不出去"的提示按目标分别回避：同一个群、同一个人各算一份"""
        target = str(route.get("qq") or "")
        return f"{kind}:{target or route.get('copree_group_id') or ''}"

    async def _report_unreachable(self, kind: str, route: dict, reason: str) -> None:
        """这条发不到 QQ → 通知 AI 主人。

        为什么不能只写日志：QQ 那边没收到的时候，人在 Copree 里只看到 AI 说得热闹，
        不知道该去 QQ 里再 @ 一次。提示只能走系统会话发给主人——群消息表的 sender_type
        只认 human/ai，插一条"系统"会被约束拒掉；冒充人说话又会惊动群里的 AI。
        """
        target = str(route.get("qq") or "")
        key = self._notice_key(kind, route)
        now = time.time()
        missed, last = self._fail_notice.get(key, (0, 0.0))
        wait = min(UNREACHABLE_NOTICE_BASE * (2 ** missed), UNREACHABLE_NOTICE_MAX)
        if now - last < wait:
            return                      # 指数回避：5 分钟起翻倍，最高 5 天一条（发成功即清零）
        self._fail_notice[key] = (missed + 1, now)
        where = f"（QQ {'群' if kind == 'group' else '用户'} …{target[-6:]}）" if target else ""
        if kind == "group" and route.get("copree_group_id"):
            where += f"（Copree 群 #{int(route['copree_group_id'])}）"
        # 带上是谁：同一个群可能有好几台机器人接着，两条一模一样的提示等于没说
        who = str(self._target_agent or self.instance)
        text = f"「{who}」的 QQ 通道发不出消息{where}：{reason}"
        try:
            from app.database import async_session

            async with async_session() as db:
                await self._notify_owner(db, text)
                await db.commit()
        except Exception as e:
            logger.warning(f"QQ 通道[{self.instance}] 站内提示发送失败：{type(e).__name__}: {e}")

    async def _notify_owner(self, db: Any, text: str) -> None:
        """提示 AI 主人（系统用户 ↔ 主人的会话，与执行器的系统通知同一条路）"""
        from sqlalchemy import select

        from app.chat import chat_api
        from app.models.agent import Agent
        from app.models.sender import SYSTEM_SENDER

        owner_id = (await db.execute(
            select(Agent.owner_id).where(Agent.user_id == self._target_user_id)
        )).scalar()
        if not owner_id:
            return
        sender_id = SYSTEM_SENDER.sender_id
        session = await chat_api.get_or_create_dm_session(
            db, sender_id, int(owner_id), skip_friendship_check=True
        )
        session_id = str(session["session_id"])
        msg = await chat_api.send_dm_message(
            db, session_id, sender_id, text,
            skip_friendship_check=True, message_type="system",
        )
        try:
            await chat_api.broadcast_to_dm(session_id, {
                "type": "new_dm_message",
                "message": {**msg, "sender_name": "系统通知", "is_system": True},
            })
        except Exception:
            pass                        # 推不到就下次刷新看见，不该因此算发送失败

    async def _emoji_to_chars(self, text: str) -> str:
        """站内表情发往 QQ：有 unicode 的换成字符，仅图片的去掉。

        官方 API 不回传富媒体，图片表情在本通道无法显示；保留短码则会把内部写法暴露给对方。
        写法归一复用 catalog.resolve_emoji_text（与站内同一契约），此处只加通道策略。
        """
        if not text or ("[表情:" not in text and ":" not in text):
            return text
        from app.database import async_session
        from app.services.plugin import catalog

        try:
            async with async_session() as db:
                packs = await catalog.enabled_emoji_packs(db)
        except Exception as e:
            logger.warning(f"取表情包失败，按原样发：{type(e).__name__}: {e}")
            return text
        text = catalog.resolve_emoji_text(text, packs)
        # 已登记但只有图的表情去掉；未登记的短码可能是正文自带的冒号词，原样保留
        known = {face["id"] for pack in packs for face in pack["faces"]}
        text = catalog.EMOJI_SHORTCODE_RE.sub(
            lambda m: "" if m.group(1) in known else m.group(0), text
        )
        # 兼容写法只可能来自历史消息，原样发出会显示成一串中文括号，去掉
        return catalog.EMOJI_LEGACY_RE.sub("", text)

    async def _reply_reference(self, reply_to: int | None, kind: str, route: dict | None = None) -> str:
        """AI 的回复若 reply_to 指向一条 QQ 来消息，就取出它的 REFIDX 做精准引用。

        只对群消息做（官方 c2c 的引用字段没有实测过，宁可不发也不发错）。

        **REFIDX 只在它自己那个 QQ 群里有效**：拿 A 群的索引去 B 群引用，腾讯回
        「引用消息越权」(40034138) 并整条拒发——2026-10-31 群里就是这么丢消息的：
        一句话来自甲群，出站路由却选了乙群（该 Copree 群接了好几台机器人，各自连着
        不同的 QQ 群），于是引用越权、整条发不出去。所以只在被引用那条与出站路由是
        **同一个 QQ 会话**时才带引用，否则宁可不引用：引用是锦上添花，发不出去才是事故。
        """
        if not reply_to or kind != "group" or not self._quote_replies:
            return ""
        target = str((route or {}).get("qq") or "")
        if not target:
            return ""
        from sqlalchemy import select

        from app.database import async_session
        from app.models.message import Message, MessageChannelRef

        async with async_session() as db:
            # 正路：这条消息在**本实例、本会话**上的 REFIDX（合并过的消息每台机器人各有一份）
            own = (await db.execute(
                select(MessageChannelRef.channel_ref_idx).where(
                    MessageChannelRef.message_id == int(reply_to),
                    MessageChannelRef.instance == self.instance,
                    MessageChannelRef.conversation == target,
                )
            )).scalars().first()
            if own:
                return str(own)
            # 退回老列：只有"这条本来就是本实例收下来的"才敢信它（迁移前的老数据没有分实例的表）
            row = await db.get(Message, int(reply_to))
            if row is None:
                return ""
            ref = str(getattr(row, "channel_ref_idx", "") or "")
            origin = parse_channel_origin(self.channel_kind, getattr(row, "channel_origin", None))
        return ref if ref and origin is not None and origin[1] == target else ""

    async def _channel_id_for(self, message: Any, conversation: str) -> str:
        """撤回要用的那条 id：按 (消息, 本实例, 会话) 取；没有就退回老列（仅当是同会话的）。"""
        from sqlalchemy import select

        from app.database import async_session
        from app.models.message import MessageChannelRef

        async with async_session() as db:
            own = (await db.execute(
                select(MessageChannelRef.channel_msg_id).where(
                    MessageChannelRef.message_id == int(getattr(message, "id", 0) or 0),
                    MessageChannelRef.instance == self.instance,
                    MessageChannelRef.conversation == conversation,
                )
            )).scalars().first()
        if own:
            return str(own)
        legacy = str(getattr(message, "channel_msg_id", "") or "")
        origin = parse_channel_origin(self.channel_kind, getattr(message, "channel_origin", None))
        return legacy if legacy and origin is not None and origin[1] == conversation else ""

    async def _record_channel_ref(self, db: Any, message_id: int, conversation: str, *,
                                  channel_id: str = "", ref_idx: str = "") -> None:
        """给这条站内消息记下**本实例**这一侧的通道标识（唯一写入口，入站与出站都走它）。

        同一条消息在不同机器人那里是不同的 id / REFIDX（腾讯按机器人隔离），所以按
        (message_id, instance, conversation) 一份份存；只留着空值就别建行。
        """
        if not conversation or not (channel_id or ref_idx):
            return
        from sqlalchemy import select

        from app.models.message import MessageChannelRef

        row = (await db.execute(
            select(MessageChannelRef).where(
                MessageChannelRef.message_id == int(message_id),
                MessageChannelRef.instance == self.instance,
                MessageChannelRef.conversation == str(conversation),
            )
        )).scalars().first()
        if row is None:
            db.add(MessageChannelRef(
                message_id=int(message_id), instance=self.instance,
                conversation=str(conversation),
                channel_msg_id=channel_id or None, channel_ref_idx=ref_idx or None,
            ))
        else:
            # 只补空的那一格：同一侧重复推送时不要来回覆盖已有值
            if channel_id and not row.channel_msg_id:
                row.channel_msg_id = channel_id
            if ref_idx and not row.channel_ref_idx:
                row.channel_ref_idx = ref_idx

    async def _remember_channel_ids(self, kind: str, message_id: int | None, *,
                                    channel_id: str = "", ref_idx: str = "",
                                    origin: str = "") -> None:
        """把通道侧那条消息的 id 记回库里。

        为什么要另开 session：出站是 fire-and-forget 的后台任务，原请求那个 session 早就 commit 了。
        """
        if not message_id or not (channel_id or ref_idx or origin):
            return
        from app.database import async_session
        from app.models.dm import DMMessage
        from app.models.message import Message

        model = Message if kind == "group" else DMMessage
        parsed = parse_channel_origin(self.channel_kind, origin)
        try:
            async with async_session() as db:
                row = await db.get(model, int(message_id))
                if row is not None:
                    if channel_id and not getattr(row, "channel_msg_id", None):
                        row.channel_msg_id = channel_id
                    if ref_idx and not getattr(row, "channel_ref_idx", None):
                        row.channel_ref_idx = ref_idx
                    if origin and hasattr(row, "channel_origin") and not row.channel_origin:
                        row.channel_origin = origin
                    # 老列是"先到先得"的兼容字段；真正的对号入座在这里按实例记
                    if kind == "group" and parsed is not None:
                        await self._record_channel_ref(
                            db, int(message_id), parsed[1],
                            channel_id=channel_id, ref_idx=ref_idx,
                        )
                    await db.commit()
        except Exception as e:
            # 记不住不影响这条消息本身，只是撤回/引用会不可用
            logger.warning(f"通道消息 id 未记住（该条将无法在 QQ 侧撤回/引用）：{type(e).__name__}: {e}")

    async def _revoke_sink(self, db: Any, group_id: int, message: Any) -> dict | None:
        """站内撤回 → 请 QQ 一起撤（2 分钟内；机器人是群管理员时还能撤普通成员的消息）。

        撤不掉就**如实回报**：调用方要把"站内已撤、QQ 撤不掉"告诉用户，别假装成功。
        """
        origin = await self._message_origin(db, message)
        if origin is not None and origin[0] != self.instance:
            return None                   # 这条不是我们发出去的，撤回也不归我们
        route = self._route_for_outbound(group_id, origin[1] if origin else "")
        if not route:
            return None                       # 这条没经通道，或我们没记住它的通道 id
        channel_id = await self._channel_id_for(message, str(route.get("qq") or ""))
        if not channel_id:
            return None
        client = self._client
        if client is None or not self._task or self._task.done():
            return {"channel": self.key, "ok": False, "reason": "通道没在运行"}
        try:
            await client.delete_group_message(str(route.get("qq") or ""), channel_id)
        except Exception as e:
            return {"channel": self.key, "ok": False, "reason": str(e)}
        return {"channel": self.key, "ok": True}

    async def _translate_mentions(self, text: str, *, plain: bool = False) -> str:
        """<@!平台id> → 通道能渲染的写法：只有走过这条通道的人，在 QQ 侧才有 id 可 @

        认不出的（站内的人、别的通道的人）退回名字——令牌原样发到 QQ 只会是乱码。
        查库放在这一步（后台任务）做，sink 是在发消息的链路里被调的，不能拖慢它。

        plain=True（这条按纯文本发）：腾讯的纯文本消息**渲染不了内联 @**——2026-09-26 真机，
        两种写法（带 ! 与不带 !）都原样显示成尖括号。所以纯文本下只能退成 @名字：
        不会真提醒对方，但至少人看得懂，比一串尖括号强。
        """
        from sqlalchemy import select

        from app.database import async_session
        from app.models.user import User
        from app.services.channel import identity
        from app.utils.text import iter_mention_ids, render_mentions

        ids = iter_mention_ids(text)
        if not ids:
            return text
        async with async_session() as db:
            contacts = await identity.contacts(
                db, kind=self.channel_kind, owner_scope=self.instance, user_ids=ids,
            )
            # 纯文本要所有人的名字（联系人也要），Markdown 只要认不出的那些
            named = sorted(ids) if plain else [uid for uid in ids if uid not in contacts]
            names: dict[int, str] = {}
            if named:
                rows = (await db.execute(
                    select(User.id, User.username).where(User.id.in_(named))
                )).all()
                names = {int(uid): str(name or "") for uid, name in rows}

        def _render(uid: int) -> str:
            if plain:
                return f"@{names[uid]}" if names.get(uid) else ""
            if uid in contacts:
                return f"<@!{contacts[uid]}>"
            return f"@{names[uid]}" if names.get(uid) else ""

        return render_mentions(text, _render)

    def _first_size(self, length: int) -> int:
        """这一段先按多长发：整条试。

        平台没公布单条上限，"认过的长度"也就不是上限——默认照整条发，被拒了再二分（见 _deliver）。
        只有撞过钉子之后才收着来：被拒的那个长度本身就是上界，减一是还值得试的最大值，
        免得每一条长消息都拿同一个长度再撞一次。
        """
        if self._too_long:
            return max(TEXT_CHUNK_MIN, min(length, self._too_long - 1))
        return length

    def _enqueue_reply(self, target: str, prepare, label: str) -> None:
        """回复入队：**准备并发、发送排队**。

        准备（翻真 @、转表情、解析引用）各自跑，谁也不等谁；发送按入队顺序一条条来，
        因为 QQ 按到达时间显示。第一个准备好了就发出去，再看第二个准备好没有。
        """
        ready = self._replies.spawn(prepare(), label)
        prev_send = self._send_tail.get(target)
        self._send_tail[target] = self._replies.spawn(
            self._send_in_turn(prev_send, ready), f"{label} 发送",
        )

    async def _send_in_turn(self, prev_send: "asyncio.Task | None", ready: asyncio.Task) -> None:
        """等自己准备好、再等前一条发完，然后发。前一条失败不连坐这一条。"""
        try:
            send = await ready
        except Exception:
            return                      # 准备阶段自己已经记过状态，后面几条照发
        if prev_send is not None:
            await asyncio.gather(prev_send, return_exceptions=True)
        if send is not None:
            await send()

    def _send_lock(self, target: str) -> asyncio.Lock:
        """同一个会话的发送串行化：两条回复同时算 msg_seq 会撞车（腾讯按 (msg_id, msg_seq) 去重，
        后来那条被判 40054005 丢掉）。锁按会话存，最多留 512 个，满了丢最早建的那个。"""
        lock = self._send_locks.get(target)
        if lock is None:
            if len(self._send_locks) >= 512:
                self._send_locks.pop(next(iter(self._send_locks)))
            lock = self._send_locks[target] = asyncio.Lock()
        return lock

    async def _deliver(self, route: dict, kind: str, text: str, *, passive_only: bool = False,
                       reference_id: str = "") -> dict:
        """把一条消息发到 route 指向的会话（频控与被动回复窗口都在这里）

        正文格式（self._msg_type）在这里统一施加：群与私聊共用同一份配置，
        留空才走"先 Markdown、没权限降级纯文本"。

        失败一律抛异常：调用方一个要记状态（后台回复）、一个要把原因给用户看（自测），
        各写一份"为什么没发出去"迟早不一致。
        passive_only：窗口过期时宁可失败也不退成主动消息——自测走这条路，
        它不该靠一条有配额的主动消息来冒充"通"。

        窗口过期后的出路两边不同：私聊走互动召回（is_wakeup，配额按周期落库），
        群聊直接失败——腾讯早就没有群主动推送，也没有群召回字段，硬发只会换一个错误码。
        """
        target = str(route.get("qq") or "")
        if not target:
            raise RuntimeError("这条路由里没有目标会话")
        async with self._send_lock(target):
            return await self._send_on_route(
                route, kind, text, passive_only=passive_only, reference_id=reference_id,
            )

    async def _send_on_route(self, route: dict, kind: str, text: str, *,
                             passive_only: bool = False, reference_id: str = "") -> dict:
        """_deliver 的实体：拿到会话锁之后才进来，msg_seq 这本账从这里开始算。"""
        client = self._client
        if client is None or not self._task or self._task.done():
            raise RuntimeError("通道没在运行")
        target = str(route.get("qq") or "")
        if not await self._wait_for_slot(target):
            raise RuntimeError("触发频控（单会话 20/分钟、Bot 60/分钟），过一分钟再试")
        window, limit = (GROUP_WINDOW, GROUP_MAX) if kind == "group" else (DM_WINDOW, DM_MAX)
        msg_id = route.get("msg_id") or None
        seq = int(route.get("seq") or 0)
        fresh = (time.time() - float(route.get("ts") or 0)) < window
        wakeup = False
        claimed: int | None = None
        if msg_id and fresh and seq < limit:
            route["seq"] = seq + 1
            mode = "passive"
        elif passive_only:
            raise RuntimeError("被动回复窗口已过：先去那个会话里说一句（群里 @ 一次机器人）再自测")
        elif kind == "group":
            if msg_id and fresh:
                # 窗口还在，是**这条来消息的回复次数**用完了（群 5 次）——和"窗口过期"是
                # 两回事，说清楚才知道该等什么（等下一句话来，而不是去重新 @）
                raise RuntimeError(
                    f"这条消息的回复次数用完了（一条最多 {limit} 次），等下一条消息再开口"
                )
            # 群聊没有主动推送（2025-04-21 起下线），也没有召回字段：只能等下一次 @
            raise RuntimeError(f"被动回复窗口已过（{GROUP_WINDOW // 60} 分钟），QQ 群聊没有主动/召回能力")
        else:
            # 私聊走互动召回：官方只给单聊 is_wakeup，且 30 天内每个周期只有一条
            msg_id = None
            claimed, reason = await self._claim_recall(target, float(route.get("ts") or 0))
            if claimed is None:
                raise RuntimeError(reason)
            mode, wakeup = "wakeup", True
        # 单条到底能有多长，官方没给数字、只给了 40054007「消息长度超限」这个错误码，所以不猜死：
        # 整条先发；平台说太长就把这一段二分重发（前面已经发出去的段不重来），两侧边界都记下来。
        # 硬截断等于后半段凭空消失，不能再用。
        data: dict = {}
        attempts = 0
        sent_count = 0
        unsent = 0
        queue = split_message(text, self._first_size(len(text)))
        while queue:
            if attempts or sent_count:
                if mode == "passive":
                    if not (msg_id and seq + attempts < limit):
                        unsent = sum(len(p) for p in queue)
                        logger.warning(
                            f"QQ 通道[{self.instance}] 这条太长：被动回复次数已用完，"
                            f"末尾 {unsent} 字没发出去"
                        )
                        break
                else:
                    # 召回一个周期只有一条，接不下去就如实交代
                    unsent = sum(len(p) for p in queue)
                    logger.warning(f"QQ 通道[{self.instance}] 召回消息只有一条，末尾 {unsent} 字没发出去")
                    break
            piece = queue.pop(0)
            try:
                if kind == "group":
                    data = await client.send_group(
                        target, piece, msg_id=msg_id, msg_seq=seq + attempts + 1,
                        message_reference=reference_id, force_type=self._msg_type,
                    )
                else:
                    # 私聊的引用字段官方没给（也没实测过），宁可不发也不发错
                    data = await client.send_c2c(target, piece, msg_id=msg_id, msg_seq=seq + attempts + 1,
                                                 force_type=self._msg_type, wakeup=wakeup)
            except Exception as e:
                # 每被拒一次就折半，且必须比上一次小，否则原地打转（平台的真实上限可能比 TEXT_CHUNK 还小）
                too_long = _is_length_error(e) and len(piece) > TEXT_CHUNK_MIN
                if claimed is not None and not too_long:
                    # 腾讯明确拒了才把周期还回去；响应丢失时留着（见 _claim_recall）
                    await self._release_recall(target, claimed)
                    claimed = None
                if not too_long and not _is_dedup_error(e):
                    raise
                if too_long:
                    # 二分：在"平台认过的长度"和"刚被拒的长度"之间取中点，没有认过的长度就砍半。
                    good = self._max_chars
                    self._too_long = min(self._too_long or len(piece), len(piece))
                    smaller = (good + len(piece)) // 2 if good and good < len(piece) else len(piece) // 2
                    smaller = max(TEXT_CHUNK_MIN, min(smaller, len(piece) - 1))   # 必须比上一次小，否则原地打转
                    logger.info(
                        f"QQ 通道[{self.instance}] 单条 {len(piece)} 字被平台拒（长度超限）："
                        f"改按 {smaller} 字二分重发（认过的 {good or '—'}）"
                    )
                    queue = split_message(piece, smaller) + queue
                else:
                    # 序号撞车 = 平台没收到这条（按 (msg_id, msg_seq) 去重）：换个号重发，
                    # 别让用户那边凭空少一句；额度不够时上面的检查会如实记下没发出去的字数
                    queue.insert(0, piece)
                    logger.warning(
                        f"QQ 通道[{self.instance}] msg_seq {seq + attempts + 1} 被判「消息被去重」，换号重发"
                    )
                attempts += 1        # 被拒的这次也占了序号：同一个 msg_id+seq 重发会被判"消息被去重"
                continue
            attempts += 1
            sent_count += 1
            if len(piece) > self._max_chars:
                # 平台认了这一段：它就是"认过的长度"，下次二分时当下界用
                self._max_chars = len(piece)
        if msg_id:
            # 每一次尝试都占一次回复额度（含被拒的那次）；少记了会让下一个 seq 撞车。
            # 只许往前：另一条回复可能已经把这本账推到更前了，拿旧基数回写会把它拽回来
            route["seq"] = max(int(route.get("seq") or 0), seq + max(1, attempts))
        if wakeup:
            self.recalls += 1
        if kind == "group":
            self.replies += 1
        else:
            self.dm_replies += 1
        self.last_error = ""
        return {"mode": mode, "response": dict(data or {}), "unsent": unsent}


    # ── 互动召回记账 ───────────────────────────────────────────
    async def _claim_recall(self, target: str, anchor_ts: float) -> tuple[int | None, str]:
        """领一个召回周期：返回 (周期号, 领不到的原因)。

        先记账再发（见 _deliver）：腾讯的响应丢了不代表消息没发出去，
        所以宁可这个周期少发一条，也不要同一周期发两条。
        周期起点只认账本里那一行——内存里的路由 ts 每条消息都在变，
        拿它算"过了几天"会让周期永远停在当天，等于把 4 条额度当无限用。
        """
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.database import async_session
        from app.models.external import ChannelWakeupLedger
        from app.utils.pure.timeutil import as_utc

        now = time.time()
        async with async_session() as db:
            row = (await db.execute(
                select(ChannelWakeupLedger).where(
                    ChannelWakeupLedger.kind == self.channel_kind,
                    ChannelWakeupLedger.owner_scope == self.instance,
                    ChannelWakeupLedger.target == target,
                )
            )).scalars().first()
            started = as_utc(row.anchor_at).timestamp() if row is not None else 0.0
            if row is None or now - started >= RECALL_PERIOD_DAYS[-1] * 86400:
                # 没有账本、或上一轮已过 30 天：这次对话开一个新周期
                started = anchor_ts or now
                if row is None:
                    row = ChannelWakeupLedger(
                        kind=self.channel_kind, owner_scope=self.instance, target=target,
                        anchor_at=datetime.fromtimestamp(started, tz=timezone.utc).replace(tzinfo=None),
                        used_mask=0,
                    )
                    db.add(row)
                else:
                    row.anchor_at = datetime.fromtimestamp(started, tz=timezone.utc).replace(tzinfo=None)
                    row.used_mask = 0
            index = recall_period((now - started) / 86400)
            if index is None:
                return None, "对方最近一次主动对话已超过 30 天，召回额度不再可用：等对方来说一句话"
            if int(row.used_mask or 0) >> index & 1:
                return None, f"本周期的召回名额已经用掉（共 {len(RECALL_PERIOD_DAYS)} 个周期）：等对方来说一句话"
            row.used_mask = int(row.used_mask or 0) | (1 << index)
            await db.commit()
        return index, ""

    async def _release_recall(self, target: str, index: int) -> None:
        """这次发送被腾讯明确拒了 → 把刚占的周期还回去，本轮还能再试"""
        from sqlalchemy import select

        from app.database import async_session
        from app.models.external import ChannelWakeupLedger

        try:
            async with async_session() as db:
                row = (await db.execute(
                    select(ChannelWakeupLedger).where(
                        ChannelWakeupLedger.kind == self.channel_kind,
                        ChannelWakeupLedger.owner_scope == self.instance,
                        ChannelWakeupLedger.target == target,
                    )
                )).scalars().first()
                if row is None:
                    return
                row.used_mask = int(row.used_mask or 0) & ~(1 << index)
                await db.commit()
        except Exception as e:
            logger.warning(f"QQ 通道[{self.instance}] 归还召回周期失败：{type(e).__name__}: {e}")

    def _live_route(self) -> tuple[dict[str, Any] | None, str]:
        """最近一次来消息的那条路由（群或私聊）——自测要打在最可能通的那条路上"""
        candidates = [(float(r.get("ts") or 0), "group", r) for r in self._routes.values()]
        candidates += [(float(r.get("ts") or 0), "dm", r) for r in self._dm_route.values()]
        if not candidates:
            return None, ""
        _, kind, route = max(candidates, key=lambda c: c[0])
        return route, kind

    async def _message_origin(self, db: Any, message: Any) -> tuple[str, str] | None:
        """这条消息来自哪个通道会话 → (实例, 会话)。

        先看它自己那一行（AI 的回复发出去之后会带回同一个标识，线程就延续下来了），
        再看它回的那条——引用/回复都要能把上下文接上。返回 None = 不是本通道的消息。
        """
        origin = parse_channel_origin(self.channel_kind, getattr(message, "channel_origin", None))
        if origin is not None:
            return origin
        reply_to = getattr(message, "reply_to", None)
        if not reply_to:
            return None
        from sqlalchemy import select

        from app.models.message import Message

        row = (await db.execute(
            select(Message.channel_origin).where(Message.id == int(reply_to))
        )).first()
        return parse_channel_origin(self.channel_kind, row[0] if row else None)
