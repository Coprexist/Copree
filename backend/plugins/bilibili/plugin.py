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

接口口径（实测 2026-10-31 / 2026-11-01）：
- 无需登录：x/web-interface/view、x/web-interface/popular、x/relation/stat、直播 room/v1/Room/get_info、
  x/web-interface/search/type（按昵称搜人 / 按关键词搜视频：不带 Cookie 也能拿到结果，也不需要自己算
  WBI 签名；短时间连打会回 412 限流，间隔几秒即可，带上设备指纹 Cookie 更稳）
- 需要登录：x/polymer/web-dynamic/v1/feed/space（无 Cookie 时 B 站回 -412 / 风控页，签名也救不了）
"""
from __future__ import annotations

import asyncio
import base64
import bisect
import hashlib
import html
import io
import json
import logging
import random
import re
import time
import zlib
from contextvars import ContextVar
from urllib.parse import quote, urlencode
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
FINGER_TTL_SECONDS = 1800     # 设备指纹（buvid3/buvid4）缓存：半小时取一次就够
WBI_TTL_SECONDS = 600         # Web 签名密钥缓存（nav 给的 img_key/sub_key）

# 评论发出后 B 站先标成隐藏/折叠，过几秒才公开。工具当场不下结论：记一行待确认，服务去追。
PENDING_TICK_SECONDS = 30     # 追认间隔
PENDING_SETTLE_SECONDS = 90   # 这之前不下"被隐藏"的结论（刚发出本来就是隐藏态）
PENDING_TTL_SECONDS = 1800    # 半小时还没落定就不追了，只告诉 AI 没能确认

# WBI 签名的固定重排表（站点前端同一份）
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]


class _Expiring:
    """进程内的小缓存（设备指纹 / WBI 密钥）。

    只记"取到了"的时间：取失败也写时间戳的话，一次网络抖动会被钉成整个 TTL，
    接下来半小时全都裸奔。连续失败另有 retry 窗口，免得每个请求都去撞同一个坏接口。
    """

    def __init__(self, ttl: int, retry: int = 60) -> None:
        self.ttl = ttl
        self.retry = retry
        self.value = ""
        self.ok_at = 0.0
        self.try_at = 0.0

    def fresh(self) -> str:
        return self.value if self.value and time.time() - self.ok_at < self.ttl else ""

    def may_retry(self) -> bool:
        return time.time() - self.try_at >= self.retry

    def hit(self, value: str) -> str:
        self.value, self.ok_at, self.try_at = value, time.time(), time.time()
        return value

    def miss(self) -> None:
        self.try_at = time.time()


_NEED_LOGIN = "这个 AI 还没有登录 B 站：请创建者打开「B 站」通道卡片扫码。"

_finger = _Expiring(FINGER_TTL_SECONDS)
_wbi = _Expiring(WBI_TTL_SECONDS)

# 搜索接口的返回值会把命中的关键词包成 <em class="keyword">…</em>，展示前要脱标签
_TAG = re.compile(r"<[^>]+>")


def _quote_all(text: str, *args, **kwargs) -> str:
    """URL 编码到"一个斜杠都不放过"：WBI 签名按全转义算，quote 默认会留 / 原样。"""
    return quote(text, safe="")


def _clamp(value, default: int, cap: int) -> int:
    """参数取整并夹到 [1, cap]：模型可能给 0、负数或字符串。"""
    try:
        num = int(value if value is not None else default)
    except (TypeError, ValueError):
        num = default
    return max(1, min(num, cap))


def _plain(text) -> str:
    """去掉接口塞进文案里的高亮标签与多余空白。"""
    return html.unescape(_TAG.sub("", str(text or ""))).strip()


class BiliError(Exception):
    """B 站接口错误：带接口自己的 code/message，方便直接转述给用户。

    code 留给调用方按状态分辨（例如取消赞时 65004 = 本来就没赞过，对用户等价于成功）。
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


# ============================================================
# HTTP：一处出口（测试只打桩这一层）
# ============================================================

async def _finger_cookie() -> str:
    """设备指纹 Cookie（buvid3 / buvid4）。

    B 站把它当"这是一台真浏览器"的证据：不带它打站点接口，第一次通、连着打就 412；
    带上之后同一批接口能连续拿结果（2026-11-01 实测）。指纹本身由站点自己的
    finger/spi 接口发放，不是伪造值，所以缓存半小时复用就够，不必每次请求都取。
    """
    cached = _finger.fresh()
    if cached or not _finger.may_retry():
        return cached
    cookie = ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{API}/x/frontend/finger/spi",
                headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/"},
            )
        data = (resp.json() or {}).get("data") or {}
        parts = [f"buvid3={data['b_3']}"] if data.get("b_3") else []
        if data.get("b_4"):
            parts.append(f"buvid4={data['b_4']}")
        cookie = "; ".join(parts)
    except Exception as e:      # 拿不到指纹不算错误：没有它只是更容易被限流
        logger.debug("B 站设备指纹获取失败: %s", e)
    if cookie:
        return _finger.hit(cookie)
    _finger.miss()
    return ""


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# 网页端发评论时带的设备指纹字段（dm_*）：缺了它们，接口照样 code=0，
# 但 B 站会判定请求不完整，把评论隐藏成"仅自己可见"（2026-11-01 实测：补上后同账号同内容即公开）。
_WEB_FIELDS = {
    "dm_img_list": "[]",
    "dm_img_str": _b64("WebGL 1.0 (OpenGL ES 2.0 Chromium)"),
    "dm_cover_img_str": _b64("ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}
_WEB_LOCATION = "1315875"

# 浏览器身份 Cookie：_uuid / b_nut / b_lsid / buvid_fp。与 SESSDATA 不同，它们描述"这台设备"，
# 进程内固定（每次换一套反而更像异常请求）。
_BROWSER: dict[str, str] = {}


def _browser_cookie() -> str:
    if not _BROWSER:
        seed = f"{time.time()}-{random.getrandbits(64)}"
        _BROWSER.update({
            "_uuid": "%08X-0000-4000-8000-%012X" % (random.getrandbits(32), random.getrandbits(48)),
            "b_nut": str(int(time.time())),
            "b_lsid": "%08X_%010X" % (random.getrandbits(32), random.getrandbits(40)),
            "buvid_fp": hashlib.md5(seed.encode()).hexdigest(),
        })
    return "; ".join(f"{key}={value}" for key, value in _BROWSER.items())


async def _raw_get(url: str, params: dict | None = None, cookie: str = "") -> httpx.Response:
    headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
    merged = "; ".join(part for part in (cookie, await _finger_cookie()) if part)
    if merged:
        headers["Cookie"] = merged
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            return await client.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise BiliError(f"连接 B 站失败：{e}") from e


def _data_of(resp: httpx.Response) -> dict:
    """统一取 data 段：非 JSON（风控页）与 code != 0 都变成可读的 BiliError。"""
    if resp.status_code == 412:
        # 412 是"请求太密"的限流，不是没权限：跟登录态无关，等一下重试就好
        raise BiliError("B 站限流（HTTP 412）：这个接口短时间内被请求太多次，隔几秒再试一次。")
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
    if code == -352:
        raise BiliError("B 站空间接口风控（-352）：稍后再试；一直这样就说明这个 AI 的登录态失效了，去通道卡片重新扫码。")
    if code not in (0, None):
        raise BiliError(
            f"B 站接口返回 {code}：{payload.get('message') or payload.get('msg') or '未知错误'}",
            int(code),
        )
    return payload.get("data") or {}


async def _get(url: str, params: dict | None = None, cookie: str = "") -> dict:
    return _data_of(await _raw_get(url, params, cookie))


async def _search_rows(kind: str, keyword: str, cookie: str = "", order: str = "") -> list[dict]:
    """搜人 / 搜视频共用：search/type 的参数与 result 解包只写一处。

    order 只在搜视频时给（接口的排序口径就那几种，见 BiliSearchVideo 的 enum）。
    """
    params = {"search_type": kind, "keyword": keyword, "page": 1}
    if order:
        params["order"] = order
    data = await _get(f"{API}/x/web-interface/search/type", params, cookie)
    return data.get("result") or []


async def _view_of(bvid: str, cookie: str = "") -> dict:
    """视频详情：aid、分 P（cid）都从这一份里取，别为同一件事打两次接口。"""
    data = await _get(f"{API}/x/web-interface/view", {"bvid": bvid}, cookie)
    if not data.get("aid"):
        raise BiliError(f"查不到这条视频（{bvid}）")
    return data


async def _aid_of(bvid: str, cookie: str = "") -> int:
    """BV 号 → aid：评论区接口要数字 id，顺带确认视频存在。"""
    return int((await _view_of(bvid, cookie))["aid"])


def _pick_rpid(rows: list[dict], rpid: int) -> dict | None:
    """从一堆评论对象里挑出 rpid 那一条（统一转 int 比较，字符串形状的 rpid 会漏）。"""
    for row in rows:
        if int(row.get("rpid") or 0) == rpid:
            return row
    return None


async def _locate_comment(aid: int, rpid: int, cookie: str = "", *, with_row: bool = True) -> dict:
    """这个 rpid 落在哪栋楼里、这一条的对象是什么——一个接口问清两件事。

    为什么不再去评论列表里找：以前查状态是翻**视频的顶层评论列表**，而楼中楼的回复
    根本不在那张表上，于是永远找不到（还一直翻页）。改成问接口本身：

      - `root.rpid == rpid`：它就是根（视频下的顶层评论），对象就在 `data.root` 里，不用再请求
      - 否则它是楼中楼的一条，`data.root.rpid` 就是它所属的线程根

    楼中楼按时间**正序**排，刚发出去的那条必然在末尾——所以按 `page.count` 直接跳最后一页，
    不从第一页翻起；那一页没找到再回看第一页，B 站哪天改了排序也不至于失手。

    返回 {"thread": 线程根 rpid, "row": 这条评论的对象或 None, "total": 线程内回复数}。
    `with_row=False` 只问线程根（回复操作只要这个），省掉翻页那一次请求。
    """
    data = await _get(f"{API}/x/v2/reply/reply",
                      {"type": 1, "oid": aid, "root": rpid, "ps": 20, "pn": 1}, cookie)
    root = data.get("root") or {}
    total = int((data.get("page") or {}).get("count") or 0)
    thread = int(root.get("rpid") or 0) or rpid
    if thread == rpid:
        return {"thread": thread, "row": root, "total": total}
    if not with_row:
        return {"thread": thread, "row": None, "total": total}
    row = _pick_rpid([root, *(data.get("replies") or [])], rpid)
    if row is None and total > 20:
        tail = await _get(f"{API}/x/v2/reply/reply",
                          {"type": 1, "oid": aid, "root": thread, "ps": 20,
                           "pn": (total + 19) // 20}, cookie)
        row = _pick_rpid([tail.get("root") or {}, *(tail.get("replies") or [])], rpid)
    return {"thread": thread, "row": row, "total": total}


async def _owner_error(bvid: str, aid: int, rpid: int, cookie: str, cfg: dict) -> str:
    """这条评论要不是自己的，返回"是谁的"，否则返回空串——只在删之前问。

    为什么删之前要问：B 站的 -403「访问权限不足」对模型毫无指导性，它既可能当成
    "账号没权限"、也可能当成"再试一次"，而它真正犯的错是**拿了别人的 rpid**。
    自己先看一眼归属，就能把"是谁的"直接说出来，省掉一次白跑的失败请求。
    """
    mine = str(cfg.get("bili_mid") or "").strip()
    if not mine:
        return ""       # 不知道自己的 mid 就没法判断，交给 B 站兜底
    try:
        row = (await _locate_comment(aid, rpid, cookie))["row"]
    except BiliError:
        return ""
    member = (row or {}).get("member") or {}
    owner = str(member.get("mid") or "").strip()
    if not owner or owner == mine:
        return ""
    who = member.get("uname") or "别人"
    return (f"这条不是你发的（是「{who}」的），B 站只允许删自己的评论，删别人的一定 403。"
            f"要删自己的，先用 bili_comments 看你在 {bvid} 下发过哪几条，拿它们的 rpid。")


async def _reply_anchor(aid: int, rpid: int, cookie: str = "") -> tuple[int, int]:
    """「要回的那条评论」→ (root, parent)：楼中楼的这两个字段只能一起算、一起发。

    为什么必须成对：B 站只认这一对才挂得出"回复谁"。只发 root、或者把楼中楼的 rpid
    当成 root 传上去，它**不报错**，而是直接把这条当成视频下的新评论——回了、谁都
    看不见，是这条通道代价最高的一次静默失败。

    也为什么只给一个参数：调用方不知道手里那个 rpid 是根评论还是"别人回我的那条"，
    这个区别由这里问接口问清楚（对根评论回自己，对楼中楼回它所属的顶层），
    然后把一对结果交出去。返回二元组而不是两个函数，就是为了让"漏一个"写不出来。
    """
    # 线程根只从 _locate_comment 出：接口说不上来（这条被删/折叠）时它退回传入值，
    # 宁可挂错一层，也别让回复整个发不出去
    thread = (await _locate_comment(aid, rpid, cookie, with_row=False))["thread"]
    return thread, rpid


async def _post(path: str, data: dict, cookie: str = "", *, referer: str = "") -> dict:
    """写操作出口（点赞 / 评论 / 删评论）：按网页端的形状发。

    除了 Cookie 与 csrf，还要带上：视频页当 Referer、web_location 与 plat 查询参数、
    dm_img_* 设备指纹、以及 _uuid/buvid_fp 这组浏览器身份 Cookie。缺任何一类，接口都可能
    回 code=0 却把内容藏成"仅自己可见"——那不是账号问题，是请求不像浏览器。
    """
    headers = {
        "User-Agent": _UA,
        "Referer": referer or "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }
    merged = "; ".join(part for part in (cookie, await _finger_cookie(), _browser_cookie()) if part)
    if merged:
        headers["Cookie"] = merged
    body = {**data, **_WEB_FIELDS}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            resp = await client.post(
                f"{API}{path}", params={"web_location": _WEB_LOCATION, "plat": "1"},
                data=body, headers=headers,
            )
    except httpx.HTTPError as e:
        raise BiliError(f"连接 B 站失败：{e}") from e
    return _data_of(resp)


async def _comment_status(aid: int, rpid: int, cookie: str, *, tries: int = 1, gap: float = 2.5) -> dict:
    """这条评论对外是什么样：用**登录态**读这条评论，看 B 站挂在它身上的状态字段。

    为什么不拿"匿名能不能查到"当标准：匿名接口的返回会随策略变，可靠的信号是评论对象自己的字段：
      - `state == 0` 正常；`state == 17` 表示被 B 站隐藏——只有作者自己看得到，别人看不到
      - `invisible` 为真 = 不可见
      - `folder.is_folded` 为真 = 收在折叠栏里：仍然公开、点开就能看到。它还会自己恢复——
        2026-11-01 实测刚发出时折叠、过一阵再看已经不折叠，所以它不参与「别人看不看得到」的判断。
    定位交给 _locate_comment：按 rpid 反查线程，顶层评论和楼中楼都同样准（不必翻顶层列表），
    找不到就是已删 / 被折叠到连自己都看不见。

    刚发出的评论会先被标一段时间的隐藏/折叠（2026-11-01 实测：提交后立刻查是 `state=17`，
    3 秒后再查已经是 `state=0`），所以查状态的调用方给 `tries` > 1 等它落定，别把"还在审核"
    当成"发不出去"。
    """
    last: dict = {"found": False, "visible": False}
    for attempt in range(max(1, tries)):
        if attempt:
            await asyncio.sleep(gap)
        try:
            row = (await _locate_comment(aid, rpid, cookie))["row"]
        except BiliError as e:
            logger.debug("B 站评论状态自查失败: %s", e)
            return last
        if not row:
            continue
        folded = bool((row.get("folder") or {}).get("is_folded"))
        hidden = bool(row.get("invisible")) or row.get("state") not in (0, None)
        last = {"found": True, "visible": not hidden, "folded": folded, "hidden": hidden, "state": row.get("state")}
        if last["visible"]:
            return last
    return last


# 弹幕老接口的每一行：<d p="出现秒数,模式,字号,颜色,发送时间,池,..">正文</d>
_DM_ROW = re.compile(r'<d p="([^"]*)"[^>]*>(.*?)</d>', re.S)


def _inflate_xml(raw: bytes) -> str:
    """弹幕接口回的是 deflate 裸流（zlib 要 wbits=-15），偶尔也有明文 XML。"""
    if raw[:1] != b"<":
        try:
            raw = zlib.decompress(raw, -15)
        except zlib.error as e:
            raise BiliError(f"弹幕数据解压失败：{e}") from e
    return raw.decode("utf-8", "replace")


async def _danmaku_of(cid: int) -> list[tuple[float, str]]:
    """一个分 P 的弹幕：(出现秒数, 正文)，按时间排好。

    走的是老接口 x/v1/dm/list.so：回 deflate 压缩的 XML，标准库就能读。新版
    x/v2/dm/web/seg.so 是 protobuf，要自己走 varint（参考实现为此写了上百行解析器），
    这里没必要。跳过 mode 7/8/9——代码弹幕 / BAS / 特效，不是人说的话。
    """
    resp = await _raw_get(f"{API}/x/v1/dm/list.so", {"oid": cid, "type": 1})
    items: list[tuple[float, str]] = []
    for params, body in _DM_ROW.findall(_inflate_xml(resp.content)):
        fields = params.split(",")
        try:
            at, mode = float(fields[0]), int(fields[1])
        except (ValueError, IndexError):
            continue
        if mode in (7, 8, 9):
            continue
        text = html.unescape(_TAG.sub("", body)).strip()
        if text:
            items.append((at, text))
    items.sort()
    return items


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _spread(items: list, limit: int) -> list:
    """均匀抽 limit 条：一条热门视频的弹幕几千条，给模型看全量既没必要也塞不下。"""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]

def _seconds(value) -> float | None:
    """秒数参数：模型可能给 312、"5:12"（把上面回的 mm:ss 原样填回来）或空。"""
    if value in (None, ""):
        return None
    if isinstance(value, str) and ":" in value:
        try:
            total = 0.0
            for part in value.split(":"):
                total = total * 60 + float(part)
        except ValueError:
            return None
        return total
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


# 一次回给模型多少字：70 分钟的访谈能有一万多字，全塞进上下文既没必要也没人读得完；
# 按区间分几次读才是它该有的用法（下面的「还有」会告诉它从哪儿接着读）。
_SUB_CHARS = 2000
# B 站偶尔把**别人的**稿子挂在这条视频上（实测 70 分钟的视频拿到过 53 秒的娱乐
# 新闻稿、火锅探店稿、换车轮的 Vlog），好在这类错能认出来（见 _subtitle_body 的
# aid+cid 校验）。认出来就重问，最多问这么多次。
_SUB_TRIES = 4
# 稿子按分 P 缓存一小时：同一段视频要分几次读，不该每读一段都重新问一遍 B 站。
_SUB_TTL_SECONDS = 3600
_sub_cache: dict[int, tuple[float, list, list]] = {}


def _sub_url(track: dict) -> str:
    url = str(track.get("subtitle_url") or "").strip()
    return ("https:" + url) if url.startswith("//") else url


def _ai_url_of(aid: int, cid: int, url: str) -> bool:
    """ai 稿的地址里内嵌了 aid+cid：bfs/ai_subtitle/prod/<aid><cid><hash>。

    这是唯一能当场验证"这份稿子确实是这条视频的"的东西——B 站把别人稿子挂错时，
    地址里的数字仍然属于那条别人的视频。
    """
    path = url.split("?", 1)[0].rsplit("/", 1)[-1]
    return path.startswith(f"{aid}{cid}")


async def _subtitle_tracks(aid: int, cid: int, bvid: str, cookie: str = "") -> list[dict]:
    """这段视频有哪些稿子。B 站把稿件列表塞在播放器配置里，不在 view 里。"""
    data = await _get(f"{API}/x/player/v2", {"aid": aid, "cid": cid, "bvid": bvid}, cookie)
    return list(((data.get("subtitle") or {}).get("subtitles")) or [])


async def _subtitle_body(track: dict, aid: int, cid: int) -> list[tuple[float, float, str]]:
    """一条稿子的正文：(开始秒, 结束秒, 一句话)，按时间排好。

    这个域名回的是裸 JSON——顶层直接就是 body，没有 code/data 信封，
    所以只能走 _raw_get；过 _data_of 会被当成反爬页面。

    ai 稿还多一道校验：地址里的 aid+cid 对不上这条视频，就是串台，返回空让
    调用方重问——宁可说"拿不到稿子"，也不能把别人的话当成这条视频的内容。
    """
    url = _sub_url(track)
    if not url:
        return []
    if "/ai_subtitle/" in url and not _ai_url_of(aid, cid, url):
        return []
    resp = await _raw_get(url)
    if resp.status_code != 200:
        raise BiliError(f"字幕文件取不到（HTTP {resp.status_code}）")
    try:
        payload = resp.json()
    except ValueError as e:
        raise BiliError("字幕文件不是合法 JSON") from e
    rows: list[tuple[float, float, str]] = []
    for item in payload.get("body") or []:
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("from") or 0)
            end = float(item.get("to") or start)
        except (TypeError, ValueError):
            continue
        rows.append((start, end, text))
    rows.sort()
    return rows


async def _longest_track(
    tracks: list[dict], aid: int, cid: int
) -> list[tuple[float, float, str]]:
    """没有机器听写时，挑字数最多的那条人工稿。

    人工稿是静态文件，可以直接比字数。稿子不会多，但也不能让某个冷门视频
    拖出一串下载，所以最多比前四条。
    """
    best: list[tuple[float, float, str]] = []
    for track in tracks[:4]:
        rows = await _subtitle_body(track, aid, cid)
        if sum(len(row[2]) for row in rows) > sum(len(row[2]) for row in best):
            best = rows
    return best


async def _subtitle_rows(
    aid: int, cid: int, bvid: str, cookie: str, duration: float
) -> tuple[list[tuple[float, float, str]], list[dict]]:
    """一条分 P 的整段稿子（按时间排好）+ 这次看到的稿件清单。

    清单是给调用方分两种空用的：这条视频根本没人做稿子，还是做了但 B 站现在
    挂出来的内容对不上——对用户说法不一样。

    ai-zh 是 B 站的语音识别稿——整片人说了什么都在里面；人工字幕常常只翻歌曲和
    几句标题（同一条视频里 zh-Hans 实测只有 ai-zh 的零头），所以两者并存时要 ai-zh。
    串台的那次会被 _subtitle_body 丢掉，所以这里要留几次重问的余量。
    """
    cached = _sub_cache.get(cid)
    if cached and time.time() - cached[0] < _SUB_TTL_SECONDS:
        return cached[1], cached[2]
    best: list[tuple[float, float, str]] = []
    tracks: list[dict] = []
    for _ in range(_SUB_TRIES):
        tracks = await _subtitle_tracks(aid, cid, bvid, cookie)
        ai = [track for track in tracks if str(track.get("lan") or "") == "ai-zh"]
        rows = await _subtitle_body(ai[0], aid, cid) if ai else []
        if not rows:
            rows = await _longest_track(
                [t for t in tracks if str(t.get("lan") or "") != "ai-zh"], aid, cid
            )
        if sum(len(row[2]) for row in rows) > sum(len(row[2]) for row in best):
            best = rows
        if best and (duration <= 0 or best[-1][1] >= duration * 0.9):
            break
        await asyncio.sleep(0.5)
    if best and (duration <= 0 or best[-1][1] >= duration * 0.9):
        # 只缓存覆盖到全片的稿子：半截稿缓存下来，就把它钉住一小时了
        now = time.time()
        _sub_cache[cid] = (now, best, tracks)
        if len(_sub_cache) > 64:
            for key, (at, _, _) in list(_sub_cache.items()):
                if now - at >= _SUB_TTL_SECONDS:
                    _sub_cache.pop(key, None)
    return best, tracks


def _render_subs(rows: list[tuple[float, float, str]], timed: bool) -> str:
    """把这几句摆成一段话。

    默认（timed=False）连成一段：中文识别稿一句紧接着一句，接缝处不该有空格；
    少数视频只有英文稿，词与词之间必须留一个，否则会粘成一个词。

    timed=True 时每句前面带上 [mm:ss]：要挑"哪一小段值得看画面"时才需要这个，
    平时带上只是白占 token。
    """
    if timed:
        return "\n".join(f"[{_mmss(row[0])}] {row[2]}" for row in rows)
    text = "".join(row[2] for row in rows)
    return text if re.search(r"[\u4e00-\u9fff]", text) else " ".join(row[2] for row in rows)


# ── 取帧：B 站的缩略图雪碧图 ────────────────────────────────────────
# 每张 10×10 格、每格 480×270，格子行优先铺开；index[n] 落在第 n//100 张拼图的
# 第 n%100 格。网页播放器拿它做进度条悬停预览，这里借它当"不用解码视频的抽帧"。
_SHOT_PER_SHEET = 100
_SHOT_ROW_CELLS = 10
_SHOT_CELL_W, _SHOT_CELL_H = 480, 270
_SHOT_CELLS = 4          # 一次最多给几张：多模态按张计价，四张足够看动作变化
_SHOT_QUALITY = 82
_shot_cache: dict[int, tuple[float, list[float], list[str]]] = {}
_sprite_cache: dict[str, tuple[float, bytes]] = {}


def _times_of(value) -> list[float]:
    """at 参数：数组、单个数字、逗号分隔的 "01:23,120" 都认。"""
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = str(value).split(",")
    out: list[float] = []
    for item in raw:
        seconds = _seconds(item)
        if seconds is not None:
            out.append(seconds)
    return out[:2]     # 一个时刻出两帧，四个名额最多问两处


async def _shot_timeline(
    aid: int, cid: int, bvid: str, cookie: str = ""
) -> tuple[list[float], list[str]]:
    """一条分 P 的取帧表：(每格对应的秒数, 拼图地址)。"""
    cached = _shot_cache.get(cid)
    if cached and time.time() - cached[0] < _SUB_TTL_SECONDS:
        return cached[1], cached[2]
    data = await _get(f"{API}/x/player/videoshot", {
        "aid": aid, "cid": cid, "bvid": bvid, "index": 1,
    }, cookie)
    times = [float(v) for v in (data.get("index") or [])]
    sheets = [
        ("https:" + str(u)) if str(u).startswith("//") else str(u)
        for u in (data.get("image") or [])
    ]
    if times and sheets:
        now = time.time()
        _shot_cache[cid] = (now, times, sheets)
        if len(_shot_cache) > 32:
            for key, (at, _, _) in list(_shot_cache.items()):
                if now - at >= _SUB_TTL_SECONDS:
                    _shot_cache.pop(key, None)
    return times, sheets


def _head_picks(times: list[float], count: int = 2) -> list[tuple[int, float, float]]:
    """不给时间时取开头几格。

    跳过时间重复的格子：B 站头两格有时都标 0 秒，取两个 0 秒的帧等于只看了一张。
    """
    picks: list[tuple[int, float, float]] = []
    for cell, actual in enumerate(times):
        if any(pick[1] == actual for pick in picks):
            continue
        picks.append((cell, actual, actual))
        if len(picks) >= count:
            break
    return picks


def _shot_picks(times: list[float], at: list[float]) -> list[tuple[int, float, float]]:
    """要看的时刻 → [(格子序号, 这格的秒数, 你要的秒数)]，按时间排、去重。

    取"前后各一格"：落在它前面最近的一格和后面最近的一格。单帧可能正好是转场或
    黑帧，两帧连看才看得出动作；时刻正好落在格子上时往后再多看一格。
    """
    picks: list[tuple[int, float, float]] = []
    seen: set[int] = set()
    for want in at:
        pos = bisect.bisect_left(times, want)
        if pos < len(times) and times[pos] == want:
            pair = [pos, pos + 1]
        else:
            pair = [pos - 1, pos]
        for cell in pair:
            if 0 <= cell < len(times) and cell not in seen:
                seen.add(cell)
                picks.append((cell, times[cell], want))
    picks.sort()
    return picks


async def _sprite_bytes(url: str) -> bytes:
    """拼图原图（一张 4800×2700、几百 KB）：同一个视频会连着裁几格，缓存一张就行。"""
    cached = _sprite_cache.get(url)
    if cached and time.time() - cached[0] < _SUB_TTL_SECONDS:
        return cached[1]
    resp = await _raw_get(url)
    if resp.status_code != 200:
        raise BiliError(f"缩略图取不到（HTTP {resp.status_code}）")
    now = time.time()
    _sprite_cache[url] = (now, resp.content)
    if len(_sprite_cache) > 8:
        for key, (at, _) in list(_sprite_cache.items()):
            if now - at >= _SUB_TTL_SECONDS:
                _sprite_cache.pop(key, None)
    return resp.content


def _cell_data_url(raw: bytes, cell: int) -> str:
    """裁出第 cell 格 → JPEG data URL。Pillow 只在真要出图时导入。"""
    from PIL import Image

    local = cell % _SHOT_PER_SHEET
    col, row = local % _SHOT_ROW_CELLS, local // _SHOT_ROW_CELLS
    with Image.open(io.BytesIO(raw)) as sheet:
        cell_img = sheet.convert("RGB").crop((
            col * _SHOT_CELL_W, row * _SHOT_CELL_H,
            (col + 1) * _SHOT_CELL_W, (row + 1) * _SHOT_CELL_H,
        ))
    buf = io.BytesIO()
    cell_img.save(buf, "JPEG", quality=_SHOT_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def _wbi_mixin(cookie: str = "") -> str:
    """Web 端签名密钥：nav 里给的 img_key/sub_key 按固定表重排取前 32 位。

    空间类接口（投稿列表、用户详情）都要 w_rid 签名，不签就回 412/-352；
    密钥本身会轮换，缓存 10 分钟重取一次。
    """
    cached = _wbi.fresh()
    if cached:
        return cached
    data = await _get(f"{API}/x/web-interface/nav", None, cookie)
    wbi_img = data.get("wbi_img") or {}
    img = str(wbi_img.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = str(wbi_img.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img or not sub:
        raise BiliError("B 站 Web 签名密钥没拿到，稍后重试")
    return _wbi.hit("".join((img + sub)[i] for i in _MIXIN_TAB)[:32])


async def _signed_get(path: str, params: dict | None = None, cookie: str = "") -> dict:
    """带 WBI 签名的 GET：参数排序拼 wts，w_rid = md5(query + mixin)。"""
    mixin = await _wbi_mixin(cookie)
    signed: dict = dict(params or {})
    signed["wts"] = int(time.time())
    query = urlencode(sorted(signed.items()), quote_via=_quote_all)
    signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return await _get(f"{API}{path}", signed, cookie)




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


_FALSEY = {"0", "false", "off", "no", "none"}


def _flag(value: object, default: bool = False) -> bool:
    """配置里的布尔：平台把 checkbox 存成 "true"/"false" 字符串，"false" 也是真值。

    空值用默认；其余按字面判（与通道额度的 _as_flag 同一口径）。
    """
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


def _require_interact(cfg: dict) -> None:
    """能不能替创建者在 B 站说话，由创建者决定：默认只读。"""
    if not _flag(cfg.get("allow_interact")):
        raise BiliError(
            "创建者还没允许这个 AI 互动：「B 站」通道卡片里勾上"
            "「允许这个 AI 互动（点赞 / 评论 / 私信）」后它才能发。"
        )


async def _write_ctx(agent_id: int) -> tuple[dict, str]:
    """写操作（点赞 / 评论）共用：取配置、过创建者开关、拿登录态，缺一样就报清楚。"""
    cfg = await _instance_config(_instance_of(agent_id))
    _require_interact(cfg)
    cookie = _cookie_of(cfg)
    if not cookie:
        raise BiliError(_NEED_LOGIN)
    return cfg, cookie


def _csrf_of(cfg: dict) -> str:
    """写操作要 bili_jct 当 CSRF 令牌：登录只存了 SESSDATA 时得回卡片重扫一次。"""
    csrf = str(cfg.get("bili_jct") or "").strip()
    if not csrf:
        raise BiliError("这个 AI 的登录态里没有 bili_jct（写操作要的 CSRF 令牌）：请在通道卡片重新扫码登录。")
    return csrf





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


async def _nav_cached(cookie: str, instance: str) -> dict:
    """登录态（isLogin / uname / mid），带缓存；**顺手把 B 站那边的改名同步回配置**。

    缓存是为了卡片被反复拉取时不每次打 nav。副作用只在真正问过接口的那一次发生，
    同一次查询里发现"B 站叫这个名字、配置还记着旧名字"就回写（见 _sync_profile）——
    独立发一个同步任务，就得再判断一次该不该跑、什么时候跑，不值得。

    instance 不给默认值：谁调它谁就得说出是哪个实例，否则改名会悄悄同步不到
    （和不传 parent 就悄悄降级成新评论是同一类坑）。
    """
    now = time.time()
    hit = _nav_cache.get(cookie)
    if hit and now - hit[0] < NAV_TTL_SECONDS:
        return hit[1]
    info = await _nav(cookie)
    _nav_cache[cookie] = (now, info)
    await _sync_profile(instance, info)
    return info


async def _sync_profile(instance: str, info: dict) -> None:
    """B 站那边改了名要跟着改：nickname 只在扫码那一刻写过一次，之后谁也不刷。

    卡片看不出来（它读实时 uname），但工具回执里的「账号」读的是配置——不改的话
    AI 会照着旧名字跟用户报「我（旧名字）回复了」。nav 本来每个实例每 120 秒问一次，
    顺手把变化写回去，不额外发请求。
    """
    if not instance or not info.get("isLogin"):
        return
    uname = str(info.get("uname") or "").strip()
    if not uname:
        return
    from app.services.plugin import config as plugin_config

    cfg = await plugin_config.get_config(PLUGIN_ID, instance)
    patch: dict = {}
    if uname != str(cfg.get("nickname") or "").strip():
        patch["nickname"] = uname
    mid = str(info.get("mid") or "").strip()
    if mid and mid != str(cfg.get("bili_mid") or "").strip():
        patch["bili_mid"] = mid
    if not patch:
        return
    try:
        await plugin_config.set_config(PLUGIN_ID, patch, instance=instance, actor="bilibili-profile-sync")
    except Exception as e:  # noqa: BLE001 —— 名字同步失败不该把登录态查询一起搞挂
        logger.warning("B 站账号资料同步失败（%s）: %s", instance, e)
        return
    logger.info("B 站账号资料已同步（%s）: %s", instance, patch)


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


def _qr_live_fields(session: dict) -> dict:
    """卡片契约要的二维码字段：这张码还能不能扫、还剩几秒

    前端按 qr_state === "live" 才会把码采纳进卡片，按 qr_expires_in 跑倒计时；
    少了这两个，码拿到了也只会显示成"已过期"。
    """
    left = max(0, int(QR_TTL_SECONDS - (time.time() - session.get("created", 0))))
    live = session.get("state") in ("pending", "scanned") and left > 0
    return {
        "qr_state": "live" if live else "expired",
        "qr_expires_in": left,
        "qr_at": int(session.get("created") or 0),
    }


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
    await _trail_record(
        instance, "login",
        json.dumps({"uname": info.get("uname") or "", "mid": str(info.get("mid") or "")}, ensure_ascii=False),
    )


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

def _t(name: str) -> str:
    """插件自己的表名（前缀由平台给：plugin_bilibili__<name>）"""
    from app.services.plugin.storage import table_name

    return table_name(PLUGIN_ID, name)


def _sub_row(row) -> dict:
    return {
        "instance": str(row.instance),
        "mid": int(row.mid),
        "name": row.name or "",
        "group_id": int(row.group_id),
        "last_ts": int(row.last_ts or 0),
    }


async def _subs_all() -> list[dict]:
    """轮询用：所有实例的订阅（表由 migrations/0001_init.py 建）"""
    from sqlalchemy import text

    from app.database import async_session

    try:
        async with async_session() as db:
            rows = (await db.execute(text(
                f'SELECT instance, mid, name, group_id, last_ts FROM {_t("subscriptions")} ORDER BY id'
            ))).all()
        return [_sub_row(r) for r in rows]
    except Exception as e:  # noqa: BLE001 —— 表还没建好也不该让卡片/轮询整条挂掉
        logger.warning("B 站订阅读取失败: %s", e)
        return []


async def _subs_of(instance: str) -> list[dict]:
    return [s for s in await _subs_all() if s["instance"] == instance]


async def _sub_add(instance: str, mid: int, group_id: int, name: str) -> bool:
    """加订阅；已存在算成功（同一个 UP 可以被多个群订阅）"""
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        exists = (await db.execute(text(
            f'SELECT 1 FROM {_t("subscriptions")} '
            'WHERE instance = :i AND mid = :m AND group_id = :g LIMIT 1'
        ), {"i": instance, "m": mid, "g": group_id})).first()
        if exists:
            return False
        await db.execute(text(
            f'INSERT INTO {_t("subscriptions")} (instance, mid, name, group_id, last_ts) '
            'VALUES (:i, :m, :n, :g, 0)'
        ), {"i": instance, "m": mid, "n": name or "", "g": group_id})
        await db.commit()
    return True


async def _sub_remove(instance: str, mid: int, group_id: int) -> int:
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        result = await db.execute(text(
            f'DELETE FROM {_t("subscriptions")} '
            'WHERE instance = :i AND mid = :m AND group_id = :g'
        ), {"i": instance, "m": mid, "g": group_id})
        await db.commit()
        return int(result.rowcount or 0)


async def _sub_touch(instance: str, mid: int, group_id: int, last_ts: int) -> None:
    """推送成功后把 last_ts 往前推（冲突时取较大的，重复推送不可怕，漏推才可怕）"""
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            f'UPDATE {_t("subscriptions")} SET last_ts = CASE WHEN last_ts > :ts THEN last_ts ELSE :ts END '
            'WHERE instance = :i AND mid = :m AND group_id = :g'
        ), {"ts": int(last_ts), "i": instance, "m": mid, "g": group_id})
        await db.commit()


# ── 待确认的评论：发出去还没落定，由服务追认 ──

async def _pending_add(*, instance: str, agent_id: int, group_id: int | None, session_id: str,
                       bvid: str, aid: int, rpid: int, message: str) -> None:
    """记一条"还没落定"的评论。记账失败不影响评论本身：评论已经发出去了。"""
    from sqlalchemy import text

    from app.database import async_session

    try:
        async with async_session() as db:
            await db.execute(text(
                f'INSERT INTO {_t("pending")} '
                '(instance, agent_id, group_id, session_id, bvid, aid, rpid, message, created_at) '
                'VALUES (:i, :a, :g, :s, :bv, :aid, :rpid, :msg, :ts)'
            ), {
                "i": instance, "a": int(agent_id),
                "g": int(group_id) if group_id else None,
                "s": session_id, "bv": bvid, "aid": int(aid), "rpid": int(rpid),
                "msg": message, "ts": int(time.time()),
            })
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("B 站待确认评论记账失败（评论已发出）: %s", e)


async def _pending_open(instance: str) -> list[dict]:
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        rows = (await db.execute(text(
            f'SELECT id, agent_id, group_id, session_id, bvid, aid, rpid, message, created_at, tries '
            f'FROM {_t("pending")} WHERE instance = :i ORDER BY id'
        ), {"i": instance})).all()
    return [dict(row._mapping) for row in rows]


async def _pending_touch(row_id: int, tries: int) -> None:
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            f'UPDATE {_t("pending")} SET tries = :t, checked_at = :ts WHERE id = :id'
        ), {"t": tries, "ts": int(time.time()), "id": int(row_id)})
        await db.commit()


async def _pending_drop(row_id: int) -> None:
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(f'DELETE FROM {_t("pending")} WHERE id = :id'), {"id": int(row_id)})
        await db.commit()


async def _pending_drop_rpid(instance: str, rpid: int) -> None:
    """评论删掉了，就别再追认它的可见性。"""
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            f'DELETE FROM {_t("pending")} WHERE instance = :i AND rpid = :r'
        ), {"i": instance, "r": int(rpid)})
        await db.commit()


async def _notify(agent_id: int, group_id, session_id: str, text: str, *, ref: str) -> None:
    """往 AI 的账本投一条通知：不进群聊，AI 下一轮自己看到。

    管道（开会话 / 查 agent / 拼会话键 / 提交）交给平台的 notify_agent，这里只说事实。
    """
    from app.services.plugin import api

    await api.notify_agent(agent_id, text, ref=ref,
                           context={"group_id": group_id, "session_id": session_id})


# ============================================================
# 收件箱：B 站那边收到的回复 / @ / 赞 / 私信
#
# 两条出路共用这一套读取：他进「刷 B 站」帧时注入一次，或他用 bili_inbox 自己查。
# 唯一要紧的规矩是顺序：**先写进账本，再推进游标**。未读变已读的全部含义就是他
# 之后还能翻到；没写进账本的那一条，不能算念过。
# ============================================================

IM_API = "https://api.vc.bilibili.com"

_INBOX_CHARS = 100       # 单行正文上限
_INBOX_DM_SESSIONS = 3   # 最多翻几个未读会话
_INBOX_MAX_LINES = 12    # 账本一次最多记几条：记不下的不推进游标，下次接着念

_KIND_CN = {"reply": "回复", "at": "@", "like": "赞", "dm": "私信"}


def _clip(text: str, limit: int = _INBOX_CHARS) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= limit else one[: max(1, limit - 1)] + "…"


def _bv_of(uri: str) -> str:
    """消息中心给的是整条链接，AI 要的是 BV 号。"""
    found = re.search(r"BV[0-9A-Za-z]{10}", str(uri or ""))
    return found.group(0) if found else ""


_CST = timezone(timedelta(hours=8))


def _reply_row(kind: str, it: dict) -> dict:
    """一条「回复我的 / @我的」压成流水行：渲染要什么就存什么。

    为什么不用 JSON：这是给模型看的短行，键名和引号都是白花的 token；字段顺序
    固定就够读了。楼中楼要能定位到人，所以两个 id 都要：root 是整栋楼的根，
    src 是"回我的那条"自己的 rpid（回它才算回到这个人，回 root 只会挂到根评论下）。
    """
    item = it.get("item") or {}
    return {
        "kind": kind,
        # 消息流水号（1e15 量级），只用来当"念到哪一条"的游标，**不是** rpid
        "key": str(int(it.get("id") or 0)),
        "ts": int(it.get("reply_time") or 0),
        "who": str((it.get("user") or {}).get("nickname") or "有人").strip()[:128],
        "target": _bv_of(item.get("uri"))[:64],
        "title": _clip(item.get("title") or ""),
        "said": _clip(item.get("source_content") or item.get("desc") or ""),
        "root": str(item.get("root_id") or "").strip()[:32],
        "src": str(item.get("source_id") or "").strip()[:32],
        "counts": 0,
    }


def _like_row(it: dict) -> dict:
    """收到的赞是聚合的：一条 = 一个被赞的目标 + 一串人 + 累计赞数。

    身份用 `<id>:<like_time>`：同一个目标被再赞一次 id 不变、时间变新，只按 id 会把
    第二次当成同一条，那就永远看不见新来的赞。
    """
    names = [str((u or {}).get("nickname") or "").strip()
             for u in (it.get("users") or []) if isinstance(u, dict)]
    names = [n for n in names if n]
    who = f"{names[0]} 等 {len(names)} 人" if len(names) > 1 else (names[0] if names else "有人")
    item = it.get("item") or {}
    return {
        "kind": "like",
        "key": f"{int(it.get('id') or 0)}:{int(it.get('like_time') or 0)}",
        "ts": int(it.get("like_time") or 0),
        "who": who[:128],
        "target": _bv_of(item.get("uri"))[:64],
        "title": _clip(item.get("title") or ""),
        "said": "",
        # 被赞的是评论时，item_id 就是那条评论的 rpid：带上它才分得清"赞了哪个"
        "root": str(item.get("item_id") or "")[:32] if item.get("type") == "reply" else "",
        "counts": int(it.get("counts") or 0),
    }


def _dm_row(talker: int, name: str, msg: dict, text: str) -> dict:
    """一条私信一行：念进账本时几条会话合成一行，存流水时仍按条存。"""
    return {
        "kind": "dm",
        "key": f"{int(talker)}:{int(msg.get('msg_seqno') or 0)}",
        "ts": _to_seconds(msg.get("timestamp")) or int(time.time()),
        "who": (name or str(talker))[:128],
        "target": (name or str(talker))[:64],
        "title": "",
        "said": _clip(text),
        "root": "",
        "counts": 0,
    }


def _row_line(row: dict) -> str:
    """流水行压成一行：类别｜谁｜在哪｜说了什么（赞写累计数）。

    「在哪」的规矩：有 BV 号就用 BV 号，楼中楼再挂一个 #rpid——挂的是**这条回复自己的**
    (src)，不是整栋楼的根：拿根去回只会挂到根评论下，接不上这个人。没有 src 的（赞、旧记录）
    才退回 root。连 BV 号都没有（动态、专栏）才退回标题；私信没有「在哪」，对方就是位置。
    """
    kind = row["kind"]
    if kind == "dm":
        head = f"私信｜{row['who']}"
    else:
        where = row["target"] or _clip(row["title"] or "", 16)
        anchor = row.get("src") or row["root"]
        if row["target"] and anchor:
            where = f"{where}#{anchor}"
        head = f"{_KIND_CN[kind]}｜{row['who']}｜{where}"
    if kind == "like":
        return f"{head}｜共 {row['counts']} 赞" if row["counts"] else head
    return f"{head}｜{row['said']}" if row["said"] else head


async def _inbox_seen(db, agent_id: int) -> dict[str, int]:
    """已经念到哪一条了（按 kind 分）：只读，不提交。"""
    from sqlalchemy import text

    try:
        rows = (await db.execute(text(
            f'SELECT kind, cursor FROM {_t("inbox")} WHERE agent_id = :a'
        ), {"a": int(agent_id)})).all()
    except Exception as e:  # noqa: BLE001 —— 迁移还没跑过也不该让进帧失败
        logger.warning("B 站收件箱游标读取失败: %s", e)
        return {}
    return {str(r.kind): int(r.cursor or 0) for r in rows}


async def _inbox_advance(db, agent_id: int, cursors: dict[str, int]) -> None:
    """把这一批念过的那几条记下：同一句只念一次（B 站未读标记一个字都不动）。"""
    from sqlalchemy import text

    for kind, cursor in cursors.items():
        await db.execute(text(
            f'INSERT INTO {_t("inbox")} (agent_id, kind, cursor, updated_at) '
            'VALUES (:a, :k, :c, :ts) '
            'ON CONFLICT (agent_id, kind) DO UPDATE SET cursor = :c, updated_at = :ts'
        ), {"a": int(agent_id), "k": kind, "c": int(cursor), "ts": int(time.time())})


async def _msgfeed_items(kind: str, cookie: str) -> list[dict]:
    """回复我的 / @我的：同一种卡片形状，一条路取。"""
    data = await _get(f"{API}/x/msgfeed/{kind}",
                      {"build": 0, "mobi_app": "web", "platform": "web"}, cookie)
    return [i for i in (data.get("items") or []) if isinstance(i, dict)]


async def _msgfeed_like_items(cookie: str) -> list[dict]:
    """收到的赞。形状和「回复我的」不一样，得单独取：

    - latest.items：B 站自己认的「新赞」，末次查看时间（last_view_at）一更新就清空；
    - total.items：历史，按被赞的目标聚合（一条里 users 是一串人，counts 是累计赞数）。

    两条都读：人打开手机就会把 latest 清空，只读它等于让「谁赞了我」依赖别人开 App。
    """
    data = await _get(f"{API}/x/msgfeed/like",
                      {"build": 0, "mobi_app": "web", "platform": "web"}, cookie)
    latest = [i for i in ((data.get("latest") or {}).get("items") or []) if isinstance(i, dict)]
    total = [i for i in ((data.get("total") or {}).get("items") or []) if isinstance(i, dict)]
    # 去重按 (id, 时间)：同一个目标被再赞一次时 id 不变、时间变新，只按 id 会把它当成重复
    seen = {(int(i.get("id") or 0), int(i.get("like_time") or 0)) for i in latest}
    return latest + [i for i in total
                     if (int(i.get("id") or 0), int(i.get("like_time") or 0)) not in seen]


async def _dm_sessions(cookie: str) -> list[dict]:
    data = await _get(f"{IM_API}/session_svr/v1/session_svr/get_sessions",
                      {"session_type": 1, "group_id": 0, "build": 0,
                       "mobi_app": "web", "platform": "web"}, cookie)
    return [s for s in (data.get("session_list") or []) if isinstance(s, dict)]


async def _dm_msgs(talker: int, cookie: str, size: int = 5) -> list[dict]:
    data = await _get(f"{IM_API}/svr_sync/v1/svr_sync/fetch_session_msgs",
                      {"talker_id": int(talker), "session_type": 1, "size": size}, cookie)
    return data.get("messages") or []


def _dm_text(msg: dict) -> str:
    """一条私信的可读正文：文本消息取 content，卡片类取 title / text。"""
    try:
        body = json.loads(msg.get("content") or "{}")
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    return _clip(body.get("content") or body.get("text") or body.get("title") or "")


async def _dm_names(uids: list[int], cookie: str) -> dict[int, str]:
    if not uids:
        return {}
    try:
        rows = await _get(f"{IM_API}/account/v1/user/cards",
                          {"uids": ",".join(str(u) for u in uids)}, cookie)
    except BiliError:
        return {}
    return {int(r["mid"]): str(r.get("name") or "")
            for r in rows if isinstance(r, dict) and r.get("mid")}


def _to_seconds(value) -> int:
    """B 站的时间戳量级不统一：会话是微秒、消息是秒。先归一成秒再格式化。"""
    try:
        raw = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if raw > 10 ** 14:      # 微秒
        return raw // 1_000_000
    if raw > 10 ** 11:      # 毫秒
        return raw // 1_000
    return raw


def _dev_id() -> str:
    """web_im 要的 dev_id：就是这台"浏览器"的 _uuid。

    私信接口把它当设备身份，和 Cookie 里的 _uuid 同源；单独造一个反而像换了设备。
    """
    _browser_cookie()          # 保证 _BROWSER 已经生成
    return str(_BROWSER.get("_uuid") or "")


async def _dm_send(uid: int, text: str, cookie: str, *, csrf: str, sender_uid: str) -> dict:
    """给某个 B 站用户发一条私信（网页端 web_im 的形状）。

    与站点写操作不同：私信走 api.vc.bilibili.com，消息字段是 msg[...] 这种扁平 form，
    CSRF 仍然用 bili_jct。缺 _uuid / 指纹 Cookie 时请求会不像浏览器，容易被风控。
    """
    payload = {
        "msg[sender_uid]": str(sender_uid),
        "msg[receiver_id]": str(int(uid)),
        "msg[receiver_type]": "1",
        "msg[msg_type]": "1",
        "msg[msg_status]": "0",
        "msg[new_face_version]": "0",
        "msg[timestamp]": str(int(time.time())),
        "msg[dev_id]": _dev_id(),
        "csrf": csrf,
        "csrf_token": csrf,
    }
    headers = {
        "User-Agent": _UA,
        "Referer": "https://message.bilibili.com/",
        "Origin": "https://message.bilibili.com",
    }
    merged = "; ".join(part for part in (cookie, await _finger_cookie(), _browser_cookie()) if part)
    if merged:
        headers["Cookie"] = merged
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            resp = await client.post(
                f"{IM_API}/web_im/v1/web_im/send_msg",
                params={"web_location": "333.999", "build": "0",
                        "mobi_app": "web", "platform": "web"},
                data=payload, headers=headers,
            )
    except httpx.HTTPError as e:
        raise BiliError(f"连接 B 站失败：{e}") from e
    return _data_of(resp)


async def _dm_uid_of(who: str, cookie: str) -> int:
    """会话对象：纯数字当 UID，否则先在**已有的私信会话里**按昵称找。

    先查会话再做全站搜人：会话里的人才是真跟你聊过的；全站同名的一抓一大把，
    按昵称直接把消息发给陌生人是不可挽回的错。搜人也只认昵称完全一致的。
    """
    raw = str(who or "").strip()
    if not raw:
        raise BiliError("要先说清发给谁：给会话里的昵称或 UID")
    if raw.isdigit():
        return int(raw)
    try:
        sessions = await _dm_sessions(cookie)
    except BiliError as e:
        logger.info("B 站私信会话读取失败（按搜人处理）: %s", e)
        sessions = []
    talkers = [int(s["talker_id"]) for s in sessions if s.get("talker_id")]
    names = await _dm_names(talkers, cookie)
    for uid, name in names.items():
        if name == raw:
            return uid
    for uid, name in names.items():
        if raw and raw in name:
            return uid
    rows = await _search_rows("bili_user", raw, cookie)
    hit = next((r for r in rows if str(r.get("uname") or "") == raw), None)
    if hit is None or not hit.get("mid"):
        raise BiliError(f"没找到「{raw}」这个会话对象：用私信列表里的昵称，或者直接给 UID")
    return int(hit["mid"])


async def _inbox_take(db, agent_id: int, cookie: str) -> tuple[dict[str, int], list[str], dict[str, int], int, list[dict]]:
    """B 站那边比游标新的回复 / @ / 赞 / 私信 → (各类条数, 逐行摘要, 新游标, 没念到的条数, 见过的条目)。

    行必须**全**：调用方把这些行写进账本之后才推进游标（见 _inbox_commit）。
    所以条数按行算；一轮念不完就停在念到的那条，剩下的下次再念——多念一遍只是多几句
    话，少念一条却是一条永远读不到的消息。返回的 pending 就是这次没念到的条数。

    最后一项是**这次看见的全部条目**，不只新的：B 站的消息接口只给当前一窗、不能按时间
    查，所以「过去三天谁回了我」只能问自己——看见就存（_feed_store），查的时候只读本地。
    """
    seen = await _inbox_seen(db, agent_id)
    counts: dict[str, int] = {}
    lines: list[str] = []
    cursors: dict[str, int] = {}
    rows: list[dict] = []
    pending = 0

    for kind in ("reply", "at"):
        try:
            items = await _msgfeed_items(kind, cookie)
        except BiliError as e:
            logger.info("B 站收件箱（%s）读取失败: %s", kind, e)
            continue
        window = [_reply_row(kind, it) for it in items]
        rows.extend(window)         # 存全部：区间查询要有历史，不能只存还没念的
        fresh = [r for r in window if int(r["key"]) > seen.get(kind, 0)]
        if not fresh:
            continue
        fresh.sort(key=lambda r: r["ts"])
        room = _INBOX_MAX_LINES - len(lines)
        if room <= 0:
            pending += len(fresh)
            continue        # 这一轮记不下了：游标也别动，下次接着念
        kept = fresh[:room]
        pending += len(fresh) - len(kept)
        counts[kind] = len(kept)
        lines.extend(_row_line(r) for r in kept)
        cursors[kind] = max(int(r["key"]) for r in kept)

    # 赞单独走：它按「点赞时间」聚合（同一个目标一条），游标也只能记时间，记 id 会漏
    try:
        likes = await _msgfeed_like_items(cookie)
    except BiliError as e:
        logger.info("B 站收件箱（赞）读取失败: %s", e)
        likes = []
    window = [_like_row(it) for it in likes]
    rows.extend(window)
    fresh = [r for r in window if r["ts"] > seen.get("like", 0)]
    if fresh:
        fresh.sort(key=lambda r: r["ts"])
        room = _INBOX_MAX_LINES - len(lines)
        if room <= 0:
            pending += len(fresh)
        else:
            kept = fresh[:room]
            pending += len(fresh) - len(kept)
            counts["like"] = len(kept)
            lines.extend(_row_line(r) for r in kept)
            cursors["like"] = max(r["ts"] for r in kept)

    try:
        sessions = [s for s in await _dm_sessions(cookie) if int(s.get("unread_count") or 0) > 0]
    except BiliError as e:
        logger.info("B 站收件箱（私信会话）读取失败: %s", e)
        sessions = []
    sessions.sort(key=lambda s: int(s.get("session_ts") or 0), reverse=True)
    picked = [s for s in sessions if s.get("talker_id")][:_INBOX_DM_SESSIONS]
    names = await _dm_names([int(s["talker_id"]) for s in picked], cookie)
    for session in picked:
        talker = int(session["talker_id"])
        name = names.get(talker) or str(talker)
        kind = f"im:{talker}"
        try:
            msgs = await _dm_msgs(talker, cookie)
        except BiliError as e:
            logger.info("B 站收件箱（私信 %s）读取失败: %s", talker, e)
            continue
        # 对方的、比游标新的都算「扫到了」；系统通知（登录提醒等）只扫不念——
        # 游标要越过它们，否则每次进来都把它们重新扫一遍
        scanned = [m for m in msgs
                   if int(m.get("sender_uid") or 0) == talker
                   and int(m.get("msg_seqno") or 0) > seen.get(kind, 0)]
        if not scanned:
            continue
        fresh = [(m, _dm_text(m)) for m in scanned if not m.get("notify_code")]
        fresh = [(m, t) for m, t in fresh if t]     # 图片/卡片没有正文，不该占一行
        if not fresh:
            cursors[kind] = max(int(m.get("msg_seqno") or 0) for m in scanned)
            continue
        room = _INBOX_MAX_LINES - len(lines)
        if room <= 0:
            pending += len(fresh)
            continue
        kept = fresh[:room]
        pending += len(fresh) - len(kept)
        if len(kept) < len(fresh):
            # 念不完就先停在这儿：游标只推到真念到的那条，剩下的下次念
            cursors[kind] = int(kept[-1][0].get("msg_seqno") or 0)
        else:
            cursors[kind] = max(int(m.get("msg_seqno") or 0) for m in scanned)
        rows.extend(_dm_row(talker, name, m, t) for m, t in kept)
        counts["dm"] = counts.get("dm", 0) + 1
        lines.append(f"私信｜{name}｜{'；'.join(t for _, t in kept)}")

    return counts, lines, cursors, pending, rows


def _counts_text(counts: dict[str, int]) -> str:
    """「回复 3 · 赞 1」这种一行数完的写法：模型的第一个问题就是这个。"""
    return " · ".join(f"{_KIND_CN[k]} {n}" for k, n in counts.items() if n)


def _inbox_text(counts: dict[str, int], lines: list[str]) -> str:
    return (
        f"【B 站收件箱】新来这些：{_counts_text(counts) or '（只有图片，没有正文）'}\n"
        + "\n".join(f"· {line}" for line in lines)
        + "\n（念过的都记进账本了，之后还能翻到；看不看、回不回都由你。）"
    )


async def _inbox_commit(db, agent_id: int, instance: str, target: str,
                        counts: dict[str, int], lines: list[str],
                        cursors: dict[str, int], rows: list[dict]) -> bool:
    """先落账本，再推进游标；返回是否真写进去了。

    顺序不能反：未读变已读，唯一的意思是「他之后还能翻到」。先推游标再落账，
    中途一失败，那条消息就既没进账本、也不会再被念到了。所以通知写失败就直接返回，
    游标一个字都不动（下次重念一遍，只是多几句话）。

    流水（rows）写在最前面且不受这个返回值影响：它是「B 站给我们看过什么」的事实，
    跟这次念没念进去无关；区间查询靠它，漏存一条就永远查不到。
    """
    from app.services.plugin import api

    await _feed_store(db, instance, rows)
    if not cursors:
        return False
    if lines:
        wrote = await api.notify_agent(
            agent_id, _inbox_text(counts, lines), context_ref=target,
            ref=f"bilibili:inbox:{max(cursors.values())}", db=db,
        )
        if not wrote:
            return False
    await _inbox_advance(db, int(agent_id), cursors)
    return True


# ── 互动流水：B 站给过的每一条都留一份，按时间区间查只读它 ──
#
# B 站的消息接口只给「当前一窗」，没有按时间取的能力（x/msgfeed/reply 带 id+reply_time
# 也只能往回翻，不能按区间筛）。所以「过去三天谁回了我、哪个帖子最热闹」只能问自己：
# 扫到就存，查的时候走本地索引，O(区间里的条数)，零网络。

_FEED_CAP = 200          # 一次查询最多看多少行：区间再长也只回最近这些，别把一轮撑爆
_BY = ("帖子", "天", "人")   # 聚类口径（默认按帖子）


async def _feed_store(db, instance: str, rows: list[dict]) -> None:
    """把这次看见的条目存进流水：只写不提交（跟账本、游标同一笔事务）。

    同一条再扫到不新增（(instance, kind, item_key) 唯一），但**渲染字段按这次覆盖**：
    行是"压给模型看的样子"，口径改了（比如赞后来带上被赞的评论），下次扫到就跟着改。
    """
    from sqlalchemy import text

    if not rows or not instance:
        return
    now = int(time.time())
    await db.execute(text(
        f'INSERT INTO {_t("feed")} '
        '(instance, kind, item_key, ts, who, target, title, said, root, counts, seen_at) '
        'VALUES (:instance, :kind, :item_key, :ts, :who, :target, :title, :said, :root, '
        ':counts, :seen_at) '
        'ON CONFLICT (instance, kind, item_key) DO UPDATE SET '
        'who = EXCLUDED.who, target = EXCLUDED.target, title = EXCLUDED.title, '
        'said = EXCLUDED.said, root = EXCLUDED.root, counts = EXCLUDED.counts'
    ), [{"instance": instance, "kind": r["kind"], "item_key": r["key"], "ts": r["ts"],
         "who": r["who"], "target": r["target"], "title": r["title"], "said": r["said"],
         "root": r["root"], "counts": r["counts"], "seen_at": now} for r in rows])


async def _feed_rows(db, instance: str, since: int, until: int) -> list[dict]:
    """本地流水按时间区间取（左闭右开）：走 (instance, ts) 索引，不发请求。

    取最近的 _FEED_CAP 行再翻回时间正序：区间给得太大时，旧的那头本来也没人看。
    """
    from sqlalchemy import text

    rows = (await db.execute(text(
        f'SELECT kind, ts, who, target, title, said, root, counts FROM {_t("feed")} '
        'WHERE instance = :i AND ts >= :s AND ts < :u ORDER BY ts DESC LIMIT :n'
    ), {"i": instance, "s": int(since), "u": int(until), "n": _FEED_CAP})).all()
    return [dict(r._mapping) for r in reversed(rows)]


def _tally(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row["kind"]] = out.get(row["kind"], 0) + 1
    return out


def _group_key(row: dict, by: str) -> str:
    if by == "天":
        return datetime.fromtimestamp(int(row["ts"]), tz=_CST).strftime("%Y-%m-%d")
    if by == "人":
        return row["who"] or "（不认识的人）"
    if row["kind"] == "dm":
        return f"私信 · {row['target'] or row['who']}"
    return row["target"] or _clip(row["title"] or "", 24) or "（没挂到具体帖子）"


def _ts_short(ts) -> str:
    """一行里带的时间：只到分钟，月日在左——区间查询本来就有头行写着年份。"""
    try:
        return datetime.fromtimestamp(int(ts), tz=_CST).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _feed_report(rows: list[dict], by: str) -> str:
    """按口径把区间内的流水聚成一段话：帖子（默认）/ 天 / 人。

    聚类的意义是让模型别把同一个帖子下的八条回复当成八件事，顺带也省 token。
    组按最近一条的时间倒序，组内按时间正序——「最近热闹的在上，谁先谁后照实」。
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_group_key(row, by), []).append(row)
    out: list[str] = []
    for key, items in sorted(groups.items(),
                             key=lambda kv: max(r["ts"] for r in kv[1]), reverse=True):
        out.append(f"· {key}（{_counts_text(_tally(items))}）")
        out.extend(f"  · {_ts_short(r['ts'])} {_row_line(r)}" for r in items)
    return "\n".join(out)


_WHEN_FULL = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?")
_WHEN_MD = re.compile(r"^(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$")


def _parse_when(raw: str, default: int = 0, *, day_end: bool = False) -> int:
    """「2026-11-01」/「11-01 12:00」→ epoch 秒（东八区）；认不出就用 default。

    只给日期不给时刻时，起点取当天 00:00、终点取次日 00:00——「到 11-05」得把 11-05
    一整天算进去，否则那天的消息会凭空少掉。
    """
    text = str(raw or "").strip()
    end_of_day = day_end and ":" not in text      # 只给日期：终点算到次日 00:00
    m = _WHEN_FULL.search(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = int(m.group(4) or 0), int(m.group(5) or 0)
    else:
        m = _WHEN_MD.match(text)
        if not m:
            return default
        year = datetime.now(tz=_CST).year
        month, day = int(m.group(1)), int(m.group(2))
        hour, minute = int(m.group(3) or 0), int(m.group(4) or 0)
    try:
        start = datetime(year, month, day, hour, minute, tzinfo=_CST).timestamp()
    except ValueError:
        return default
    return int(start) + (86400 if end_of_day else 0)


def _range_of(arguments: dict) -> tuple[int, int, str]:
    """工具参数 → (起, 止, 聚类口径)，左闭右开；没有区间参数就是 (0, 0, "")。

    三种写法都收：days=7（最近七天）、since/until、只给一头。只给 until 时往回七天，
    只给 since 时到此刻——问「这几天」的人很少同时给出两头。
    """
    by = str(arguments.get("by") or "").strip()
    by = by if by in _BY else "帖子"
    since_raw = str(arguments.get("since") or "").strip()
    until_raw = str(arguments.get("until") or "").strip()
    days = arguments.get("days")
    if not since_raw and not until_raw and not days:
        return 0, 0, ""
    now = int(time.time())
    since = _parse_when(since_raw) if since_raw else 0
    if not since and days:
        since = now - _clamp(days, 7, 366) * 86400
    if not since:
        since = now - 7 * 86400
    until = _parse_when(until_raw, now, day_end=True) if until_raw else now
    return since, max(until, since + 1), by


def _quote_brief(text: str, limit: int = 40) -> str:
    body = " ".join(str(text or "").split())
    return body if len(body) <= limit else body[:limit] + "…"


def _link_of(row: dict) -> str:
    """这条评论在网页上的位置：通知里要能点回去核对。"""
    return f"https://www.bilibili.com/video/{row['bvid']}/#reply{int(row['rpid'])}"


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
        "标题": data.get("title", ""),
        "链接": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "UP主": {"mid": owner.get("mid"), "昵称": owner.get("name", "")},
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


def _video_row(item: dict, time_key: str) -> dict:
    """短视频行：搜索结果与空间投稿字段名一致，只有时间戳的键不同（pubdate / created）。"""
    bvid = item.get("bvid") or ""
    return {
        "bvid": bvid,
        "标题": _plain(item.get("title")),
        "播放": item.get("play"),
        "时长": item.get("length") or item.get("duration"),
        "发布时间": _ts_to_str(item.get(time_key)),
        # 统一给 BV 链接：接口自带的 arcurl 是 av 号，两种编号混在一套工具里看着乱
        "链接": f"https://www.bilibili.com/video/{bvid}" if bvid else (item.get("arcurl") or ""),
    }


async def _comment_pages(aid: int, cookie: str, *, mode: int = 2, pages: int = 3):
    """按游标翻评论页：wbi/main 的下一页在 cursor.pagination_reply.next_offset。

    mode 是接口的排序码：2 = 按时间（最新在前），3 = 按热度。默认取 2：
    按时间翻页才能顺着时间轴往下找，找刚发的评论更是只能靠它。
    """
    offset = ""
    for _ in range(pages):
        data = await _signed_get("/x/v2/reply/wbi/main", {
            "oid": aid, "type": 1, "mode": mode, "plat": 1,
            "pagination_str": json.dumps({"offset": offset}), "seek_rpid": "",
        }, cookie)
        yield data
        cursor = data.get("cursor") or {}
        offset = (cursor.get("pagination_reply") or {}).get("next_offset") or ""
        if not offset or cursor.get("is_end"):
            return


def _comment_row(item: dict, root: int | None = None) -> dict:
    """一条评论压成给模型看的行（字段名用中文，和本插件其它工具的返回一致）。

    root 是这栋楼要挂的根评论 rpid。楼中楼的 root 和 parent 由 _reply_anchor 一起算，
    缺一个 B 站就把回复当成新评论丢到最顶层且不报错。顶层评论的 root 就是它自己。
    """
    member = item.get("member") or {}
    content = item.get("content") or {}
    return {
        "rpid": item.get("rpid"),
        "root": int(root or item.get("rpid") or 0),
        "用户": member.get("uname", ""),
        "内容": _plain(content.get("message", "")),
        "点赞": int(item.get("like") or 0),
        "回复数": int(item.get("rcount") or 0),
        "时间": _ts_to_str(item.get("ctime")),
        "UP主": bool(member.get("is_up")),
        # 这个 AI 自己赞过没有：要 like 还是 unlike 看这一位，别让它猜
        "已赞": bool(item.get("action")),
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
# 使用轨迹：这个 AI 拿它那个账号做过什么
# ============================================================

# 为什么进表：轨迹只服务这条通道的卡片，"AI 用账号做过什么"的口径（有哪些工具、
# 推送怎么算）全归插件自己——表也写在插件自己的迁移里（migrations/0001_init.py），
# 核心只知道前缀归属。表比文件好在：只取最近 N 条不用整文件读，裁剪是 SQL，
# 写入带事务（推送和轨迹不会写一半）。
#
# 这是给人扫一屏的轨迹，不是审计账本（追责看 audit_logs）。

TRAIL_MAX_RECORDS = 500
_trail_writes = 0


def _trail_brief(args: dict | None, limit: int = 200) -> str:
    """参数压成一行（轨迹是给人扫一眼的，不追求精确回放）"""
    if not args:
        return ""
    try:
        text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= limit else text[:limit] + "…"


# 动作名（三语）由插件声明，**不进数据库**：轨迹只存机器数据（工具 id + 原始参数），
# 界面按当前语言取对应的那一条。加新工具不用动表，也不用回填历史。
# 键里既有工具名，也有 push / login 这两个非工具动作。
_TRAIL_LABELS: dict[str, tuple[str, str, str]] = {
    "bili_account": ("查账号", "Account", "アカウント確認"),
    "bili_video": ("查视频", "Video", "動画を見る"),
    "bili_hot": ("看热门", "Popular", "人気を見る"),
    "bili_user": ("查 UP 主", "Creator", "投稿者を見る"),
    "bili_search_user": ("搜用户", "Search users", "ユーザー検索"),
    "bili_search_video": ("搜视频", "Search videos", "動画検索"),
    "bili_user_videos": ("列投稿", "List videos", "投稿一覧"),
    "bili_live": ("查直播", "Live room", "配信を見る"),
    "bili_dynamics": ("看动态", "Dynamics", "動態を見る"),
    "bili_subscribe": ("管订阅", "Subscriptions", "購読管理"),
    "bili_like": ("点赞", "Like", "いいね"),
    "bili_comments": ("列评论", "Comments", "コメント一覧"),
    "bili_danmaku": ("看弹幕", "Danmaku", "弾幕"),
    "bili_comment": ("评论", "Comment", "コメント"),
    "bili_dm": ("私信", "DM", "DM"),
    "comment_public": ("评论公开成功", "Comment public", "コメント公開"),
    "comment_hidden": ("评论没公开", "Comment hidden", "コメント非公開"),
    "bilibili_summary": ("汇总", "Summary", "まとめ"),
    "push": ("推送", "Push", "プッシュ"),
    "login": ("扫码登录", "Sign-in", "ログイン"),
}


# 参数同样只是展示，而且要说清"这个数字是谁的"：limit 是 AI **要的条数**（上限），
# 不是搜到的条数——直接写"条数=2"会被读成结果数。所以参数可以带一句格式：
#   fmt 里用 {} 占位值，出现 fmt 就把整段替换成人话（"最多 2 条"），否则退回 "名字=值"。
# 依旧不落库：库里只有参数键值原文。
_TRAIL_ARGS: dict[str, dict[str, tuple[str, str, str] | None]] = {
    # 参数名（三语）          值的格式（三语，可选）
    "keyword": {"text": ("关键词", "keyword", "キーワード"), "fmt": ("「{}」", "「{}」", "「{}」")},
    "limit": {"text": ("条数", "count", "件数"), "fmt": ("最多 {} 条", "up to {}", "最大 {} 件")},
    "mid": {"text": ("UP 的 mid", "creator mid", "投稿者 mid"), "fmt": ("UP {}", "UP {}", "UP {}")},
    "bvid": {"text": ("BV 号", "BV id", "BV 番号"), "fmt": ("BV 号 {}", "BV {}", "BV {}")},
    "room_id": {"text": ("直播间号", "room id", "配信ルーム"), "fmt": ("房间 {}", "room {}", "配信 {}", )},
    "message": {"text": ("正文", "text", "本文"), "fmt": ("「{}」", "「{}」", "「{}」")},
    "name": {"text": ("名字", "name", "名前"), "fmt": ("「{}」", "「{}」", "「{}」")},
    "up": {"text": ("UP 主", "creator", "投稿者"), "fmt": ("{}", "{}", "{}")},
    "group_id": {"text": ("群", "group", "グループ"), "fmt": ("群 {}", "group {}", "グループ {}")},
    "like": {"text": ("点赞", "like", "いいね"), "fmt": None},
    "action": {"text": ("操作", "action", "操作"), "fmt": None},
    "p": {"text": ("分P", "part", "パート"), "fmt": ("第 {} P", "part {}", "第 {} P")},
    "at": {"text": ("时间点", "at", "時点"), "fmt": ("{} 秒", "{} s", "{} 秒")},
}


def _trail_i18n() -> dict[str, dict[str, dict[str, str]]]:
    """轨迹的展示字典：动作名与参数（名字 + 值的格式）各一份，都是三语。

    形状与插件其它三语文案一致（text / text_en / text_ja / fmt / fmt_en / fmt_ja）；
    界面用 pick(lang, …) 取。库里只存机器数据，换语言、改叫法、加工具都不用动数据。
    """
    def _text(item: tuple[str, str, str]) -> dict[str, str]:
        return {"text": item[0], "text_en": item[1], "text_ja": item[2]}

    args: dict[str, dict[str, str]] = {}
    for key, spec in _TRAIL_ARGS.items():
        entry = _text(spec["text"])
        fmt = spec.get("fmt")
        if fmt:
            entry.update({"fmt": fmt[0], "fmt_en": fmt[1], "fmt_ja": fmt[2]})
        args[key] = entry

    return {
        "actions": {k: _text(v) for k, v in _TRAIL_LABELS.items()},
        "args": args,
    }


async def _trail_record(
    instance: str, action: str, raw: str = "", *, ok: bool = True, duration_ms: int = 0,
) -> None:
    """记一条轨迹；任何失败都只写日志——记不上不许影响工具或推送本身"""
    global _trail_writes
    from sqlalchemy import text

    from app.database import async_session

    try:
        async with async_session() as db:
            await db.execute(text(
                f'INSERT INTO {_t("activity")} (instance, action, raw, ok, duration_ms) '
                'VALUES (:i, :a, :r, :ok, :ms)'
            ), {
                "i": instance, "a": str(action or "")[:60], "r": (raw or "")[:600],
                "ok": bool(ok), "ms": max(0, int(duration_ms or 0)),
            })
            # 每 20 条裁一次：按实例只留最近 TRAIL_MAX_RECORDS 条
            _trail_writes += 1
            if _trail_writes % 20 == 0:
                await db.execute(text(
                    f'DELETE FROM {_t("activity")} WHERE instance = :i AND id <= ('
                    f'SELECT id FROM {_t("activity")} WHERE instance = :i '
                    'ORDER BY id DESC OFFSET 500 LIMIT 1)'
                ), {"i": instance})
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("B 站使用轨迹写入失败: %s", e)


async def _trail_recent(instance: str, limit: int = 20) -> dict:
    """给卡片用：{count, items}（新→旧）"""
    from sqlalchemy import text

    from app.database import async_session

    try:
        async with async_session() as db:
            total = int((await db.scalar(text(
                f'SELECT count(*) FROM {_t("activity")} WHERE instance = :i'
            ), {"i": instance})) or 0)
            rows = (await db.execute(text(
                f'SELECT action, raw, ok, duration_ms, created_at FROM {_t("activity")} '
                'WHERE instance = :i ORDER BY id DESC LIMIT :n'
            ), {"i": instance, "n": max(1, min(int(limit), 100))})).all()
    except Exception as e:  # noqa: BLE001
        logger.debug("B 站使用轨迹读取失败: %s", e)
        return {"count": 0, "items": []}
    return {
        "count": total,
        # 动作名与参数名的翻译：库里的 action / raw 都是机器数据，界面按语言取这份字典
        "i18n": _trail_i18n(),
        "items": [
            {
                "at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                "action": r.action,
                "raw": r.raw or "",
                "ok": bool(r.ok),
                "ms": int(r.duration_ms or 0),
            }
            for r in rows
        ],
    }


# ============================================================
# 工具（AI 在对话里调用；登录态按 agent → 实例取）
# ============================================================

# 工具只收 arguments，但"这次是在哪个会话里说的"要留给后续通知用（评论发出后的公开确认）。
# 用 ContextVar 而不是 self.xxx：工具实例是共享的，并发调用会把别人的会话串进来。
_call_ctx: ContextVar[dict] = ContextVar("bili_call_ctx", default={})


class _BiliTool(ToolPlugin):
    """B 站工具的共同外壳：一次调用记一条使用轨迹。

    为什么不借平台的工具记账点：那套是全局日聚合（只有次数）；而"AI 拿它自己的账号
    做了什么"是插件自己的事——数据也落在插件自己的表里。子类只实现 run()。
    """

    async def execute(self, db, agent_id, group_id, arguments, context):
        started = time.monotonic()
        instance = _instance_of(agent_id)
        raw = _trail_brief(arguments)   # 模型实际传的参数：界面上点「原文」看的就是它
        token = _call_ctx.set({
            "agent_id": agent_id,
            "group_id": group_id,
            "session_id": str((context or {}).get("session_id") or ""),
            # 平台的回合事务：插件不再自己开第二个会话（两个会话互相等锁是老毛病）
            "db": db,
        })
        try:
            result = await self.run(agent_id, arguments)
        except Exception:
            await _trail_record(
                instance, self.name, raw, ok=False,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        finally:
            _call_ctx.reset(token)
        await _trail_record(
            instance, self.name, raw,
            ok=not (isinstance(result, dict) and result.get("error")),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    async def run(self, agent_id: int, arguments: dict) -> dict:
        raise NotImplementedError


class BiliAccount(_BiliTool):
    name = "bili_account"
    description = "查这个 AI 绑定的 B 站账号：是否已登录、B 站昵称与 UID。未登录时说明需要创建者去通道卡片扫码。"
    segment = SEGMENT
    parameters = {}
    states = ["active", "dnd"]
    admin_description = "查本 AI 的 B 站登录态。"
    trigger_condition = "用户问 AI 的 B 站账号情况"
    usage_notes = [(300, "未登录时不要假装能查动态；先让创建者去通道卡片扫码。")]

    async def run(self, agent_id: int, arguments: dict):
        instance = _instance_of(agent_id)
        cfg = await _instance_config(instance)
        cookie = _cookie_of(cfg)
        if not cookie:
            return {"ok": True, "logged_in": False, "hint": _NEED_LOGIN}
        info = await _nav_cached(cookie, instance)
        return {
            "ok": True,
            "已登录": bool(info.get("isLogin")),
            "昵称": info.get("uname") or cfg.get("nickname") or "",
            "UID": info.get("mid") or cfg.get("bili_mid"),
        }


class BiliVideo(_BiliTool):
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

    async def run(self, agent_id: int, arguments: dict):
        raw = str(arguments.get("bvid") or "").strip()
        params = {"aid": raw[2:]} if raw.lower().startswith("av") else {"bvid": raw}
        data = await _get(f"{API}/x/web-interface/view", params, await _agent_cookie(agent_id))
        return {"ok": True, "video": _video_result(data)}


class BiliHot(_BiliTool):
    name = "bili_hot"
    description = "B 站全站热门榜。返回名次、BV 号、标题、UP 主、播放量。"
    segment = SEGMENT
    parameters = {"limit": {"type": "integer", "description": "要几条，默认 5，最多 20"}}
    states = ["active", "dnd"]
    admin_description = "取 B 站全站热门视频榜。"
    trigger_condition = "用户问 B 站最近有什么热门"
    usage_notes = [(300, "热门榜没有分区参数；要分区内容只能按标题自己筛。")]

    async def run(self, agent_id: int, arguments: dict):
        limit = _clamp(arguments.get("limit"), 5, 20)
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


class BiliUser(_BiliTool):
    name = "bili_user"
    description = "查 B 站 UP 主粉丝数与关注数（mid 即 space.bilibili.com/<mid> 里的数字）。"
    segment = SEGMENT
    parameters = {"mid": {"type": "integer", "description": "UP 主的 mid（UID）"}}
    required = ["mid"]
    states = ["active", "dnd"]
    admin_description = "查 UP 主粉丝/关注数据。"
    trigger_condition = "用户问某个 UP 主有多少粉丝"
    usage_notes = [(400, "粉丝数是快照，别当实时赛事记分。")]

    async def run(self, agent_id: int, arguments: dict):
        mid = int(arguments["mid"])
        data = await _get(f"{API}/x/relation/stat", {"vmid": mid}, await _agent_cookie(agent_id))
        return {
            "ok": True,
            "mid": mid,
            "粉丝数": data.get("follower", 0),
            "关注数": data.get("following", 0),
            "主页": f"https://space.bilibili.com/{mid}",
        }


class BiliSearchUser(_BiliTool):
    name = "bili_search_user"
    description = "按昵称搜 B 站用户：返回 mid、昵称、粉丝数、签名。"
    segment = SEGMENT
    parameters = {
        "keyword": {"type": "string", "description": "要搜的昵称（或昵称里的几个字）"},
        "limit": {"type": "integer", "description": "要几个候选人，默认 5，最多 10"},
    }
    required = ["keyword"]
    states = ["active", "dnd"]
    admin_description = "按昵称搜 B 站用户（昵称 → mid）。"
    trigger_condition = "用户只给了 UP 主昵称，没给 mid 或主页"
    usage_notes = []

    async def run(self, agent_id: int, arguments: dict):
        keyword = str(arguments.get("keyword") or "").strip()
        if not keyword:
            return {"ok": False, "error": "要给 keyword（昵称或昵称里的几个字）"}
        limit = _clamp(arguments.get("limit"), 5, 10)
        rows = await _search_rows("bili_user", keyword, await _agent_cookie(agent_id))
        items = []
        for it in rows[:limit]:
            mid = it.get("mid")
            items.append({
                "mid": mid,
                "昵称": _plain(it.get("uname")),
                "粉丝数": it.get("fans"),
                "投稿数": it.get("videos"),
                "等级": it.get("level"),
                "签名": _plain(it.get("usign"))[:80],
                "主页": f"https://space.bilibili.com/{mid}" if mid else "",
            })
        return {"ok": True, "keyword": keyword, "count": len(items), "items": items}


# 搜视频的排序口径：接口就认这几个值，enum、参数说明、回传校验共用这一张表
_SEARCH_ORDERS = {
    "totalrank": "综合",
    "pubdate": "最新发布",
    "click": "播放最多",
    "dm": "弹幕最多",
    "stow": "收藏最多",
    "scores": "评论最多",
}


class BiliSearchVideo(_BiliTool):
    name = "bili_search_video"
    description = "按关键词搜 B 站视频：BV 号、标题、UP 主、播放量、时长；order 换排序（默认综合）。"
    segment = SEGMENT
    parameters = {
        "keyword": {"type": "string", "description": "视频关键词（内容、标题里的词）"},
        "order": {
            "type": "string",
            "description": "、".join(f"{k} {v}" for k, v in _SEARCH_ORDERS.items()),
            "enum": list(_SEARCH_ORDERS),
        },
        "limit": {"type": "integer", "description": "要几条，默认 5，最多 10"},
    }
    required = ["keyword"]
    states = ["active", "dnd"]
    admin_description = "按关键词搜索 B 站视频（可按最新/播放等排序）。"
    trigger_condition = "用户想找某类/某个主题的视频，但没给 BV 号"
    usage_notes = []

    async def run(self, agent_id: int, arguments: dict):
        keyword = str(arguments.get("keyword") or "").strip()
        if not keyword:
            return {"ok": False, "error": "要给 keyword（视频关键词）"}
        limit = _clamp(arguments.get("limit"), 5, 10)
        order = str(arguments.get("order") or "totalrank").strip().lower()
        if order not in _SEARCH_ORDERS:
            order = "totalrank"
        rows = await _search_rows("video", keyword, await _agent_cookie(agent_id), order)
        items = []
        for it in rows[:limit]:
            items.append({**_video_row(it, "pubdate"), "UP主": _plain(it.get("author"))})
        return {"ok": True, "keyword": keyword, "count": len(items), "items": items}


class BiliUserVideos(_BiliTool):
    name = "bili_user_videos"
    description = "列某个 UP 主的投稿视频（标题、BV 号、播放量、发布时间），可按标题关键词筛选；需要这个 AI 已登录 B 站。"
    segment = SEGMENT
    parameters = {
        "mid": {"type": "integer", "description": "UP 主的 mid"},
        "keyword": {"type": "string", "description": "只看标题里含这个词的投稿（可选）"},
        "limit": {"type": "integer", "description": "要几条，默认 10，最多 30"},
    }
    required = ["mid"]
    states = ["active", "dnd"]
    admin_description = "列 UP 主投稿（可选标题过滤）。"
    trigger_condition = "用户想知道某个 UP 主发过哪些视频"
    usage_notes = []

    async def run(self, agent_id: int, arguments: dict):
        mid = int(arguments["mid"])
        limit = _clamp(arguments.get("limit"), 10, 30)
        keyword = str(arguments.get("keyword") or "").strip().lower()
        cookie = await _agent_cookie(agent_id)
        if not cookie:
            raise BiliError(_NEED_LOGIN)
        # 按关键词筛就一页多拿几条（一页最多 50），凑满 limit 就停，最多翻三页
        page_size = 50 if keyword else limit
        items: list[dict] = []
        for page in range(1, 4):
            data = await _signed_get(
                "/x/space/wbi/arc/search",
                {"mid": mid, "ps": page_size, "pn": page, "order": "pubdate"},
                cookie,
            )
            rows = ((data.get("list") or {}).get("vlist") or [])
            items.extend(
                _video_row(v, "created") for v in rows
                if not keyword or keyword in _plain(v.get("title")).lower()
            )
            if len(items) >= limit or len(rows) < page_size:
                break
        items = items[:limit]
        return {"ok": True, "mid": mid, "keyword": keyword or None, "count": len(items), "items": items}


class BiliLike(_BiliTool):
    name = "bili_like"
    description = "给 B 站视频点赞或取消点赞（用这个 AI 自己的账号）。"
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号"},
        "like": {"type": "boolean", "description": "true 点赞，false 取消，默认点赞"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "用 AI 自己的账号给视频点赞/取消。"
    trigger_condition = "用户让 AI 给某个视频点赞"
    usage_notes = []

    async def run(self, agent_id: int, arguments: dict):
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        like = arguments.get("like", True)
        like = True if like is None else bool(like)
        cfg, cookie = await _write_ctx(agent_id)
        try:
            await _post(
                "/x/web-interface/archive/like",
                # 接口口径：1 = 点赞，2 = 取消（0 会被判 -400）
                {"bvid": bvid, "like": 1 if like else 2, "csrf": _csrf_of(cfg)},
                cookie,
                referer=f"https://www.bilibili.com/video/{bvid}/",
            )
        except BiliError as e:
            # 65004 = 取消赞失败、本来就没赞过：对用户来说就是"现在没赞"，按成功回
            if not like and e.code == 65004:
                return {"ok": True, "bvid": bvid, "liked": False, "already": True, "账号": cfg.get("nickname") or ""}
            raise
        return {"ok": True, "bvid": bvid, "liked": like, "账号": cfg.get("nickname") or ""}


class BiliComments(_BiliTool):
    name = "bili_comments"
    description = "列 B 站视频的评论（用户怎么说）；给 root 就列那条评论下的楼中楼。"
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号"},
        "root": {"type": "integer", "description": "只看某条评论下的回复（rpid，可选）"},
        "sort": {
            "type": "string",
            "description": "hot 按热度（默认）、time 按时间（最新在前）；B 站没有「最早在前」这个排序",
            "enum": ["hot", "time"],
        },
        "limit": {"type": "integer", "description": "要几条，默认 10，最多 30"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "读视频评论（含楼中楼）。"
    trigger_condition = "用户想知道某条视频下面大家在说什么"
    usage_notes = []

    async def run(self, agent_id: int, arguments: dict):
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        limit = _clamp(arguments.get("limit"), 10, 30)
        sort = "time" if str(arguments.get("sort") or "hot").lower() == "time" else "hot"
        cfg = await _instance_config(_instance_of(agent_id))
        cookie = _cookie_of(cfg)
        aid = await _aid_of(bvid, cookie)

        root = arguments.get("root")
        rows: list[dict] = []
        if root:
            data = await _get(
                f"{API}/x/v2/reply/reply",
                {"type": 1, "oid": aid, "root": int(root), "ps": limit, "pn": 1},
                cookie,
            )
            # 楼中楼每一行的 root 都是这一栋的根：模型照着 root 回，就不会漏掉 parent
            rows = [_comment_row(r, root=int(root)) for r in (data.get("replies") or [])]
        else:
            # 接口口径：mode=3 按热度，mode=2 按时间倒序（实测，见 README「已知限制」）
            async for data in _comment_pages(aid, cookie, mode=2 if sort == "time" else 3, pages=4):
                rows.extend(_comment_row(r) for r in (data.get("replies") or []))
                if len(rows) >= limit:
                    break
            rows = rows[:limit]
        return {"ok": True, "bvid": bvid, "root": root or None, "count": len(rows), "items": rows}


class BiliDanmaku(_BiliTool):
    name = "bili_danmaku"
    description = (
        "看 B 站视频的弹幕（观众边看边打在画面上飘的话）。不给 at 就跨整条视频均匀取几条；"
        "给了 at 只看那一小段前后几秒。"
    )
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号"},
        "p": {"type": "integer", "description": "第几个分 P，从 1 开始，默认 1"},
        "at": {"type": "number", "description": "只看这个时间点前后 8 秒（秒，可选）"},
        "limit": {"type": "integer", "description": "要几条，默认 20，最多 50"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "读视频弹幕（不需要登录）。"
    trigger_condition = "用户想知道某条视频的弹幕在说什么"
    usage_notes = [(300, "弹幕是观众随手打的、一句一句；要成段的观点还是用 bili_comments。")]

    async def run(self, agent_id: int, arguments: dict):
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        limit = _clamp(arguments.get("limit"), 20, 50)
        pages = (await _view_of(bvid)).get("pages") or []
        try:
            index = int(arguments.get("p") or 1)
        except (TypeError, ValueError):
            index = 1
        if index < 1 or index > len(pages):
            # 越界要报错，不能悄悄换成第 1 P：模型会以为拿到的是它要的那一 P
            return {"ok": False, "error": f"这条视频只有 {len(pages)} 个分 P，p 从 1 开始"}
        rows = await _danmaku_of(int(pages[index - 1].get("cid") or 0))

        at = arguments.get("at")
        if at in (None, ""):
            picked, span = _spread(rows, limit), "全片"
        else:
            try:
                center = float(at)
            except (TypeError, ValueError):
                return {"ok": False, "error": "at 是秒数，直接给数字"}
            picked = _spread([row for row in rows if abs(row[0] - center) <= 8], limit)
            span = f"{_mmss(center - 8)}–{_mmss(center + 8)}"
        return {
            "ok": True,
            "bvid": bvid,
            "第几分P": index,
            "全片弹幕": len(rows),
            "范围": span,
            "count": len(picked),
            "items": [{"时间": _mmss(t), "内容": body} for t, body in picked],
        }


class BiliSubtitle(_BiliTool):
    name = "bili_subtitle"
    description = (
        "读 B 站视频的稿子（B 站自己给语音做的识别稿）：片子里的人说了什么。"
        "默认从开头读一段；片子长就按时间区间分几次读完。要看画面时给 timed=true，"
        "每句带时间，再用那个时间去取画面。"
    )
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号"},
        "p": {"type": "integer", "description": "第几个分 P，从 1 开始，默认 1"},
        "from": {"type": "number", "description": "从第几秒开始读，默认 0"},
        "to": {"type": "number", "description": "读到第几秒为止（可选；默认读到字数上限）"},
        "timed": {"type": "boolean", "description": "逐句带时间（[mm:ss]），要看画面时用；默认只给连续正文"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "读视频的语音识别稿（字幕）。"
    trigger_condition = "用户想知道某条视频里具体讲了什么"
    # 唯一必写的边界：这是"听到的话"，不是"画面上的字"
    usage_notes = [(300, "这是机器听写、有错字；它说的是嘴里的话，画面上的字要看截图。")]

    async def run(self, agent_id: int, arguments: dict):
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        cookie = await _agent_cookie(agent_id)
        view = await _view_of(bvid, cookie)
        pages = view.get("pages") or []
        try:
            index = int(arguments.get("p") or 1)
        except (TypeError, ValueError):
            index = 1
        if index < 1 or index > len(pages):
            # 越界要报错，不能悄悄换成第 1 P：模型会以为读到的是它要的那一 P
            return {"ok": False, "error": f"这条视频只有 {len(pages)} 个分 P，p 从 1 开始"}
        page = pages[index - 1]
        duration = float(page.get("duration") or view.get("duration") or 0)
        rows, tracks = await _subtitle_rows(
            int(view["aid"]), int(page.get("cid") or 0), bvid, cookie, duration
        )
        if not tracks:
            return {
                "ok": False,
                "error": "这条视频没有字幕稿（B 站没给这段做语音识别），读不了原话；可以改看弹幕或评论。",
            }
        if not rows:
            return {
                "ok": False,
                "error": "这条视频有稿件，但 B 站现在挂出来的内容和它对不上（重试几次都是别人的稿子）；"
                         "过一会儿再读一次，急的话先看弹幕或评论。",
            }

        start = _seconds(arguments.get("from"))
        end = _seconds(arguments.get("to"))
        picked = [
            row for row in rows
            if (start is None or row[1] > start) and (end is None or row[0] < end)
        ]
        if not picked:
            return {
                "ok": False,
                "error": (
                    f"这一段没有字幕；整段字幕是 {_mmss(rows[0][0])}–{_mmss(rows[-1][1])}"
                ),
            }

        taken: list[tuple[float, float, str]] = []
        used = 0
        for row in picked:
            if taken and used + len(row[2]) > _SUB_CHARS:
                break
            taken.append(row)
            used += len(row[2])
        out = {
            "ok": True,
            "bvid": bvid,
            "第几分P": index,
            "字幕": "机器听写（说的是什么，不是画面上的字）",
            "范围": f"{_mmss(taken[0][0])}–{_mmss(taken[-1][1])}",
            "全片": _mmss(duration) if duration else "",
            "count": len(taken),
            "text": _render_subs(taken, bool(arguments.get("timed"))),
        }
        if duration and rows[-1][1] < duration - 30:
            out["注意"] = (
                f"稿子只到 {_mmss(rows[-1][1])}，后面没有内容——B 站还没把它生成完，"
                "或者后面只有画面、没人说话。过一会儿再读一次可能能读到更多。"
            )
        nxt = picked[len(taken)] if len(taken) < len(picked) else None
        if nxt is not None:
            out["还有"] = (
                f"{_mmss(nxt[0])} 之后还有 {int((rows[-1][1] - nxt[0]) / 60) + 1} 分钟没读，"
                f"接着读就给 from={int(nxt[0])}"
            )
        return out


class BiliShot(_BiliTool):
    name = "bili_shot"
    description = (
        "看 B 站视频某几秒的画面（B 站抽好的缩略帧，不用下载视频）。给了时间就取那前后各一格，"
        "两帧连看看得出动作；不给时间就取开头两格。"
    )
    segment = SEGMENT
    parameters = {
        "bvid": {"type": "string", "description": "BV 号"},
        "p": {"type": "integer", "description": "第几个分 P，从 1 开始，默认 1"},
        "at": {
            "type": "array",
            "items": {"type": "number"},
            "description": "要看哪几秒的画面，如 [83] 或 [83, 120]；最多两个时间点（每个出两帧）",
        },
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "取视频某几秒的画面（缩略帧）。"
    trigger_condition = "想知道某个时间点的画面长什么样时"
    # 唯一必写的边界：帧是等间隔抽的，别把标签时间当成"你要的那一秒"
    usage_notes = [(300, "帧是等间隔抽的（约 5–8 秒一格）；每张的实际画面时间写在 frames 里，按它说，不要当成你要的那一秒。")]

    async def run(self, agent_id: int, arguments: dict):
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        cookie = await _agent_cookie(agent_id)
        view = await _view_of(bvid, cookie)
        pages = view.get("pages") or []
        try:
            index = int(arguments.get("p") or 1)
        except (TypeError, ValueError):
            index = 1
        if index < 1 or index > len(pages):
            # 越界要报错，不能悄悄换成第 1 P：模型会以为看到的是它要的那一 P
            return {"ok": False, "error": f"这条视频只有 {len(pages)} 个分 P，p 从 1 开始"}
        page = pages[index - 1]
        times, sheets = await _shot_timeline(
            int(view["aid"]), int(page.get("cid") or 0), bvid, cookie
        )
        if not times or not sheets:
            return {"ok": False, "error": "这条视频没有可用的缩略帧，看不了画面；只能靠字幕、弹幕或评论。"}

        asked = _times_of(arguments.get("at"))
        duration = float(page.get("duration") or view.get("duration") or 0)
        if duration and asked and max(asked) > duration:
            # 超出片长多半是拿了别的视频的时间轴，报错比悄悄给最后一帧有用
            return {
                "ok": False,
                "error": f"这条视频只有 {_mmss(duration)}，at 里的时间超出片长了（{_mmss(max(asked))}）。",
            }
        picks = _shot_picks(times, asked)[:_SHOT_CELLS] if asked else _head_picks(times)

        frames: list[dict] = []
        images: list[dict] = []
        for cell, actual, want in picks:
            try:
                url = _cell_data_url(await _sprite_bytes(sheets[cell // _SHOT_PER_SHEET]), cell)
            except Exception as e:
                logger.warning("B 站取帧失败 bvid=%s cell=%s: %s", bvid, cell, e)
                continue
            label = _mmss(actual)
            frame = {"画面": label}
            if asked:
                frame["你要的"] = _mmss(want)
            frames.append(frame)
            images.append({"url": url, "label": label})
        if not images:
            return {"ok": False, "error": "缩略图下载或裁剪失败，过一会儿再试。"}

        return {
            "ok": True,
            "bvid": bvid,
            "第几分P": index,
            "count": len(frames),
            "frames": frames,
            "images": images,
        }


# 评论工具的动作表：enum 与给模型看的说明都从这一张表取。
# 之前这里手写了「发评论，或删掉自己发过的评论」，模型就照着它认定自己不会给评论点赞。
_COMMENT_ACTIONS = {
    "post": "发评论（默认）",
    "reply": "回某条",
    "like": "给评论点赞",
    "unlike": "取消赞",
    "pin": "置顶（UP 主）",
    "unpin": "取消置顶",
    "delete": "删自己发的",
}

_ACTIONS_TEXT = "、".join(f"{k} {v}" for k, v in _COMMENT_ACTIONS.items())


class BiliComment(_BiliTool):
    name = "bili_comment"
    description = f"用这个 AI 自己的账号对 B 站视频的评论动手（包括给它点赞）：{_ACTIONS_TEXT}。"
    segment = SEGMENT
    parameters = {
        "action": {
            "type": "string",
            "description": _ACTIONS_TEXT,
            "enum": list(_COMMENT_ACTIONS),
        },
        "bvid": {"type": "string", "description": "BV 号"},
        "message": {"type": "string", "description": "评论正文（post 必填，最多 1000 字）"},
        "rpid": {"type": "integer", "description": "目标评论的 rpid（reply / like / unlike / pin / unpin / delete 要）"},
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "用 AI 自己的账号在视频下发评论、删自己发过的评论。"
    trigger_condition = "用户让 AI 在某条视频下留言"
    # 两条硬约束：提交成功 ≠ 别人看到；发出去 ≠ 落到了该在的位置
    usage_notes = [
        (400, "发完只回「发布中」：公开后会收到一条通知，没收到就是被折叠或隐藏了，别说成已经公开。"),
        (300, "回复落没落到那人下面，你看不到：所以只说「回复已发出」，别说成「已经回在他那条下面了」。"),
    ]

    async def run(self, agent_id: int, arguments: dict):
        action = str(arguments.get("action") or "post").strip().lower()
        bvid = str(arguments.get("bvid") or "").strip()
        if not bvid:
            return {"ok": False, "error": "要给 bvid"}
        cfg, cookie = await _write_ctx(agent_id)
        aid = await _aid_of(bvid, cookie)

        if action in ("like", "unlike", "pin", "unpin", "delete"):
            rpid = arguments.get("rpid")
            if not rpid:
                return {"ok": False, "error": f"{action} 要给 rpid"}
            rpid = int(rpid)
            if action in ("like", "unlike"):
                await _post("/x/v2/reply/action", {
                    "type": 1, "oid": aid, "rpid": rpid,
                    "action": 1 if action == "like" else 0, "csrf": _csrf_of(cfg),
                }, cookie, referer=f"https://www.bilibili.com/video/{bvid}/")
                return {"ok": True, "bvid": bvid, "rpid": rpid, "liked": action == "like"}
            if action in ("pin", "unpin"):
                await _post("/x/v2/reply/top", {
                    "type": 1, "oid": aid, "rpid": rpid,
                    "action": 1 if action == "pin" else 0, "csrf": _csrf_of(cfg),
                }, cookie, referer=f"https://www.bilibili.com/video/{bvid}/")
                return {"ok": True, "bvid": bvid, "rpid": rpid, "pinned": action == "pin"}
            # 先看归属再动手：拿错 rpid 的典型后果是 -403，而那句话对模型毫无指导性
            wrong = await _owner_error(bvid, aid, rpid, cookie, cfg)
            if wrong:
                return {"ok": False, "error": wrong}
            try:
                await _post("/x/v2/reply/del", {
                    "type": 1, "oid": aid, "rpid": rpid, "csrf": _csrf_of(cfg),
                }, cookie, referer=f"https://www.bilibili.com/video/{bvid}/")
            except BiliError as e:
                # 12022 = 已经被删除了：对用户来说"现在没有这条"就是成功
                if e.code != 12022:
                    raise
            # 不再回查列表：评论列表有缓存，刚删完还会查到，回 found=true 反而误导
            await _pending_drop_rpid(_instance_of(agent_id), rpid)
            return {"ok": True, "bvid": bvid, "rpid": rpid, "deleted": True, "账号": cfg.get("nickname") or ""}

        message = str(arguments.get("message") or "").strip()
        if not message:
            return {"ok": False, "error": "评论正文不能为空"}
        if len(message) > 1000:
            return {"ok": False, "error": "评论正文最多 1000 字"}
        if action == "reply" and not arguments.get("rpid"):
            return {"ok": False, "error": "reply 要给 rpid（回哪条评论）"}
        body: dict = {"oid": aid, "type": 1, "message": message, "csrf": _csrf_of(cfg)}
        if action == "reply":
            body["root"], body["parent"] = await _reply_anchor(aid, int(arguments["rpid"]), cookie)
        data = await _post("/x/v2/reply/add", body, cookie,
                           referer=f"https://www.bilibili.com/video/{bvid}/")
        rpid = int((data or {}).get("rpid") or 0)
        if rpid:
            call = _call_ctx.get()
            await _pending_add(
                instance=_instance_of(agent_id), agent_id=agent_id,
                group_id=call.get("group_id"), session_id=call.get("session_id", ""),
                bvid=bvid, aid=aid, rpid=rpid, message=message,
            )
        # 不在这里等它公开：B 站先标隐藏/折叠、几秒后才放出来，等下去只是把 AI 的整轮对话卡住
        return {
            "ok": True,
            "bvid": bvid,
            "rpid": rpid or None,
            "评论": message,
            "账号": cfg.get("nickname") or "",
            "状态": "发布中",
            "note": "B 站不会立刻放出来。公开之后会再收到一条通知；等不到就是被折叠或隐藏了。",
        }


class BiliLive(_BiliTool):
    name = "bili_live"
    description = "查 B 站直播间状态：是否在播、标题、当前人气、主播 uid。参数是房间号。"
    segment = SEGMENT
    parameters = {"room_id": {"type": "integer", "description": "直播间房间号"}}
    required = ["room_id"]
    states = ["active", "dnd"]
    admin_description = "查直播间开播状态与标题。"
    trigger_condition = "用户问某个主播在不在播"
    usage_notes = [(300, "live_status=1 是在播；人气值是平台口径，不是人数。")]

    async def run(self, agent_id: int, arguments: dict):
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


class BiliDynamics(_BiliTool):
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

    async def run(self, agent_id: int, arguments: dict):
        mid = int(arguments["mid"])
        limit = _clamp(arguments.get("limit"), 5, 20)
        cookie = await _agent_cookie(agent_id)
        if not cookie:
            raise BiliError(_NEED_LOGIN)
        data = await _get(
            f"{API}/x/polymer/web-dynamic/v1/feed/space",
            {"host_mid": mid, "features": "itemOpusStyle"},
            cookie,
        )
        items = [_dynamic_one(i) for i in (data.get("items") or [])[:limit]]
        return {"ok": True, "mid": mid, "count": len(items), "items": items}


class BiliSubscribe(_BiliTool):
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

    async def run(self, agent_id: int, arguments: dict):
        action = str(arguments.get("action") or "").lower()
        instance = _instance_of(agent_id)

        if action == "list":
            mine = await _subs_of(instance)
            only = arguments.get("group_id")
            items = [s for s in mine if only is None or s["group_id"] == int(only)]
            return {"ok": True, "count": len(items), "items": items}

        mid, gid = arguments.get("mid"), arguments.get("group_id")
        if mid is None or gid is None:
            return {"ok": False, "error": "add / remove 需要同时给 mid 和 group_id"}
        mid, gid = int(mid), int(gid)

        if action == "add":
            added = await _sub_add(instance, mid, gid, (arguments.get("name") or "").strip())
            return {"ok": True, "subscribed": True, "already": not added, "mid": mid, "group_id": gid}

        if action == "remove":
            removed = await _sub_remove(instance, mid, gid)
            return {"ok": True, "removed": removed, "mid": mid, "group_id": gid}

        return {"ok": False, "error": f"未知 action：{action}（只支持 list / add / remove）"}


# ============================================================
# 私信：看会话、读内容、真发出去
# ============================================================

_DM_ACTIONS = {
    "list": "列会话",
    "read": "读最近几条",
    "send": "发一条",
}
_DM_CHARS = 500   # 单条私信上限：B 站自己的限制在 1000 字上下，留一半给安全余量


class BiliDm(_BiliTool):
    name = "bili_dm"
    description = (
        "用这个 AI 自己的 B 站账号看、回私信：list 列会话（谁、几条未读、最后一句），"
        "read 读某个会话最近几条，send 真的发一条过去。"
    )
    segment = SEGMENT
    parameters = {
        "action": {
            "type": "string",
            "description": "、".join(f"{k} {v}" for k, v in _DM_ACTIONS.items()),
            "enum": list(_DM_ACTIONS),
        },
        "who": {"type": "string", "description": "对方：会话里的昵称或 UID（read / send 要）"},
        "text": {"type": "string", "description": "要发的正文（send 要，最多 500 字）"},
        "limit": {"type": "integer", "description": "list 列几个会话 / read 取几条，默认 5，最多 10"},
    }
    required = ["action"]
    states = ["active", "dnd"]
    admin_description = "用 AI 自己的账号读、发 B 站私信。"
    trigger_condition = "用户让 AI 看或回 B 站私信"
    # 私信是对着真人的：先看清再发，发出去收不回
    usage_notes = [(300, "回之前先 read：对方最后说的是什么别猜。发出去不能撤回。")]

    async def run(self, agent_id: int, arguments: dict):
        action = str(arguments.get("action") or "list").strip().lower()
        if action not in _DM_ACTIONS:
            return {"ok": False, "error": f"未知 action：{action}（只支持 {' / '.join(_DM_ACTIONS)}）"}
        cfg = await _instance_config(_instance_of(agent_id))
        cookie = _cookie_of(cfg)
        if not cookie:
            return {"ok": False, "error": _NEED_LOGIN}
        limit = _clamp(arguments.get("limit"), 5, 10)
        if action == "list":
            return await self._list(cookie, limit, cfg)
        uid = await _dm_uid_of(str(arguments.get("who") or ""), cookie)
        if action == "read":
            return await self._read(uid, cookie, limit, cfg)
        return await self._send(uid, arguments, cfg, cookie)

    async def _list(self, cookie: str, limit: int, cfg: dict) -> dict:
        """最近聊过的会话，从新到旧：AI 要的是"跟谁在说话"，不是 B 站的未读红点。

        最后一条直接用会话里带的 `last_msg`：一个一个去拉消息会把 N 个会话打成 N 次请求。
        系统通知（登录提醒那类，带 `notify_code`）不算会话——那不是人。
        每条都标**这一句是谁说的**：会话的"最后一条"经常是它自己发的，不标方向会被读成对方
        说了这句话（线上就发生过：它把创建者自己的话当成对方来找它交流）。
        """
        mine = str(cfg.get("bili_mid") or cfg.get("dedeuserid") or "").strip()
        sessions = [s for s in await _dm_sessions(cookie) if s.get("talker_id")]
        sessions.sort(key=lambda s: int(s.get("session_ts") or 0), reverse=True)
        picked = []
        for session in sessions:
            if (session.get("last_msg") or {}).get("notify_code"):
                continue
            picked.append(session)
            if len(picked) >= limit:
                break
        names = await _dm_names([int(s["talker_id"]) for s in picked], cookie)
        items = []
        for session in picked:
            uid = int(session["talker_id"])
            last_msg = session.get("last_msg") or {}
            if not _dm_text(last_msg):
                try:
                    msgs = await _dm_msgs(uid, cookie, size=1)
                except BiliError as e:
                    logger.info("B 站私信（%s）取最后一条失败: %s", uid, e)
                    msgs = []
                if msgs:
                    last_msg = msgs[-1]
            items.append({
                "uid": uid,
                "昵称": names.get(uid) or "",
                "未读": int(session.get("unread_count") or 0),
                "最后一条": {
                    "谁": "我" if str(last_msg.get("sender_uid") or "") == mine else "对方",
                    "text": _dm_text(last_msg),
                },
                "时间": _ts_to_str(_to_seconds(session.get("session_ts"))),
            })
        return {"ok": True, "count": len(items), "items": items}

    async def _read(self, uid: int, cookie: str, limit: int, cfg: dict) -> dict:
        mine = str(cfg.get("bili_mid") or cfg.get("dedeuserid") or "").strip()
        msgs = await _dm_msgs(uid, cookie, size=max(1, limit))
        items = []
        for msg in msgs:
            text = _dm_text(msg)
            if not text:      # 图片/卡片/系统提示没有正文，跳过比塞一段空的强
                continue
            items.append({
                "方向": "我" if str(msg.get("sender_uid") or "") == mine else "对方",
                "text": text,
                "时间": _ts_to_str(msg.get("timestamp")),
            })
        names = await _dm_names([uid], cookie)
        return {"ok": True, "uid": uid, "昵称": names.get(uid) or "", "messages": items}

    async def _send(self, uid: int, arguments: dict, cfg: dict, cookie: str) -> dict:
        text = str(arguments.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "send 要给 text"}
        if len(text) > _DM_CHARS:
            return {"ok": False, "error": f"私信正文最多 {_DM_CHARS} 字"}
        _require_interact(cfg)      # 跟点赞、评论同一道闸：创建者点了才让它替自己说话
        sender = str(cfg.get("bili_mid") or cfg.get("dedeuserid") or "").strip()
        if not sender:
            return {"ok": False, "error": "登录态里没有 UID（DedeUserID）：请在通道卡片重新扫码登录。"}
        if str(uid) == sender:
            return {"ok": False, "error": "这是它自己，不能给自己发私信"}
        await _dm_send(uid, text, cookie, csrf=_csrf_of(cfg), sender_uid=sender)
        names = await _dm_names([uid], cookie)
        return {"ok": True, "sent": True, "uid": uid,
                "昵称": names.get(uid) or "", "chars": len(text)}


class BiliInbox(_BiliTool):
    name = "bili_inbox"
    description = (
        "看 B 站消息中心：谁回复了你的评论、@了你、赞了你、私信了你。"
        "不带参数 = 只报没念过的（念到的当场记进你的账本，之后还能翻到）。"
        "要看「过去几天谁找过我、哪个帖子最热闹」就带 days 或 since/until："
        "查的是本地流水，按帖子（或天、人）聚类，不发请求。"
    )
    segment = SEGMENT
    parameters = {
        "days": {"type": "integer", "description": "看最近几天，如 7（不给 since 时用）"},
        "since": {"type": "string", "description": "起点：2026-11-01 或 11-01 或 11-01 12:00"},
        "until": {"type": "string", "description": "终点：写法同 since，只给日期算到当天结束"},
        "by": {"type": "string", "description": "聚类口径：帖子（默认）/ 天 / 人",
               "enum": ["帖子", "天", "人"]},
    }
    states = ["active", "dnd"]
    admin_description = "读这个 AI 的 B 站消息中心（回复 / @ / 赞 / 私信），可按时间区间查流水。"
    trigger_condition = "用户问 AI 有没有新赞、新回复、新私信，或问某段时间谁互动过"
    usage_notes = [
        (300, "不带参数只报没念过的；要翻某段时间（含已念过的）就带 days 或 since/until，"
              "查的是本地流水，不发请求。"),
    ]

    async def run(self, agent_id: int, arguments: dict):
        instance = _instance_of(agent_id)
        cfg = await _instance_config(instance)
        cookie = _cookie_of(cfg)
        if not cookie:
            return {"ok": False, "error": _NEED_LOGIN}
        db = _call_ctx.get().get("db")
        if db is not None:
            # 平台的回合事务：写在这个事务里，别自己再开一个（两个会话互相等锁是老毛病）
            return await self._run(db, agent_id, instance, cookie, arguments)
        from app.database import async_session

        async with async_session() as own:
            out = await self._run(own, agent_id, instance, cookie, arguments)
            await own.commit()      # 自己开的会话自己落盘；平台的会话由平台在下一步提交
            return out

    async def _run(self, db, agent_id: int, instance: str, cookie: str, arguments: dict) -> dict:
        since, until, by = _range_of(arguments)
        if since:
            return await self._history(db, instance, since, until, by)

        from app.services.history.context_sync import context_ref

        call = _call_ctx.get()
        target = (context_ref(session_id=call.get("session_id", "")) if call.get("session_id")
                  else context_ref(group_id=call.get("group_id")))
        counts, lines, cursors, pending, rows = await _inbox_take(db, agent_id, cookie)
        stored = await _inbox_commit(db, agent_id, instance, target,
                                     counts, lines, cursors, rows)
        if not cursors:
            return {"ok": True, "新": "没有新的互动"}
        if not stored:
            return {"ok": False, "error": "账本没写进去，这次先没念（下次会再念一遍）"}
        tail = (f"另有 {pending} 条这次没念到，下次叫我会接着念" if pending
                else "已记进账本，之后还能翻")
        return {"ok": True, "新": f"{_counts_text(counts) or '只有图片'}（{tail}）", "条目": lines}

    async def _history(self, db, instance: str, since: int, until: int, by: str) -> dict:
        """按时间区间翻本地流水：零网络，O(区间里的条数)，按口径聚类。"""
        rows = await _feed_rows(db, instance, since, until)
        span = f"{_ts_to_str(since)} ～ {_ts_to_str(until - 1)}"
        if not rows:
            return {
                "ok": True, "区间": span,
                "新": "这段时间本地没有记录（先用不带参数的方式扫一次，"
                      "它会把 B 站现在能看到的都存下来）",
            }
        return {
            "ok": True, "区间": span,
            "新": f"{_counts_text(_tally(rows))}（按{by}聚类）",
            "聚类": _feed_report(rows, by),
        }


# ============================================================
# 服务：这个实例的登录态展示 + 订阅轮询推送
# ============================================================

_PUSH_TEMPLATE = "【B 站更新】{name} 发了新动态：\n{text}\n{link}"

# 插件替 AI 预建的「刷 B 站」状态帧（类型在 plugin.json 的 frames 段声明）
_BROWSE_FRAME = "bilibili_browse"


async def _ensure_browse_frame(instance: str, cfg: dict | None = None) -> str:
    """创建者勾了「创建 B 站状态帧」就预建一条沉睡帧。

    沉睡帧只进存储、不改变 AI 当前在哪（push_state 才切），进不进去由它自己决定。
    失败只记日志：通道的登录与推送不能被这件事拖垮。
    """
    try:
        cfg = cfg if cfg is not None else await _instance_config(instance)
        if not _flag(cfg.get("frame_enabled"), True):
            return ""
        from app.database import async_session
        from app.models.agent import Agent
        from app.services.plugin import api
        from app.services.plugin.channel import agent_id_of

        agent_id = agent_id_of(instance)
        if not agent_id:
            return ""
        async with async_session() as db:
            agent = await db.get(Agent, int(agent_id))
            if agent is None:
                return ""
            # 帧的额度策略由插件声明、平台记账：把设置推给通道额度那一套（卡片上的「对话额度」读它）
            from app.services.channel import token_quota
            from app.services.channel.quota_gate import policy_from_config

            await token_quota.set_policy(
                db, int(agent_id), "bilibili", **policy_from_config(cfg))
            # 每次启动都重新声明这一帧（已有就复活/刷新）：白名单跟着 manifest 走，
            # 说明文字也跟着这一版的 tool 列表走，省得一份旧文案冻在库里
            frame = await api.enter_frame(
                db, agent, context_ref=f"bilibili:{agent_id}", type=_BROWSE_FRAME,
                label="刷 B 站", switch=False,
                why=("想看视频的时候进来：先 list_states 找到这帧，再 enter_state 进来——"
                     "帧上已经配好 B 站那套工具和单独的额度，不用 push_state 另建。"
                     "出来用 pop_state。"),
            )
            await db.commit()
            return str(frame.get("id") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("B 站状态帧预建失败（不影响通道）: %s", e)
        return ""


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
        "frame_enabled": {
            "type": "boolean",
            "title": "创建 B 站状态帧",
            "title_en": "Create a Bilibili state frame",
            "title_ja": "Bilibili 状態フレームを作る",
            "default": True,
            "description": (
                "给这个 AI 一个独立的「刷 B 站」状态：进入后它的工具换成 B 站那一套"
                "（查视频、看弹幕、点赞、评论、收发私信）；"
                "这个状态的调用额度和轮次单独算，不占它平时聊天的额度。"
            ),
            "description_en": (
                "Give this AI its own \"browsing Bilibili\" state: inside it the tools are the Bilibili "
                "set (videos, danmaku, likes, comments, DMs); its budget and round limits are counted "
                "separately from normal chatting."
            ),
            "description_ja": (
                "この AI に「Bilibili を見る」専用の状態を用意します。中ではツールが Bilibili 用"
                "（動画・弾幕・いいね・コメント・DM）に切り替わります。"
                "呼び出しの上限とラウンドは通常の会話とは別に数えます。"
            ),
        },
        "frame_round_limit": {
            "type": "integer",
            "title": "状态帧：一轮最多调用几次",
            "title_en": "State frame: calls per turn",
            "title_ja": "状態フレーム：1 ターンあたりの回数",
            "default": 50,
            "description": (
                "「一次」= 一次模型调用。普通对话用 AI 设置里的工具轮次上限（默认 6）；"
                "刷 B 站这一帧主打持续，单独放宽，默认 50。"
            ),
            "description_en": (
                "One call = one model request. Normal chats use the agent's tool-round limit (default 6); "
                "the Bilibili frame is meant to keep going, so it has its own wider limit (default 50)."
            ),
            "description_ja": (
                "1 回＝モデル呼び出し 1 回。通常の会話は AI 設定のツール呼び出し上限（既定 6）を使いますが、"
                "Bilibili フレームは継続前提なので別枠（既定 50）です。"
            ),
        },
        "frame_session_minutes": {
            "type": "integer",
            "title": "状态帧：建议单次刷多久",
            "title_en": "State frame: suggested session length",
            "title_ja": "状態フレーム：1 回あたりの推奨時間",
            "default": 20,
            "description": (
                "只是建议：写在它每轮都能看到的那行预算里，平台不按这个掐断，"
                "想多刷一会儿由它自己判断。0 = 不提建议。"
            ),
            "description_en": (
                "Advisory only: it goes into the budget line the AI sees every turn; the platform does "
                "not cut it off. 0 = no suggestion."
            ),
            "description_ja": (
                "あくまで目安です。AI が毎ターン見る予算行に出るだけで、プラットフォームは打ち切りません。"
                "0＝提案しない。"
            ),
        },
        "frame_hour_limit": {
            "type": "integer",
            "title": "状态帧：每小时最多调用几次",
            "title_en": "State frame: calls per hour",
            "title_ja": "状態フレーム：1 時間あたりの回数",
            "default": 0,
            "description": "0 = 不限。用到 80% 会在它自己的账本里提醒还剩多少，用满就不再开始新一轮。",
            "description_en": "0 = unlimited. At 80% the AI is told how much is left in its own ledger; at the cap no new turn starts.",
            "description_ja": "0＝無制限。80% で残量を AI 自身の台帳に通知し、上限に達すると新しいターンを始めません。",
        },
        "frame_day_limit": {
            "type": "integer",
            "title": "状态帧：每天最多调用几次",
            "title_en": "State frame: calls per day",
            "title_ja": "状態フレーム：1 日あたりの回数",
            "default": 0,
            "description": "0 = 不限。按平台展示时区换天。",
            "description_en": "0 = unlimited. The day rolls over in the platform display timezone.",
            "description_ja": "0＝無制限。日付はプラットフォームの表示タイムゾーンで切り替わります。",
        },
        "quota_meter_mode": {
            "type": "string",
            "title": "状态帧额度按什么算",
            "title_en": "How the frame budget is metered",
            "title_ja": "状態フレームの上限をどう数えるか",
            "default": "billable",
            "options": [
                {"value": "billable", "label": "交涉 token（未命中输入 + 输出，默认）",
                 "label_en": "Billable (uncached input + output)",
                 "label_ja": "課金対象（未キャッシュ入力＋出力）"},
                {"value": "total", "label": "全部 token（含缓存命中）",
                 "label_en": "All tokens (including cache hits)",
                 "label_ja": "全トークン（キャッシュ命中を含む）"},
            ],
            "description": "这个状态帧单独记账，卡片上的「对话额度」能看到用量并单独重置。",
            "description_en": "The frame is metered on its own; the budget card on this page shows usage and can reset it.",
            "description_ja": "この状態フレームは別枠で計上されます。カードの「会話の上限」で使用量の確認とリセットができます。",
        },
        "quota_limit_tokens": {
            "type": "string",
            "title": "状态帧默认额度（token）",
            "title_en": "Default frame budget (tokens)",
            "title_ja": "状態フレームの既定上限（トークン）",
            "default": "1000000",
            "description": "留空 = 1000000（100 万）。用完之后这个状态帧不再开始新一轮。",
            "description_en": "Empty = 1,000,000. Once used up, the frame stops starting new turns.",
            "description_ja": "空欄＝1,000,000。使い切るとこの状態フレームは新しいターンを始めません。",
        },
        "quota_reset_enabled": {
            "type": "boolean",
            "title": "状态帧额度自动重置",
            "title_en": "Reset the frame budget automatically",
            "title_ja": "状態フレームの上限を自動リセット",
            "default": False,
            "description": "开启后按周期换窗口：新窗口从 0 开始算，旧窗口的数留在账里。",
            "description_en": "When on, the window rolls over per the period below; the new window starts from zero.",
            "description_ja": "オンにすると周期で窓が切り替わり、新しい窓は 0 から数えます。",
        },
        "quota_reset_period": {
            "type": "string",
            "title": "状态帧额度多久重置一次",
            "title_en": "How often the frame budget resets",
            "title_ja": "状態フレーム上限のリセット間隔",
            "default": "day",
            "show_if": {"field": "quota_reset_enabled", "equals": "true"},
            "options": [
                {"value": "hour", "label": "每小时", "label_en": "Every hour", "label_ja": "1 時間ごと"},
                {"value": "day", "label": "每天", "label_en": "Every day", "label_ja": "毎日"},
                {"value": "2days", "label": "每 2 天", "label_en": "Every 2 days", "label_ja": "2 日ごと"},
                {"value": "week", "label": "每周（周一）", "label_en": "Every week (Monday)", "label_ja": "毎週（月曜）"},
            ],
        },
        "quota_reset_anchor": {
            "type": "string",
            "title": "状态帧额度几点重置（HH:MM）",
            "title_en": "Frame budget resets at (HH:MM)",
            "title_ja": "状態フレーム上限のリセット時刻（HH:MM）",
            "default": "00:00",
            "show_if": [
                {"field": "quota_reset_enabled", "equals": "true"},
                {"field": "quota_reset_period", "in": ["day", "2days", "week"]},
            ],
            "description": "只在「每天 / 每 2 天 / 每周」时用得上（按小时是整点换窗口）。",
            "description_en": "Only used for daily / every 2 days / weekly (hourly rolls over on the hour).",
            "description_ja": "「毎日 / 2 日ごと / 毎週」のときだけ使います（毎時は正時に切り替わります）。",
        },
        "allow_interact": {
            "type": "boolean",
            "title": "允许这个 AI 互动（点赞 / 评论 / 私信）",
            "title_en": "Let this AI interact (like / comment / DM)",
            "title_ja": "この AI のいいね・コメント・DM を許可",
            "default": False,
            "description": "打开后，它可以用上面登录的 B 站账号给视频点赞、发评论（评论是公开的），也能看和回私信；关闭时只能查。",
            "description_en": "When on, the AI may like videos, post public comments, and read/reply to DMs with the logged-in account. Off: read-only.",
            "description_ja": "オンにすると、ログイン中のアカウントでいいね・公開コメント・DM の閲覧と返信ができます。オフなら閲覧のみ。",
        },

    },
)
class BilibiliChannel(ServicePlugin):
    """一个实例 = 一个 AI 的 B 站账号（登录态 + 订阅都按实例隔离）。"""

    def __init__(self) -> None:
        super().__init__()
        self._task: asyncio.Task | None = None
        self._watch: asyncio.Task | None = None
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
        await _ensure_browse_frame(instance, cfg)   # 配置里刚勾上也能补建
        cookie = _cookie_of(cfg)

        subs = await _subs_of(instance)
        detail: dict = {
            "running": self._task is not None and not self._task.done(),
            "state": "未登录",
            "login": None,
            "account": None,
            "subscriptions": len(subs),
            "last_run": self._last_run,
            "last_error": self._last_error,
            "pushed": self._pushed,
            # 这个 AI 拿账号做过什么（工具调用 / 推送 / 登录），数据在插件自己的表里
            "activity": await _trail_recent(instance),
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
                    **_qr_live_fields(session),
                }
            except BiliError as e:
                detail["login"] = {"state": "error", "text": str(e)}
            return detail

        info = await _nav_cached(cookie, instance)
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
        activity = await _trail_recent(instance)
        # hosted_status 只为"还没保存配置"的卡片服务：轨迹放在 login 之外，
        # 前端拿到 hosted_endpoint 就顺带拿到 activity（同一个 detail 里）
        cfg = await _instance_config(instance)
        cookie = _cookie_of(cfg)
        if cookie:
            info = await _nav_cached(cookie, instance)
            if info.get("isLogin"):
                return {
                    "held_by_platform": True,
                    "logged_in": True,
                    "bot_name": info.get("uname") or cfg.get("nickname") or "",
                    "activity": activity,
                }
        try:
            session = _qr_sessions.get(instance) or await _qr_start(instance)
            if session.get("state") in ("pending", "scanned") or time.time() - session.get("created", 0) > QR_TTL_SECONDS:
                session = await _qr_poll(instance) if session.get("state") in ("pending", "scanned") else await _qr_start(instance)
            qr_url = str(session.get("url") or "")
            if session.get("state") == "done":
                # 刚扫完码：Cookie 与昵称已经写回实例配置（_qr_poll → _save_login），
                # 这一轮就把昵称一起报出去。否则卡片会先收到「已登录 + 空昵称」，
                # 看起来像没登录上，还得多等一次轮询。
                cfg = await _instance_config(instance)
                return {
                    "held_by_platform": True,
                    "logged_in": True,
                    "bot_name": str(cfg.get("nickname") or ""),
                    "activity": activity,
                }
            return {
                "held_by_platform": True,
                "logged_in": False,
                "bot_name": "",
                "qr_png": _qr_png_b64(qr_url),
                "qr_url": qr_url,
                **_qr_live_fields(session),
                "activity": activity,
            }
        except BiliError as e:
            return {"held_by_platform": True, "logged_in": False, "bot_name": "", "error": str(e)[:120]}

    @classmethod
    async def refresh_login_qr(cls, *, agent_id: int | None = None) -> dict:
        """卡片上的「获取登录二维码」：从用户点下去的这一刻重新要一张

        与 hosted_status 的区别是"要不要重开"：那个是顺手把手上这张报上来（轮询用），
        这个是用户主动点了——倒计时必须从这一刻算，拿旧码只能猜它还剩几秒。
        """
        instance = _instance_of(int(agent_id)) if agent_id is not None else ""
        session = await _qr_start(instance)
        qr_url = str(session.get("url") or "")
        return {"qr_png": _qr_png_b64(qr_url), "qr_url": qr_url, **_qr_live_fields(session)}

    # ── 帧钩子：他进「刷 B 站」这一帧时，把 B 站那边的新消息念一遍 ──

    async def on_frame_enter(self, db, agent, frame: dict) -> None:
        """注入，不是唤醒：平台不因这些消息开新轮，回不回由他自己看上下文决定。

        收件箱的游标存在插件表里（`plugin_bilibili__inbox`）——B 站的未读标记一个字都不动，
        那是用户手机上的红点，替他清掉等于把人的消息也"读"了。
        """
        if str((frame or {}).get("type") or "") != _BROWSE_FRAME:
            return
        cfg = await _instance_config(self.instance)
        cookie = _cookie_of(cfg)
        if not cookie:
            return
        counts, lines, cursors, _pending, rows = await _inbox_take(db, int(agent.id), cookie)
        await _inbox_commit(db, int(agent.id), str(self.instance),
                            str(frame.get("context_ref") or ""), counts, lines, cursors, rows)

    # ── 生命周期 ──

    async def start(self) -> bool:
        # 没登录也启动：轮询任务自己会跳过，卡片仍要能显示二维码
        await _ensure_browse_frame(self.instance)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name=f"bilibili-{self.instance}")
        if self._watch is None or self._watch.done():
            self._watch = asyncio.create_task(self._watch_loop(), name=f"bilibili-pending-{self.instance}")
        return True

    async def stop(self) -> bool:
        for attr in ("_task", "_watch"):
            task = getattr(self, attr)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            setattr(self, attr, None)
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

    async def _watch_loop(self) -> None:
        """追认刚发的评论是否公开：与订阅轮询分开跑，节奏固定 30 秒，不受 poll_minutes 影响。"""
        await asyncio.sleep(15)     # 让启动时的迁移与登录态先就位
        while True:
            try:
                await self._check_pending()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 —— 追认失败不能把任务打死
                logger.warning("B 站评论追认失败（%s）: %s", self.instance, e)
            await asyncio.sleep(PENDING_TICK_SECONDS)

    async def _check_pending(self) -> None:
        """一条一条落定，然后按结果分开发——成功合并、失败逐条。

        为什么要合并成功：一次回十条评论是常态，"十条都公开了"对 AI 没有任何增量信息，
        也没有任何可做的动作，只会在它的账本里糊住别的通知。而失败每条都不一样（被审核卡住、
        被折叠、被删），细节才是信息，所以失败保持一条一条、带正文与链接。

        刚发出的一段时间内 B 站本来就标成隐藏（PENDING_SETTLE_SECONDS），这之前不下结论，
        否则会把"还没放出来"误判成"永远不会公开"。
        """
        rows = await _pending_open(self.instance)
        if not rows:
            return
        cookie = _cookie_of(await _instance_config(self.instance))
        if not cookie:
            return      # 没登录就先不动：这些行还在，登录后接着追
        public: list[dict] = []
        failed: list[tuple[dict, str, str]] = []      # (行, verdict, 文案)
        for row in rows:
            age = time.time() - int(row["created_at"] or 0)
            rpid, aid = int(row["rpid"]), int(row["aid"])
            status = await _comment_status(aid, rpid, cookie)
            if status.get("visible"):
                public.append(row)
                continue
            link = f"https://www.bilibili.com/video/{row['bvid']}/#reply{rpid}"
            quote = _quote_brief(row["message"])
            if status.get("hidden") and age >= PENDING_SETTLE_SECONDS:
                verdict = "隐藏"
                text = f"【B 站】你刚发的评论没公开，被隐藏了（只有这个账号自己能看到）：{quote}\n{link}"
            elif age >= PENDING_TTL_SECONDS:
                verdict = "未确认"
                text = f"【B 站】你刚发的评论没能确认公开（可能被折叠或隐藏了）：{quote}\n{link}"
            else:
                await _pending_touch(int(row["id"]), int(row["tries"] or 0) + 1)
                continue
            failed.append((row, verdict, text))

        # 成功的按"说到哪个对话里"分组：同一次回话批次的评论合成一条
        buckets: dict[tuple, list[dict]] = {}
        for row in public:
            key = (int(row["agent_id"]), row["group_id"], row["session_id"] or "")
            buckets.setdefault(key, []).append(row)
        for (agent_id, group_id, session_id), batch in buckets.items():
            if len(batch) == 1:
                text = f"【B 站】你刚发的评论已经公开：{_quote_brief(batch[0]['message'])}\n{_link_of(batch[0])}"
            else:
                per_bvid: dict[str, int] = {}
                for row in batch:
                    per_bvid[row["bvid"]] = per_bvid.get(row["bvid"], 0) + 1
                where = "、".join(f"{bvid} 下 {n} 条" for bvid, n in per_bvid.items())
                text = f"【B 站】刚发的 {len(batch)} 条评论都已公开，不用再管了（{where}）。"
            ids = [int(row["id"]) for row in batch]
            await _notify(
                agent_id, group_id, session_id, text,
                ref=f"bili_comment:public:{min(ids)}-{max(ids)}",
            )
            for row in batch:
                await _pending_drop(int(row["id"]))
                await _trail_record(self.instance, "comment_public", json.dumps(
                    {"bvid": row["bvid"], "rpid": int(row["rpid"])}, ensure_ascii=False))

        for row, verdict, text in failed:
            rpid = int(row["rpid"])
            await _notify(
                int(row["agent_id"]), row["group_id"], row["session_id"], text,
                ref=f"bili_comment:{rpid}:{verdict}",
            )
            await _pending_drop(int(row["id"]))
            await _trail_record(self.instance, "comment_hidden", json.dumps(
                {"bvid": row["bvid"], "rpid": rpid}, ensure_ascii=False))

    async def _run_once(self) -> None:
        cfg = await _instance_config(self.instance)
        cookie = _cookie_of(cfg)
        mine = await _subs_of(self.instance)
        if not mine:
            self._last_run = _now()
            return
        if not cookie:
            self._last_run = _now()
            raise BiliError("这个 AI 还没登录 B 站，订阅无法检查（查询类工具不受影响）")

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
            await _sub_touch(sub["instance"], int(sub["mid"]), int(sub["group_id"]), latest)
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
                await _trail_record(
                    self.instance, "push",
                    json.dumps({"group_id": group_id, "skipped": "群里没有 AI"}, ensure_ascii=False),
                    ok=False,
                )
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
            up_name = sub.get("name") or f"UP {sub['mid']}"
            await _trail_record(
                self.instance, "push",
                json.dumps({"group_id": group_id, "up": up_name}, ensure_ascii=False),
            )
            await db.commit()
        return True


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _register_segment() -> None:
    ToolRegistry.register_segment(SEGMENT, {
        "name": "B 站",
        "description": "查 B 站视频、热门、UP 主、直播间，按昵称搜人、按关键词搜视频，并订阅 UP 主动态",
        "admin_description": "让 AI 直接回答 B 站相关问题：视频数据、热门榜、UP 主粉丝、直播状态、最新动态；订阅后新动态自动推到群里。登录态是「这个 AI 自己的 B 站账号」，由创建者扫码。",
        "trigger_conditions": ["用户发 B 站链接", "用户问 B 站热门或某个视频", "订阅的 UP 主发新动态"],
        "icon": "play",
        "usage_notes": [
            (300, "查询类工具（视频/热门/粉丝/直播/搜索）不需要登录；动态与订阅推送需要这个 AI 已扫码登录 B 站。"),
        ],
    }, owner=PLUGIN_ID)


_register_segment()
