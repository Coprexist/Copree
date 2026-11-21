"""
QQ 通道 — 把 Copree 的 AI 接入 QQ（官方 QQ 机器人 API v2）

**多实例插件**：一个插件、多份配置——每份配置是一个 QQ 机器人（一个 AppID），
背后绑定一个 Copree 的 AI。所以「每个 AI 一个自己的机器人」= 这个插件下的多个实例，
而不是多个插件。

一条链路，两头都不另立门户：
  收：WebSocket 长连网关（op10 hello → op2 identify → op1 心跳），只订 GROUP_AND_C2C_EVENT；
      群里被 @、或有人私聊机器人 → 落进 Copree（群消息进绑定的群，私信进与该 AI 的私信会话）
  发：AI 的回复 → app/chat/outbound.py 的出口分发 → REST 发回 QQ（群消息 / 私聊消息）

为什么坚持官方 API：第三方协议端（NapCat / mirai / go-cqhttp 一类）违反用户协议、有风控封号风险，
只适合自用；要对外提供的通道必须走官方机器人。

凭据从插件配置读（加密落库、接口不回显），不写环境变量、不进日志。

已知限制（v1，先写在这里，免得被当成 bug）：
- 图片双向通了：收——QQ 的图下下来存成站内附件（AI 的多模态上下文拿得到字节）；
  发——走 `msg_type=7` 图片消息，与正文格式无关，纯文本模式也发得出去。
  没做的是语音（要转 silk）、视频（要转码）、普通文件（要另走 file_name）：
  它们在正文里仍是 `[语音]`/`[文件]` 占位，AI 不会瞎猜内容
- 多个 QQ 群共用一个 Copree 群时，群回复回到**最近一次来消息**的那个 QQ 群
- 私聊不校验"是不是 AI 的创造者"：QQ 侧没有身份锚，认人要靠绑定流程（下一步）
- 引用目前只做群消息（双向：QQ 引用 → 站内引用块，站内引用 → message_reference）。私聊既没记
  REFIDX 也没实测过 c2c 的引用字段，所以私聊里的引用仍是平文本

代码按职责分在 `spec.py`（声明与配置）、`gateway.py`（连接与协议）、`format.py`（纯字符串）、
`media.py`（媒体口径：取图、mime → file_type、大小上限），
装配与生命周期留在本文件：类由四个 mixin 拼起来——收消息在 `inbound.py`、发消息在
`outbound.py`、通道身份（建锚点、认亲、影子自愈）在 `identity.py`，共用常量与纯函数在
`common.py`。
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from typing import Any

from app.services.plugin.api import ServicePlugin, service
from app.services.plugin.tasks import PluginTasks
from app.utils.pure.channel_landing import flag_on

from .common import (
    DEDUP_SIZE,
    _msg_type_of,
    _split_list,
    logger,
)
from .format import _parse_group_map, parse_channel_origin
from .gateway import GatewayMixin, QqClient
from .identity import IdentityMixin
from .inbound import InboundMixin
from .outbound import OutboundMixin
from .spec import SERVICE


@service(**SERVICE)
class QqChannelPlugin(IdentityMixin, InboundMixin, OutboundMixin, GatewayMixin, ServicePlugin):
    self_testable = True

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        # 生效中的配置（start() 时读取；改配置后重启这个实例生效）
        self._copree_group_id = 0
        self._target_agent = ""
        self._target_agent_id = 0
        self._target_user_id = 0
        # 额度闸门（绑定目标后按新主人重建，见 _quota_gate）
        self._quota: Any | None = None
        self._allow: set[str] = set()
        self._dm_policy = "pairing"
        # 正文格式：None = 默认（先 Markdown，没权限降级纯文本）；有值 = 固定成它
        self._msg_type: int | None = None
        # 引用回复（独立维度）：回复 QQ 来消息时带 message_reference
        self._quote_replies = True
        # 配对码通知节流：同一个人反复私聊时别把码刷屏（60 秒最多提醒一次）
        self._pair_notified: dict[str, float] = {}
        # 回复路由：QQ 群 openid → 那条会话的被动回复凭据（含 msg_id/seq/ts）。
        # 一个机器人可以在多个 QQ 群里，回复必须回到"触发它的那条消息所在的群"，
        # 所以按 openid 分槽；原来按 Copree 群一个槽，后说话的群会把前一个顶掉。
        # 路由里带着 msg_id/seq/ts：被动回复要用「触发它的那条消息」的 id，
        # 且同一个 msg_id 只能用一次（官方：相同 msg_id+msg_seq 重复发送会失败），
        # 所以这里存的是活字典，发送时原地递增 seq。
        self._routes: dict[str, dict[str, Any]] = {}
        # QQ 群 → Copree 群 的映射（配置键 group_map）；没映射的群落到默认落点群
        self._group_map: dict[str, int] = {}
        # 首次说话的 QQ 群自动建落点群（配置键 auto_create_group，默认开）：
        # 开了以后没映射的群各自建一个群，而不是全挤进默认落点群
        self._auto_create = True
        # 每个 QQ 群建群只建一次：同群的两条消息可能同时走到这一步（建完就写进 group_map）
        self._landing_locks: dict[str, asyncio.Lock] = {}
        # OrderedDict：满了丢最早建立的那条（见 _remember_dm_route）
        self._dm_route: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # 后台回复任务：保引用 + 停止前 drain（见 services/plugin/tasks.py）
        self._replies = PluginTasks()
        # 会话 → 发送锁：msg_seq 是"按那条来消息"发的，同一个会话的两条回复必须排队算号
        self._send_locks: dict[str, asyncio.Lock] = {}
        # 会话 → 该会话最后一条回复任务：同一会话的回复串成一条链，前一条发完才发下一条
        self._send_tail: dict[str, asyncio.Task] = {}
        self._seen: deque[str] = deque(maxlen=DEDUP_SIZE)
        # 已落库的消息：msg_id → (站内消息 id, 落库时的正文)。
        # 同一条消息的另一种事件（@模式 ↔ 全量模式）更全时，用它把正文补上
        self._delivered: dict[str, tuple[int, str]] = {}
        self._delivered_order: deque[str] = deque()
        # 最近见到过的 QQ 群（诊断用，内存态）：卡片上要能看见群 openid 才好填白名单
        self._seen_groups: dict[str, dict[str, Any]] = {}
        # 今天拉到的群信息（QQ 群 openid → 群名/简介/分类/标签/人数）：群名要用来对齐落点群名、
        # 也得给卡片和 AI 看，但它是低频变化的事实——按"今天第一条群消息"问一次，见 _group_facts_of
        self._group_facts: dict[str, dict[str, Any]] = {}
        # "这条发不到 QQ"的站内提示：按目标记 (已经提示过几次, 上次提示时间)。
        # 失败要看得见，但不能刷屏——间隔按次数指数回避（5 分钟 → 翻倍 → 最高 5 天）
        self._fail_notice: dict[str, tuple[int, float]] = {}
        # 补拉到的 union_openid（群:成员 → (时刻, union)）：成员接口只有 30 QPM，不能每条消息都问
        # 单条上限实测出来的两侧边界：最长发成功的一段 / 最短被拒的一段（0 = 还没测到）
        self._max_chars = 0
        self._too_long = 0
        self._union_cache: dict[str, tuple[float, str]] = {}
        self._union_order: deque[str] = deque()
        self._member_info_denied = False
        # 这个群的推送模式（最近一次观测到的事件类型 + 时间）：True=全量、False=只喂点名。
        # None = 本次启动后还没收到过群消息。通道说明据此选文案；给 AI 的持久记录在账本里
        self._full_mode: tuple[bool, float] | None = None
        self._sent: dict[str, deque[float]] = {}
        self._bot_sent: deque[float] = deque()
        # 连接状态与会话凭据由连接层管理
        self._init_gateway()
        # 运行状态（只报事实）
        self.bot_name = ""
        self.replies = 0
        self.dm_replies = 0
        self.recalls = 0
        self.last_error = ""
        self.started_at = 0.0


    # ── 生命周期 ───────────────────────────────────────────────
    async def _recent_groups(self) -> list[dict[str, Any]]:
        """最近收到过消息的 QQ 群：**以消息行为准**（重启不丢），内存里的观测补更新的一次。

        为什么不能只用内存那份：它进程重启就清空；而卡片要靠它列群、并给「可能是同一个群」提供入口，
        丢了就等于重启后这个功能消失，要等群里再说话才回来。"群里说过话"这件事，消息行上的
        channel_origin 已经记着，不必再存第二份账本。
        """
        from sqlalchemy import func, select

        from app.database import async_session
        from app.models.message import Message

        rows: dict[str, dict[str, Any]] = {}
        try:
            async with async_session() as db:
                found = (await db.execute(
                    select(Message.channel_origin, func.count(Message.id), func.max(Message.created_at))
                    .where(Message.channel_origin.like(f"{self.channel_kind}:{self.instance}:%"))
                    .group_by(Message.channel_origin)
                )).all()
        except Exception as e:
            logger.debug(f"按消息行取最近群失败（非致命）: {type(e).__name__}: {e}")
            found = []
        for raw, count, last_at in found:
            parsed = parse_channel_origin(self.channel_kind, raw)
            origin = parsed[1] if parsed else ""
            if not origin:
                continue
            rows[origin] = {
                "origin": origin, "count": int(count or 0),
                "last_at": float(last_at.timestamp()) if last_at else 0.0,
                "allowed": bool((not self._allow) or origin in self._allow),
            }
        for origin, seen in self._seen_groups.items():   # 内存观测：更近的时刻、更新的条数
            row = rows.setdefault(origin, {
                "origin": origin, "count": 0, "last_at": 0.0,
                "allowed": bool(seen.get("allowed", True)),
            })
            row["count"] = max(int(row["count"]), int(seen.get("count") or 0))
            row["last_at"] = max(float(row["last_at"]), float(seen.get("last_at") or 0))
        # 已经指过落点的群也算"我的群"：落点在配置里（落库的），重启后照样列得出来——
        # 落点就是这台机器人确实接过它的凭证，比"见过一条消息"更硬（count=0 表示还没收到过）
        for origin in self._group_map:
            rows.setdefault(origin, {
                "origin": origin, "count": 0, "last_at": 0.0,
                "allowed": bool((not self._allow) or origin in self._allow),
            })
        return sorted(rows.values(), key=lambda r: float(r.get("last_at") or 0), reverse=True)[:20]

    async def get_status(self) -> dict:
        running = self._task is not None and not self._task.done()
        # 现读一次落点：卡片上刚改的落点、刚合并过去的，立刻看得见（不用重启实例）
        await self._reload_group_map()
        recent_rows = await self._recent_groups()
        suggestions = await self._landing_suggestions([str(r["origin"]) for r in recent_rows])
        return {
            "installed": True,
            "running": running,
            "connected": self.connected,
            "bot_name": self.bot_name,
            "target_agent": self._target_agent or None,
            "copree_group_id": self._copree_group_id or None,
            "dm_policy": self._dm_policy,
            # 最近收到过消息的 QQ 群：openid + 时间 + 条数 + 白名单 + 落在哪个 Copree 群 + 今天的群名
            "recent_groups": [
                {
                    **row,
                    "copree_group_id": self._landing_group(str(row.get("origin") or "")) or None,
                    "mapped": str(row.get("origin") or "") in self._group_map,
                    # 今天拉到的群信息（没拉到就是空串/0）：卡片靠它显示真名、预填新建落点群的名字
                    "name": str(
                        (self._group_facts.get(str(row.get("origin") or "")) or {}).get("name") or ""
                    ),
                    "member_num": int(
                        (self._group_facts.get(str(row.get("origin") or "")) or {}).get("member_num") or 0
                    ),
                    # "可能是同一个群"：同一个主人的别的机器人把同名的群接在哪儿（只读候选，见
                    # channel.landing_suggestion）；卡片拿它画子列 + 一键合并
                    "possible_group": suggestions.get(str(row.get("origin") or "")),
                }
                for row in recent_rows
            ],
            # 群 → Copree 群 的映射（卡片据此让每个群选落点）与"还没指定的群"
            "group_map": dict(self._group_map),
            "unmapped_groups": sorted(o for o in self._seen_groups if o not in self._group_map),
            # 卡片上的"一键加白名单"该往哪个字段写：插件自己说，平台不猜字段名
            "recent_field": "qq_group_allowlist",
            "routed_groups": sorted(self._routes),
            "dm_sessions": len(self._dm_route),
            # 实测出来的单条上限两侧：最长发成功的一段 / 最短被拒的一段（0 = 还没测到）
            "text_limit": self._max_chars or None,
            "text_too_long": self._too_long or None,
            "replies_sent": self.replies,
            "dm_replies_sent": self.dm_replies,
            "recalls_sent": self.recalls,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "last_error": self.last_error,
        }

    async def start(self) -> bool:
        cfg = await self.config()
        app_id = str(cfg.get("app_id") or "").strip()
        secret = str(cfg.get("client_secret") or "").strip()
        agent_id = str(cfg.get("target_agent_id") or "").strip()
        missing = [
            label for label, value in (("AppID", app_id), ("ClientSecret", secret), ("AI", agent_id))
            if not value
        ]
        if missing:
            self.last_error = f"未配置：{'、'.join(missing)}"
            logger.warning(f"QQ 通道[{self.instance}] 启动中止：{self.last_error}")
            return False

        group_raw = str(cfg.get("copree_group_id") or "").strip()
        try:
            self._copree_group_id = int(group_raw) if group_raw else 0
        except ValueError:
            self.last_error = f"Copree 群 ID 不是数字：{group_raw}"
            return False

        try:
            await self._bind_target(agent_id)
        except Exception as e:
            # 起不来要给出人话原因：管理页会把它显示在状态里
            self.last_error = str(e)
            logger.warning(f"QQ 通道[{self.instance}] 启动中止：{self.last_error}")
            return False

        self._allow = _split_list(cfg.get("qq_group_allowlist"))
        self._group_map = _parse_group_map(cfg.get("group_map"))
        self._auto_create = flag_on(cfg.get("auto_create_group"), default=True)
        self._dm_policy = (str(cfg.get("dm_policy") or "pairing").strip().lower() or "pairing")
        self._msg_type = _msg_type_of(cfg.get("body_format"))
        self._quote_replies = flag_on(cfg.get("quote_replies"), default=True)
        await self._quota_gate().sync_policy(cfg)
        self._client = QqClient(app_id, secret)
        self.last_error = ""
        self.connected = False
        self._session_id, self._seq = "", 0
        self._ready = False
        self.started_at = time.time()

        from app.chat.outbound import register_sink

        # 出口注册名用 **key（带实例）**：id 是插件类型，两个实例同名会互相顶掉——
        # 2026-09-25 线上实测：新绑的第二个 QQ 通道把第一个的出口覆盖，群 64 的 AI 回复被静默丢弃
        self._sink_handle = register_sink(
            self.key, group=self._outbound_sink, dm=self._dm_outbound_sink, revoke=self._revoke_sink
        )
        self._task = asyncio.create_task(self._supervise())
        logger.info(
            f"QQ 通道[{self.instance}] 已启动：AI={self._target_agent}"
            f"，群={self._copree_group_id or '未绑定'}，私聊={self._dm_policy}"
        )
        return True

    async def stop(self) -> bool:
        from app.chat.outbound import unregister_sink

        unregister_sink(getattr(self, "_sink_handle", None) or self.key)
        self._routes.clear()
        self._dm_route.clear()
        self._fail_notice.clear()
        self._union_cache.clear()
        self._union_order.clear()
        self._delivered.clear()
        self._delivered_order.clear()
        # 先等在飞的回复发完：紧接着 aclose() 会把它们的 HTTP 客户端收走
        await self._replies.drain()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass                     # 我们自己取消的，就是正常收尾
            except Exception as e:
                # 崩过一次才退出的 supervise 要和"被取消"分开：不能静默
                logger.warning(f"QQ 通道[{self.instance}] supervise 异常退出：{type(e).__name__}: {e}")
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
        # 显式停掉就不能再续旧会话了：下次启动是一次新的鉴权
        self._session_id, self._seq = "", 0
        self.started_at = 0.0
        logger.info(f"QQ 通道[{self.instance}] 已停止")
        return True

    async def _bind_target(self, agent_id: str) -> None:
        """认下"这个机器人背后是哪个 AI"：只按 id 认，名字只是显示。

        两边共用一个身份：群里 @ 的是它、私聊聊的也是它——不存在"群一个 AI、私聊另一个"的错位。
        """
        from app.database import async_session
        from app.services.plugin.target_agent import bind, remember

        async with async_session() as db:
            bound = await bind(db, agent_id=agent_id, group_id=self._copree_group_id)
        self._target_agent = bound.name
        self._target_agent_id = bound.agent_id
        self._target_user_id = bound.user_id
        self._quota = None
        await remember(self.id, self.instance, bound)

    async def environment(self, *, origin: str | int) -> dict[str, Any] | None:
        """这个 Copree 群在 QQ 侧的环境事实（纯读内存，零新增网络往返）

        契约要求每轮调用廉价：这里只取「今天拉到的群信息」与观测到的推送模式。
        真正的网络往返发生在收到群消息时（当天第一条）与用户手动同步时；
        refresh_group_facts 更新 _group_facts 之后，下一轮读到的就是新值。
        """
        from app.utils.pure.channel_landing import group_id_of_session

        group_id = group_id_of_session(origin)
        if group_id is None:
            return None
        facts = self.facts_for_group(group_id)
        if not facts:
            return None
        env: dict[str, Any] = {"channel": "qq"}
        name = str(facts.get("name") or "").strip()
        if name:
            env["origin_name"] = name
        num = int(facts.get("member_num") or 0)
        if num:
            env["member_num"] = num
        memo = str(facts.get("memo") or "").strip()
        if memo:
            env["origin_memo"] = memo
        mode = self.observed_full_mode()
        if mode is not None:
            env["full_mode"] = bool(mode)
        return env

    @property
    def channel_kind(self) -> str:
        """外部身份的类别名：读本插件 manifest 里的 channel.kind（插件不自己写死字符串）"""
        kind = getattr(self, "_channel_kind", "")
        if not kind:
            from app.services.plugin import catalog

            kind = self._channel_kind = catalog.channel_kind(self.id)
        return kind

    async def self_test(self) -> dict[str, Any] | None:
        """通道自测：在最近那条路由上真发一条，把腾讯的原始响应带回来

        为什么只能在本进程里点：被动回复凭据（msg_id/seq）只活在内存的路由表里，
        换个进程就只剩一份过期的副本——这也是它不能走"插件配置"那条接口的原因。

        探针一句话里并排**三种写法**，要问的是腾讯那个问题：纯文本 / Markdown 各认哪种内联 @。
        内联 @ 后面出现蓝色的对方 = 认；显示成原文尖括号 = 不认（那就退成纯文本的 @名字）。
        三种都要问的原因：腾讯**入站**用的写法是 `<@openid>`（没有那个 `!`，2026-09-26 真机原文），
        而我们验过可点的是 Markdown 下的 `<@!openid>`——纯文本认哪种，只有真机知道。
        """
        route, kind = self._live_route()
        if route is None:
            return {"sent": False, "reason": "还没有收到过消息：先去那个 QQ 会话里说一句（群里 @ 一次机器人）"}
        openid = str(route.get("peer_openid") or "") if kind == "group" else str(route.get("qq") or "")
        name = str(route.get("peer_name") or "")
        probes = [p for p in (
            f"内联@：<@!{openid}>" if openid else "",
            f"无叹号@：<@{openid}>" if openid else "",
            f"纯文本：@{name}" if name else "",
        ) if p]
        text = "（通道自测，请忽略）" + " ／ ".join(probes)
        try:
            # 也按配置的正文格式发：点一次自测＝用当前配置的真实发一条
            result = await self._deliver(route, kind, text, passive_only=True)
        except Exception as e:
            return {"sent": False, "target": kind, "reason": f"{type(e).__name__}: {e}"}
        return {"sent": True, "target": kind, "text": text, **result}
