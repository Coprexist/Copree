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
        self, path: str, content: str, msg_id: str | None, msg_seq: int,
        message_reference: str = "", force_type: int | None = None,
    ) -> dict:
        """先按 Markdown 发，机器人没有 MD 权限时退回纯文本。

        MD 权限是**机器人账号维度**的（腾讯那边开通），同一个平台里有的号有、有的没有；
        所以在同一次发送里降级，而不是在平台配置里写死——不然每接一个号都要先问一遍。

        force_type 非空 = 用户明确指定了消息类型（有的 QQ 客户端只显示特定类型）：
        这时不做降级、发一次，把响应或错误原样带回去。
        """
        from app.utils.text import plainify_markdown

        extra: dict[str, Any] = {"msg_id": msg_id, "msg_seq": msg_seq} if msg_id else {}
        if message_reference:
            # 精准引用：填了它 QQ 里就以引用形式展示（官方 message_reference）
            extra["message_reference"] = {"message_id": message_reference}
        if force_type is not None:
            body: dict[str, Any] = {"msg_type": int(force_type), **extra}
            if int(force_type) == 2:
                body["markdown"] = {"content": content[:TEXT_LIMIT]}
            else:
                body["content"] = plainify_markdown(content)[:TEXT_LIMIT]
            return await self._post(path, body)
        try:
            return await self._post(path, {"msg_type": 2, "markdown": {"content": content[:TEXT_LIMIT]}, **extra})
        except RuntimeError as e:
            # 只对"没权限"这类降级；其它错误（频控、参数错）照旧抛出去，别吞
            if not any(word in str(e) for word in ("无权限", "权限", "markdown", "msg_type")):
                raise
            logger.info("机器人没有 Markdown 权限，这条退回纯文本：%s", str(e)[:120])
        return await self._post(path, {"msg_type": 0, "content": plainify_markdown(content)[:TEXT_LIMIT], **extra})

    async def send_group(
        self, group_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1,
        message_reference: str = "", force_type: int | None = None,
    ) -> dict:
        """发群消息；带 msg_id = 被动回复（5 分钟内、最多 5 次），不带 = 主动消息（有频控）。

        message_reference = 要精准引用的那条消息的 REFIDX（见 message_reference 文档）。
        """
        return await self._send_rich(
            f"/v2/groups/{group_openid}/messages", content, msg_id, msg_seq, message_reference, force_type
        )

    async def send_c2c(
        self, user_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1
    ) -> dict:
        """发私聊消息（被动回复 60 分钟内、最多 4 次）"""
        return await self._send_rich(f"/v2/users/{user_openid}/messages", content, msg_id, msg_seq)

    async def delete_group_message(self, group_openid: str, message_id: str) -> dict:
        """撤回群消息（官方：发送超过 2 分钟不可撤回；机器人是群管理员时还能撤普通成员的消息）"""
        http = await self._client()
        res = await http.delete(
            f"/v2/groups/{group_openid}/messages/{message_id}",
            headers={"Authorization": f"QQBot {await self.token()}"},
        )
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("code"):
            raise RuntimeError(f"撤回 QQ 消息失败（HTTP {res.status_code}）：{data}")
        return data


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
        "body_format": {
            "type": "string", "title": "正文格式",
            "title_en": "Body format", "title_ja": "本文の形式",
            "description": "默认：先按 Markdown 发，机器人没权限时自动降级纯文本；"
                           "也可以固定成纯文本（有些不渲染 Markdown 的客户端）或固定成 Markdown。"
                           "注意：腾讯的纯文本消息**渲染不了内联 @**（实测两种写法都显示成尖括号原文），"
                           "所以纯文本模式下 @ 会退成 @名字（看得懂但不会提醒对方）",
            "description_en": "Default: try Markdown first and fall back to plain text; "
                              "or pin plain text / Markdown",
            "description_ja": "既定：まず Markdown、権限が無ければ自動でプレーンテキスト。"
                              "プレーン／Markdown に固定も可",
            "options": [
                {"value": "", "label": "默认（Markdown 优先，没权限自动降级）",
                 "label_en": "Default (Markdown first, fall back to text)",
                 "label_ja": "既定（まず Markdown、権限が無ければテキスト）"},
                {"value": "plain", "label": "纯文本", "label_en": "Plain text",
                 "label_ja": "プレーンテキスト"},
                {"value": "markdown", "label": "Markdown", "label_en": "Markdown",
                 "label_ja": "Markdown"},
            ],
        },
        "quote_replies": {
            "type": "boolean", "title": "引用回复",
            "title_en": "Quote the replied message", "title_ja": "引用返信",
            "description": "回复群里某条消息时以引用形式展示（官方 message_reference）。"
                           "它是**独立维度**：跟上面选哪种正文格式都能叠",
            "description_en": "Show the replied message as a quote (official message_reference). "
                              "Independent from the body format above",
            "description_ja": "返信時に対象メッセージを引用表示（公式 message_reference）。"
                              "本文の形式とは独立して設定できます",
        },
        "dm_policy": {
            "type": "string", "title": "私聊策略（pairing / owner / open / off）",
            "description": "默认 pairing：陌生人私聊只会收到一个配对码，批准之后才能跟 AI 说话；"
                           "owner 只认已配对的人（陌生人静默忽略）；open 谁都能聊；off 关闭私聊",
        },
    },
)
class QqChannelPlugin(ServicePlugin):
    # 能发消息，所以有「通道自测」（管理卡片上的按钮据此显示）
    self_testable = True

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
        # 正文格式：None = 默认（先 Markdown，没权限降级纯文本）；有值 = 固定成它
        self._msg_type: int | None = None
        # 引用回复（独立维度）：回复 QQ 来消息时带 message_reference
        self._quote_replies = True
        # 配对码通知节流：同一个人反复私聊时别把码刷屏（60 秒最多提醒一次）
        self._pair_notified: dict[str, float] = {}
        # 回复路由：群 → 最近一次来消息的 QQ 群；私信会话 → 那条私聊（含被动回复凭据）
        # 路由里带着 msg_id/seq/ts：被动回复要用「触发它的那条消息」的 id，
        # 且同一个 msg_id 只能用一次（官方：相同 msg_id+msg_seq 重复发送会失败），
        # 所以这里存的是活字典，发送时原地递增 seq。
        self._route: dict[int, dict[str, Any]] = {}
        self._dm_route: dict[str, dict[str, Any]] = {}
        self._seen: deque[str] = deque(maxlen=DEDUP_SIZE)
        # 已落库的消息：msg_id → (站内消息 id, 落库时的正文)。
        # 同一条消息的另一种事件（@模式 ↔ 全量模式）更全时，用它把正文补上
        self._delivered: dict[str, tuple[int, str]] = {}
        self._delivered_order: deque[str] = deque()
        # 最近见到过的 QQ 群（诊断用，内存态）：卡片上要能看见群 openid 才好填白名单
        self._seen_groups: dict[str, dict[str, Any]] = {}
        # 这个群的推送模式（最近一次观测到的事件类型 + 时间）：True=全量、False=只喂点名。
        # None = 本次启动后还没收到过群消息。通道说明据此选文案；给 AI 的持久记录在账本里
        self._full_mode: tuple[bool, float] | None = None
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
        self._msg_type = _msg_type_of(cfg.get("body_format"))
        self._quote_replies = str(cfg.get("quote_replies", "true")).strip().lower() not in ("false", "0", "off")
        self._client = QqClient(app_id, secret)
        self.last_error = ""
        self.connected = False
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
        self._route.clear()
        self._dm_route.clear()
        self._delivered.clear()
        self._delivered_order.clear()
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
        if event == "GROUP_MESSAGE_CREATE":
            # 全量模式：群主在 QQ 群设置里把「机器人可获取的群聊消息范围」设成「群内全部消息」后，
            # 群里每条消息都推这个事件（同一个 Intent）。没开就根本收不到，@ 那条路照旧——开不开都兼容
            await self._on_group_message(data)
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
                pairing.pairing_reply(code, "QQ 通道"),
                msg_id=msg_id,
            )
            logger.info(f"QQ 陌生人私聊 → 已下发配对码（openid …{openid[-6:]}，昵称 {row.display_name or '?'}）")
        except Exception as e:
            self.last_error = f"配对码下发失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 配对码下发失败：{self.last_error}")
        return False

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
        """这条消息点没点到机器人。

        全量事件里官方给 mentions（带 bot 标记），优先用它；拿不到就退回按名字判
        （@机器人 的前缀在两种事件里都被官方去掉了，所以名字判定是唯一兜底）。
        """
        for user in d.get("mentions") or []:
            if isinstance(user, dict) and user.get("bot"):
                return True
        from app.utils.text import check_mention

        return check_mention(self._readable_content(d), self._target_agent)

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
        if not self._copree_group_id:
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
        from app.services.history.context_sync import append_events, context_ref
        from app.utils.pure.history import make_entry

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
            await append_events(db, agent, ref, [make_entry(
                "notice", _channel_mode_notice(full),
                flags={"channel_mode": _channel_mode(full)},
            )])
            await db.commit()
            logger.info(
                f"QQ 通道[{self.instance}] 群推送模式变化，已给 AI 账本投递通知（{_channel_mode(full)}）"
            )

    async def _materialize_mentions(self, d: dict, content: str) -> str:
        """把这条消息 @ 到的人先建成 Copree 群成员，并在正文里补上他们的 id 令牌。

        为什么：正文里的 @名字 要靠入口归一变成 <@!平台id>，前提是"这人已经是群成员"；
        而被 @ 的人可能还没说过话（还没账号）。QQ 有时还会把 @成员的提及从正文里摘掉
        （只在 mentions 里给）——那就在这里直接补 <@!id>，AI 和界面都认得。
        只有全量模式（官方给 mentions）才做得了；@模式载荷里没有这个字段。

        QQ 写在正文里的原始提及是 `<@openid>`（**没有那个 `!`**，那是它自己的 id）：谁都不认，
        一律按 mentions 里的 origin 精确摘掉。机器人自己那条只摘不补——点名由唤醒令牌负责，
        不摘就存成 `<@!40> <@5872…>` 这种"两个令牌、其中一个还是乱码"（2026-09-26 真机）。
        """
        mentions = [m for m in (d.get("mentions") or []) if isinstance(m, dict)]
        if not mentions or not self._copree_group_id:
            return content

        from app.database import async_session
        from app.services.plugin.channel_user import ensure_channel_user
        from app.utils.text import mention_token

        tokens: list[str] = []
        async with async_session() as db:
            for user in mentions:
                origin = str(user.get("id") or user.get("member_openid") or "")
                if not origin:
                    continue
                # 原始提及先摘：留着它，AI 与界面看到的都是一串 openid
                for marker in (f"<@!{origin}>", f"<@{origin}>"):
                    content = content.replace(marker + " ", "").replace(marker, "")
                if user.get("bot") or user.get("is_you"):
                    continue                  # 机器人自己：唤醒令牌已经点名，不再补一个
                ensured = await ensure_channel_user(
                    db, kind=self.channel_kind, owner_scope=self.instance, origin=origin,
                    display_name=str(user.get("username") or ""),
                    origin_channel=self.channel_kind[:16], join_group=self._copree_group_id,
                )
                if ensured is None:
                    continue
                uid, name = ensured
                if name and f"@{name}" in content:
                    continue          # 正文里本来就写着，交给入口归一
                tokens.append(mention_token(uid))
            await db.commit()
        return ("".join(tokens) + " " + content) if tokens else content

    def _with_mention_prefix(self, content: str) -> str:
        """两边语义对齐：QQ 里 @机器人 = 在 Copree 里 @这个 AI（群自己的唤醒规则仍然生效）。

        用 id 令牌点名而不是名字：名字会改、会重名，绑定出来的 user_id 才是身份；
        万一没绑到 user_id 才退回名字（旧写法仍被识别）。
        """
        from app.utils.text import mention_token

        prefix = mention_token(self._target_user_id) if self._target_user_id else f"@{self._target_agent}"
        return f"{prefix} {content}"

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
            await self._upgrade_delivered_content(
                msg_id,
                self._with_mention_prefix(await self._materialize_mentions(d, self._readable_content(d))),
            )
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
        content = await self._materialize_mentions(d, content)
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
        raw_ext = scene.get("ext") if isinstance(scene.get("ext"), list) else []
        ext: dict[str, str] = {}
        for item in raw_ext:
            key, _, value = str(item).partition("=")
            if key:
                ext[key] = value
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

        try:
            delivered = await self._deliver_to_group(
                qq_group, author, content, msg_id, str(ext.get("msg_idx") or "")
            )
            peer_user_id, peer_name, copree_msg_id = delivered or (0, "", 0)
            self._remember_delivered(msg_id, copree_msg_id, content)
            self._route[self._copree_group_id] = {
                "qq": qq_group, "msg_id": msg_id, "seq": 0, "ts": time.time(),
                # 出站摘 @ 要用它：QQ 的被动回复自己显示 @对方，正文里那个 @是谁要对得上
                "peer_name": peer_name or "",
                # 自测要在正文里 @ 回去：群消息里只有 member_openid 能当 @ 的目标
                "peer_openid": _openid,
                # 摘正文开头那个 @ 要用平台 id（入口归一之后它是 <@!id>，名字摘不动）
                "peer_user_id": peer_user_id,
                # 这条是不是「@ 事件」（GROUP_AT_MESSAGE_CREATE）送来的：腾讯只在回复这种事件时
                # 自己补一个 @对方。全量事件送来的消息哪怕正文 @ 了机器人它也不补——2026-09-26 实测：
                # 09-25 与 08:28 两条 @ 事件的回复都自动出现 @；11:00 那条全量事件（正文 @ 了机器人）
                # 发出去就没有 @。所以摘不摘看**事件类型**，不看"有没有点名"。
                "at_event": not full,
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

    async def _deliver_to_group(
        self, qq_group: str, author: dict, content: str, channel_msg_id: str = "",
        channel_ref_idx: str = "",
    ) -> tuple[int, str, int] | None:
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
            if channel_msg_id:
                # 记下通道侧的消息 id：站内撤回时要请 QQ 一起撤
                # （机器人是群管理员时，还能撤回普通成员的消息）
                message.channel_msg_id = channel_msg_id
            if channel_ref_idx:
                # 记下它的 REFIDX：AI 回复这条时要精准引用（message_reference 用它）
                message.channel_ref_idx = channel_ref_idx
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
            # 连 id 一起带回去：出站摘正文开头的 @ 要用它（入口归一之后是 <@!id>，名字摘不动）；
            # 站内消息 id 给"另一种事件更全时补正文"用
            return sender_id, str(peer_name or ""), int(message.id)

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
        asyncio.create_task(self._send_reply(
            route, text, kind="group", link_mentions=True, message_id=getattr(message, "id", None),
            reply_to=getattr(message, "reply_to", None),
        ))

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
        asyncio.create_task(self._send_reply(route, text, kind="dm", message_id=msg.get("id")))

    async def _send_reply(self, route: dict, text: str, kind: str, *, link_mentions: bool = False,
                          message_id: int | None = None, reply_to: int | None = None) -> None:
        """后台发一条回复：这是 fire-and-forget 的尾巴，失败了没人接得住，只能记进状态

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
        reference_id = await self._reply_reference(reply_to, kind)
        try:
            # 群回复按配置的消息类型发（留空 = 默认：先 Markdown、没权限降级纯文本）
            result = await self._deliver(
                route, kind, text, reference_id=reference_id,
                msg_type=self._msg_type if kind == "group" else None,
            )
        except Exception as e:
            self.last_error = f"发送失败：{type(e).__name__}: {e}"
            logger.warning(
                f"回复到 QQ {'群' if kind == 'group' else '用户'} "
                f"{str(route.get('qq') or '')[-6:]} 失败：{self.last_error}"
            )
            return
        # 记下通道侧那条消息的 id（撤回要用）与引用索引（引用要用）
        response = result.get("response") or {}
        await self._remember_channel_ids(
            kind, message_id,
            channel_id=str(response.get("id") or ""),
            ref_idx=str((response.get("ext_info") or {}).get("ref_idx") or ""),
        )

    async def _reply_reference(self, reply_to: int | None, kind: str) -> str:
        """AI 的回复若 reply_to 指向一条 QQ 来消息，就取出它的 REFIDX 做精准引用。

        只对群消息做（官方 c2c 的引用字段没有实测过，宁可不发也不发错）。
        """
        if not reply_to or kind != "group" or not self._quote_replies:
            return ""
        from app.database import async_session
        from app.models.message import Message

        async with async_session() as db:
            row = await db.get(Message, int(reply_to))
        return str(getattr(row, "channel_ref_idx", "") or "") if row is not None else ""

    async def _remember_channel_ids(self, kind: str, message_id: int | None, *,
                                    channel_id: str = "", ref_idx: str = "") -> None:
        """把通道侧那条消息的 id 记回库里。

        为什么要另开 session：出站是 fire-and-forget 的后台任务，原请求那个 session 早就 commit 了。
        """
        if not message_id or not (channel_id or ref_idx):
            return
        from app.database import async_session
        from app.models.dm import DMMessage
        from app.models.message import Message

        model = Message if kind == "group" else DMMessage
        try:
            async with async_session() as db:
                row = await db.get(model, int(message_id))
                if row is not None:
                    if channel_id and not getattr(row, "channel_msg_id", None):
                        row.channel_msg_id = channel_id
                    if ref_idx and not getattr(row, "channel_ref_idx", None):
                        row.channel_ref_idx = ref_idx
                    await db.commit()
        except Exception as e:
            # 记不住不影响这条消息本身，只是撤回/引用会不可用
            logger.warning(f"通道消息 id 未记住（该条将无法在 QQ 侧撤回/引用）：{type(e).__name__}: {e}")

    async def _revoke_sink(self, db: Any, group_id: int, message: Any) -> dict | None:
        """站内撤回 → 请 QQ 一起撤（2 分钟内；机器人是群管理员时还能撤普通成员的消息）。

        撤不掉就**如实回报**：调用方要把"站内已撤、QQ 撤不掉"告诉用户，别假装成功。
        """
        if not self._copree_group_id or group_id != self._copree_group_id:
            return None
        channel_id = str(getattr(message, "channel_msg_id", "") or "")
        route = self._route.get(group_id)
        if not channel_id or not route:
            return None                       # 这条没经通道，或我们没记住它的通道 id
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
        from app.services.plugin.channel_user import channel_contacts
        from app.utils.text import iter_mention_ids, render_mentions

        ids = iter_mention_ids(text)
        if not ids:
            return text
        async with async_session() as db:
            contacts = await channel_contacts(db, kind=self.channel_kind, owner_scope=self.instance)
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

    async def _deliver(self, route: dict, kind: str, text: str, *, passive_only: bool = False,
                       reference_id: str = "", msg_type: int | None = None) -> dict:
        """把一条消息发到 route 指向的会话（频控与被动回复窗口都在这里）

        失败一律抛异常：调用方一个要记状态（后台回复）、一个要把原因给用户看（自测），
        各写一份"为什么没发出去"迟早不一致。
        passive_only：窗口过期时宁可失败也不退成主动消息——自测走这条路，
        它不该靠一条有配额的主动消息来冒充"通"。
        """
        client = self._client
        if client is None or not self._task or self._task.done():
            raise RuntimeError("通道没在运行")
        target = str(route.get("qq") or "")
        if not target:
            raise RuntimeError("这条路由里没有目标会话")
        if not await self._wait_for_slot(target):
            raise RuntimeError("触发频控（单会话 20/分钟、Bot 60/分钟），过一分钟再试")
        window, limit = (GROUP_WINDOW, GROUP_MAX) if kind == "group" else (DM_WINDOW, DM_MAX)
        msg_id = route.get("msg_id") or None
        seq = int(route.get("seq") or 0)
        fresh = (time.time() - float(route.get("ts") or 0)) < window
        if msg_id and fresh and seq < limit:
            route["seq"] = seq + 1
            mode = "passive"
        elif passive_only:
            raise RuntimeError("被动回复窗口已过：先去那个会话里说一句（群里 @ 一次机器人）再自测")
        else:
            msg_id = None
            mode = "active"
        if kind == "group":
            data = await client.send_group(
                target, text, msg_id=msg_id, msg_seq=seq + 1, message_reference=reference_id,
                force_type=msg_type,
            )
        else:
            # 私聊的引用字段官方没给（也没实测过），宁可不发也不发错
            data = await client.send_c2c(target, text, msg_id=msg_id, msg_seq=seq + 1)
        if kind == "group":
            self.replies += 1
        else:
            self.dm_replies += 1
        self.last_error = ""
        return {"mode": mode, "response": dict(data or {})}

    def _live_route(self) -> tuple[dict[str, Any] | None, str]:
        """最近一次来消息的那条路由（群或私聊）——自测要打在最可能通的那条路上"""
        candidates = [(float(r.get("ts") or 0), "group", r) for r in self._route.values()]
        candidates += [(float(r.get("ts") or 0), "dm", r) for r in self._dm_route.values()]
        if not candidates:
            return None, ""
        _, kind, route = max(candidates, key=lambda c: c[0])
        return route, kind

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
            # 也按配置的消息类型发：点一次自测＝用当前配置的真实发一条
            result = await self._deliver(
                route, kind, text, passive_only=True,
                msg_type=self._msg_type if kind == "group" else None,
            )
        except Exception as e:
            return {"sent": False, "target": kind, "reason": f"{type(e).__name__}: {e}"}
        return {"sent": True, "target": kind, "text": text, **result}

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


def _channel_mode(full: bool) -> str:
    """账本 flag 里的模式取值（写和读都走它，两处各写一遍迟早漂）"""
    return "full" if full else "at"


def _last_channel_mode(entries: list[dict]) -> str | None:
    """账本里最后一次"通道推送模式"通知说的是哪个模式（没说 → None）"""
    for entry in reversed(entries or []):
        mode = (entry.get("flags") or {}).get("channel_mode")
        if mode:
            return str(mode)
    return None


def _channel_mode_notice(full: bool) -> str:
    """模式翻转通知的正文（唯一来源）。

    只说"能收到什么"，不说"会不会叫醒你"：后者由平台的意愿模型决定，不归通道管
    （说了就是给 AI 一个不成立的承诺，2026-09-26）。
    """
    if full:
        return (
            "【通道变更】这个 QQ 群开始推送群内全部消息（群主在 QQ 群设置里开了「获取群内全部消息」，"
            "也就是全量模式）：群里每个人说的话都会进 Copree，你都能看到；"
            "别人 @ 别人会在正文里显示成 `<@!id>`，正文不再在 @ 处断掉。"
        )
    return (
        "【通道变更】这个 QQ 群停止推送全部消息（群主关掉了「获取群内全部消息」，回到只喂点名）："
        "从此只有点名到你的消息会进 Copree，正文可能在 @ 别人处断掉。"
    )


def _msg_type_of(raw: Any) -> int | None:
    """配置里的"正文格式" → 协议里的 msg_type。

    配置里写的是**意图**（plain / markdown），不是协议数字：官方发送侧只有 0=文本、2=Markdown、
    7=富媒体（3 文档没写清），把数字暴露到界面上只会让人猜。留空 = 默认（先 Markdown，没权限降级）。
    数字串照样认（老配置/手工填的值）。
    """
    text = str(raw or "").strip().lower()
    if text in ("plain", "text", "0"):
        return 0
    if text in ("markdown", "md", "2"):
        return 2
    return None


def _split_list(raw: Any) -> set[str]:
    """逗号分隔（中英文逗号都认）→ 集合"""
    text = str(raw or "").replace("，", ",")
    return {item.strip() for item in text.split(",") if item.strip()}
