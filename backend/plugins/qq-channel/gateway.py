"""QQ 官方网关：客户端与连接状态机。

入口（plugin.py）只管声明与装配；这里负责取 token/网关地址、握手、续接（op6）、心跳、
事件派发与重连节奏。协议语义见 docs/plugin-dev/channel-plugins.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

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
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10

BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
# 换会话（op7 token 到期 / op9 会话失效）后的重连停顿：这不是故障，不该走指数退避。
# 退避到 60 秒上限时，每小时一次的例行换会话就变成 60 秒收不到消息的空窗
# （2026-11-12 22:27 用户那条 @ 正是掉在里面）。再乘 0~1 的抖动，避免十几条通道同时打网关。
RECONNECT_PAUSE = 1.0


class _GatewayRestart(Exception):
    """网关要求换会话：重连即可，不进退避。"""

    # op9（会话失效）时缓存的 token 和旧会话都已不可信
    drop_token = False
    reset_session = False


class _InvalidSession(_GatewayRestart):
    """会话失效（token 或 intents 不对）：换新 token、丢会话，重新鉴权。"""

    drop_token = True
    reset_session = True


class GatewayMixin:
    """连接生命周期。字段由 _init_gateway() 建好（插件 __init__ 调用）。"""

    def _init_gateway(self) -> None:
        """连接状态：实例创建时调一次。"""
        self._client: QqClient | None = None
        self._heartbeat: asyncio.Task | None = None
        self.connected = False
        # 网关会话：重连时发 op6 续上它，网关会把断线期间漏掉的事件补发一遍
        self._session_id = ""
        self._seq = 0
        # 本次连接是否真的建立了会话（收到 READY 或 RESUMED）：续接被静默拒绝时靠它兜底
        self._ready = False

    # ── 网关连接（含重连） ──────────────────────────────────────
    async def _supervise(self) -> None:
        backoff = BACKOFF_START
        while True:
            try:
                if await self._connect_once():
                    backoff = BACKOFF_START
                else:
                    # 连上了却没走到 READY/RESUMED：续接可能被静默拒绝（服务端不保证一定回 op9）。
                    # 丢会话、照一次退避再试，下一轮走新的鉴权，避免拿着死 session 空转。
                    self._session_id, self._seq = "", 0
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    logger.warning(f"QQ 通道[{self.instance}] 连接未建立会话，{backoff:.0f}s 后重试")
                    await asyncio.sleep(backoff)
            except _GatewayRestart as e:
                # 换会话是网关的例行动作，不是故障：等一个抖动就重连，退避留给真故障。
                # 能走到这里说明连接本身是通的，真故障下次也从 1 秒重新起算。
                self.connected = False
                backoff = BACKOFF_START
                if e.drop_token and self._client is not None:
                    self._client.forget_token()
                if e.reset_session:
                    self._session_id, self._seq = "", 0
                logger.info(f"QQ 通道[{self.instance}] 网关要求换会话，重连中")
                await asyncio.sleep(RECONNECT_PAUSE * (1.0 + random.random()))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"QQ 网关连接异常，{backoff:.0f}s 后重连：{self.last_error}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    async def _connect_once(self) -> bool:
        """跑一次连接，返回这次是否真的建立了会话（收到 READY / RESUMED）。"""
        import websockets

        assert self._client is not None
        url = await self._client.gateway()
        logger.info(f"QQ 通道[{self.instance}] 连接网关中…")
        self._ready = False
        # WS 层心跳不需要：官方网关有自己的应用层心跳（op=HEARTBEAT，间隔由 HELLO 下发，
        # 见 _heartbeat_loop），保活与断线判据都走它
        async with websockets.connect(url, ping_interval=None, max_size=2 ** 22, close_timeout=5) as ws:
            interval = await self._handshake(ws)
            await self._open_session(ws)
            self.last_error = ""
            await self._pump(ws, interval)
        return self._ready

    async def _handshake(self, ws: Any) -> float:
        """等 op10 HELLO，取出应用层心跳间隔（秒）。"""
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if hello.get("op") != OP_HELLO:
            raise RuntimeError(f"握手异常：{hello.get('op')}")
        return float((hello.get("d") or {}).get("heartbeat_interval") or 45000) / 1000.0

    async def _pump(self, ws: Any, interval: float) -> None:
        """连接期间只做一件事：把网关下发的 payload 交给 _handle_payload 派发。"""
        self._heartbeat = asyncio.create_task(self._heartbeat_loop(ws, interval))
        try:
            async for raw in ws:
                await self._handle_payload(json.loads(raw))
        finally:
            self.connected = False
            if self._heartbeat is not None:
                self._heartbeat.cancel()
                self._heartbeat = None

    async def _open_session(self, ws: Any) -> None:
        """连上之后先续会话，续不了再鉴权。

        续会话（op6）是"一条不丢"的关键：网关会把断线这段时间漏掉的 DISPATCH 重发一遍，
        所以换会话、网络抖动只丢连接，不丢消息。会话过期时服务端回 op9，主循环按会话失效
        重连一次，下一次自然走 IDENTIFY。
        """
        token = await self._client.token()
        if self._session_id:
            logger.info(f"QQ 通道[{self.instance}] 续接会话中…")
            await ws.send(json.dumps({
                "op": OP_RESUME,
                "d": {"token": f"QQBot {token}", "session_id": self._session_id, "seq": self._seq},
            }))
            return
        await ws.send(json.dumps({
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENTS,
                "shard": [0, 1],
                "properties": {"$os": "linux", "$browser": "copree", "$device": "copree"},
            },
        }))

    async def _handle_payload(self, payload: dict) -> None:
        if payload.get("s") is not None:
            self._seq = int(payload["s"])
        op = payload.get("op")
        if op == OP_DISPATCH:
            await self._on_event(str(payload.get("t") or ""), payload.get("d") or {})
        elif op == OP_RECONNECT:
            raise _GatewayRestart()
        elif op == OP_INVALID_SESSION:
            raise _InvalidSession()

    async def _heartbeat_loop(self, ws: Any, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq or None}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"QQ 心跳结束：{e}")


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

    def forget_token(self) -> None:
        """丢掉缓存的 access_token：会话被判失效后旧 token 不再可信。"""
        self._token = ""
        self._expire_at = 0.0

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
        message_reference: str = "", force_type: int | None = None, wakeup: bool = False,
    ) -> dict:
        """先按 Markdown 发，机器人没有 MD 权限时退回纯文本。

        MD 权限是**机器人账号维度**的（腾讯那边开通），同一个平台里有的号有、有的没有；
        所以在同一次发送里降级，而不是在平台配置里写死——不然每接一个号都要先问一遍。

        force_type 非空 = 用户明确指定了消息类型（有的 QQ 客户端只显示特定类型）：
        这时不做降级、发一次，把响应或错误原样带回去。
        """
        from app.utils.text import plainify_markdown, render_color_markup

        # 彩色是站内语法，QQ 不认：按 Markdown 发的那条降级成 **加粗**（比褪成黑字强），
        # 纯文本那条由 plainify_markdown 脱掉标签——两条各自在下面取用
        rich = render_color_markup(content, markdown=True)

        extra: dict[str, Any] = {"msg_id": msg_id, "msg_seq": msg_seq} if msg_id else {}
        if wakeup:
            # 互动召回：与 msg_id / event_id 互斥（官方），所以只在不带 msg_id 时才可能为真
            extra["is_wakeup"] = True
        if message_reference:
            # 精准引用：填了它 QQ 里就以引用形式展示（官方 message_reference）
            extra["message_reference"] = {"message_id": message_reference}
        if force_type is not None:
            body: dict[str, Any] = {"msg_type": int(force_type), **extra}
            # 长度由 _deliver 统一决定（只有它知道学到的单条上限），这里再截一次会静悄悄吃掉一截正文
            if int(force_type) == 2:
                body["markdown"] = {"content": rich}
            else:
                body["content"] = plainify_markdown(content)
            return await self._post(path, body)
        try:
            return await self._post(path, {"msg_type": 2, "markdown": {"content": rich}, **extra})
        except RuntimeError as e:
            # 只对"没权限"这类降级；其它错误（频控、参数错）照旧抛出去，别吞
            if not any(word in str(e) for word in ("无权限", "权限", "markdown", "msg_type")):
                raise
            logger.info("机器人没有 Markdown 权限，这条退回纯文本：%s", str(e)[:120])
        return await self._post(path, {"msg_type": 0, "content": plainify_markdown(content), **extra})

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
        self, user_openid: str, content: str, msg_id: str | None = None, msg_seq: int = 1,
        force_type: int | None = None, wakeup: bool = False,
    ) -> dict:
        """发私聊消息（被动回复 60 分钟内、最多 4 次；wakeup = 互动召回，见 _deliver）

        force_type 与群一致：正文格式是通道级配置，私聊也要能固定成纯文本。
        """
        return await self._send_rich(
            f"/v2/users/{user_openid}/messages", content, msg_id, msg_seq,
            force_type=force_type, wakeup=wakeup,
        )

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

    async def member_info(self, group_openid: str, member_openid: str) -> dict:
        """群成员信息（官方 30 QPM）。跨应用统一的 union_openid 只有这条路才稳定拿到：
        事件载荷里那个字段常常是空的（官方本身就写了"可能为空"）。

        需要白名单权限（无权限时返回 11253），调用方负责降级。
        """
        http = await self._client()
        res = await http.get(
            f"/v2/groups/{group_openid}/members/{member_openid}",
            headers={"Authorization": f"QQBot {await self.token()}"},
        )
        try:
            data = res.json() if res.content else {}
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("code"):
            raise RuntimeError(f"取群成员信息失败（HTTP {res.status_code}）：{data}")
        return data

    async def group_info(self, group_openid: str) -> dict:
        """群基本信息（官方：群名、群简介、群分类、群标签、群人数）。

        调用方自己决定多久问一次：这些是低频变化的事实，每条消息都问等于拿限流换个不变的答案。
        需要机器人在该群、且接口权限正常（无权限/异常时抛错，由调用方降级）。
        """
        http = await self._client()
        res = await http.get(
            f"/v2/groups/{group_openid}/info",
            headers={"Authorization": f"QQBot {await self.token()}"},
        )
        try:
            data = res.json() if res.content else {}
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("code"):
            raise RuntimeError(f"取群信息失败（HTTP {res.status_code}）：{data}")
        return data


