"""入站：QQ 上的消息怎么进 Copree

事件分发、群消息与私聊两条入口、全量/点名两种观测模式、落点群与群资料、以及"@ 点名"
的物化（提到谁、认亲、影子自愈都在这里发起调用）。身份细节在 identity.py，回消息在
outbound.py；本文件只负责"收进来、落库、唤醒"。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

from .common import (
    BOT_PER_MINUTE,
    DEDUP_SIZE,
    DM_ROUTE_MAX,
    GROUP_FACTS_MAX,
    GROUP_PER_MINUTE,
    PAIR_NOTIFY_CACHE_MAX,
    PAIR_NOTIFY_INTERVAL,
    _CHANNEL_MODE_REF,
    _channel_mode,
    _channel_mode_notice,
    _last_channel_mode,
    logger,
)
from .format import _parse_group_map, format_channel_origin, humanize_faces
from .media import event_attachments


class InboundMixin:
    # ── 入站：QQ 事件 → Copree ─────────────────────────────────
    async def _on_event(self, event: str, data: dict) -> None:
        # 每类事件都留一行：出事时能一眼分清"腾讯没推"还是"我们挡了"
        # （心跳是 op11、不进这里，所以不会刷屏）
        if event not in ("READY", "RESUMED"):
            logger.info(f"QQ 事件[{self.instance}]: {event}")
        if event == "READY":
            self.bot_name = str((data.get("user") or {}).get("username") or "")
            # 会话 id 是重连续接（op6）的唯一凭据，必须记下来；丢了就只能重新鉴权，
            # 断线期间的事件也就补不回来了
            self._session_id = str(data.get("session_id") or "")
            self.connected = True
            self._ready = True
            logger.info(f"QQ 机器人已就绪：{self.bot_name}（实例 {self.instance}）")
            return
        if event == "RESUMED":
            self.connected = True
            self._ready = True
            logger.info(f"QQ 通道[{self.instance}] 会话已续接")
            return
        if event == "GROUP_MESSAGE_CREATE":
            # 全量模式：群主在 QQ 群设置里把「机器人可获取的群聊消息范围」设成「群内全部消息」后，
            # 群里每条消息都推这个事件（同一个 Intent）。没开就根本收不到，@ 那条路照旧——开不开都兼容
            await self._on_group_message(data)
            return
        if event == "GROUP_AT_MESSAGE_CREATE":
            await self._on_group_at(data)
            return
        if event == "GROUP_ADD_ROBOT":
            # 机器人被拉进一个 QQ 群：这一刻只认领同名的已有落点群（有就立刻接上），不新建
            await self._on_group_added(data)
            return
        if event == "C2C_MESSAGE_CREATE":
            await self._on_c2c(data)
            return

    async def _on_group_at(self, d: dict) -> None:
        """群 @ 机器人事件：这个事件本身就是"被点名"，不用再判一次。

        事件名也是模式判据：这个事件只在"没开全量"时来（2026-09-26 真机：开了之后连 @ 的消息
        也只来 GROUP_MESSAGE_CREATE 一条）。
        """
        await self._handle_group_message(d, addressed=True, full=False)

    async def _on_group_message(self, d: dict) -> None:
        """全量模式（开了「接收所有消息」）：群里每条消息都推过来，**有消息就进 Copree**
        （能拿到多少拿多少——镜像群本来就该是完整的对话）。

        但"进 Copree"和"叫 AI"是两件事：只有点名到机器人才加唤醒令牌，否则 AI 会被迫
        围观全部闲聊。**开不开都兼容**：没开这个功能就收不到这个事件，@ 那条路照旧。
        """
        await self._handle_group_message(d, addressed=self._addressed_to_bot(d), full=True)

    def _addressed_to_bot(self, d: dict) -> bool:
        """这条消息点没点到**我**。

        全量事件里官方给 mentions，每条带 bot / is_you 两个标记，**只有 is_you 是"这条是我"**：
        bot 只说明"被点的是个机器人"，群里有两个机器人时，@ 另一个机器人同样带 bot: true
        （2026-10-05 真机载荷：`{"bot": true, "is_you": false, "username": "某成员"}`——旧判据把它
        当成"我被 @ 了"，于是给这条消息补上唤醒令牌，另一台机器人替被 @ 的那台答了话）。
        拿不到 mentions 再退回按名字判
        （@机器人 的前缀在两种事件里都被官方去掉了，所以名字判定是唯一兜底）。
        """
        for user in d.get("mentions") or []:
            if isinstance(user, dict) and user.get("is_you"):
                return True
        from app.utils.text import check_mention

        return check_mention(self._readable_content(d), self._target_agent)

    async def _dm_allowed(self, openid: str, author: dict, msg_id: str) -> bool:
        """私聊放行判断 —— 默认 pairing：陌生人不进 AI，只领一个配对码。

        配对状态住在 Copree 的 external_identities 里（这个外部身份那一行），批准口在「这个 AI 的 QQ 通道卡片」上；
        机器人这边只做两件事：挡住陌生人、把码告诉他。
        官方只给 openid（拿不到 QQ 号），所以认人只能认 openid，昵称仅用于展示。
        """
        if self._dm_policy == "open":
            return True

        from app.database import async_session
        from app.services.channel import identity

        async with async_session() as db:
            status = await identity.status_of(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=openid
            )
            if status == identity.APPROVED:
                return True
            if status == identity.BLOCKED or self._dm_policy == "owner":
                logger.info(f"QQ 私聊被挡下（{status or '未配对'}）：openid …{openid[-6:]}")
                return False
            row = await identity.upsert_pending(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=openid,
                display_name=str(author.get("username") or ""),
            )
            code = row.code

        now = time.time()
        # 陌生人没有上限，这张表只服务下面这 60 秒的判断：过期条目顺手清掉
        if len(self._pair_notified) > PAIR_NOTIFY_CACHE_MAX:
            self._pair_notified = {
                key: at for key, at in self._pair_notified.items()
                if now - at < PAIR_NOTIFY_INTERVAL
            }
        if now - self._pair_notified.get(openid, 0) < PAIR_NOTIFY_INTERVAL:
            logger.debug(f"配对码已发过，未到重发间隔：openid …{openid[-6:]}")
            return False
        self._pair_notified[openid] = now
        try:
            await self._client.send_c2c(
                openid,
                identity.pairing_reply(code, "QQ 通道"),
                msg_id=msg_id,
            )
            logger.info(f"QQ 陌生人私聊 → 已下发配对码（openid …{openid[-6:]}，昵称 {row.display_name or '?'}）")
        except Exception as e:
            self.last_error = f"配对码下发失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 配对码下发失败：{self.last_error}")
        return False

    async def _handle_group_message(self, d: dict, *, addressed: bool, full: bool) -> None:
        author = d.get("author") or {}
        if author.get("bot"):
            return
        msg_id = str(d.get("id") or "")
        qq_group = str(d.get("group_openid") or "")
        if not msg_id or not qq_group:
            return
        # 模式观测的唯一落点：事件名就是权威判据（见 _note_group_mode）
        await self._note_group_mode(full)
        if self._seen_before(msg_id):
            # 同一条消息的两种事件（@模式 + 全量模式）都会到：谁先到谁落库；
            # 后来那个更全就把库里的正文补上（@模式的正文会在"@其他成员"处断掉）
            # 补正文这一步也要走 @ 处理：全量事件后才补上的正文同样带原始提及
            known_landing = int(self._group_map.get(qq_group) or self._copree_group_id or 0)
            await self._upgrade_delivered_content(
                msg_id,
                self._with_mention_prefix(await self._materialize_mentions(
                    d, self._readable_content(d), join_group=known_landing)),
            )
            return
        # 先记账再判白名单：白名单该怎么填，前提是界面能看见"机器人在哪些群里出现过"。
        # 被白名单挡下的群同样记下来（allowed=False），否则用户永远发现不了它。
        self._note_group(qq_group, allowed=(not self._allow) or qq_group in self._allow)
        # 白名单先挡：被挡下的群不该走到"自动建群"那一步（建了群却收不到消息，比没建更糟）
        if self._allow and qq_group not in self._allow:
            logger.debug(f"QQ 群 {qq_group} 不在白名单，忽略")
            return
        # 凭据一到手就成立：落点能从内存里的映射算出来就先记上（见 _note_route），
        # 别等拉完群信息——那一段时间里 AI 回的话会因为"没有凭据"被整条跳过
        known_landing = int(self._group_map.get(qq_group) or self._copree_group_id or 0)
        if known_landing:
            self._note_route(qq_group, known_landing, msg_id, full=full)
        # 群信息（群名/人数…）先拿：认领已有落点群要靠群名对号（见 channel._claim_landing），
        # 下面落库时还要用它对齐落点群名。今天问过就用手里的，不额外发请求。
        facts = await self._group_facts_of(qq_group)
        landing = await self._resolve_landing(qq_group, name=str((facts or {}).get("name") or ""))
        if not landing:
            logger.debug("这个 QQ 群没有落点群（实例没绑 Copree 群、映射里也没有），忽略")
            return

        content = self._readable_content(d)
        content = await self._materialize_mentions(d, content, join_group=landing)
        quoted_text, quoted_ref = self._quoted_of(d)
        # 只引用、一个字没打的「空引用」也要落库：不落的话用户看着像消息没发出去。
        # 被引用的原文进正文，AI 才接得住"他说的是哪句"（引用块只给人看，AI 看不到它）
        if not content and quoted_text:
            content = f"[引用] {quoted_text}"
        # 点名到机器人 → 加唤醒令牌（Copree 的唤醒规则认它）；没点名 → 只入库、不叫 AI
        if addressed:
            content = self._with_mention_prefix(content).strip()
        if not content:
            return

        # 官方事件表里 author 有 username，但真机上我们只拿到过占位名 —— 打一行真实字段，
        # 一眼分清「腾讯没给昵称」还是「我们没读出来」（openid 只留尾号，标识不进日志）
        _openid = str(author.get("member_openid") or author.get("user_openid") or author.get("id") or "")
        # message_scene.ext 里带 msg_idx（本条消息的 REFIDX，出站引用要用它）与 ref_msg_idx
        # （对方引用的是哪条）。只打键名：ext 里还可能有 auth_token，值不进日志
        scene = d.get("message_scene") or {}
        ext = self._scene_ext(d)
        elements = d.get("msg_elements") if isinstance(d.get("msg_elements"), list) else []
        # 只报长度与类型，不报值：ext 里还有 auth_token（凭据），值一律不进日志。
        # msg_idx 是本条消息的引用索引（出站精准引用要用它）、ref_msg_idx 是"对方引用了哪条"
        logger.info(
            f"QQ 群消息[{self.instance}]: openid …{_openid[-6:]} "
            f"昵称={author.get('username') or '(空)'} author字段={sorted(author.keys())} "
            f"消息字段={sorted(d.keys())} message_type={d.get('message_type')} "
            f"mentions={json.dumps(d.get('mentions') or d.get('message_mentions'), ensure_ascii=False)[:200]} "
            f"场景={scene.get('source')} scene_ext键={sorted(ext)} "
            f"msg_idx长度={len(ext.get('msg_idx') or '')} ref_msg_idx长度={len(ext.get('ref_msg_idx') or '')} "
            f"元素类型={[e.get('message_type') for e in elements if isinstance(e, dict)]}",
        )


        # 事件里的 union_openid 常常是空的（官方也写了"可能为空"），跨机器人认人只能按需补拉一次
        await self._fill_union(qq_group, author)
        try:
            delivered = await self._deliver_to_group(
                landing, qq_group, author, content, msg_id, str(ext.get("msg_idx") or ""), quoted_ref,
                qq_facts=facts,
                # 图片在落库时下下来存成站内附件：正文里的 [图片] 只是"有人发了图"，
                # 图本身得进 messages.attachments，AI 的多模态上下文才拿得到字节
                media=event_attachments(d),
            )
            peer_user_id, peer_name, copree_msg_id = delivered or (0, "", 0)
            self._remember_delivered(msg_id, copree_msg_id, content)
            # 落点已认准、peer 信息也有了：覆写一次（时间戳顺带刷新成投递完成的时刻）
            self._note_route(
                qq_group, landing, msg_id, full=full,
                peer_name=str(peer_name or ""), peer_openid=_openid, peer_user_id=peer_user_id,
            )
        except Exception as e:
            self.last_error = f"入站失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 群消息进 Copree 失败：{self.last_error}", exc_info=True)

    async def _on_c2c(self, d: dict) -> None:
        author = d.get("author") or {}
        if author.get("bot"):
            return
        msg_id = str(d.get("id") or "")
        openid = str(author.get("user_openid") or author.get("id") or "")
        if not msg_id or not openid:
            return
        if self._seen_before(msg_id):
            return
        if self._dm_policy == "off":
            logger.debug("私聊策略为 off，忽略私聊消息")
            return
        if not await self._dm_allowed(openid, author, msg_id):
            return

        content = self._readable_content(d)
        if not content:
            return
        try:
            session_id = await self._deliver_to_dm(
                openid, author, content, channel_msg_id=msg_id, media=event_attachments(d),
            )
            if session_id:
                self._remember_dm_route(session_id, {
                    "qq": openid, "msg_id": msg_id, "seq": 0, "ts": time.time(),
                    # 账本便签（"这句话/这张图没发出去"）要知道往哪个会话里写
                    "session_id": session_id,
                })
        except Exception as e:
            self.last_error = f"私信入站失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 私聊消息进 Copree 失败：{self.last_error}", exc_info=True)

    def _note_route(self, qq_group: str, landing: int, msg_id: str, *, full: bool,
                    peer_name: str = "", peer_openid: str = "", peer_user_id: int = 0) -> None:
        """这个 QQ 群现在有可用的被动回复凭据了 → 记进路由表（入站唯一的写入口）。

        为什么要先记一次、投递完再覆写一次：凭据是"腾讯刚把这条推给我"，从**收到**那一刻就
        成立；而落库要先拉群信息、认落点（有时还要建群），慢的时候 AI 已经回完话了——那台
        机器人就会被判成"没有凭据"，这条回话白丢（2026-10-05 真机：插件配置一存实例重启、
        路由清空，紧接着第一条消息就踩中）。peer 信息要等投递完才有，所以覆写补齐。
        """
        self._routes[qq_group] = {
            "qq": qq_group, "copree_group_id": int(landing or 0), "msg_id": msg_id, "seq": 0,
            "ts": time.time(),
            # 会话标识：AI 的回复发出去之后要写回它那一行（线程里的下一条还认得出这个群）
            "origin": format_channel_origin(self.channel_kind, self.instance, qq_group),
            # 出站摘 @ 要用它：QQ 的被动回复自己显示 @对方，正文里那个 @是谁要对得上
            "peer_name": peer_name or "",
            # 自测要在正文里 @ 回去：群消息里只有 member_openid 能当 @ 的目标
            "peer_openid": peer_openid,
            # 摘正文开头那个 @ 要用平台 id（入口归一之后它是 <@!id>，名字摘不动）
            "peer_user_id": int(peer_user_id or 0),
            # 这条是不是「@ 事件」（GROUP_AT_MESSAGE_CREATE）送来的：腾讯只在回复这种事件时
            # 自己补一个 @对方。全量事件送来的消息哪怕正文 @ 了机器人它也不补——2026-09-26 实测：
            # 09-25 与 08:28 两条 @ 事件的回复都自动出现 @；11:00 那条全量事件（正文 @ 了机器人）
            # 发出去就没有 @。所以摘不摘看**事件类型**，不看"有没有点名"。
            "at_event": not full,
        }

    def _remember_dm_route(self, session_id: str, route: dict[str, Any]) -> None:
        """记下"这条私信回到哪个 openid"：出站要用，但只留最近建立的那批

        按会话记 = 每个「QQ 用户 ↔ 这个 AI」一条，只随配对人数涨、不随时间落。
        满了丢最早那条：那个人下次来消息会重新入表，代价只是这期间 AI 主动找他时回不到 QQ。
        """
        self._dm_route[session_id] = route
        while len(self._dm_route) > DM_ROUTE_MAX:
            self._dm_route.popitem(last=False)
            logger.info(f"QQ 通道[{self.instance}] 私信路由已达上限 {DM_ROUTE_MAX}，丢弃最早的一条")

    def _remember_delivered(self, msg_id: str, message_id: int, content: str) -> None:
        """记住"这条消息我们落库了、正文是什么"——另一种事件带来更全的正文时要用它补"""
        if not msg_id or not message_id:
            return
        self._delivered[msg_id] = (int(message_id), content)
        self._delivered_order.append(msg_id)
        while len(self._delivered_order) > DEDUP_SIZE:
            self._delivered.pop(self._delivered_order.popleft(), None)

    async def _upgrade_delivered_content(self, msg_id: str, fuller: str) -> None:
        """同一条消息的另一个事件更全 → 把库里的正文补上。

        为什么需要：@模式事件的正文会在"@其他成员"处断掉（官方只给到那一截）。
        开了全量模式后，同一个 msg_id 还会再来一条完整的；谁先到谁落库，后到的更全就补。
        账本里那条若已经写下就改不动了（账本只追加），所以这条纠正主要给界面显示、
        以及还没轮到 AI 看的时候——比"永远只有半截"强。
        """
        remembered = self._delivered.get(msg_id)
        if not remembered or not fuller:
            return
        message_id, stored = remembered
        if len(fuller) <= len(stored) or not fuller.startswith(stored):
            return
        from app.database import async_session
        from app.models.message import Message

        try:
            async with async_session() as db:
                row = await db.get(Message, message_id)
                if row is not None and str(row.content or "") == stored:
                    row.content = fuller
                    await db.commit()
            self._delivered[msg_id] = (message_id, fuller)
            logger.info(
                f"QQ 群消息正文已用更全的事件补上（站内 msg {message_id}：{len(stored)} → {len(fuller)} 字）"
            )
        except Exception as e:
            logger.warning(f"补全正文失败（非致命）：{type(e).__name__}: {e}")

    async def _resolve_quoted(self, db: Any, group_id: int, conversation: str,
                             ref_idx: str) -> int | None:
        """按 REFIDX 找回被引用的那条站内消息（找不到就返回 None）。

        REFIDX 是**按机器人实例隔离**的：同一个 QQ 群里，A 机器人看到的索引在 B 机器人那里
        指向别的东西，甚至根本不存在。所以这里必须按 (实例, 会话) 限定（见 message_channel_refs），
        否则会把别台机器人的索引错认成自己的引用对象。
        找不到只有两种情形：那条消息机器人没在线、压根没进过站内；或者它来自别的实例。
        此时宁可不引用，也不猜一个 id。
        """
        if not ref_idx:
            return None
        from sqlalchemy import select

        from app.models.message import Message, MessageChannelRef

        row = (await db.execute(
            select(Message.id)
            .join(MessageChannelRef, MessageChannelRef.message_id == Message.id)
            .where(
                Message.group_id == group_id,
                MessageChannelRef.instance == self.instance,
                MessageChannelRef.conversation == conversation,
                MessageChannelRef.channel_ref_idx == ref_idx,
            )
            .order_by(Message.id.desc())
            .limit(1)
        )).first()
        if row is not None:
            return int(row[0])
        # 退回老列：一条 QQ 群消息在别的实例下被引用时，那个 REFIDX 只存在对方那一侧
        # （也可能来自分实例表之前的老数据）。引用对象按"哪个群里的哪条消息"认，
        # 不按"哪台机器人先看到"——找不到就不引用，不猜。
        legacy = (await db.execute(
            select(Message.id)
            .where(Message.group_id == group_id, Message.channel_ref_idx == ref_idx)
            .order_by(Message.id.desc())
            .limit(1)
        )).first()
        return int(legacy[0]) if legacy else None

    @staticmethod
    def _scene_ext(d: dict) -> dict:
        """message_scene.ext 的 key=value 列表 → 字典。

        值一律不外传（里面有 auth_token 这个凭据），只用于取 msg_idx / ref_msg_idx。
        """
        scene = d.get("message_scene") or {}
        raw = scene.get("ext") if isinstance(scene.get("ext"), list) else []
        ext: dict[str, str] = {}
        for item in raw:
            key, _, value = str(item).partition("=")
            if key:
                ext[key] = value
        return ext

    @staticmethod
    def _quoted_of(d: dict) -> tuple[str, str]:
        """引用消息（message_type=103）→ (被引用的正文, 被引用那条的 REFIDX)。

        腾讯不给"被引用的是哪条 msg_id"，只把内容塞进 msg_elements；它自己的索引在
        message_scene.ext 的 ref_msg_idx 里，而那个值正是我们落库时写在 channel_ref_idx 上的
        同一个索引——站内靠它找回回复对象（见 _resolve_quoted）。非引用消息返回空串。
        """
        try:
            if int(d.get("message_type") or 0) != 103:
                return "", ""
        except (TypeError, ValueError):
            return "", ""
        elements = d.get("msg_elements") if isinstance(d.get("msg_elements"), list) else []
        quoted = " ".join(
            InboundMixin._readable_content(e) for e in elements if isinstance(e, dict)
        ).strip()
        return quoted, InboundMixin._scene_ext(d).get("ref_msg_idx", "")

    @staticmethod
    def _readable_content(d: dict) -> str:
        """事件里的可用文本：正文优先，图片/语音/文件转成占位，别让 AI 以为没人说话"""
        text = str(d.get("content") or "").strip()
        if text:
            return humanize_faces(text)
        parts: list[str] = []
        for att in d.get("attachments") or []:
            ctype = str((att or {}).get("content_type") or "")
            asr = str((att or {}).get("asr_refer_text") or "").strip()
            if asr:
                parts.append(f"[语音] {asr}")
            elif ctype.startswith("image/"):
                parts.append("[图片]")
            elif ctype == "voice":
                parts.append("[语音]")
            elif ctype == "video/mp4":
                parts.append("[视频]")
            else:
                parts.append(f"[文件] {(att or {}).get('filename') or ''}".strip())
        return " ".join(parts).strip()

    def _note_group(self, openid: str, *, allowed: bool) -> None:
        """记下最近见到过的 QQ 群：卡片据此列出"哪些群在跟机器人说话"，可一键加白名单。

        只存在内存里（进程重启就清空）：这是诊断用的观察数据，不是配置，丢了不可惜。
        """
        # 用 epoch 秒而不是 ISO：界面按浏览器本地时区渲染，不用猜服务端时区
        now = time.time()
        row = self._seen_groups.get(openid)
        if row is None:
            self._seen_groups[openid] = {
                "origin": openid, "first_at": now, "last_at": now, "count": 1, "allowed": allowed,
            }
        else:
            row["last_at"] = now
            row["count"] = int(row.get("count", 0)) + 1
            row["allowed"] = allowed
        if len(self._seen_groups) > 20:
            oldest = min(self._seen_groups.values(), key=lambda r: float(r.get("last_at") or 0))
            self._seen_groups.pop(str(oldest.get("origin")), None)

    async def _group_facts_of(self, qq_group: str, *, force: bool = False) -> dict[str, Any] | None:
        """这个 QQ 群的群信息：按"今天的第一条群消息"问一次。

        为什么按天：群名/简介/人数都是低频变化的事实，而收消息是高频的——每条都问等于拿限流
        换一个不变的答案。"今天"按服务端本地日期算，够用且不用跟时区较劲。
        问不到就用手里的旧值（可能没有）：群名拿不到不该挡这条消息。
        force 给"用户刚打开跟随通道群名"这种一次性动作：他正看着界面，等不到下一条消息。
        """
        today = time.strftime("%Y-%m-%d")
        cached = self._group_facts.get(qq_group)
        if cached and cached.get("day") == today and not force:
            return cached
        client = self._client
        if client is None:
            return cached
        try:
            data = await client.group_info(qq_group)
        except Exception as e:
            logger.info(f"取 QQ 群信息失败（不挡消息，下一条再试）：{type(e).__name__}: {e}")
            return cached
        facts = {
            "day": today,
            "at": time.time(),
            "name": str(data.get("group_name") or "").strip(),
            "memo": str(data.get("group_finger_memo") or "").strip(),
            "class_text": str(data.get("group_class_text") or "").strip(),
            "tags": [str(t).strip() for t in (data.get("group_tags") or []) if str(t).strip()],
            "member_num": int(data.get("group_member_num") or 0),
        }
        self._group_facts[qq_group] = facts
        if len(self._group_facts) > GROUP_FACTS_MAX:
            oldest = min(self._group_facts, key=lambda k: float(self._group_facts[k].get("at") or 0))
            self._group_facts.pop(oldest, None)
        return facts

    async def refresh_group_facts(self, group_id: int, *, force: bool = True) -> dict[str, Any] | None:
        """现去通道问一次这个群的群信息（用户在看界面时用：打开资料卡、刚打开"跟随通道群名"）

        只在知道通道侧群标识时才问得了：映射表里有它，或它以前来过消息（默认落点群要知道是哪个群）。
        都没见过就返回 None——那说明这个群还没收到过通道消息，等下一条来自然就有了。
        force=False 时仍按"今天问过就用手里的"：资料卡会被反复打开，不能每次都去问通道。
        """
        origins = [o for o in self._group_map if self._landing_group(o) == int(group_id)]
        if not origins:
            origins = [o for o in self._seen_groups if self._landing_group(o) == int(group_id)]
        for origin in origins:
            facts = await self._group_facts_of(origin, force=force)
            if facts:
                return facts
        return None

    def facts_for_group(self, group_id: int) -> dict[str, Any] | None:
        """这个 Copree 群对应的 QQ 群信息（今天拉到的）：平台给 AI 讲通道规矩时要说明"你在哪个群"

        多个 QQ 群共用一个落点群时取最近问到的那个——同一条消息只回最近来消息的群，口径一致。
        """
        hit = [f for origin, f in self._group_facts.items() if self._landing_group(origin) == int(group_id)]
        return max(hit, key=lambda f: float(f.get("at") or 0)) if hit else None

    def _seen_before(self, msg_id: str) -> bool:
        """相同 msg_id 可能重复推送（官方明说），不去重就会重复回答"""
        if msg_id in self._seen:
            return True
        self._seen.append(msg_id)
        return False

    def observed_full_mode(self) -> bool | None:
        """这个群最近一次观测到的推送模式（None = 本次启动后还没收到过群消息）

        判据是**事件类型**，不是载荷里有没有 mentions：后者是"这条消息带不带成员名单"
        （全量模式下普通消息也有这个键、值是 null），答的不是"这个群在喂全量吗"。
        """
        return self._full_mode[0] if self._full_mode else None

    async def _note_group_mode(self, full: bool) -> None:
        """记下这次观测；模式真的变了就给 AI 的账本投一条通知。

        内存只用于"刚刚是不是这个模式"的快速判断（省掉每条消息都查一次账本），
        真正的幂等以账本为准（见 _deliver_mode_notice）：重启后第一条群消息照样对得上。
        """
        prev = self._full_mode
        self._full_mode = (bool(full), time.time())
        if not self._copree_group_id and not self._group_map:
            return                      # 只做私聊的实例：没有群账本可投
        if prev is not None and prev[0] == bool(full):
            return
        await self._deliver_mode_notice(bool(full))

    async def _deliver_mode_notice(self, full: bool) -> None:
        """模式翻转 → 给这个群的账本追加一条通知（AI 从此知道新规矩）。

        为什么落账本而不是只改文案：通道说明在尾部、每轮重发，但**历史里的旧结论改不掉**
        ——它之前说过"通道不转发别人的 @"这种话，一条通知才是"旧结论作废"的锚点。
        幂等判据也走账本：它是"上次告诉过它什么"的唯一来源，重启不丢、也不会重复投。
        """
        from sqlalchemy import select

        from app.database import async_session
        from app.models.agent import Agent
        from app.services.history import history_service as hs
        from app.services.history.context_sync import context_ref
        from app.services.plugin import api

        async with async_session() as db:
            agent = (await db.execute(
                select(Agent).where(Agent.user_id == self._target_user_id)
            )).scalars().first()
            if agent is None:
                return
            ref = context_ref(group_id=self._copree_group_id)
            # 账本里一条都没说过 = 老规矩（只喂点名），全量模式出现之前本来就是这样
            said = _last_channel_mode(await hs.read(db, agent.id, ref)) or _channel_mode(False)
            if said == _channel_mode(full):
                return
            # 走公共面：条目长什么样（类型 / drop_on_unlock / 已读）由平台定，通道只说事实
            await api.report_notice(
                db, agent, ref, _channel_mode_notice(full),
                ref=f"{_CHANNEL_MODE_REF}{_channel_mode(full)}", transient=False,
            )
            await db.commit()
            logger.info(
                f"QQ 通道[{self.instance}] 群推送模式变化，已给 AI 账本投递通知（{_channel_mode(full)}）"
            )

    async def _on_group_added(self, d: dict) -> None:
        """被拉进群：先认领同名的已有落点群（有就立刻接上），没有就等第一条消息再建。

        为什么不在这一刻就建群：群可能只是被拉进去、一句还没人说，那一刻建出来是空群。
        "接上"真正需要的信号是"这个群里有对话"，那是第一条消息。认领不一样——它是把两台机器人
        眼里的**同一个** QQ 群并到一个 Copree 群上（群标识是各 app 的 openid，只能按群名对号）。
        """
        qq_group = str(d.get("group_openid") or "")
        if not qq_group:
            return
        self._note_group(qq_group, allowed=(not self._allow) or qq_group in self._allow)
        if self._allow and qq_group not in self._allow:
            logger.debug(f"QQ 群 {qq_group} 不在白名单，忽略")
            return
        from app.utils.pure.channel_landing import auto_create_wanted

        if not auto_create_wanted(enabled=self._auto_create, group_map=self._group_map, origin=qq_group):
            return
        facts = await self._group_facts_of(qq_group)
        name = str((facts or {}).get("name") or "")
        landed = await self._ensure_landing_group(qq_group, name=name, create=False)
        if landed:
            logger.info(f"QQ 群 …{qq_group[-6:]}（{name or '未知名'}）已加入，落点群 #{landed}")
        else:
            logger.info(f"QQ 群 …{qq_group[-6:]}（{name or '未知名'}）已加入，暂无对应落点群，等第一条消息")


    # ── 出站：AI 的回复 → QQ ───────────────────────────────────
    # ── 落点与路由 ─────────────────────────────────────────────
    def _landing_group(self, qq_group: str) -> int:
        """这个 QQ 群落到哪个 Copree 群：先查映射表，没有就落到实例的默认落点群"""
        from app.utils.pure.channel_landing import landing_group

        return landing_group(
            group_map=self._group_map, default_group_id=self._copree_group_id, origin=qq_group
        )

    async def _landing_suggestions(self, origins: list[str]) -> dict[str, dict[str, Any]]:
        """没落点的 QQ 群 → "可能是同一个群"的候选（卡片显示 + 一键合并用）。

        只对**没指定过落点**的群算：指定过的以人的选择为准，算了也没用。判定本身在平台侧
        （channel.landing_suggestion：同一个主人的别的机器人把同名的通道群接在哪个 Copree 群）；
        插件只把结果塞进状态，算不出来就空着——状态上报不该被它拖住。
        """
        out: dict[str, dict[str, Any]] = {}
        if not self._auto_create:
            return out
        from app.database import async_session
        from app.services.plugin.channel import agent_id_of, landing_suggestion

        agent_id = agent_id_of(self.instance)
        if agent_id is None:
            return out
        # 只看卡片真会画出来的那几个（前 6 行）：认领候选要查库、可能还要问一次通道，
        # 不是当前这屏要看的群就别算了
        for origin in origins[:6]:
            facts = self._group_facts.get(origin)
            if not str((facts or {}).get("name") or ""):
                # 重启后内存里的群信息是空的：现问一次（按天缓存，和收消息那条路共用同一份）
                facts = await self._group_facts_of(origin, force=True)
            name = str((facts or {}).get("name") or "")
            if not name:
                continue
            current = int(self._group_map.get(origin) or self._copree_group_id or 0)
            try:
                async with async_session() as db:
                    found = await landing_suggestion(
                        db, agent_id=agent_id, name=name, exclude_group_id=current,
                    )
            except Exception as e:
                logger.debug(f"认领候选算不出来（非致命）: {type(e).__name__}: {e}")
                continue
            if found:
                out[origin] = found
        return out

    async def _reload_group_map(self) -> dict[str, int]:
        """现读一份配置里的 group_map。

        为什么必须现读：卡片上改落点、在别处把两个群合并，都是写配置——实例是启动时读进内存的，
        不重读就要等重启才生效（表现是"刚合并过去，消息又落回老群/又建了个新群"）。
        """
        from app.database import async_session
        from app.services.plugin.config import get_config

        try:
            async with async_session() as db:
                cfg = await get_config(self.id, self.instance, db=db)
        except Exception as e:
            logger.debug(f"重读通道配置失败（继续用内存里的）: {type(e).__name__}: {e}")
            return self._group_map
        self._group_map = _parse_group_map(cfg.get("group_map"))
        return self._group_map

    async def _resolve_landing(self, qq_group: str, *, name: str = "") -> int:
        """落点：映射表 → 认领/新建（勾选开时）→ 默认落点群。

        自动接住（配置键 auto_create_group，默认开）只对"本来没有落点的群"生效：人显式指定过
        落点的群不受影响。**落点留空也算接**——"在 QQ 里把它拉进群，Copree 侧自己接上"就是这个
        开关的承诺；要只做私聊就把它关掉（那时退回默认落点群，没有默认落点就整群忽略）。
        接住失败不丢消息：退回默认落点群。
        """
        from app.utils.pure.channel_landing import auto_create_wanted

        if auto_create_wanted(enabled=self._auto_create, group_map=self._group_map, origin=qq_group):
            landed = await self._ensure_landing_group(qq_group, name=name)
            if landed:
                return landed
        return self._landing_group(qq_group)

    async def _ensure_landing_group(self, qq_group: str, *, name: str = "", create: bool = True) -> int:
        """给这个 QQ 群安排落点（认领同名的已有群；create 时还可以新建一个并把这个 AI 加进去）。

        加锁的理由是"认领/建群真的会落库"：同一条消息的两种事件、同群连发的两条消息都可能
        同时走到这儿，重复跑就是两个空群。做完立刻写进内存映射，后续直接命中。
        """
        lock = self._landing_locks.setdefault(qq_group, asyncio.Lock())
        async with lock:
            # 先现读一份配置：卡片上刚指过的落点（或刚合并过去的）要立刻生效，别等重启实例
            known = await self._reload_group_map()
            if qq_group in known:
                return int(known[qq_group])
            from app.database import async_session
            from app.services.plugin.channel import agent_id_of, resolve_landing

            agent_id = agent_id_of(self.instance)
            if agent_id is None:
                return 0
            try:
                async with async_session() as db:
                    group_id = await resolve_landing(
                        db, plugin_id=self.id, agent_id=agent_id, origin=qq_group,
                        name=name, create=create,
                    )
            except Exception as e:
                self.last_error = f"自动接住通道群失败：{type(e).__name__}: {e}"
                logger.warning(
                    f"QQ 群 …{qq_group[-6:]} 自动接住失败，退回默认落点群：{e}", exc_info=True
                )
                return 0
            self._group_map[qq_group] = int(group_id)
            logger.info(f"QQ 群 …{qq_group[-6:]} 首次说话 → 落点群 #{group_id}")
            return int(group_id)

    async def _sync_group_name(self, db: Any, group_id: int, qq_facts: dict[str, Any] | None) -> None:
        """落点群的名字对齐成 QQ 那边的真名——只在群设置里打开"跟随通道群名"的群上做。

        一个 AI 接两个 QQ 群时，两个落点群默认都叫「<AI 名> 的群」，界面和 AI 都只能靠 id 分辨；
        对齐真名把"哪个是哪个"还回来。开关由群主在群设置里决定：群名是他的东西，
        通道不能自己往上写（打开时平台那边也会立刻对齐一次，这里只管之后通道侧改名的情况）。
        """
        name = str((qq_facts or {}).get("name") or "").strip()
        if not name:
            return
        from app.models.group import Group

        group = await db.get(Group, group_id)
        if group is None or not bool(getattr(group, "name_from_channel", False)):
            return
        clean = name[:100]
        if clean == str(group.name or "").strip():
            return
        group.name = clean
        logger.info(f"落点群 #{group_id} 的名字对齐成 QQ 群名")

    def _quota_gate(self):
        """额度闸门：绑定哪个 AI、哪种通道就记着那层关系；同步设置与准入都走它。

        设置解析、判定、异常兜底都在 app.services.channel.quota_gate —— 两个 QQ 插件
        各写一遍同样的东西，迟早会在其中一份里漏掉一个 if。
        """
        if self._quota is None:
            from app.services.channel.quota_gate import QuotaGate

            self._quota = QuotaGate(self._target_agent_id, self.channel_kind, logger)
        return self._quota

    async def _send_quota_notice(self, *, kind: str, text: str, qq_group: str = "", openid: str = "",
                                 channel_msg_id: str = "") -> None:
        """额度说明走被动回复（群 5 分钟 / 私聊 60 分钟窗口）；发不出去就算了——它只是解释"""
        client = self._client
        if client is None or not text:
            return
        try:
            if kind == "group" and qq_group:
                await client.send_group(qq_group, text, msg_id=channel_msg_id or None)
            elif kind == "dm" and openid:
                await client.send_c2c(openid, text, msg_id=channel_msg_id or None)
        except Exception as e:
            logger.info(f"额度说明没发出去（{kind}）：{str(e)[:120]}")

    async def _wait_for_slot(self, key: str) -> bool:
        """频控：单关系 20/qpm、Bot 60/qpm。等不到就放弃（回复有时效，排队反而更糟）"""
        for _ in range(6):
            now = time.time()
            bucket = self._sent.setdefault(key, deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            while self._bot_sent and now - self._bot_sent[0] > 60:
                self._bot_sent.popleft()
            if len(bucket) < GROUP_PER_MINUTE and len(self._bot_sent) < BOT_PER_MINUTE:
                bucket.append(now)
                self._bot_sent.append(now)
                return True
            await asyncio.sleep(1)
        return False
