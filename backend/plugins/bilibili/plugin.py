"""B 站通道 — 一个 AI 一个 B 站账号，由 AI 创作者自己扫码登录。

和另外两个参考实现的差别（为什么不是它们的形状）：
- AstrBot 的 bilibili 插件：全局一份 Cookie，动态订阅推到固定会话；
- bili-cli（bili-agent）：本地 CLI，80+ 命令，目标是让 Agent 当 UP 主；
- DSH 的 bilibili 插件：只把链接解析成卡片。

Copree 的形态：**通道侧**。一个 AI 一个实例（instance = agent-<id>），
B 站登录态是**这个 AI 的**，存在该实例的加密配置里——不是插件全局的，也不是平台的。
AI 在对话里查 B 站用工具（按 agent 取该实例的登录态）；订阅推送由服务轮询后用群里 AI 的身份发出。

登录流程（扫码，和 QQ 通道"每台机器人一份凭据"同一个道理）：
1. 保存配置（此时还没登录也能保存——sessdata 故意不是 required，否则"没登录就不能保存、
   不能保存就出不了二维码"成死锁）
2. 卡片调 get_status() 时：没有 Cookie 就生成/复用二维码并顺带走一次 poll，
   手机扫码确认后把 Set-Cookie 里的 SESSDATA / bili_jct / DedeUserID 加密写回该实例
3. 有 Cookie 后用 nav 接口确认登录态与昵称；失效就清掉、重新出码

接口口径（实测 2026-10-31）：
- 无需登录：x/web-interface/view、x/web-interface/popular、x/relation/stat、直播 room/v1/Room/get_info
- 需要登录：x/polymer/web-dynamic/v1/feed/space（无 Cookie 时 B 站回 -412 / 风控页，签名也救不了）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.services.plugin.api import service, ServicePlugin
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

PLUGIN_ID = "bilibili"
SEGMENT = "bilibili"

API = "https://api.bilibili.com"
LIVE_API = "https://api.live.bilibili.com"
PASSPORT_API = "https://passport.bilibili.com"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

QR_TTL_SECONDS = 180          # B 站二维码有效期（官方约 180 秒）
NAV_TTL_SECONDS = 120         # 登录态校验缓存：卡片会被反复拉取，别每次都打 nav


class BiliError(Exception):
    """B 站接口错误：带接口自己的 code/message，方便直接转述给用户"""


# ============================================================
# HTTP：一处出口（测试只打桩这一层）
# ============================================================

async def _raw_get(url: str, params: dict | None = None, cookie: str = "") -> httpx.Response:
    headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            return await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise BiliError(f"连接 B 站失败：{e}") from e


def _data_of(resp: httpx.Response) -> dict:
    """统一取 data 段：非 JSON（风控页）与 code != 0 都变成可读的 BiliError。"""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" not in ctype:
        raise BiliError(
            "B 站返回了反爬页面，不是 JSON。这个接口需要登录态：请在通道卡片上扫码登录。"
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise BiliError("B 站返回的数据不是合法 JSON") from e
    code = payload.get("code")
    if code not in (0, None):
        raise BiliError(f"B 站接口返回 {code}：{payload.get('message') or payload.get('msg') or '未知错误'}")
    return payload.get("data") or {}


async def _get(url: str, params: dict | None = None, cookie: str = "") -> dict:
    return _data_of(await _raw_get(url, params, cookie))


# ============================================================
# 实例与登录态
# ============================================================

def _instance_of(agent_id: int) -> str:
    """一个 AI 一个实例（与 QQ 通道同一套命名，平台侧由 channel.instance_of 定义）。"""
    from app.services.plugin.channel import instance_of

    return instance_of(int(agent_id))


async def _instance_config(instance: str) -> dict:
    from app.services.plugin import config as plugin_config

    return await plugin_config.get_config(PLUGIN_ID, instance)


def _cookie_of(cfg: dict) -> str:
    """把实例配置里的三个 Cookie 拼成请求头；缺 SESSDATA 视为未登录。"""
    sessdata = (cfg.get("sessdata") or "").strip()
    if not sessdata:
        return ""
    parts = [f"SESSDATA={sessdata}"]
    if cfg.get("bili_jct"):
        parts.append(f"bili_jct={cfg['bili_jct']}")
    if cfg.get("dedeuserid"):
        parts.append(f"DedeUserID={cfg['dedeuserid']}")
    return "; ".join(parts)


async def _agent_cookie(agent_id: int) -> str:
    return _cookie_of(await _instance_config(_instance_of(agent_id)))


def _cookies_from_response(resp: httpx.Response) -> dict:
    """从 poll 响应的 Set-Cookie 里挑出登录要用的三项。"""
    found: dict[str, str] = {}
    for raw in resp.headers.get_list("set-cookie"):
        head = raw.split(";", 1)[0]
        if "=" not in head:
            continue
        name, _, value = head.partition("=")
        name = name.strip()
        if name in ("SESSDATA", "bili_jct", "DedeUserID"):
            found[name.lower()] = value.strip()
    return {
        "sessdata": found.get("sessdata", ""),
        "bili_jct": found.get("bili_jct", ""),
        "dedeuserid": found.get("dedeuserid", ""),
    }


async def _nav(cookie: str) -> dict:
    """确认登录态：isLogin / uname / mid。没有 Cookie 时直接返回未登录。"""
    if not cookie:
        return {"isLogin": False}
    try:
        data = await _get(f"{API}/x/web-interface/nav", None, cookie)
    except BiliError as e:
        logger.info("B 站登录态校验失败（按未登录处理）: %s", e)
        return {"isLogin": False}
    return {
        "isLogin": bool(data.get("isLogin")),
        "uname": data.get("uname", ""),
        "mid": data.get("mid"),
        "face": data.get("face", ""),
    }


# 二维码会话：一个实例一份（同一个人反复刷新卡片不许反复换码，B 站会判失效）
_qr_sessions: dict[str, dict] = {}
_nav_cache: dict[str, tuple[float, dict]] = {}


async def _nav_cached(cookie: str) -> dict:
    now = time.time()
    hit = _nav_cache.get(cookie)
    if hit and now - hit[0] < NAV_TTL_SECONDS:
        return hit[1]
    info = await _nav(cookie)
    _nav_cache[cookie] = (now, info)
    return info


async def _qr_start(instance: str) -> dict:
    data = await _get(f"{PASSPORT_API}/x/passport-login/web/qrcode/generate")
    key = str(data.get("qrcode_key") or "")
    session = {
        "key": key,
        "url": str(data.get("url") or ""),
        "created": time.time(),
        "state": "pending",
        "state_text": "等待扫码",
    }
    _qr_sessions[instance] = session
    return session


def _qr_png_b64(url: str) -> str:
    """把登录链接画成二维码 PNG（base64）。

    为什么 vendor 一个库：后端镜像里没有 qrcode，而容器/宿主都可能连不上 PyPI
    （实测容器内 DNS 失败）。"装上就能扫码"不该以能不能联网装依赖为前提。
    """
    if not url:
        return ""
    try:
        import base64
        import io
        import sys
        from pathlib import Path

        vendor = Path(__file__).resolve().parent / "_vendor"
        if str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        import qrcode  # noqa: PLC0415 —— 只在真的要画码时导入
        from PIL import Image, ImageDraw

        qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        box, border = 6, 16
        size = len(matrix) * box + border * 2
        img = Image.new("RGB", (size, size), "white")
        draw = ImageDraw.Draw(img)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    x0, y0 = border + x * box, border + y * box
                    draw.rectangle([x0, y0, x0 + box - 1, y0 + box - 1], fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:  # noqa: BLE001 —— 画不出码不该让整张卡片报错
        logger.warning("B 站二维码渲染失败（%s），只回链接: %s", url[:40], e)
        return ""


_QR_STATE_TEXT = {
    0: "登录成功",
    86101: "等待扫码",
    86090: "已扫码，等待手机上确认",
    86038: "二维码已失效",
}


async def _qr_poll(instance: str) -> dict:
    """poll 一次，并在成功时把 Cookie 写回该实例（唯一写入点）。

    B 站把真正的业务码放在 data.code 里（外层 code 仍是 0），所以这里看的是 data。
    """
    session = _qr_sessions.get(instance)
    if not session or time.time() - session["created"] > QR_TTL_SECONDS:
        return await _qr_start(instance) if not session else {**session, "state": "expired"}

    resp = await _raw_get(
        f"{PASSPORT_API}/x/passport-login/web/qrcode/poll",
        {"qrcode_key": session["key"]},
    )
    data = _data_of(resp)
    code = int(data.get("code") or 0)
    session["state_text"] = _QR_STATE_TEXT.get(code, f"未知状态 {code}")

    if code == 0:
        cookies = _cookies_from_response(resp)
        if not cookies["sessdata"]:
            session["state"] = "error"
            session["state_text"] = "登录成功但没拿到 SESSDATA，请重试"
            return session
        # 刚拿到的 Cookie 直接拼一次（不经过实例配置读回来：那一步有加密往返，也没必要）
        info = await _nav(
            "; ".join(
                f"{name}={cookies[key]}"
                for name, key in (("SESSDATA", "sessdata"), ("bili_jct", "bili_jct"), ("DedeUserID", "dedeuserid"))
                if cookies.get(key)
            )
        )
        await _save_login(instance, cookies, info)
        session["state"] = "done"
        session["state_text"] = f"登录成功：{info.get('uname') or '未知昵称'}"
        _qr_sessions.pop(instance, None)
        return session

    if code == 86090:
        session["state"] = "scanned"
    elif code in (86101,):
        session["state"] = "pending"
    else:  # 86038 失效等
        session["state"] = "expired"
    return session


async def _save_login(instance: str, cookies: dict, info: dict) -> None:
    from app.services.plugin import config as plugin_config

    await plugin_config.set_config(
        PLUGIN_ID,
        {
            "sessdata": cookies.get("sessdata", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "dedeuserid": cookies.get("dedeuserid", ""),
            "nickname": str(info.get("uname") or ""),
            "bili_mid": str(info.get("mid") or ""),
        },
        instance=instance,
        actor="bilibili-qr-login",
    )
    _nav_cache.clear()
    logger.info("B 站登录成功并已写入实例配置: %s", instance)


async def _clear_login(instance: str) -> None:
    from app.services.plugin import config as plugin_config

    await plugin_config.set_config(
        PLUGIN_ID,
        {"sessdata": "", "bili_jct": "", "dedeuserid": "", "nickname": "", "bili_mid": ""},
        instance=instance,
        actor="bilibili-logout",
    )
    _nav_cache.clear()
    _qr_sessions.pop(instance, None)


# ============================================================
# 订阅存储（插件私有的 JSON；按实例隔离，一个 AI 的订阅不串到别人）
# ============================================================

_sub_path: Path | None = None
_subs_lock = asyncio.Lock()


def _sub_file() -> Path:
    global _sub_path
    if _sub_path is None:
        from app.paths import PLUGIN_DATA_DIR

        _sub_path = PLUGIN_DATA_DIR / PLUGIN_ID / "subscriptions.json"
    return _sub_path


async def _load_subs() -> dict:
    async with _subs_lock:
        path = _sub_file()
        if not path.exists():
            return {"subs": []}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 —— 坏文件不该让整条通道挂掉
            logger.warning("B 站订阅文件损坏，按空处理: %s: %s", path, e)
            return {"subs": []}
        if not isinstance(raw, dict) or not isinstance(raw.get("subs"), list):
            return {"subs": []}
        return raw


async def _save_subs(store: dict) -> None:
    async with _subs_lock:
        path = _sub_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


# ============================================================
# 结果整形
# ============================================================

def _ts_to_str(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _video_result(data: dict) -> dict:
    stat = data.get("stat") or {}
    owner = data.get("owner") or {}
    bvid = data.get("bvid", "")
    return {
        "bvid": bvid,
        "title": data.get("title", ""),
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "up主": {"mid": owner.get("mid"), "name": owner.get("name", "")},
        "分区": data.get("tname", ""),
        "时长秒": data.get("duration", 0),
        "发布时间": _ts_to_str(data.get("pubdate")),
        "数据": {
            "播放": stat.get("view", 0),
            "弹幕": stat.get("danmaku", 0),
            "评论": stat.get("reply", 0),
            "点赞": stat.get("like", 0),
            "投币": stat.get("coin", 0),
            "收藏": stat.get("favorite", 0),
            "分享": stat.get("share", 0),
        },
        "简介": (data.get("desc") or "")[:300],
        "分P数": len(data.get("pages") or []),
    }


def _dynamic_text(item: dict) -> str:
    modules = item.get("modules") or {}
    dynamic = modules.get("module_dynamic") or {}
    desc = dynamic.get("desc") or {}
    text = (desc.get("text") or "").strip()
    major = dynamic.get("major") or {}
    archive = major.get("archive") or {}
    if archive.get("title"):
        text = f"{text}\n[投稿] {archive.get('title')}（{archive.get('bvid', '')}）".strip()
    opus = major.get("opus") or {}
    if opus.get("title"):
        text = f"{text}\n[图文] {opus.get('title')}".strip()
    return text or "[无正文]"


def _dynamic_one(item: dict) -> dict:
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    return {
        "动态id": str(item.get("id_str") or ""),
        "类型": item.get("type", ""),
        "作者": author.get("name", ""),
        "发布时间": _ts_to_str(author.get("pub_ts")),
        "pub_ts": int(author.get("pub_ts") or 0),
        "正文": _dynamic_text(item),
        "链接": f"https://t.bilibili.com/{item.get('id_str', '')}",
    }


# ============================================================
# 工具（AI 在对话里调用；登录态按 agent → 实例取）
# ============================================================

class BiliAccount(ToolPlugin):
    name = "bili_account"
    description = "查这个 AI 绑定的 B 站账号：是否已登录、B 站昵称与 UID。未登录时说明需要创建者去通道卡片扫码。"
    segment = SEGMENT
    parameters = {}
    states = ["active", "dnd"]
    admin_description = "查本 AI 的 B 站登录态。"
    trigger_condition = "用户问 AI 的 B 站账号情况"
    usage_notes = [(300, "未登录时不要假装能查动态；先让创建者去通道卡片扫码。")]

    async def execute(self, db, agent_id, group_id, arguments, context):
        cfg = await _instance_config(_instance_of(agent_id))
        cookie = _cookie_of(cfg)
        if not cookie:
            return {"ok": True, "logged_in": False, "hint": "这个 AI 还没有登录 B 站：请创建者打开「B 站」通道卡片扫码。"}
        info = await _nav_cached(cookie)
        return {
            "ok": True,
            "logged_in": bool(info.get("isLogin")),
            "uname": info.get("uname") or cfg.get("nickname") or "",
            "mid": info.get("mid") or cfg.get("bili_mid"),
        }


class BiliVideo(ToolPlugin):
    name = "bili_video"
    description = "查 B 站视频详情：标题、UP 主、播放/点赞/投币/收藏、时长、发布时间、简介。参数给 BV 号（BV1xx…）或 av 号。"
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号（BV1GJ411x7h7）或 av 号（如 av170001）"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "查单条 B 站视频的详情数据。"
    trigger_condition = "用户发了 B 站视频链接或问某个视频"
    usage_notes = [(300, "只给 BV/av 号就够；用户发的是整条 URL 就自己截取 BV 号。")]

    async def execute(self, db, agent_id, group_id, arguments, context):
        raw = str(arguments.get("bvid") or "").strip()
        params = {"aid": raw[2:]} if raw.lower().startswith("av") else {"bvid": raw}
        data = await _get(f"{API}/x/web-interface/view", params, await _agent_cookie(agent_id))
        return {"ok": True, "video": _video_result(data)}


class BiliHot(ToolPlugin):
    name = "bili_hot"
    description = "B 站全站热门榜。返回名次、BV 号、标题、UP 主、播放量。"
    segment = SEGMENT
    parameters = {"limit": {"type": "integer", "description": "要几条，默认 5，最多 20"}}
    states = ["active", "dnd"]
    admin_description = "取 B 站全站热门视频榜。"
    trigger_condition = "用户问 B 站最近有什么热门"
    usage_notes = [(300, "热门榜没有分区参数；要分区内容只能按标题自己筛。")]

    async def execute(self, db, agent_id, group_id, arguments, context):
        limit = max(1, min(int(arguments.get("limit") or 5), 20))
        data = await _get(
            f"{API}/x/web-interface/popular", {"ps": limit, "pn": 1}, await _agent_cookie(agent_id)
        )
        items = []
        for rank, item in enumerate((data.get("list") or [])[:limit], start=1):
            owner = item.get("owner") or {}
            stat = item.get("stat") or {}
            items.append({
                "名次": rank,
                "bvid": item.get("bvid", ""),
                "标题": item.get("title", ""),
                "UP主": owner.get("name", ""),
                "播放": stat.get("view", 0),
                "链接": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
            })
        return {"ok": True, "count": len(items), "items": items}


class BiliUser(ToolPlugin):
    name = "bili_user"
    description = "查 B 站 UP 主粉丝数与关注数（mid 即 space.bilibili.com/<mid> 里的数字）。"
    segment = SEGMENT
    parameters = {"mid": {"type": "integer", "description": "UP 主的 mid（UID）"}}
    required = ["mid"]
    states = ["active", "dnd"]
    admin_description = "查 UP 主粉丝/关注数据。"
    trigger_condition = "用户问某个 UP 主有多少粉丝"
    usage_notes = [(400, "粉丝数是快照，别当实时赛事记分。")]

    async def execute(self, db, agent_id, group_id, arguments, context):
        mid = int(arguments["mid"])
        data = await _get(f"{API}/x/relation/stat", {"vmid": mid}, await _agent_cookie(agent_id))
        return {
            "ok": True,
            "mid": mid,
            "粉丝数": data.get("follower", 0),
            "关注数": data.get("following", 0),
            "主页": f"https://space.bilibili.com/{mid}",
        }


class BiliLive(ToolPlugin):
    name = "bili_live"
    description = "查 B 站直播间状态：是否在播、标题、当前人气、主播 uid。参数是房间号。"
    segment = SEGMENT
    parameters = {"room_id": {"type": "integer", "description": "直播间房间号"}}
    required = ["room_id"]
    states = ["active", "dnd"]
    admin_description = "查直播间开播状态与标题。"
    trigger_condition = "用户问某个主播在不在播"
    usage_notes = [(300, "live_status=1 是在播；人气值是平台口径，不是人数。")]

    async def execute(self, db, agent_id, group_id, arguments, context):
        room_id = int(arguments["room_id"])
        data = await _get(f"{LIVE_API}/room/v1/Room/get_info", {"room_id": room_id})
        return {
            "ok": True,
            "room_id": room_id,
            "在播": data.get("live_status") == 1,
            "标题": data.get("title", ""),
            "人气": data.get("online", 0),
            "主播uid": data.get("uid"),
            "链接": f"https://live.bilibili.com/{room_id}",
        }


class BiliDynamics(ToolPlugin):
    name = "bili_dynamics"
    description = "看某个 UP 主最近的动态（需要这个 AI 已登录 B 站，否则 B 站风控拒绝）。"
    segment = SEGMENT
    parameters = {
        "mid": {"type": "integer", "description": "UP 主的 mid"},
        "limit": {"type": "integer", "description": "要几条，默认 5，最多 20"},
    }
    required = ["mid"]
    states = ["active", "dnd"]
    admin_description = "取 UP 主最新动态（依赖该 AI 的 B 站登录态）。"
    trigger_condition = "用户问某个 UP 主最近发了什么"
    usage_notes = [
        (300, "没登录时报错就去让创建者扫码，不要改去搜索猜内容。"),
        (300, "带 [投稿] 标记的才是视频，其余是图文动态。"),
    ]

    async def execute(self, db, agent_id, group_id, arguments, context):
        mid = int(arguments["mid"])
        limit = max(1, min(int(arguments.get("limit") or 5), 20))
        cookie = await _agent_cookie(agent_id)
        if not cookie:
            raise BiliError("这个 AI 还没有登录 B 站：请创建者打开「B 站」通道卡片扫码。")
        data = await _get(
            f"{API}/x/polymer/web-dynamic/v1/feed/space",
            {"host_mid": mid, "features": "itemOpusStyle"},
            cookie,
        )
        items = [_dynamic_one(i) for i in (data.get("items") or [])[:limit]]
        return {"ok": True, "mid": mid, "count": len(items), "items": items}


class BiliSubscribe(ToolPlugin):
    name = "bili_subscribe"
    description = "管理这个 AI 的 B 站订阅：订阅 UP 主后，对方发新动态会推送到指定群。action=list 看订阅，add 订阅，remove 退订。"
    segment = SEGMENT
    parameters = {
        "action": {"type": "string", "description": "list / add / remove", "enum": ["list", "add", "remove"]},
        "mid": {"type": "integer", "description": "UP 主 mid（add / remove 时必填）"},
        "group_id": {"type": "integer", "description": "推送目标群（add / remove 时必填；list 可省略）"},
        "name": {"type": "string", "description": "UP 主昵称（可选，只用于推送文案）"},
    }
    required = ["action"]
    states = ["active"]
    admin_description = "订阅 UP 主动态并推送到群。推送以该群某个 AI 的身份发出。"
    trigger_condition = "用户要求订阅某个 UP 主"
    usage_notes = [
        (300, "订阅按「这个 AI + 群」算：同一个 UP 可以被多个群订阅，退订只影响指定群。"),
        (300, "没登录 B 站时订阅能存下来但不会推送，要如实告诉用户。"),
    ]

    async def execute(self, db, agent_id, group_id, arguments, context):
        action = str(arguments.get("action") or "").lower()
        instance = _instance_of(agent_id)
        store = await _load_subs()
        mine = [s for s in store["subs"] if s.get("instance") == instance]

        if action == "list":
            only = arguments.get("group_id")
            items = [s for s in mine if only is None or s["group_id"] == int(only)]
            return {"ok": True, "count": len(items), "items": items}

        mid, gid = arguments.get("mid"), arguments.get("group_id")
        if mid is None or gid is None:
            return {"ok": False, "error": "add / remove 需要同时给 mid 和 group_id"}
        mid, gid = int(mid), int(gid)

        if action == "add":
            if not any(s["mid"] == mid and s["group_id"] == gid for s in mine):
                store["subs"].append({
                    "instance": instance,
                    "mid": mid,
                    "group_id": gid,
                    "name": (arguments.get("name") or "").strip(),
                    "last_ts": 0,
                })
                await _save_subs(store)
            return {"ok": True, "subscribed": True, "mid": mid, "group_id": gid}

        if action == "remove":
            before = len(store["subs"])
            store["subs"] = [
                s for s in store["subs"]
                if not (s.get("instance") == instance and s["mid"] == mid and s["group_id"] == gid)
            ]
            removed = before - len(store["subs"])
            if removed:
                await _save_subs(store)
            return {"ok": True, "removed": removed, "mid": mid, "group_id": gid}

        return {"ok": False, "error": f"未知 action：{action}（只支持 list / add / remove）"}


# ============================================================
# 服务：这个实例的登录态展示 + 订阅轮询推送
# ============================================================

_PUSH_TEMPLATE = "【B 站更新】{name} 发了新动态：\n{text}\n{link}"


@service(
    name="B 站",
    description="一个 AI 一个 B 站账号：创建者扫码登录后，AI 能在对话里查 B 站，订阅的 UP 主更新也会推到群里。",
    multi_instance=True,
    config_schema={
        "target_agent": {
            "type": "string", "title": "这个账号是谁（AI 名字）", "required": True, "managed": True,
            "description": "由平台按「这个 AI 的通道页」自动填：登录的是这个 AI 的 B 站账号",
        },
        "sessdata": {
            "type": "string", "title": "SESSDATA", "secret": True, "credential": True,
            "description": "扫码登录后自动填入（也可以手工粘贴）。加密存库，接口不回显",
            "description_en": "Filled automatically after QR login (you may paste it manually). Stored encrypted, never echoed",
            "description_ja": "QR ログイン後に自動入力されます（手動貼り付けも可）。暗号化して保存し、API は返しません",
        },
        "bili_jct": {
            "type": "string", "title": "bili_jct", "secret": True, "credential": True,
            "description": "扫码登录后自动填入（写操作要用；只读查询可以不填）",
        },
        "dedeuserid": {
            "type": "string", "title": "DedeUserID", "secret": True, "credential": True,
            "description": "扫码登录后自动填入",
        },
        "nickname": {"type": "string", "title": "B 站昵称", "description": "扫码登录后自动填入，卡片上显示"},
        "bili_mid": {"type": "string", "title": "B 站 UID", "description": "扫码登录后自动填入"},
        "poll_minutes": {
            "type": "integer", "title": "检查间隔（分钟）", "default": 10,
            "description": "订阅轮询间隔，最小 1 分钟。每个 UP 主一次请求，订阅多了要相应放宽。",
        },
    },
)
class BilibiliChannel(ServicePlugin):
    """一个实例 = 一个 AI 的 B 站账号（登录态 + 订阅都按实例隔离）。"""

    def __init__(self) -> None:
        super().__init__()
        self._task: asyncio.Task | None = None
        self._last_run: str = ""
        self._last_error: str = ""
        self._pushed: int = 0

    # ── 状态：卡片就是靠它驱动扫码的 ──

    async def get_status(self) -> dict:
        """卡片状态 + 扫码推进。

        为什么在 get_status 里推进登录：通道卡片没有任何"扫码"按钮的约定，
        卡片被打开/刷新时拿状态是唯一一定会发生的事。每取一次状态顺手 poll 一次，
        手机上确认后下一次刷新就是"已登录"，不需要新接口、也不需要前端配合。
        """
        instance = self.instance
        cfg = await _instance_config(instance)
        cookie = _cookie_of(cfg)

        subs = (await _load_subs())["subs"]
        detail: dict = {
            "running": self._task is not None and not self._task.done(),
            "state": "未登录",
            "login": None,
            "account": None,
            "subscriptions": len([s for s in subs if s.get("instance") == instance]),
            "last_run": self._last_run,
            "last_error": self._last_error,
            "pushed": self._pushed,
        }

        if not cookie:
            try:
                session = _qr_sessions.get(instance)
                if session is None:
                    session = await _qr_start(instance)
                elif session.get("state") in ("pending", "scanned"):
                    session = await _qr_poll(instance)
                elif session.get("state") == "expired" or time.time() - session.get("created", 0) > QR_TTL_SECONDS:
                    session = await _qr_start(instance)
                qr_url = str(session.get("url") or "")
                detail["login"] = {
                    "state": session.get("state"),
                    "text": session.get("state_text"),
                    "qr_url": qr_url,
                    "expires_in": max(0, int(QR_TTL_SECONDS - (time.time() - session.get("created", 0)))),
                }
                # 卡片真正渲染的是 hosted_endpoint（前端既有契约，QQ/NapCat 共用）。
                # 只报 login 的话界面上一个码都没有——用户会问"哪里有码"。
                detail["hosted_endpoint"] = {
                    "held_by_platform": True,
                    "logged_in": False,
                    "bot_name": "",
                    "qr_png": _qr_png_b64(qr_url),
                    "qr_url": qr_url,
                    "qr_at": int(session.get("created") or 0),
                }
            except BiliError as e:
                detail["login"] = {"state": "error", "text": str(e)}
            return detail

        info = await _nav_cached(cookie)
        if not info.get("isLogin"):
            # 登录失效：清掉凭据，下一次状态调用就会重新出码
            await _clear_login(instance)
            detail["state"] = "登录失效"
            detail["login"] = {"state": "expired", "text": "B 站登录已失效，请重新扫码"}
            return detail

        detail["state"] = "已登录"
        uname = info.get("uname") or cfg.get("nickname") or ""
        detail["account"] = {"uname": uname, "mid": info.get("mid") or cfg.get("bili_mid")}
        detail["hosted_endpoint"] = {"held_by_platform": True, "logged_in": True, "bot_name": uname}
        return detail

    @classmethod
    async def hosted_status(cls, *, agent_id: int | None = None) -> dict:
        """实例还没建（配置还没保存）时也要出码：用户不该对着空表单猜"码在哪"。

        二维码按实例生成（一个 AI 一个 B 站账号），所以这里用平台传进来的 agent_id 推实例名；
        扫码成功后 Cookie 就写进该实例的配置——配置一旦存在，平台会自己把实例建起来，
        卡片随后从 get_status()（已登录）接管。
        """
        if agent_id is None:
            return {"held_by_platform": True, "logged_in": False, "bot_name": ""}
        instance = _instance_of(agent_id)
        cfg = await _instance_config(instance)
        cookie = _cookie_of(cfg)
        if cookie:
            info = await _nav_cached(cookie)
            if info.get("isLogin"):
                return {
                    "held_by_platform": True,
                    "logged_in": True,
                    "bot_name": info.get("uname") or cfg.get("nickname") or "",
                }
        try:
            session = _qr_sessions.get(instance) or await _qr_start(instance)
            if session.get("state") in ("pending", "scanned") or time.time() - session.get("created", 0) > QR_TTL_SECONDS:
                session = await _qr_poll(instance) if session.get("state") in ("pending", "scanned") else await _qr_start(instance)
            qr_url = str(session.get("url") or "")
            return {
                "held_by_platform": True,
                "logged_in": session.get("state") == "done",
                "bot_name": "",
                "qr_png": _qr_png_b64(qr_url),
                "qr_url": qr_url,
                "qr_at": int(session.get("created") or 0),
            }
        except BiliError as e:
            return {"held_by_platform": True, "logged_in": False, "bot_name": "", "error": str(e)[:120]}

    # ── 生命周期 ──

    async def start(self) -> bool:
        if self._task is not None and not self._task.done():
            return True
        # 没登录也启动：轮询任务自己会跳过，卡片仍要能显示二维码
        self._task = asyncio.create_task(self._loop(), name=f"bilibili-{self.instance}")
        return True

    async def stop(self) -> bool:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        return True

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # 进程刚起来时数据库/网络都没热，第一圈就失败会留下误导性的 last_error
        while True:
            try:
                await self._run_once()
                self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 轮询任务不能因为一次异常死掉
                self._last_error = str(e)[:200]
                logger.warning("B 站订阅轮询失败（%s）: %s", self.instance, e)
            await asyncio.sleep(await self._interval_seconds())

    async def _interval_seconds(self) -> int:
        cfg = await _instance_config(self.instance)
        try:
            minutes = int(cfg.get("poll_minutes") or 10)
        except (TypeError, ValueError):
            minutes = 10
        return max(1, minutes) * 60

    async def _run_once(self) -> None:
        cfg = await _instance_config(self.instance)
        cookie = _cookie_of(cfg)
        store = await _load_subs()
        mine = [s for s in store["subs"] if s.get("instance") == self.instance]
        if not mine:
            self._last_run = _now()
            return
        if not cookie:
            self._last_run = _now()
            raise BiliError("这个 AI 还没登录 B 站，订阅无法检查（查询类工具不受影响）")

        changed = False
        for sub in mine:
            try:
                fresh = await self._fetch_new(sub, cookie)
            except BiliError as e:
                self._last_error = f"mid={sub['mid']}: {e}"
                logger.warning("B 站订阅检查失败 mid=%s: %s", sub["mid"], e)
                continue
            if not fresh:
                continue
            latest = max(i["pub_ts"] for i in fresh)
            pushed = 0
            for item in fresh[:3]:  # 一轮最多推 3 条：超出的直接跳过，不补推（补推只会刷屏）
                if await self._push(sub, item):
                    pushed += 1
            self._pushed += pushed
            sub["last_ts"] = max(int(sub.get("last_ts") or 0), latest)
            changed = True
        if changed:
            await _save_subs(store)
        self._last_run = _now()

    async def _fetch_new(self, sub: dict, cookie: str) -> list[dict]:
        data = await _get(
            f"{API}/x/polymer/web-dynamic/v1/feed/space",
            {"host_mid": int(sub["mid"]), "features": "itemOpusStyle"},
            cookie,
        )
        last_ts = int(sub.get("last_ts") or 0)
        items = [_dynamic_one(i) for i in (data.get("items") or [])]
        fresh = [i for i in items if i["pub_ts"] > last_ts]
        fresh.sort(key=lambda i: i["pub_ts"])
        return fresh

    async def _push(self, sub: dict, item: dict) -> bool:
        """以群里某个 AI 的身份发一条推送；没有 AI 的群不推（幽灵消息比不推更糟）。"""
        from sqlalchemy import select

        from app.chat.gm import send_gm_message
        from app.database import async_session
        from app.models.group import GroupMember

        group_id = int(sub["group_id"])
        async with async_session() as db:
            row = (await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.member_type == "ai",
                ).limit(1)
            )).scalar_one_or_none()
            if row is None:
                self._last_error = f"群 {group_id} 里没有 AI，跳过推送"
                return False
            await send_gm_message(
                db,
                group_id=group_id,
                sender_type="ai",
                sender_id=int(row.member_id),
                content=_PUSH_TEMPLATE.format(
                    name=sub.get("name") or f"UP {sub['mid']}",
                    text=item["正文"],
                    link=item["链接"],
                ),
                source="world",   # 不是"人发的"：别触发群消息钩子，否则可能自激
                via="bilibili",
            )
            await db.commit()
        return True


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _register_segment() -> None:
    ToolRegistry.register_segment(SEGMENT, {
        "name": "B 站",
        "description": "查 B 站视频、热门、UP 主、直播间，并订阅 UP 主动态",
        "admin_description": "让 AI 直接回答 B 站相关问题：视频数据、热门榜、UP 主粉丝、直播状态、最新动态；订阅后新动态自动推到群里。登录态是「这个 AI 自己的 B 站账号」，由创建者扫码。",
        "trigger_conditions": ["用户发 B 站链接", "用户问 B 站热门或某个视频", "订阅的 UP 主发新动态"],
        "icon": "play",
        "usage_notes": [
            (300, "查询类工具（视频/热门/粉丝/直播）不需要登录；动态与订阅推送需要这个 AI 已扫码登录 B 站。"),
        ],
    }, owner=PLUGIN_ID)


_register_segment()
