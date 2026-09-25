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
- 富媒体不回传；收到的图片/语音/文件只转成文字占位，让 AI 知道"有人发了东西"
- 多个 QQ 群共用一个 Copree 群时，群回复回到**最近一次来消息**的那个 QQ 群
- 私聊不校验"是不是 AI 的创造者"：QQ 侧没有身份锚，认人要靠绑定流程（下一步）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

import httpx

from app.services.plugin.api import ServicePlugin, service

logger = logging.getLogger(__name__)

# ── 官方接口（见 QQ 机器人文档「接口调用与鉴权」）──
API_BASE = "https://api.bot.qq.com"
TOKEN_PATH = "/app/getAppAccessToken"
GATEWAY_PATH = "/gateway/bot"
TOKEN_REFRESH_MARGIN = 60          # 到期前 60 秒换新（官方保证这 60 秒内新旧都有效）

# 事件订阅位：GROUP_AND_C2C_EVENT(1<<25) 覆盖群 @ 与单聊消息。只订需要的，别把全量事件拉回来
INTENTS = 1 << 25

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10

# 被动回复窗口（官方：群 5 分钟/5 次，单聊 60 分钟/4 次）
GROUP_WINDOW, GROUP_MAX = 300, 5
DM_WINDOW, DM_MAX = 3600, 4
GROUP_PER_MINUTE = 20              # 单群频控 20/qpm（主动消息）
BOT_PER_MINUTE = 60                # Bot 维度 60/qpm
PAIR_NOTIFY_INTERVAL = 60           # 陌生人反复私聊时，配对码最多 60 秒提醒一次
TEXT_LIMIT = 1000                  # 文本超长直接截断，否则整条会被拒
DEDUP_SIZE = 500                   # 相同 msg_id 可能重复推送，按 id 去重
BACKOFF_MAX = 60.0


class QqClient:
    """官方 API v2 的最小客户端：取凭证 / 拿网关 / 发消息。"""

    def __init__(self, app_id: str, client_secret: str) -> None:
        self.app_id = app_id
        self._secret = client_secret
        self._token = ""
        self._expire_at = 0.0
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=API_BASE, timeout=15.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def token(self) -> str:
        """取 access_token（带缓存；过期前 60 秒自动换新）"""
        if self._token and time.time() < self._expire_at - TOKEN_REFRESH_MARGIN:
            return self._token
        http = await self._client()
        res = await http.post(TOKEN_PATH, json={"appId": self.app_id, "clientSecret": self._secret})
        data = res.json() if res.content else {}
        token = str(data.get("access_token") or "")
        if res.status_code != 200 or not token:
            # 不回显凭据本身，只报服务端说了什么
            raise RuntimeError(f"取 access_token 失败（HTTP {res.status_code}）：{data}")
        self._token = token
        self._expire_at = time.time() + float(data.get("expires_in") or 7200)
        return token

    async def gateway(self) -> str:
        http = await self._client()
        res = await http.get(GATEWAY_PATH, headers={"Authorization": f"QQBot {await self.token()}"})
        data = res.json() if res.content else {}
        url = str(data.get("url") or "")
        if res.status_code != 200 or not url:
            raise RuntimeError(f"取网关地址失败（HTTP {res.status_code}）：{data}")
        return url

    async def _post(self, path: str, body: dict) -> dict:
        http = await self._client()
        res = await http.post(
            path, json=body, headers={"Authorization": f"QQBot {await self.token()}"}
        )
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("code"):
            raise RuntimeError(f"发 QQ 消息失败（HTTP {res.status_code}）：{data}")
        return data

    async def _send_rich(
        self, path: str, content: str, msg_id: str | None, msg_seq: int
    ) -> dict:
        """先按 Markdown 发，机器人没有 MD 权限时退回纯文本。

        MD 权限是**机器人账号维度**的（腾讯那边开通），同一个平台里有的号有、有的没有；
        所以在同一次发送里降级，而不是在平台配置里写死——不然每接一个号都要先问一遍。
        """
        from app.utils.text import plainify_markdown

        extra: dict[str, Any] = {"msg_id": msg_id, "msg_seq": msg_seq} if msg_id else {}
        try:
            return await self._post(path, {"msg_type": 2, "markdown": {"content": content[:TEXT_LIMIT]}, **extra})
        except RuntimeError as e:
            # 只对"没权限"这类降级；其它错误（频控、参数错）照旧抛出去，别吞
            if not any(word in str(e) for word in ("无权限", "权限", "markdown", "msg_type")):
                raise
            logger.info("机器人没有 Markdown 权限，这条退回纯文本：%s", str(e)[:120])
        return await self._post(path, {"msg_type": 0, "content": plainify_markdown(content)[:TEXT_LIMIT], **extra})

    async def send_group(
        self, group_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1
    ) -> dict:
        """发群消息；带 msg_id = 被动回复（5 分钟内、最多 5 次），不带 = 主动消息（有频控）"""
        return await self._send_rich(f"/v2/groups/{group_openid}/messages", content, msg_id, msg_seq)

    async def send_c2c(
        self, user_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1
    ) -> dict:
        """发私聊消息（被动回复 60 分钟内、最多 4 次）"""
        return await self._send_rich(f"/v2/users/{user_openid}/messages", content, msg_id, msg_seq)


@service(
    name="QQ 通道",
    description="一个机器人接一个 AI：群里被 @、或私聊机器人，都进 Copree；AI 的回复发回 QQ",
    multi_instance=True,
    config_schema={
        "app_id": {
            "type": "string", "title": "AppID", "required": True,
            "description": "QQ 开放平台 → 机器人管理页获取",
            "description_en": "From the QQ Open Platform bot settings page",
            "description_ja": "QQオープンプラットフォームのボット管理ページで取得します",
        },
        "client_secret": {
            "type": "string", "title": "ClientSecret", "secret": True, "required": True,
            "description": "只在这里填，加密落库、接口不回显",
            "description_en": "Enter it here only; stored encrypted and never echoed back",
            "description_ja": "ここにのみ入力します。暗号化して保存し、APIは返しません",
        },
        "target_agent": {
            "type": "string", "title": "这个机器人是谁（AI 名字）", "required": True, "managed": True,
            "description": "由平台按「这个 AI 的页面」自动填，不需要手工填；群里 @机器人 就等于在群里 @它",
        },
        "copree_group_id": {
            "type": "string", "title": "接入的 Copree 群 ID",
            "title_en": "Landing Copree group ID", "title_ja": "接続先 Copree グループID",
            "description": "群消息落到哪个群；留空 = 只做私聊，不接群",
        },
        "qq_group_allowlist": {
            "type": "string", "title": "允许接入的 QQ 群",
            "title_en": "Allowed QQ groups", "title_ja": "許可する QQ グループ",
            "description": "群 openid，多个用逗号分隔；留空 = 不限制",
            "description_en": "Group openids, comma separated; empty = no limit",
            "description_ja": "グループ openid をカンマ区切り。空欄＝制限なし",
        },
        "dm_policy": {
            "type": "string", "title": "私聊策略（pairing / owner / open / off）",
            "description": "默认 pairing：陌生人私聊只会收到一个配对码，批准之后才能跟 AI 说话；"
                           "owner 只认已配对的人（陌生人静默忽略）；open 谁都能聊；off 关闭私聊",
        },
    },
)
class QqChannelPlugin(ServicePlugin):
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._client: QqClient | None = None
        self._heartbeat: asyncio.Task | None = None
        # 生效中的配置（start() 时读取；改配置后重启这个实例生效）
        self._copree_group_id = 0
        self._target_agent = ""
        self._target_user_id = 0
        self._allow: set[str] = set()
        self._dm_policy = "pairing"
        # 配对码通知节流：同一个人反复私聊时别把码刷屏（60 秒最多提醒一次）
        self._pair_notified: dict[str, float] = {}
        # 回复路由：群 → 最近一次来消息的 QQ 群；私信会话 → 那条私聊（含被动回复凭据）
        # 路由里带着 msg_id/seq/ts：被动回复要用「触发它的那条消息」的 id，
        # 且同一个 msg_id 只能用一次（官方：相同 msg_id+msg_seq 重复发送会失败），
        # 所以这里存的是活字典，发送时原地递增 seq。
        self._route: dict[int, dict[str, Any]] = {}
        self._dm_route: dict[str, dict[str, Any]] = {}
        self._seen: deque[str] = deque(maxlen=DEDUP_SIZE)
        # 最近见到过的 QQ 群（诊断用，内存态）：卡片上要能看见群 openid 才好填白名单
        self._seen_groups: dict[str, dict[str, Any]] = {}
        self._sent: dict[str, deque[float]] = {}
        self._bot_sent: deque[float] = deque()
        # 运行状态（只报事实）
        self.connected = False
        self.bot_name = ""
        self.replies = 0
        self.dm_replies = 0
        self.last_error = ""
        self.started_at = 0.0

    # ── 生命周期 ───────────────────────────────────────────────
    async def get_status(self) -> dict:
        running = self._task is not None and not self._task.done()
        return {
            "installed": True,
            "running": running,
            "connected": self.connected,
            "bot_name": self.bot_name,
            "target_agent": self._target_agent or None,
            "copree_group_id": self._copree_group_id or None,
            "dm_policy": self._dm_policy,
            # 最近收到过消息的 QQ 群：openid + 时间 + 条数 + 是否已被白名单放行
            "recent_groups": sorted(
                self._seen_groups.values(), key=lambda r: float(r.get("last_at") or 0), reverse=True
            ),
            # 卡片上的"一键加白名单"该往哪个字段写：插件自己说，平台不猜字段名
            "recent_field": "qq_group_allowlist",
            "routed_groups": sorted({r.get("qq", "") for r in self._route.values() if r.get("qq")}),
            "dm_sessions": len(self._dm_route),
            "replies_sent": self.replies,
            "dm_replies_sent": self.dm_replies,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "last_error": self.last_error,
        }

    async def start(self) -> bool:
        cfg = await self.config()
        app_id = str(cfg.get("app_id") or "").strip()
        secret = str(cfg.get("client_secret") or "").strip()
        agent = str(cfg.get("target_agent") or "").strip()
        missing = [
            label for label, value in (("AppID", app_id), ("ClientSecret", secret), ("AI 名字", agent))
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
            await self._bind_target(agent)
        except Exception as e:
            # 起不来要给出人话原因：管理页会把它显示在状态里
            self.last_error = str(e)
            logger.warning(f"QQ 通道[{self.instance}] 启动中止：{self.last_error}")
            return False

        self._allow = _split_list(cfg.get("qq_group_allowlist"))
        self._dm_policy = (str(cfg.get("dm_policy") or "pairing").strip().lower() or "pairing")
        self._client = QqClient(app_id, secret)
        self.last_error = ""
        self.connected = False
        self.started_at = time.time()

        from app.chat.outbound import register_sink

        # 出口注册名用 **key（带实例）**：id 是插件类型，两个实例同名会互相顶掉——
        # 2026-09-25 线上实测：新绑的第二个 QQ 通道把第一个的出口覆盖，群 64 的 AI 回复被静默丢弃
        self._sink_handle = register_sink(self.key, group=self._outbound_sink, dm=self._dm_outbound_sink)
        self._task = asyncio.create_task(self._supervise())
        logger.info(
            f"QQ 通道[{self.instance}] 已启动：AI={self._target_agent}"
            f"，群={self._copree_group_id or '未绑定'}，私聊={self._dm_policy}"
        )
        return True

    async def stop(self) -> bool:
        from app.chat.outbound import unregister_sink

        unregister_sink(getattr(self, "_sink_handle", None) or self.key)
        self._route.clear()
        self._dm_route.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
        self.started_at = 0.0
        logger.info(f"QQ 通道[{self.instance}] 已停止")
        return True

    async def _bind_target(self, agent_name: str) -> None:
        """认下"这个机器人背后是哪个 AI"：优先在绑定的群里找，其次全局找同名 agent。

        两边共用一个字段：群里 @ 的是它、私聊聊的也是它——不存在"群一个 AI、私聊另一个"的错位。
        """
        from sqlalchemy import select

        from app.database import async_session
        from app.models.agent import Agent
        from app.models.group import GroupMember

        async with async_session() as db:
            if self._copree_group_id:
                row = (await db.execute(
                    select(Agent.id, Agent.user_id, Agent.name)
                    .join(GroupMember, (GroupMember.member_type == "ai") & (GroupMember.member_id == Agent.user_id))
                    .where(GroupMember.group_id == self._copree_group_id, Agent.name == agent_name)
                )).first()
            else:
                rows = (await db.execute(
                    select(Agent.id, Agent.user_id, Agent.name).where(Agent.name == agent_name)
                )).all()
                if len(rows) > 1:
                    raise ValueError(f"有多个 AI 叫「{agent_name}」，请填 Copree 群 ID 以确定是哪一个")
                row = rows[0] if rows else None

        if row is None:
            scope = f"Copree 群 #{self._copree_group_id}" if self._copree_group_id else "全站"
            raise ValueError(f"{scope}里找不到名叫「{agent_name}」的 AI")
        self._target_agent = str(row[2])
        self._target_user_id = int(row[1])

    # ── 网关连接（含重连） ──────────────────────────────────────
    async def _supervise(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"QQ 网关连接异常，{backoff:.0f}s 后重连：{self.last_error}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    async def _connect_once(self) -> None:
        import websockets

        assert self._client is not None
        url = await self._client.gateway()
        logger.info(f"QQ 通道[{self.instance}] 连接网关中…")
        async with websockets.connect(url, ping_interval=None, max_size=2 ** 22, close_timeout=5) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if hello.get("op") != OP_HELLO:
                raise RuntimeError(f"握手异常：{hello.get('op')}")
            interval = float((hello.get("d") or {}).get("heartbeat_interval") or 45000) / 1000.0
            token = await self._client.token()
            await ws.send(json.dumps({
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": INTENTS,
                    "shard": [0, 1],
                    "properties": {"$os": "linux", "$browser": "copree", "$device": "copree"},
                },
            }))
            self.connected = True
            self.last_error = ""
            logger.info(f"QQ 通道[{self.instance}] 已鉴权，开始收事件")
            seq = 0
            self._heartbeat = asyncio.create_task(self._heartbeat_loop(ws, interval, lambda: seq))
            try:
                async for raw in ws:
                    payload = json.loads(raw)
                    op = payload.get("op")
                    if payload.get("s") is not None:
                        seq = int(payload["s"])
                    if op == OP_DISPATCH:
                        await self._on_event(str(payload.get("t") or ""), payload.get("d") or {})
                    elif op == OP_RECONNECT:
                        raise RuntimeError("服务端要求重连")
                    elif op == OP_INVALID_SESSION:
                        raise RuntimeError("会话失效（token 或 intents 有问题）")
            finally:
                self.connected = False
                if self._heartbeat is not None:
                    self._heartbeat.cancel()
                    self._heartbeat = None

    async def _heartbeat_loop(self, ws: Any, interval: float, get_seq: Any) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": get_seq() or None}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"QQ 心跳结束：{e}")

    # ── 入站：QQ 事件 → Copree ─────────────────────────────────
    async def _on_event(self, event: str, data: dict) -> None:
        # 每类事件都留一行：出事时能一眼分清"腾讯没推"还是"我们挡了"
        # （心跳是 op11、不进这里，所以不会刷屏）
        if event != "READY":
            logger.info(f"QQ 事件[{self.instance}]: {event}")
        if event == "READY":
            self.bot_name = str((data.get("user") or {}).get("username") or "")
            logger.info(f"QQ 机器人已就绪：{self.bot_name}（实例 {self.instance}）")
            return
        if event == "GROUP_AT_MESSAGE_CREATE":
            await self._on_group_at(data)
            return
        if event == "C2C_MESSAGE_CREATE":
            await self._on_c2c(data)
            return

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

    def _seen_before(self, msg_id: str) -> bool:
        """相同 msg_id 可能重复推送（官方明说），不去重就会重复回答"""
        if msg_id in self._seen:
            return True
        self._seen.append(msg_id)
        return False

    @property
    def channel_kind(self) -> str:
        """外部身份的类别名：读本插件 manifest 里的 channel.kind（插件不自己写死字符串）"""
        kind = getattr(self, "_channel_kind", "")
        if not kind:
            from app.services.plugin import catalog

            kind = self._channel_kind = catalog.channel_kind(self.id)
        return kind

    async def _dm_allowed(self, openid: str, author: dict, msg_id: str) -> bool:
        """私聊放行判断 —— 默认 pairing：陌生人不进 AI，只领一个配对码。

        配对状态住在 Copree 的 external_identities 里（这个外部身份那一行），批准口在「这个 AI 的 QQ 通道卡片」上；
        机器人这边只做两件事：挡住陌生人、把码告诉他。
        官方只给 openid（拿不到 QQ 号），所以认人只能认 openid，昵称仅用于展示。
        """
        if self._dm_policy == "open":
            return True

        from app.database import async_session
        from app.services.plugin import pairing

        async with async_session() as db:
            status = await pairing.status_of(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=openid
            )
            if status == pairing.APPROVED:
                return True
            if status == pairing.BLOCKED or self._dm_policy == "owner":
                logger.info(f"QQ 私聊被挡下（{status or '未配对'}）：openid …{openid[-6:]}")
                return False
            row = await pairing.upsert_pending(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=openid,
                display_name=str(author.get("username") or ""),
            )
            code = row.code

        now = time.time()
        if now - self._pair_notified.get(openid, 0) < PAIR_NOTIFY_INTERVAL:
            logger.debug(f"配对码已发过，未到重发间隔：openid …{openid[-6:]}")
            return False
        self._pair_notified[openid] = now
        try:
            await self._client.send_c2c(
                openid,
                f"配对码：{code}\n把它填到 Copree 里这个 AI 的「QQ 通道」卡片上，我才会回话。",
                msg_id=msg_id,
            )
            logger.info(f"QQ 陌生人私聊 → 已下发配对码（openid …{openid[-6:]}，昵称 {row.display_name or '?'}）")
        except Exception as e:
            self.last_error = f"配对码下发失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 配对码下发失败：{self.last_error}")
        return False

    async def _on_group_at(self, d: dict) -> None:
        author = d.get("author") or {}
        if author.get("bot"):
            return
        msg_id = str(d.get("id") or "")
        qq_group = str(d.get("group_openid") or "")
        if not msg_id or not qq_group:
            return
        if self._seen_before(msg_id):
            return
        # 先记账再判白名单：白名单该怎么填，前提是界面能看见"机器人在哪些群里出现过"。
        # 被白名单挡下的群同样记下来（allowed=False），否则用户永远发现不了它。
        self._note_group(qq_group, allowed=(not self._allow) or qq_group in self._allow)
        if not self._copree_group_id:
            logger.debug("未绑定 Copree 群，忽略群消息（这个实例只做私聊）")
            return
        if self._allow and qq_group not in self._allow:
            logger.debug(f"QQ 群 {qq_group} 不在白名单，忽略")
            return

        content = self._readable_content(d)
        if not content:
            return
        # 两边语义对齐：QQ 里 @机器人 = 在 Copree 里 @这个 AI（群自己的唤醒规则仍然生效）
        content = f"@{self._target_agent} {content}"

        # 官方事件表里 author 有 username，但真机上我们只拿到过占位名 —— 打一行真实字段，
        # 一眼分清「腾讯没给昵称」还是「我们没读出来」（openid 只留尾号，标识不进日志）
        _openid = str(author.get("member_openid") or author.get("user_openid") or author.get("id") or "")
        logger.info(
            f"QQ 群消息[{self.instance}]: openid …{_openid[-6:]} "
            f"昵称={author.get('username') or '(空)'} author字段={sorted(author.keys())} "
            f"消息字段={sorted(d.keys())} mentions={json.dumps(d.get('mentions') or d.get('message_mentions'), ensure_ascii=False)[:200]}",
        )

        try:
            peer_name = await self._deliver_to_group(qq_group, author, content)
            self._route[self._copree_group_id] = {
                "qq": qq_group, "msg_id": msg_id, "seq": 0, "ts": time.time(),
                # 出站摘 @ 要用它：QQ 的被动回复自己显示 @对方，正文里那个 @是谁要对得上
                "peer_name": peer_name or "",
            }
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
            session_id = await self._deliver_to_dm(openid, author, content)
            if session_id:
                self._dm_route[session_id] = {
                    "qq": openid, "msg_id": msg_id, "seq": 0, "ts": time.time(),
                }
        except Exception as e:
            self.last_error = f"私信入站失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 私聊消息进 Copree 失败：{self.last_error}", exc_info=True)

    @staticmethod
    def _readable_content(d: dict) -> str:
        """事件里的可用文本：正文优先，图片/语音/文件转成占位，别让 AI 以为没人说话"""
        text = str(d.get("content") or "").strip()
        if text:
            return text
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

    async def _deliver_to_group(self, qq_group: str, author: dict, content: str) -> str | None:
        """把 QQ 群消息当作一次正常的群发言落库，并走与网页端完全相同的投递链路"""
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
            ensured = await self._ensure_qq_user(db, author, join_group=self._copree_group_id)
            if ensured is None:
                return None
            sender_id, peer_name = ensured
            message = await send_gm_message(
                db,
                group_id=self._copree_group_id,
                sender_type="human",
                sender_id=sender_id,
                content=content,
                via="qq",          # 群里要能看出这条是从 QQ 来的
            )
            await db.flush()
            # 序列化一次，两处共用：AI 成员（fanout）和群里的人（broadcast）
            msg_data = await message_view(db, message)
            await fanout_group_message(db, self._copree_group_id, message, content, msg_data)
            await broadcast_group_message(self._copree_group_id, msg_data)
            await db.commit()
            await forward_group_message_federated(self._copree_group_id, msg_data, db)
            wake_group_ai(self._copree_group_id, message, content)
            await maybe_vectorize_group_message(db, self._copree_group_id, message)
            logger.info(f"QQ 群 {qq_group} 的消息已进入 Copree 群 #{self._copree_group_id}（msg {message.id}）")
            return peer_name

    async def _deliver_to_dm(self, openid: str, author: dict, content: str) -> str | None:
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
            payload = await send_dm_message(db, session_id, sender_id=sender_id, content=content)
            await fanout_dm_message(session_id, payload, sender_id)
            await db.commit()
            wake_dm_ai(session_id, payload, sender_id=sender_id, sender_type="human")
            await forward_dm_federated(session_id, payload)
            logger.info(f"QQ 私聊已进入会话 {session_id}（AI {self._target_agent}，msg {payload.get('id')}）")
            return session_id

    async def _ensure_qq_user(self, db: Any, author: dict, join_group: int = 0) -> tuple[int, str] | None:
        """QQ 用户 → 外部身份那一行（+ 过渡期的本地锚点账号）；需要时加入绑定的群。

        为什么按人建身份：AI 看到的是「谁在说话」。都塞进一个"QQ 访客"账号，
        群里每个人都长得一样，记忆和关系也就无从谈起。身份住在 external_identities
        （谁、从哪来、放不放行），不在 users 里占号。
        建号/补名这件事与 NapCat 通道共用 ensure_channel_user：kind 取自本插件 manifest
        （qq），所以 email 锚点仍是 {openid}@qq.bridge，已有行不受影响。
        """
        from app.services.plugin.channel_user import ensure_channel_user

        openid = str(author.get("member_openid") or author.get("user_openid") or author.get("id") or "").strip()
        return await ensure_channel_user(
            db,
            kind=self.channel_kind,
            owner_scope=self.instance,
            origin=openid,
            display_name=str(author.get("username") or "").strip(),
            origin_channel=self.channel_kind[:16],
            join_group=join_group,
            # 投递链路在 fanout/broadcast 之后统一 commit，这里不能提前落盘
            commit=False,
        )

    # ── 出站：AI 的回复 → QQ ───────────────────────────────────
    async def _outbound_sink(self, db: Any, group_id: int, message: Any, source: str) -> None:
        """群消息出口：只转发绑定群里 AI 发的消息。

        必须 fire-and-forget：这个 sink 在 send_gm_message 里、commit 之前被调用，
        等一次 HTTP 往返会把"发消息"本身拖慢。
        """
        if not self._copree_group_id or group_id != self._copree_group_id:
            return
        if getattr(message, "sender_type", None) != "ai":
            return
        route = self._route.get(group_id)
        text = str(getattr(message, "content", "") or "").strip()
        if not route or not text:
            return
        # QQ 的被动回复自己就会显示「@对方」，正文里再带一个 @名字 就成了两个 @
        # （用户 2026-09-25 实测）。只摘掉开头对**这次回的那个人**的 @，站内内容不动。
        # 正文里的 Markdown 不再在这里降级：能不能发 MD 由 QqClient._send_rich 按机器人权限决定
        from app.utils.text import strip_leading_mention

        text = strip_leading_mention(text, str(route.get("peer_name") or "")).strip()
        if not text:
            return
        asyncio.create_task(self._send_reply(route, text, kind="group"))

    async def _dm_outbound_sink(self, db: Any, session_id: str, msg: dict) -> None:
        """私信出口：这条私信是我们经手的会话、且是 AI 发的，就发回 QQ"""
        route = self._dm_route.get(str(session_id))
        if not route:
            return
        sender_id = msg.get("sender_id")
        if int(sender_id or 0) != self._target_user_id:
            return                      # 只转发这个 AI 的回复
        # 同上：Markdown 由发送层按权限决定发 MD 还是降级纯文本
        text = str(msg.get("content") or "").strip()
        if not text:
            return
        asyncio.create_task(self._send_reply(route, text, kind="dm"))

    async def _send_reply(self, route: dict, text: str, kind: str) -> None:
        client = self._client
        if client is None or not self._task or self._task.done():
            return
        target = str(route.get("qq") or "")
        if not target:
            return
        if not await self._wait_for_slot(target):
            logger.warning(f"QQ {'群' if kind == 'group' else '用户'} {target[-6:]} 触发频控，丢弃一条回复：{text[:40]}")
            return
        window, limit = (GROUP_WINDOW, GROUP_MAX) if kind == "group" else (DM_WINDOW, DM_MAX)
        msg_id = route.get("msg_id") or None
        seq = int(route.get("seq") or 0)
        fresh = (time.time() - float(route.get("ts") or 0)) < window
        if msg_id and fresh and seq < limit:
            route["seq"] = seq + 1
        else:
            msg_id = None
        try:
            if kind == "group":
                await client.send_group(target, text, msg_id=msg_id, msg_seq=seq + 1)
                self.replies += 1
            else:
                await client.send_c2c(target, text, msg_id=msg_id, msg_seq=seq + 1)
                self.dm_replies += 1
            self.last_error = ""
        except Exception as e:
            self.last_error = f"发送失败：{type(e).__name__}: {e}"
            logger.warning(f"回复到 QQ {'群' if kind == 'group' else '用户'} {target[-6:]} 失败：{self.last_error}")

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


def _split_list(raw: Any) -> set[str]:
    """逗号分隔（中英文逗号都认）→ 集合"""
    text = str(raw or "").replace("，", ",")
    return {item.strip() for item in text.split(",") if item.strip()}
