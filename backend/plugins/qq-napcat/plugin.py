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
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict, deque
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.services.plugin.api import ServicePlugin, service
from app.services.plugin.tasks import PluginTasks
from app.utils.pure.qr import png_b64
from app.utils.text import check_mention
from .accounts import load_account, save_account
from .panel import entry as _panel_entry, rewrite as _panel_rewrite

logger = logging.getLogger(__name__)


# ── 协议端控制面（NapCat WebUI） ───────────────────────────────────────
# 登录到哪一步、重新要一张码、重启协议端进程，都走它的 HTTP 接口。不读协议端的
# 磁盘目录：多实例时每个实例的 cache 目录各不相同，读文件既要知道路径也要猜时序，
# 而接口直接给答案。
_WEBUI_TOKEN_SALT = ".napcat"


class NapCatWebUIError(RuntimeError):
    """协议端控制面调用失败（连不上、鉴权不过、接口报错）"""


class NapCatWebUI:
    def __init__(self, base_url: str, token: str, *, timeout: float = 8.0) -> None:
        self.base = (base_url or "").rstrip("/")
        self._token = token or ""
        self._timeout = timeout
        self._credential = ""

    @property
    def configured(self) -> bool:
        return bool(self.base and self._token)

    def _token_hash(self) -> str:
        return hashlib.sha256((self._token + _WEBUI_TOKEN_SALT).encode()).hexdigest()

    async def _login(self, client: httpx.AsyncClient) -> str:
        if not self.configured:
            raise NapCatWebUIError("协议端控制面地址或 token 未配置")
        try:
            res = await client.post("/api/auth/login", json={"hash": self._token_hash(), "totpCode": ""})
        except httpx.HTTPError as e:
            raise NapCatWebUIError(f"连不上协议端控制面（{self.base}）：{e}") from e
        body = _webui_body(res)
        credential = str(body.get("Credential") or "") if isinstance(body, dict) else ""
        if not credential:
            raise NapCatWebUIError(f"协议端鉴权失败：{_webui_message(res)}")
        self._credential = credential
        return credential

    async def _post(self, path: str, payload: dict | None = None):
        """POST 一个控制面接口，返回 data 段；凭证失效时重换一次再试"""
        if not self.configured:
            raise NapCatWebUIError("协议端控制面地址或 token 未配置")
        async with httpx.AsyncClient(base_url=self.base, timeout=self._timeout) as client:
            for attempt in (0, 1):
                credential = self._credential or await self._login(client)
                try:
                    res = await client.post(path, json=payload or {},
                                            headers={"Authorization": f"Bearer {credential}"})
                except httpx.HTTPError as e:
                    raise NapCatWebUIError(f"调用协议端失败（{path}）：{e}") from e
                if _webui_code(res) == 0:
                    return _webui_body(res)
                # 只有凭证过期值得重换一次；真正的业务失败直接抛给调用方
                if attempt == 0 and _webui_needs_relogin(res):
                    self._credential = ""
                    continue
                raise NapCatWebUIError(_webui_message(res) or f"协议端返回异常（{path}）")
        raise NapCatWebUIError("协议端鉴权反复失败")

    async def auth_headers(self) -> dict[str, str]:
        """给反代用的鉴权头：没换过凭证就先换一次"""
        if not self._credential:
            async with httpx.AsyncClient(base_url=self.base, timeout=self._timeout) as client:
                await self._login(client)
        return {"Authorization": f"Bearer {self._credential}"}

    async def status(self) -> dict:
        data = await self._post("/api/QQLogin/CheckLoginStatus")
        return data if isinstance(data, dict) else {}

    async def refresh_qr(self) -> str:
        data = await self._post("/api/QQLogin/RefreshQRcode")
        url = str(data.get("qrcodeurl") or "") if isinstance(data, dict) else ""
        if url:
            return url
        restarting = bool(data.get("restarting")) if isinstance(data, dict) else False
        raise NapCatWebUIError("协议端正在重启，稍后再取一次" if restarting else "协议端没有返回登录码")

    async def login_info(self) -> dict:
        data = await self._post("/api/QQLogin/GetQQLoginInfo")
        return data if isinstance(data, dict) else {}

    async def quick_login(self, uin: str) -> None:
        """让客户端用本地保存的登录态恢复这个号（被顶下线后的一键拉起）"""
        await self._post("/api/QQLogin/SetQuickLogin", {"uin": str(uin)})


def _webui_code(res: httpx.Response) -> int:
    try:
        payload = res.json()
    except ValueError:
        return -1
    if isinstance(payload, dict) and "code" in payload:
        try:
            return int(payload.get("code") or 0)
        except (TypeError, ValueError):
            return -1
    return 0 if res.status_code < 400 else -1


def _webui_body(res: httpx.Response):
    try:
        payload = res.json()
    except ValueError:
        return None
    return payload.get("data") if isinstance(payload, dict) else payload


def _webui_message(res: httpx.Response) -> str:
    try:
        payload = res.json()
    except ValueError:
        return f"HTTP {res.status_code}"
    return str(payload.get("message") or "") if isinstance(payload, dict) else f"HTTP {res.status_code}"


def _webui_needs_relogin(res: httpx.Response) -> bool:
    if res.status_code in (401, 403):
        return True
    message = _webui_message(res).lower()
    return "token" in message and "empty" in message


# 重连退避：上限 60s（与官方通道同款），连上并稳定这么久之后才重置
BACKOFF_MAX = 60.0
STABLE_SECONDS = 30.0
# 同一条消息可能被重复推送（协议端重连后补发），按 message_id 去重
DEDUP_SIZE = 500
# 陌生人反复私聊时，配对码最多 60 秒提醒一次
PAIR_NOTIFY_INTERVAL = 60
# 配对码通知表涨到这么大才清一次过期条目（只在这张表上做，别每次私聊都重建）
PAIR_NOTIFY_CACHE_MAX = 256
# 私信路由上限：按会话记，只随配对人数增长
DM_ROUTE_MAX = 500
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


# 部署方自带协议端时，这三项只是新建实例时的默认值，不是全局覆盖：
# 两个 AI 各连一个 NapCat 时，各改各的，互不影响。留空＝让用户自己填。
_DEFAULT_WS_URL = os.environ.get("NAPCAT_WS_URL", "").strip()
_DEFAULT_WEBUI_URL = os.environ.get("NAPCAT_WEBUI_URL", "").strip()
_DEFAULT_WEBUI_TOKEN = os.environ.get("NAPCAT_WEBUI_TOKEN", "").strip()
_DEFAULT_WS_TOKEN = os.environ.get("NAPCAT_TOKEN", "").strip()

# 腾讯签发的扫码 ticket 有效期约 122 秒。协议端自己会轮转，但卡片上的倒计时必须从
# "我们确实刚要了一张"那一刻起算 —— 所以时间戳记在"要码"这个动作里，而不是从文件
# 年龄或协议端状态里反推（那两种算法都会把上一张码的年龄算进来）。
QR_TTL = 122

# 每一张码的出生时间，按协议端地址分开记：多实例时各算各的倒计时。
# 只放内存——最坏情况是重启后让用户再点一次"获取二维码"，不值得为此落库。
_QR_ISSUED: dict[str, tuple[str, float]] = {}


def _qr_state(issued_at: float | None, now: float) -> tuple[str, int]:
    """这张码还能不能扫，顺带给出剩余秒数

    live     还能扫（剩余秒数交给前端倒计时）
    expired  已经过了有效期，等用户点"刷新二维码"重新要一张
    """
    if not issued_at:
        return "expired", 0
    left = int(QR_TTL - max(0.0, now - issued_at))
    return ("live", left) if left > 0 else ("expired", 0)


def _qr_client(base_url: str = "", token: str = "") -> NapCatWebUI:
    """按调用方给的地址建一个控制面客户端；空值退回部署默认的协议端"""
    return NapCatWebUI(base_url or _DEFAULT_WEBUI_URL, token or _DEFAULT_WEBUI_TOKEN)


async def _account_of(client: NapCatWebUI) -> dict[str, Any]:
    """协议端现在挂的是哪个号（问不到就当不知道，不影响别的状态）"""
    try:
        raw = await client.login_info()
    except NapCatWebUIError:
        return {}
    return {
        "uin": str(raw.get("uin") or "").strip(),
        "nick": str(raw.get("nick") or "").strip(),
        "avatar": str(raw.get("avatarUrl") or "").strip(),
    }


async def _login_progress(
    client: NapCatWebUI, *, self_id: str = "", bot_name: str = "", instance: str = ""
) -> dict[str, Any] | None:
    """协议端的登录进度：登没登进去、卡在哪一步、挂的是哪个号、手上有没有能扫的码

    码本身不主动要——默认只是"把已经发出去的那张报出来"，让用户点击时再申请新的一张，
    这样倒计时永远从点击那一刻算起。
    """
    if not client.configured:
        return None
    try:
        state = await client.status()
    except NapCatWebUIError as e:
        return {"held_by_platform": True, "logged_in": False, "bot_name": bot_name or self_id,
                "login_error": str(e)}
    logged_in = bool(state.get("isLogin"))
    info: dict[str, Any] = {
        "held_by_platform": True,
        "logged_in": logged_in,
        "bot_name": bot_name or self_id,
        "login_phase": str(state.get("loginPhase") or ""),
    }
    if logged_in:
        account = await _account_of(client)
        info["qq_uin"] = account.get("uin") or ""
        info["qq_nick"] = account.get("nick") or ""
        if account.get("avatar"):
            info["qq_avatar"] = account["avatar"]
        # 登进去了但 isOffline：号还在，只是连不上服务器（被顶/网络断）
        info["online"] = not bool(state.get("isOffline"))
        if not info["bot_name"]:
            info["bot_name"] = info["qq_nick"] or info["qq_uin"]
        try:
            await save_account(instance, account)
        except SQLAlchemyError as e:   # 记账号失败不该把登录状态页弄挂；代码错误仍要炸出来
            logger.warning(f"记下登录账号失败（不影响登录）: {e}")
        issued = _QR_ISSUED.pop(client.base, None)
        if issued:
            info["qr_url"] = issued[0]
        return info
    # 没登进去：号也可能还记着（被顶下线），报出来页面才画得出"是谁掉线了"
    known = await load_account(instance)
    info["qq_uin"] = known.get("uin") or ""
    info["qq_nick"] = known.get("nick") or ""
    if known.get("avatar"):
        info["qq_avatar"] = known["avatar"]
    cached = _QR_ISSUED.get(client.base)
    if not cached:
        # 还没人点过"获取二维码"：只报状态，不给码
        return info
    url, issued_at = cached
    status_code, left = _qr_state(issued_at, time.time())
    info["qr_state"] = status_code
    info["qr_expires_in"] = left
    info["qr_url"] = url
    if status_code == "live":
        png = png_b64(url)
        if png:
            info["qr_png"] = png
            info["qr_at"] = int(issued_at)
    return info


async def _request_qr(client: NapCatWebUI) -> dict[str, Any]:
    """向协议端要一张新的登录码，并记下它的出生时间"""
    url = await client.refresh_qr()
    issued_at = time.time()
    _QR_ISSUED[client.base] = (url, issued_at)
    return {
        "qr_url": url,
        "qr_png": png_b64(url),
        "qr_expires_in": QR_TTL,
        "qr_at": int(issued_at),
    }


def _endpoint_schema() -> dict:
    """协议端怎么连

    一个协议端对应一个 QQ 号，所以这些字段属于实例：两个 AI 各接一个协议端时，
    各填各的地址与 token，互不覆盖。部署方配了默认值就预填，用户可以改。
    """
    return {
        "ws_url": {
            "section": _SEC_CONNECT,
            "type": "string", "title": "NapCat WebSocket 地址", "required": True,
            "title_en": "NapCat WebSocket URL", "title_ja": "NapCat WebSocket アドレス",
            "default": _DEFAULT_WS_URL or None,
            "description": "NapCat 的正向 WebSocket 地址，如 ws://napcat:3000；一个协议端一个地址",
            "description_en": "The forward WebSocket URL of NapCat, e.g. ws://napcat:3000 - one endpoint per URL",
            "description_ja": "NapCat の順方向 WebSocket アドレス（例 ws://napcat:3000）。プロトコル端末ごとに 1 つ",
        },
        "access_token": {
            "section": _SEC_CONNECT,
            "type": "string", "title": "AccessToken", "secret": True,
            "default": _DEFAULT_WS_TOKEN or None,
            "description": "NapCat 配置里的 access_token，没设就留空；只在这里填，加密落库、接口不回显",
            "description_en": "The access_token configured in NapCat; leave empty if unset; stored encrypted and never echoed back",
            "description_ja": "NapCat 側の access_token。未設定なら空欄。ここにのみ入力し、暗号化して保存します",
        },
        "webui_url": {
            "section": _SEC_CONNECT,
            "type": "string", "title": "协议端控制台地址", "required": True,
            "title_en": "Protocol console URL", "title_ja": "プロトコル端末コンソール",
            "default": _DEFAULT_WEBUI_URL or None,
            "description": "协议端 WebUI 地址，如 http://napcat:6099；扫码登录和登录状态都走它",
            "description_en": "The endpoint WebUI URL, e.g. http://napcat:6099 - QR sign-in and login state go through it",
            "description_ja": "プロトコル端末の WebUI アドレス（例 http://napcat:6099）。QR ログインと状態取得に使います",
        },
        "webui_token": {
            "section": _SEC_CONNECT,
            "type": "string", "title": "协议端控制台 Token", "secret": True,
            "default": _DEFAULT_WEBUI_TOKEN or None,
            "description": "协议端 WebUI 的 token（webui.json 里的那个）；同样加密落库、不回显",
            "description_en": "The protocol endpoint WebUI token (from webui.json); stored encrypted and never echoed back",
            "description_ja": "プロトコル端末 WebUI の token（webui.json の値）。暗号化して保存し、返しません",
        },
    }


# 设置项分块（平台按 section 把字段归到同一块里；块头由组内成员声明，这里只写一次）
_SEC_CONNECT = {"key": "connect", "title": "接入", "title_en": "Connection", "title_ja": "接続"}
_SEC_REPLY = {"key": "reply", "title": "回复", "title_en": "Replies", "title_ja": "返信"}
_SEC_QUOTA = {"key": "quota", "title": "对话额度", "title_en": "Conversation budget", "title_ja": "会話ごとの上限"}
_SEC_DM = {"key": "dm", "title": "私聊", "title_en": "Direct messages", "title_ja": "ダイレクトメッセージ"}


@service(
    name="QQ 通道（NapCat）",
    description="用 NapCat / OneBot v11 协议端把 AI 接进 QQ：群里被 @ 才唤醒，私聊默认要配对；AI 的回复发回 QQ",
    multi_instance=True,
    config_schema={
        **_endpoint_schema(),
        "quota_reset_enabled": {
            "section": _SEC_QUOTA,
            "type": "boolean", "title": "额度自动重置", "default": False,
            "inline": {
                "key": "quota_reset",
                "label": "额度自动重置",
                "label_en": "Budget reset",
                "label_ja": "上限の自動リセット",
                "hint": "开启后按「多久 + 几点」换窗口：新窗口从 0 开始算，旧窗口的数留在账里；"
                        "关着就一直累计，直到手动重置。窗口是算出来的，不需要定时任务，重启也不丢账。",
                "hint_en": "When on, the window rolls over per the period/time on the right: a new window "
                           "starts from zero and old numbers stay on record. When off it accumulates until "
                           "you reset manually. Windows are computed, so restarts change nothing.",
                "hint_ja": "オンにすると右の周期・時刻で窓が切り替わり、新しい窓は 0 から数えます（旧い数は記録に残ります）。"
                           "オフなら手動リセットまで累積します。窓は計算で決まるため再起動しても失いません。",
            },
            "title_en": "Reset the budget automatically",
            "title_ja": "上限を自動でリセット",
            "description": "开启后按下面的周期与时间点「换窗口」：新窗口从 0 开始算，旧窗口的数留在账里。"
                           "窗口是算出来的，不需要定时任务，重启也不丢账；关着就一直累计，直到手动重置。",
            "description_en": "When on, the budget window rolls over at the period/time below: a new window "
                              "starts from zero and the old numbers stay on record. Windows are computed, "
                              "not scheduled, so restarts change nothing; when off it accumulates until you "
                              "reset manually.",
            "description_ja": "オンにすると下の周期と時刻で窓が切り替わり、新しい窓は 0 から数えます（旧い数は記録として残ります）。"
                              "窓は計算で決まるため再起動しても失いません。オフなら手動リセットまで累積します。",
        },
        "quota_reset_period": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "多久重置一次", "default": "day",
            "inline": {"key": "quota_reset"},
            "show_if": {"field": "quota_reset_enabled", "equals": "true"},
            "title_en": "How often to reset", "title_ja": "リセットの間隔",
            "description": "每天 / 每 2 天 / 每周都以周一为基准；每小时只认「几点重置」里的分钟。",
            "description_en": "Daily, every 2 days and weekly are anchored on Monday; hourly only uses the "
                              "minute of the reset time.",
            "description_ja": "毎日・2 日ごと・毎週は月曜基準です。毎時は「リセット時刻」の分だけを使います。",
            "options": [
                {"value": "hour", "label": "每小时", "label_en": "Every hour", "label_ja": "1 時間ごと"},
                {"value": "day", "label": "每天", "label_en": "Every day", "label_ja": "毎日"},
                {"value": "2days", "label": "每 2 天", "label_en": "Every 2 days", "label_ja": "2 日ごと"},
                {"value": "week", "label": "每周（周一）", "label_en": "Every week (Monday)",
                 "label_ja": "毎週（月曜）"},
            ],
        },
        "quota_reset_anchor": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "几点重置（HH:MM）", "default": "00:00",
            "inline": {"key": "quota_reset"},
            "title_en": "Reset at (HH:MM)", "title_ja": "リセット時刻（HH:MM）",
            "show_if": [
                {"field": "quota_reset_enabled", "equals": "true"},
                {"field": "quota_reset_period", "in": ["day", "2days", "week"]},
            ],
            "description": "只在「每天 / 每 2 天 / 每周」时用得上（按小时是整点换窗口）。"
                           "按平台展示时区解释，每周固定以周一为基准。",
            "description_en": "Only used for daily / every 2 days / weekly (hourly rolls over on the hour). "
                              "Interpreted in the platform display timezone; weekly is anchored on Monday.",
            "description_ja": "「毎日 / 2 日ごと / 毎週」のときだけ使います（毎時は正時に切り替わります）。"
                              "プラットフォームの表示タイムゾーンで解釈し、毎週は月曜基準です。",
        },
        "quota_meter_mode": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "对话额度按什么算",
            "title_en": "How conversation budget is metered",
            "title_ja": "会話ごとの上限をどう数えるか",
            "description": "每个 QQ 群 / 每个私聊单独记账。默认按交涉 token（未命中的输入 + 输出）；"
                           "也可以按全部 token（含缓存命中）。",
            "description_en": "Each QQ group and each DM is metered separately. Default is billable tokens "
                              "(uncached input + output); alternatively every token including cache hits.",
            "description_ja": "QQ グループと DM を個別に計上します。既定は課金対象（未キャッシュ入力＋出力）。"
                              "キャッシュ命中を含む全トークンでも数えられます。",
            "default": "billable",
            "options": [
                {"value": "billable", "label": "交涉 token（未命中输入 + 输出，默认）",
                 "label_en": "Billable (uncached input + output)",
                 "label_ja": "課金対象（未キャッシュ入力＋出力）"},
                {"value": "total", "label": "全部 token（含缓存命中）",
                 "label_en": "All tokens (including cache hits)",
                 "label_ja": "全トークン（キャッシュ命中を含む）"},
            ],
        },
        "quota_limit_tokens": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "每个对话的默认额度（token）",
            "title_en": "Default budget per conversation (tokens)",
            "title_ja": "会話ごとの既定上限（トークン）",
            "description": "留空 = 1000000（100 万）。单个对话可在下面的「对话额度」里单独调整。",
            "description_en": "Empty = 1,000,000. A single conversation can be adjusted below.",
            "description_ja": "空欄＝1,000,000。会話ごとに下で個別調整できます。",
            "default": "1000000",
        },
        "quota_blocked_reply": {
            "section": _SEC_QUOTA,
            "type": "boolean", "title": "额度用完后自动回一句", "default": True,
            "title_en": "Reply automatically when the budget is used up",
            "title_ja": "上限に達したら自動で返信する",
            "description": "默认开启：同一个对话被拦下时回一句说明（10 分钟内最多一次）。",
            "description_en": "On by default: reply once per 10 minutes when a conversation is blocked.",
            "description_ja": "既定でオン：ブロック時に 10 分に 1 回まで説明を返します。",
        },
        "quota_blocked_reply_text": {
            "section": _SEC_QUOTA,
            "type": "string", "title": "自动回复内容",
            "title_en": "Auto reply text", "title_ja": "自動返信の文面",
            "description": "留空 = 平台默认的官方口吻那一句。",
            "description_en": "Empty = the platform's default wording.",
            "description_ja": "空欄＝プラットフォーム既定の文面。",
        },
        "target_agent": {
            "type": "string", "title": "这个通道接哪个 AI", "required": True, "managed": True,
            "description": "由平台按「这个 AI 的页面」自动填，不需要手工填；群里 @机器人 就等于在群里 @它",
        },
        "target_agent_id": {
            "type": "string", "title": "AI 的 id", "managed": True,
            "description": "平台填。认人按 id，名字只用来显示：AI 改名不影响通道",
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
            "section": _SEC_DM,
            "type": "string", "title": "私聊策略（pairing / owner / open / off）",
            "description": "默认 pairing：陌生人私聊只会收到一个配对码，批准之后才能跟 AI 说话；"
                           "owner 只认已配对的人（陌生人静默忽略）；open 谁都能聊；off 关闭私聊",
        },
    },
)
class QqNapcatPlugin(ServicePlugin):
    @classmethod
    async def hosted_status(cls, *, agent_id: int | None = None) -> dict[str, Any] | None:
        """实例还没建时，告诉卡片"部署默认那个协议端登录到哪一步了"

        这一步只服务"用户还没保存配置"的卡片：那时没有实例，也就谈不上"你的协议端"，
        只能问部署方预置的那一个。实例一旦存在，状态由实例自己报（见 get_status）。
        """
        return await _login_progress(_qr_client())

    async def _webui(self) -> NapCatWebUI:
        """本实例的控制面客户端

        运行中直接用 start() 读到的地址；还没跑起来（刚保存配置、起失败）就读已保存的配置——
        卡片扫码、"我的"页看账号，都发生在实例没跑起来的时候。
        """
        if self._panel is not None:
            return self._panel
        url, token = self._webui_url, self._webui_token
        if not url and not token:
            cfg = await self.config()
            url = str(cfg.get("webui_url") or "").strip()
            token = str(cfg.get("webui_token") or "").strip()
        self._panel = _qr_client(url, token)
        return self._panel

    async def panel_client(self) -> NapCatWebUI:
        """"打开协议端控制台"用的客户端：反代拿它的地址与鉴权头"""
        return await self._webui()

    def panel_entry(self, ticket: str) -> str:
        """控制台入口（相对平台反代基路径）：NapCat WebUI 在 webui/ 下"""
        return _panel_entry(ticket)

    def panel_rewrite(self, text: str, base: str) -> str:
        """把 WebUI 页面里的绝对路径补上反代基路径（平台只管转发，改写口径在这里）"""
        return _panel_rewrite(text, base)

    async def login_account(self) -> dict[str, Any]:
        """我的页要的账号视图：这个实例现在挂的是哪个号（从没挂过就都是空）"""
        info = await _login_progress(
            await self._webui(), self_id=self.self_id, instance=self.instance
        ) or {}
        return {
            "uin": info.get("qq_uin") or "",
            "nick": info.get("qq_nick") or "",
            "avatar": info.get("qq_avatar") or "",
            "logged_in": bool(info.get("logged_in")),
            "online": bool(info.get("online")),
        }

    async def request_login_qr(self) -> dict[str, Any]:
        """卡片上的"获取登录二维码"：向本实例自己的协议端要一张新的码"""
        return await _request_qr(await self._webui())

    async def quick_login(self) -> dict[str, Any]:
        """一键拉起上次扫过码的号

        先让协议端用本地保存的登录态恢复；腾讯那边已经作废（别处登录过、太久没上）
        就当场要一张新码——前端还是同一个按钮，用户不必知道这次走的是哪条路。
        """
        client = await self._webui()
        uin = str((await load_account(self.instance)).get("uin") or "").strip()
        if uin:
            try:
                await client.quick_login(uin)
            except NapCatWebUIError as e:
                logger.info(f"快登 {uin} 失败，改发新码：{e}")
            else:
                # 协议端是异步登录：等它登进去再回话，别让页面报个假成功
                for _ in range(6):
                    await asyncio.sleep(0.5)
                    try:
                        state = await client.status()
                    except NapCatWebUIError:
                        break
                    if state.get("isLogin"):
                        return {"logged_in": True, "qq_uin": uin}
        return {"logged_in": False, **await _request_qr(client)}

    @classmethod
    async def refresh_login_qr(cls, *, agent_id: int | None = None) -> dict[str, Any]:
        """向协议端要一张新的登录码

        走"要一张新的"而不是"把现有的拿出来"：倒计时要能从用户点击这一刻算起，
        拿旧码只能猜它还剩几秒。
        """
        return await _request_qr(_qr_client())

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._client: NapCatClient | None = None
        # 生效中的配置（start() 时读取；改配置后重启这个实例生效）
        self._ws_url = ""
        self._http_base = ""
        self._access_token = ""
        self._webui_url = ""
        self._webui_token = ""
        self._panel: NapCatWebUI | None = None
        self._copree_group_id = 0
        self._target_agent = ""
        self._target_agent_id = 0
        self._target_user_id = 0
        # 额度闸门（绑定目标后按新主人重建，见 _quota_gate）
        self._quota: Any | None = None
        self._allow: set[str] = set()
        self._dm_policy = "pairing"
        # 配对码通知节流：同一个人反复私聊时别把码刷屏（60 秒最多提醒一次）
        self._pair_notified: dict[str, float] = {}
        # 回复路由：群 → 最近一次来消息的 QQ 群；私信会话 → 那条私聊
        self._route: dict[int, dict[str, Any]] = {}
        # OrderedDict：满了丢最早建立的那条（见 _remember_dm_route）
        self._dm_route: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # 后台回复任务：保引用 + 停止前 drain（见 services/plugin/tasks.py）
        self._replies = PluginTasks()
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
            "hosted_endpoint": await _login_progress(
                await self._webui(), self_id=self.self_id, instance=self.instance
            ),
        }

    async def start(self) -> bool:
        self._panel = None          # 端点可能变了：控制面客户端按新配置重建
        cfg = await self.config()
        ws_url = str(cfg.get("ws_url") or "").strip()
        webui_url = str(cfg.get("webui_url") or "").strip()
        webui_token = str(cfg.get("webui_token") or "").strip()
        agent_id = str(cfg.get("target_agent_id") or "").strip()
        group_raw = str(cfg.get("copree_group_id") or "").strip()
        missing = [
            label for label, value in (
                ("NapCat WebSocket 地址", ws_url),
                ("AI", agent_id),
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
            await self._bind_target(agent_id)
        except Exception as e:
            # 起不来要给出人话原因：管理页会把它显示在状态里
            self.last_error = str(e)
            logger.warning(f"QQ 通道(NapCat)[{self.instance}] 启动中止：{self.last_error}")
            return False

        self._ws_url = ws_url
        self._http_base = _http_base(ws_url)
        self._access_token = str(cfg.get("access_token") or "").strip()
        # 控制面地址留空时退回部署默认的那一个：单协议端部署不用填
        self._webui_url = webui_url
        self._webui_token = webui_token
        self._allow = _split_list(cfg.get("qq_group_allowlist"))
        self._dm_policy = (str(cfg.get("dm_policy") or "pairing").strip().lower() or "pairing")
        await self._quota_gate().sync_policy(cfg)
        self._client = NapCatClient(self._http_base, self._access_token)
        self.last_error = ""
        self.connected = False
        self.started_at = time.time()

        from app.chat.outbound import register_sink

        # 出口注册名用 **key（带实例）**：id 是插件类型，两个实例同名会互相顶掉——
        # 2026-09-25 线上实测：新绑的第二个 QQ 通道把第一个的出口覆盖，群 64 的 AI 回复被静默丢弃
        self._sink_handle = register_sink(self.key, group=self._outbound_sink, dm=self._dm_outbound_sink)
        self._task = asyncio.create_task(self._supervise())
        logger.info(
            f"QQ 通道(NapCat)[{self.instance}] 已启动：AI={self._target_agent}"
            f"，群={self._copree_group_id}，私聊={self._dm_policy}，地址={self._http_base}"
        )
        return True

    async def stop(self) -> bool:
        from app.chat.outbound import unregister_sink

        unregister_sink(getattr(self, "_sink_handle", None) or self.key)
        self._route.clear()
        self._dm_route.clear()
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
                logger.warning(
                    f"QQ 通道(NapCat)[{self.instance}] supervise 异常退出：{type(e).__name__}: {e}"
                )
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
        self.started_at = 0.0
        logger.info(f"QQ 通道(NapCat)[{self.instance}] 已停止")
        return True

    async def _bind_target(self, agent_id: str) -> None:
        """认下"这个通道背后是哪个 AI"：只按 id 认，名字只是显示。

        两边共用一个身份：群里 @ 的是它、私聊聊的也是它 —— 不存在"群一个 AI、私聊另一个"。
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
        # WS 层心跳关着：协议端自己按 heartbeat_interval 推 meta 事件，保活不靠我们。
        # 代价是 NAT/代理静默断连时读循环会一直挂着、状态仍显示已连接 —— 开 ping_interval
        # 能治它，但改之前得先实测协议端会不会回 pong（本机协议端 WS 未开启，测不了）。
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
        # by_name：用户手打的 @机器人名（协议里没有 at 段，正文自己带着名字）
        by_name = check_mention(content, self._target_agent)
        wake = mentioned or by_name
        if wake and not by_name:
            # 用 id 令牌点名（名字会改会重名，绑定出来的 user_id 才是身份）；
            # 万一没绑到 user_id 才退回名字——旧写法仍被识别。
            # 正文里已经写着 @名字 就不补：补了 AI 会看到自己被点了两次名
            from app.utils.text import mention_token

            prefix = mention_token(self._target_user_id) if self._target_user_id else f"@{self._target_agent}"
            content = f"{prefix} {content}".strip()
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
            self._remember_dm_route(session_id, {"qq": user_id})

    def _remember_dm_route(self, session_id: str, route: dict[str, Any]) -> None:
        """记下"这条私信回到哪个 QQ 用户"：出站要用，但只留最近建立的那批

        按会话记 = 每个「QQ 用户 ↔ 这个 AI」一条，只随配对人数涨、不随时间落。
        满了丢最早那条：那个人下次来消息会重新入表，代价只是这期间 AI 主动找他时回不到 QQ。
        """
        self._dm_route[session_id] = route
        while len(self._dm_route) > DM_ROUTE_MAX:
            self._dm_route.popitem(last=False)
            logger.info(
                f"QQ 通道(NapCat)[{self.instance}] 私信路由已达上限 {DM_ROUTE_MAX}，丢弃最早的一条"
            )

    async def _dm_allowed(self, user_id: str, sender: dict) -> bool:
        """私聊放行判断 —— 默认 pairing：陌生人不进 AI，只领一个配对码。

        配对状态住在 Copree 的 external_identities 里，批准口在平台上；这里只做两件事：
        挡住陌生人、把码告诉他。NapCat 拿得到 QQ 号，但认人仍按 QQ 号（换号即换人）。
        """
        if self._dm_policy == "open":
            return True

        from app.database import async_session
        from app.services.channel import identity

        async with async_session() as db:
            status = await identity.status_of(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=user_id
            )
            if status == identity.APPROVED:
                return True
            if status == identity.BLOCKED or self._dm_policy == "owner":
                logger.info(f"QQ 私聊被挡下（{status or '未配对'}）：QQ {user_id}")
                return False
            row = await identity.upsert_pending(
                db, kind=self.channel_kind, owner_scope=self.instance, origin=user_id,
                display_name=str(sender.get("card") or sender.get("nickname") or ""),
            )
            code = row.code

        now = time.time()
        # 陌生人没有上限，这张表只服务下面这 60 秒的判断：过期条目顺手清掉
        if len(self._pair_notified) > PAIR_NOTIFY_CACHE_MAX:
            self._pair_notified = {
                key: at for key, at in self._pair_notified.items()
                if now - at < PAIR_NOTIFY_INTERVAL
            }
        if now - self._pair_notified.get(user_id, 0) < PAIR_NOTIFY_INTERVAL:
            logger.debug(f"配对码已发过，未到重发间隔：QQ {user_id}")
            return False
        self._pair_notified[user_id] = now
        try:
            if self._client is not None:
                await self._client.send_private_msg(
                    int(user_id),
                    identity.pairing_reply(code, "QQ 通道（NapCat）"),
                )
            logger.info(f"QQ 陌生人私聊 → 已下发配对码（QQ {user_id}，昵称 {row.display_name or '?'}）")
        except Exception as e:
            self.last_error = f"配对码下发失败：{type(e).__name__}: {e}"
            logger.warning(f"QQ 配对码下发失败：{self.last_error}")
        return False

    def _quota_gate(self):
        """额度闸门：绑定哪个 AI、哪种通道就记着那层关系；同步设置与准入都走它。

        设置解析、判定、异常兜底都在 app.services.channel.quota_gate —— 两个 QQ 插件
        各写一遍同样的东西，迟早会在其中一份里漏掉一个 if。
        """
        if self._quota is None:
            from app.services.channel.quota_gate import QuotaGate

            self._quota = QuotaGate(self._target_agent_id, self.channel_kind, logger)
        return self._quota

    async def _send_quota_notice(self, *, kind: str, text: str, qq_group: str = "", qq_user: str = "") -> None:
        """额度说明：NapCat 是自建协议端，主动发没有被动窗口限制；发不出去就算了"""
        client = self._client
        if client is None or not text:
            return
        try:
            if kind == "group" and qq_group:
                await client.send_group_msg(int(qq_group), text)
            elif kind == "dm" and qq_user:
                await client.send_private_msg(int(qq_user), text)
        except Exception as e:
            logger.info(f"额度说明没发出去（{kind}）：{str(e)[:120]}")

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
        from app.services.channel.anchor import ensure_channel_user

        async with async_session() as db:
            if not await self._quota_gate().admit(
                db, kind="group", target=self._copree_group_id,
                copree_group_id=self._copree_group_id,
                notify=lambda text: self._send_quota_notice(
                    kind="group", text=text, qq_group=qq_group),
            ):
                await db.commit()
                return
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
        from app.services.channel.anchor import ensure_channel_user

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
            if not await self._quota_gate().admit(
                db, kind="dm", target=session_id, label=str(_peer_name or ""),
                dm_session_id=session_id,
                notify=lambda text: self._send_quota_notice(
                    kind="dm", text=text, qq_user=user_id),
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
        # 正文里的 <@!平台id> 翻成 CQ 的真 @
        self._replies.spawn(
            self._send_reply(route, text, kind="group", link_mentions=True), f"{self.key} 群回复",
        )

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
        self._replies.spawn(self._send_reply(route, text, kind="dm"), f"{self.key} 私信回复")

    async def _translate_mentions(self, text: str) -> str:
        """<@!平台id> → [CQ:at,qq=<QQ号>]：只有走过这条通道的人，在 QQ 侧才有号可 @

        认不出的（站内的人、别的通道的人）退回名字——令牌原样发过去只会是乱码。
        查库放在这一步（后台任务）做，sink 是在发消息的链路里被调的，不能拖慢它。
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
            unknown = [uid for uid in ids if uid not in contacts]
            names: dict[int, str] = {}
            if unknown:
                rows = (await db.execute(
                    select(User.id, User.username).where(User.id.in_(unknown))
                )).all()
                names = {int(uid): str(name or "") for uid, name in rows}
        return render_mentions(
            text,
            lambda uid: (
                f"[CQ:at,qq={contacts[uid]}]" if uid in contacts
                else (f"@{names[uid]}" if names.get(uid) else "")
            ),
        )

    async def _send_reply(self, route: dict, text: str, kind: str, *, link_mentions: bool = False) -> None:
        """link_mentions：群回复才翻真 @（要查一次库认人）；私聊没有 @ 这回事"""
        from app.utils.text import strip_color_markup

        # 彩色标签是站内语法，QQ 客户端不认：出站先脱标签留文字
        text = strip_color_markup(text)
        client = self._client
        target = str(route.get("qq") or "")
        if client is None or not target:
            return
        if link_mentions:
            try:
                text = await self._translate_mentions(text)
            except Exception as e:
                # 翻不成真 @ 也要照发：这条回复本身比 @ 的成色重要
                logger.warning(f"QQ 群 @ 映射失败，按原文发送：{type(e).__name__}: {e}")
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
