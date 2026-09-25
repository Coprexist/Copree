"""
QQ 通道（NapCat / OneBot v11） — 用第三方协议端把 Copree 的 AI 接进 QQ

为什么单独一个插件而不是扩官方 QQ 通道：两者的协议、凭据、能力完全不同，硬塞进一个
插件只会让两边都变形 ——
  收：NapCat 的**正向 WebSocket**（我们主动连它）推 OneBot v11 事件；
  发：NapCat 的 HTTP 接口（send_group_msg / send_private_msg）；
  协议端支持 Markdown，所以出站不降级（官方 QQ 群没开通原生 MD，必须降级）。

风险自担：协议端违反 QQ 用户协议，有封号风险，建议只用自己的备用号；对外提供服务请用
官方 QQ 通道（qq-channel 插件）。

一条链路，两头都不另立门户（与 qq-channel 完全一致）：
  收：群消息 → 绑定的 Copree 群（与网页端同一条投递链路）；私聊 → 与该 AI 的私信会话
  发：AI 的回复 → app/chat/outbound.py 的出口分发 → HTTP 发回 QQ

已知限制（骨架 v1）：
- 富媒体不回传；收到的图片/语音/文件只转成文字占位，让 AI 知道"有人发了东西"
- 多个 QQ 群共用一个 Copree 群时，群回复回到**最近一次来消息**的那个 QQ 群
- 私聊不校验"是不是 AI 的创造者"：认人靠 pairing 配对流程（与官方通道同语义）
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from app.services.plugin.api import ServicePlugin, service
from app.utils.text import check_mention

logger = logging.getLogger(__name__)

# 重连退避：上限 60s（与官方通道同款），连上并稳定这么久之后才重置
BACKOFF_MAX = 60.0
STABLE_SECONDS = 30.0
# 同一条消息可能被重复推送（协议端重连后补发），按 message_id 去重
DEDUP_SIZE = 500
# 陌生人反复私聊时，配对码最多 60 秒提醒一次
PAIR_NOTIFY_INTERVAL = 60
HTTP_TIMEOUT = 10.0

# OneBot 段类型 → 占位文字：AI 要知道"有人发了东西"，而不是以为没人说话
_MEDIA_PLACEHOLDER = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "face": "[表情]",
    "forward": "[合并转发]",
    "json": "[卡片消息]",
    "xml": "[卡片消息]",
    "poke": "[戳一戳]",
}


def _http_base(ws_url: str) -> str:
    """ws://127.0.0.1:3000/ws → http://127.0.0.1:3000

    同一个 NapCat 的 WS 与 HTTP 同源，地址只需填一遍；路径部分对 HTTP 接口无意义。
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(str(ws_url or "").strip())
    scheme = "https" if parts.scheme in ("wss", "https") else "http"
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def _split_list(raw: Any) -> set[str]:
    """逗号分隔（中英文逗号都认）→ 集合"""
    text = str(raw or "").replace("，", ",")
    return {item.strip() for item in text.split(",") if item.strip()}


def _read_parts(message: Any, self_id: str) -> tuple[str, bool]:
    """OneBot 消息段数组 → (可读正文, 是否 @ 了机器人)

    为什么不复用 qq-channel 的 _readable_content：那是官方事件的 content+attachments
    结构，这里是段数组；共用只会让以后改一边时误伤另一边。
    """
    # 协议端把 message_format 配成 string 时推的是 CQ 字符串而不是段数组：
    # 解析不了也别把整条消息丢掉（退回原样文本，@ 判定交给正文里的 @名字）
    if isinstance(message, str):
        return message.strip(), False
    chunks: list[str] = []
    mentioned = False
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        seg_type = str(seg.get("type") or "")
        data = seg.get("data") or {}
        if seg_type == "text":
            chunks.append(str(data.get("text") or ""))
        elif seg_type == "at":
            qq = str(data.get("qq") or "")
            if self_id and qq == self_id:
                mentioned = True          # 这个 at 就是"叫机器人"，正文里不再重复一遍
            else:
                chunks.append(f"@{data.get('name') or qq} ")
        elif seg_type == "reply":
            continue                      # 引用回复：被引用的内容不在正文里，跳过
        elif seg_type == "file":
            name = str(data.get("name") or data.get("file") or "").strip()
            chunks.append(f"[文件] {name}".strip())
        else:
            chunks.append(_MEDIA_PLACEHOLDER.get(seg_type, f"[{seg_type}]"))
    return "".join(chunks).strip(), mentioned


class NapCatClient:
    """NapCat 的最小 HTTP 客户端：只发消息（收事件走 WebSocket）。

    只走一处 _post，token 只进请求头不进日志：排查时贴日志不会泄凭据。
    """

    def __init__(self, http_base: str, access_token: str = "") -> None:
        self._base = str(http_base or "").rstrip("/")
        self._token = access_token or ""
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(self, action: str, payload: dict) -> dict:
        http = await self._client()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        res = await http.post(f"{self._base}/{action}", json=payload, headers=headers)
        try:
            data = res.json() if res.content else {}
        except Exception:
            data = {}
        # OneBot 的失败可能藏在 retcode 里：HTTP 200 也可能 retcode != 0
        if res.status_code >= 400 or int(data.get("retcode") or 0) != 0:
            raise RuntimeError(f"调用 NapCat {action} 失败（HTTP {res.status_code}）：{data}")
        return data

    async def send_group_msg(self, group_id: int, message: str) -> dict:
        return await self._post("send_group_msg", {"group_id": int(group_id), "message": message})

    async def send_private_msg(self, user_id: int, message: str) -> dict:
        return await self._post("send_private_msg", {"user_id": int(user_id), "message": message})


# 协议端由谁提供，字段就由谁填：平台自带协议端（compose 的 napcat profile）时后端会注入
# 这两个环境变量，声明里就把地址与 token 标成 managed（卡片不显示、也不算"还缺什么"）。
_HOSTED_WS_URL = os.environ.get("NAPCAT_WS_URL", "").strip()
_HOSTED_TOKEN = os.environ.get("NAPCAT_TOKEN", "").strip()
# 托管时 NapCat 的 cache 目录也挂给后端：它把登录二维码写在那儿，
# 于是"扫码"这一步能直接画在我们自己的卡片上，用户不用再去别处找入口
_HOSTED_CACHE = os.environ.get("NAPCAT_CACHE", "").strip()


def _qr_url_from_log() -> str:
    """从协议端日志里抓最新的「二维码解码URL」（= 二维码里装的那条授权链接）

    电脑上没有扫码条件时，用户要的是这条链接（发到手机打开即可授权）。
    NapCat 只把它打到 stdout，所以 compose 用 tee 把输出也落一份到挂载目录供这里读。
    """
    if not _HOSTED_CACHE:
        return ""
    log = Path(_HOSTED_CACHE) / "napcat.log"
    try:
        with log.open("r", encoding="utf-8", errors="ignore") as fh:
            # 日志不大（几百 KB），但也没必要全读：只扫尾部，够找到最后一条码
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 64 * 1024))
            text = fh.read()
    except OSError:
        return ""
    found = re.findall(r"https://txz\.qq\.com/p\?[^\s\u001b]+", text)
    return found[-1] if found else ""


def _hosted_login(self_id: str, bot_name: str) -> dict[str, Any] | None:
    """托管协议端的登录进度：没登录就把二维码（png，几百字节）一并报给卡片"""
    if not _HOSTED_WS_URL:
        return None
    info: dict[str, Any] = {
        "held_by_platform": True,
        "logged_in": bool(self_id),
        "bot_name": bot_name or self_id or "",
    }
    if info["logged_in"]:
        return info
    qr = Path(_HOSTED_CACHE) / "qrcode.png" if _HOSTED_CACHE else None
    if qr is not None and qr.is_file():
        info["qr_png"] = base64.b64encode(qr.read_bytes()).decode()
        info["qr_at"] = int(qr.stat().st_mtime)
    info["qr_url"] = _qr_url_from_log()
    return info


def _hosted(spec: dict, value: str) -> dict:
    """平台托管这份值时，字段就地变成 managed（值由平台写进实例配置）"""
    return {**spec, "managed": True, "default": value} if value else spec


def _endpoint_schema() -> dict:
    return {
        "ws_url": _hosted({
            "type": "string", "title": "NapCat WebSocket 地址", "required": True,
            "title_en": "NapCat WebSocket URL", "title_ja": "NapCat WebSocket アドレス",
            "description": "NapCat 的正向 WebSocket 地址，如 ws://127.0.0.1:3000",
            "description_en": "The forward WebSocket URL of NapCat, e.g. ws://127.0.0.1:3000",
            "description_ja": "NapCat の順方向 WebSocket アドレス（例 ws://127.0.0.1:3000）",
        }, _HOSTED_WS_URL),
        "access_token": _hosted({
            "type": "string", "title": "AccessToken", "secret": True,
            "description": "NapCat 配置里的 access_token，没设就留空；只在这里填，加密落库、接口不回显",
            "description_en": "The access_token configured in NapCat; leave empty if unset; stored encrypted and never echoed back",
            "description_ja": "NapCat 側の access_token。未設定なら空欄。ここにのみ入力し、暗号化して保存します",
        }, _HOSTED_TOKEN),
    }


@service(
    name="QQ 通道（NapCat）",
    description="用 NapCat / OneBot v11 协议端把 AI 接进 QQ：群里被 @ 才唤醒，私聊默认要配对；AI 的回复发回 QQ",
    multi_instance=True,
    config_schema={
        **_endpoint_schema(),
        "target_agent": {
            "type": "string", "title": "这个通道接哪个 AI", "required": True, "managed": True,
            "description": "由平台按「这个 AI 的页面」自动填，不需要手工填；群里 @机器人 就等于在群里 @它",
        },
        "copree_group_id": {
            "type": "string", "title": "接入的 Copree 群 ID", "required": True,
            "title_en": "Landing Copree group ID", "title_ja": "接続先 Copree グループID",
            "description": "QQ 群消息落到哪个 Copree 群",
        },
        "qq_group_allowlist": {
            "type": "string", "title": "允许接入的 QQ 群号",
            "title_en": "Allowed QQ group numbers", "title_ja": "許可する QQ グループ番号",
            "description": "QQ 群号，多个用逗号分隔；留空 = 不限制",
            "description_en": "QQ group numbers, comma separated; empty = no limit",
            "description_ja": "QQグループ番号をカンマ区切り。空欄＝制限なし",
        },
        "dm_policy": {
            "type": "string", "title": "私聊策略（pairing / owner / open / off）",
            "description": "默认 pairing：陌生人私聊只会收到一个配对码，批准之后才能跟 AI 说话；"
                           "owner 只认已配对的人（陌生人静默忽略）；open 谁都能聊；off 关闭私聊",
        },
    },
)
class QqNapcatPlugin(ServicePlugin):
    @classmethod
    async def hosted_status(cls) -> dict[str, Any] | None:
        """托管协议端时，实例还没建也要能告诉卡片"登录到哪一步了"（含登录二维码）"""
        return _hosted_login("", "")

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._client: NapCatClient | None = None
        # 生效中的配置（start() 时读取；改配置后重启这个实例生效）
        self._ws_url = ""
        self._http_base = ""
        self._access_token = ""
        self._copree_group_id = 0
        self._target_agent = ""
        self._target_user_id = 0
        self._allow: set[str] = set()
        self._dm_policy = "pairing"
        # 配对码通知节流：同一个人反复私聊时别把码刷屏（60 秒最多提醒一次）
        self._pair_notified: dict[str, float] = {}
        # 回复路由：群 → 最近一次来消息的 QQ 群；私信会话 → 那条私聊
        self._route: dict[int, dict[str, Any]] = {}
        self._dm_route: dict[str, dict[str, Any]] = {}
        self._seen: deque[str] = deque(maxlen=DEDUP_SIZE)
        # 运行状态（只报事实）
        self.connected = False
        self.self_id = ""
        self.replies = 0
        self.dm_replies = 0
        self.last_error = ""
        self.started_at = 0.0

    # ── 生命周期 ───────────────────────────────────────────────
    async def get_status(self) -> dict:
        running = self._task is not None and not self._task.done()
        if self.last_error:
            detail = self.last_error
        elif self.connected:
            detail = f"已连接 NapCat（机器人 {self.self_id or '未知'}）"
        elif running:
            detail = "正在连接 NapCat…"
        else:
            detail = "未运行"
        return {
            "installed": True,
            "running": running,
            "detail": detail,
            "connected": self.connected,
            "self_id": self.self_id or None,
            "ws_url": self._ws_url or None,
            "target_agent": self._target_agent or None,
            "copree_group_id": self._copree_group_id or None,
            "dm_policy": self._dm_policy,
            "allowed_groups": sorted(self._allow),
            "routed_groups": sorted({r.get("qq", "") for r in self._route.values() if r.get("qq")}),
            "dm_sessions": len(self._dm_route),
            "replies_sent": self.replies,
            "dm_replies_sent": self.dm_replies,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "last_error": self.last_error,
            "hosted_endpoint": _hosted_login(self.self_id, ""),
        }

    async def start(self) -> bool:
        cfg = await self.config()
        ws_url = str(cfg.get("ws_url") or "").strip()
        agent = str(cfg.get("target_agent") or "").strip()
        group_raw = str(cfg.get("copree_group_id") or "").strip()
        missing = [
            label for label, value in (
                ("NapCat WebSocket 地址", ws_url),
                ("AI 名字", agent),
                ("Copree 群 ID", group_raw),
            ) if not value
        ]
        if missing:
            self.last_error = f"未配置：{'、'.join(missing)}"
            logger.warning(f"QQ 通道(NapCat)[{self.instance}] 启动中止：{self.last_error}")
            return False

        try:
            self._copree_group_id = int(group_raw)
        except ValueError:
            self.last_error = f"Copree 群 ID 不是数字：{group_raw}"
            logger.warning(f"QQ 通道(NapCat)[{self.instance}] 启动中止：{self.last_error}")
            return False

        try:
            await self._bind_target(agent)
        except Exception as e:
            # 起不来要给出人话原因：管理页会把它显示在状态里
            self.last_error = str(e)
            logger.warning(f"QQ 通道(NapCat)[{self.instance}] 启动中止：{self.last_error}")
            return False

        self._ws_url = ws_url
        self._http_base = _http_base(ws_url)
        self._access_token = str(cfg.get("access_token") or "").strip()
        self._allow = _split_list(cfg.get("qq_group_allowlist"))
        self._dm_policy = (str(cfg.get("dm_policy") or "pairing").strip().lower() or "pairing")
        self._client = NapCatClient(self._http_base, self._access_token)
        self.last_error = ""
        self.connected = False
        self.started_at = time.time()

        from app.chat.outbound import register_sink

        register_sink(self.id, group=self._outbound_sink, dm=self._dm_outbound_sink)
        self._task = asyncio.create_task(self._supervise())
        logger.info(
            f"QQ 通道(NapCat)[{self.instance}] 已启动：AI={self._target_agent}"
            f"，群={self._copree_group_id}，私聊={self._dm_policy}，地址={self._http_base}"
        )
        return True

    async def stop(self) -> bool:
        from app.chat.outbound import unregister_sink

        unregister_sink(self.id)
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
        logger.info(f"QQ 通道(NapCat)[{self.instance}] 已停止")
        return True

    async def _bind_target(self, agent_name: str) -> None:
        """认下"这个通道背后是哪个 AI"：优先在绑定的群里找，其次全局找同名 agent。

        两边共用一个字段：群里 @ 的是它、私聊聊的也是它 —— 不存在"群一个 AI、私聊另一个"。
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

    # ── WebSocket 连接（含重连） ────────────────────────────────
    async def _supervise(self) -> None:
        backoff = 1.0
        while True:
            started = time.time()
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"QQ 通道(NapCat) 连接异常，{backoff:.0f}s 后重连：{self.last_error}")
            else:
                # 正常返回 = 对端把连接关了；也走退避，否则服务端秒关时会变成死循环重连
                self.connected = False
                self.last_error = self.last_error or "连接已断开"
                logger.info(f"QQ 通道(NapCat)[{self.instance}] 连接已断开，{backoff:.0f}s 后重连")
            if time.time() - started >= STABLE_SECONDS:
                backoff = 1.0        # 连上并稳定过一段时间 = 上次故障已恢复
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _connect_once(self) -> None:
        import websockets

        # 鉴权走请求头而不是 ?access_token=：URL 容易进日志，token 不该跟着走
        headers = {"Authorization": f"Bearer {self._access_token}"} if self._access_token else None
        logger.info(f"QQ 通道(NapCat)[{self.instance}] 连接 {self._ws_url} 中…")
        async with websockets.connect(
            self._ws_url, additional_headers=headers, ping_interval=None,
            max_size=2 ** 22, close_timeout=5,
        ) as ws:
            self.connected = True
            self.last_error = ""
            logger.info(f"QQ 通道(NapCat)[{self.instance}] 已连接，开始收事件")
            try:
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        logger.warning(f"QQ 通道(NapCat) 收到非 JSON 帧，已忽略：{str(raw)[:120]}")
                        continue
                    await self._on_payload(payload if isinstance(payload, dict) else {})
            finally:
                self.connected = False

    # ── 入站：OneBot 事件 → Copree ─────────────────────────────
    async def _on_payload(self, payload: dict) -> None:
        # 每个事件都带 self_id：顺手记下来，别只等 lifecycle（重连后的事件可能先于它到）
        if payload.get("self_id"):
            self.self_id = str(payload["self_id"])
        post_type = str(payload.get("post_type") or "")
        if post_type == "meta_event":
            if payload.get("meta_event_type") == "lifecycle":
                logger.info(
                    f"QQ 通道(NapCat)[{self.instance}] 机器人已就绪：{self.self_id}"
                    f"（{payload.get('sub_type') or ''}）"
                )
            return
        # message_sent 是"机器人自己发的消息"的镜像事件；notice/request 与消息无关，都不处理
        if post_type != "message":
            return
        if self.self_id and str(payload.get("user_id") or "") == self.self_id:
            return
        message_type = str(payload.get("message_type") or "")
        try:
            if message_type == "group":
                await self._on_group(payload)
            elif message_type == "private":
                await self._on_private(payload)
        except Exception as e:
            self.last_error = f"入站失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 通道(NapCat) 入站失败：{self.last_error}", exc_info=True)

    def _seen_before(self, message_id: str) -> bool:
        """同一条消息可能重复推送（协议端重连后补发），不去重就会重复回答"""
        if not message_id:
            return False
        if message_id in self._seen:
            return True
        self._seen.append(message_id)
        return False

    @property
    def channel_kind(self) -> str:
        """外部身份的类别名：读本插件 manifest 里的 channel.kind（插件不自己写死字符串）"""
        kind = getattr(self, "_channel_kind", "")
        if not kind:
            from app.services.plugin import catalog

            kind = self._channel_kind = catalog.channel_kind(self.id)
        return kind

    async def _on_group(self, d: dict) -> None:
        qq_group = str(d.get("group_id") or "")
        user_id = str(d.get("user_id") or "")
        message_id = str(d.get("message_id") or "")
        if not qq_group or not user_id:
            return
        if self._seen_before(message_id):
            return
        # 白名单外只记日志不入库：这是"这个群不接这条通道"，不是"这条消息有问题"。
        # （官方通道会把见过的群报给卡片好让用户填白名单；本骨架暂时不做这一步）
        if self._allow and qq_group not in self._allow:
            logger.debug(f"QQ 群 {qq_group} 不在白名单，忽略")
            return

        sender = d.get("sender") or {}
        content, mentioned = _read_parts(d.get("message"), self.self_id)
        # 唤醒规则（与官方通道同语义）：QQ 里 @机器人 = 在 Copree 里 @这个 AI。
        # 没被 @ 时不加前缀 —— 加了等于群里每句话都点名 AI；落库仍走完整投递链路，
        # Copree 群自己的触发设置照常生效。
        wake = mentioned or check_mention(content, self._target_agent)
        if wake:
            content = f"@{self._target_agent} {content}".strip()
        if not content:
            return
        logger.info(
            f"QQ 群消息[{self.instance}]: 群={qq_group} 用户={user_id} "
            f"昵称={sender.get('card') or sender.get('nickname') or '(空)'} 唤醒={wake}"
        )
        await self._deliver_to_group(qq_group, user_id, sender, content, wake)

    async def _on_private(self, d: dict) -> None:
        user_id = str(d.get("user_id") or "")
        message_id = str(d.get("message_id") or "")
        if not user_id:
            return
        if self._seen_before(message_id):
            return
        if self._dm_policy == "off":
            logger.debug("私聊策略为 off，忽略私聊消息")
            return
        sender = d.get("sender") or {}
        if not await self._dm_allowed(user_id, sender):
            return
        content, _mentioned = _read_parts(d.get("message"), self.self_id)
        if not content:
            return
        session_id = await self._deliver_to_dm(user_id, sender, content)
        if session_id:
            self._dm_route[session_id] = {"qq": user_id}

    async def _dm_allowed(self, user_id: str, sender: dict) -> bool:
        """私聊放行判断 —— 默认 pairing：陌生人不进 AI，只领一个配对码。

        配对状态住在 Copree 的 external_identities 里，批准口在平台上；这里只做两件事：
        挡住陌生人、把码告诉他。NapCat 拿得到 QQ 号，但认人仍按 QQ 号（换号即换人）。
        """
        if self._dm_policy == "open":
            return True

        from app.database import async_session
        from app.services.plugin import pairing

        async with async_session() as db:
            status = await pairing.status_of(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=user_id
            )
            if status == pairing.APPROVED:
                return True
            if status == pairing.BLOCKED or self._dm_policy == "owner":
                logger.info(f"QQ 私聊被挡下（{status or '未配对'}）：QQ {user_id}")
                return False
            row = await pairing.upsert_pending(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=user_id,
                display_name=str(sender.get("card") or sender.get("nickname") or ""),
            )
            code = row.code

        now = time.time()
        if now - self._pair_notified.get(user_id, 0) < PAIR_NOTIFY_INTERVAL:
            logger.debug(f"配对码已发过，未到重发间隔：QQ {user_id}")
            return False
        self._pair_notified[user_id] = now
        try:
            if self._client is not None:
                await self._client.send_private_msg(
                    int(user_id),
                    f"配对码：{code}\n把它填到 Copree 里这个 AI 的「QQ 通道（NapCat）」卡片上，我才会回话。",
                )
            logger.info(f"QQ 陌生人私聊 → 已下发配对码（QQ {user_id}，昵称 {row.display_name or '?'}）")
        except Exception as e:
            self.last_error = f"配对码下发失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 配对码下发失败：{self.last_error}")
        return False

    async def _deliver_to_group(
        self, qq_group: str, user_id: str, sender: dict, content: str, wake: bool
    ) -> None:
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
        from app.services.plugin.channel_user import ensure_channel_user

        async with async_session() as db:
            ensured = await ensure_channel_user(
                db,
                kind=self.channel_kind,
                owner_scope=self.instance,
                origin=user_id,
                display_name=str(sender.get("card") or sender.get("nickname") or f"QQ{user_id}"),
                origin_channel=self.channel_kind[:16],
                join_group=self._copree_group_id,
                # 投递链路在 fanout/broadcast 之后统一 commit，这里不能提前落盘
                commit=False,
            )
            if ensured is None:
                return
            sender_id, _peer_name = ensured
            message = await send_gm_message(
                db,
                group_id=self._copree_group_id,
                sender_type="human",
                sender_id=sender_id,
                content=content,
                via=self.channel_kind,     # 群里要能看出这条是从哪条通道来的
            )
            await db.flush()
            # 序列化一次，两处共用：AI 成员（fanout）和群里的人（broadcast）
            msg_data = await message_view(db, message)
            await fanout_group_message(db, self._copree_group_id, message, content, msg_data)
            await broadcast_group_message(self._copree_group_id, msg_data)
            await db.commit()
            await forward_group_message_federated(self._copree_group_id, msg_data, db)
            if wake:
                wake_group_ai(self._copree_group_id, message, content)
            await maybe_vectorize_group_message(db, self._copree_group_id, message)
            # 群回复回到最近一次来消息的那个 QQ 群（多群共用一个 Copree 群时的已知取舍）
            self._route[self._copree_group_id] = {"qq": qq_group}
            logger.info(f"QQ 群 {qq_group} 的消息已进入 Copree 群 #{self._copree_group_id}（msg {message.id}）")

    async def _deliver_to_dm(self, user_id: str, sender: dict, content: str) -> str | None:
        """QQ 私聊 → 与该 AI 的私信会话（私信落库后同样走共用分发；涉及 AI 免好友校验）"""
        from app.chat.dm import get_or_create_dm_session, send_dm_message
        from app.chat.dm_delivery import fanout_dm_message, forward_dm_federated, wake_dm_ai
        from app.database import async_session
        from app.services.plugin.channel_user import ensure_channel_user

        async with async_session() as db:
            ensured = await ensure_channel_user(
                db,
                kind=self.channel_kind,
                owner_scope=self.instance,
                origin=user_id,
                display_name=str(sender.get("card") or sender.get("nickname") or f"QQ{user_id}"),
                origin_channel=self.channel_kind[:16],
                join_group=0,
                commit=False,
            )
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
        # 不降级 Markdown：NapCat 侧的 QQ 客户端能渲染，这正是用协议端的价值
        asyncio.create_task(self._send_reply(route, text, kind="group"))

    async def _dm_outbound_sink(self, db: Any, session_id: str, msg: dict) -> None:
        """私信出口：这条私信是我们经手的会话、且是目标 AI 发的，就发回 QQ"""
        route = self._dm_route.get(str(session_id))
        if not route:
            return
        if int(msg.get("sender_id") or 0) != self._target_user_id:
            return                      # 只转发这个 AI 的回复
        text = str(msg.get("content") or "").strip()
        if not text:
            return
        asyncio.create_task(self._send_reply(route, text, kind="dm"))

    async def _send_reply(self, route: dict, text: str, kind: str) -> None:
        client = self._client
        target = str(route.get("qq") or "")
        if client is None or not target:
            return
        try:
            if kind == "group":
                await client.send_group_msg(int(target), text)
                self.replies += 1
            else:
                await client.send_private_msg(int(target), text)
                self.dm_replies += 1
            self.last_error = ""
        except Exception as e:
            self.last_error = f"发送失败：{type(e).__name__}: {e}"
            logger.warning(
                f"回复到 QQ {'群' if kind == 'group' else '用户'} {target} 失败：{self.last_error}"
            )
