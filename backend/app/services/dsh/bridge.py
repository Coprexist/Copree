"""
DSH 桥接服务 —— Copree 管理端与 DSH 本体会话之间的**唯一通道**。

数据流（三段，各一段一个入口）：

    Copree 前端 ──► 本模块（/admin/dsh/*） ──► DSH web 的 /copree-bridge/*
                                              └─ dsh-copree 插件 ──► sessionController（DSH 本体）

**闸门只有一个，且在 DSH 侧**：人要在 DSH 的「设置 → Copree」里点同意接入，插件才开始
每 20 秒向这里注册心跳。没同意之前，插件连心跳都不发——这里什么都收不到，管理端只会显示
「未检测到」，更不存在「绕过 Copree 的同意去连 DSH」这回事。

为什么不在 Copree 再加一道同意：那道闸门保护的是 Copree，而 Copree 这侧本来就只有管理员能发指令
（除非管理员自己愿意，否则没有任何指令会出去）；DSH 侧那道保护的才是真正值钱的东西——
能在 DSH 里跑代码、改文件的会话。同一个管理员点两次不等于两道锁，只会变成假安全感。
Coproee 能做的只有「更窄」：`forget` 断开、不再转发，方向单调、不会放宽 DSH 的决定。

本模块不落地任何会话数据：会话、消息、工具调用都在 DSH 本体里，这里只是搬运字节。
"""
from __future__ import annotations

import logging
import secrets as py_secrets
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

#: DSH 侧插件标识（注册体里带，用于未来多插件区分）。
PLUGIN_ID = "dsh-copree"

#: 桥接路由前缀（与 dsh-copree 的 BRIDGE_PREFIX 一致）。
BRIDGE_PATH = "/copree-bridge"


class DshBridgeError(RuntimeError):
    """桥接不可用（未注册 / 不可达 / DSH 侧报错）——由路由层翻成 503。"""


@dataclass(frozen=True)
class Registration:
    """最近一次心跳记录（进程内存，Copree 重启后由下一次心跳自动补齐）。"""

    advertise_url: str
    version: str
    seen_at: float


_registration: Optional[Registration] = None


def verify_secret(token: Optional[str]) -> bool:
    """恒定时间比较共享密钥。未配置密钥一律拒绝：DSH 没同意，桥接就不存在。"""
    expected = settings.dsh_bridge_secret or ""
    if not expected or not token:
        return False
    return py_secrets.compare_digest(token, expected)


def _normalized_url(advertise_url: str) -> str:
    """地址只接受 http(s)，避免把请求发到 file:// 之类的载体上。"""
    url = (advertise_url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise DshBridgeError("advertiseUrl 必须是 http(s) 地址")
    return url


def record_registration(advertise_url: str, version: str) -> Registration:
    """记录一次心跳；地址只接受 http(s)，避免把请求发到 file:// 之类的载体上。

    唯一闸门在 DSH 侧：DSH 没在「设置 → Copree」里同意接入，插件就不会发心跳，
    这里也就什么都收不到——所以本模块不需要再来一道同意。
    """
    url = _normalized_url(advertise_url)
    global _registration
    _registration = Registration(advertise_url=url, version=version or "", seen_at=time.time())
    logger.info(f"DSH 桥接已注册（插件 v{version or '未知'}）")
    return _registration


def current_registration(now: Optional[float] = None) -> Optional[Registration]:
    """当前有效注册（超过 TTL 视为掉线，返回 None 而不清理，便于诊断最后心跳）。"""
    registration = _registration
    if registration is None:
        return None
    if (now if now is not None else time.time()) - registration.seen_at > settings.dsh_bridge_ttl_seconds:
        return None
    return registration


def last_seen_seconds() -> Optional[float]:
    """距上次心跳的秒数（无注册返回 None）。"""
    return None if _registration is None else round(time.time() - _registration.seen_at, 1)


def forget_registration() -> None:
    """管理员手动断开：清掉注册，下次心跳（≤20 秒）会重新登记。"""
    global _registration
    _registration = None


def _target(path: str) -> tuple[str, dict]:
    """把桥接路径翻成 DSH 地址 + 认证头；未注册直接拒绝（不发任何请求）。"""
    registration = current_registration()
    if registration is None:
        raise DshBridgeError("未检测到 DSH 桥接：DSH 尚未在「设置 → Copree」同意接入，或插件已离线")
    headers = {"x-copree-bridge-token": settings.dsh_bridge_secret}
    return f"{registration.advertise_url}{BRIDGE_PATH}{path}", headers


def _detail(response: httpx.Response) -> str:
    """桥接是密钥认证的内部通道，DSH 的报错原文对排查最有价值，原样带上。"""
    try:
        payload = response.json()
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("detail") or "")
    return ""


def _raise_for(response: httpx.Response) -> None:
    """把 DSH 的状态码翻成中文可诊断错误（401 专门点名密钥不匹配）。"""
    if response.status_code == 401:
        raise DshBridgeError("DSH 侧拒绝：两端的共享密钥不一致")
    if response.status_code >= 400:
        detail = _detail(response)
        raise DshBridgeError(f"DSH 返回 {response.status_code}" + (f"：{detail}" if detail else ""))


async def status() -> dict:
    """探测 DSH 插件是否在线（管理端状态卡的唯一来源）。"""
    url, headers = _target("/status")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
    _raise_for(response)
    return response.json()


async def list_sessions() -> list[dict]:
    """列 DSH 全部会话（跨工作区；DSH 侧只透出展示字段）。"""
    url, headers = _target("/sessions")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
    _raise_for(response)
    return list(response.json().get("items") or [])


async def prompt(
    text: str,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    attachments: Optional[list[dict]] = None,
    mode: Optional[str] = None,
) -> dict:
    """把一条消息投给 DSH 会话；没有 session_id 时由 DSH 侧现建一个会话。

    attachments 只传引用（fileId/name/mime）：字节由 DSH 插件回调
    /dsh-bridge/attachment/{file_id} 自取，图片因此撑不爆请求体。
    """
    url, headers = _target("/prompt")
    payload: dict = {"text": text}
    if session_id:
        payload["sessionId"] = session_id
    if cwd:
        payload["cwd"] = cwd
    if attachments:
        payload["attachments"] = attachments
    if mode:
        payload["mode"] = mode
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
    _raise_for(response)
    return response.json()


async def cancel(session_id: str) -> None:
    """取消 DSH 会话的当前轮次（不动会话本身）。"""
    url, headers = _target("/cancel")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json={"sessionId": session_id})
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
    _raise_for(response)


async def answer(payload: dict) -> dict:
    """把人的回答交回 DSH：审批的同意/不同意、提问所选的选项，都走这一条。

    为什么与 prompt 分开：prompt 是"我说什么"，answer 是"我答它问的"——
    两件事在 DSH 侧也是两条链（turn 投递 vs 回答者瀑布），合并会各自都表达不清。
    """
    url, headers = _target("/answer")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
    _raise_for(response)
    return response.json()


async def stream(session_id: str, after_seq: int = -1) -> AsyncIterator[bytes]:
    """原样中继 DSH 的 SSE 事件流（前端只认 DSH 的帧，Copree 不解释内容）。

    read 超时置空：一轮对话里模型思考可能长时间无输出，靠 DSH 侧的心跳注释保活。
    """
    url, headers = _target("/stream")
    timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
    params = {"sessionId": session_id, "afterSeq": str(after_seq)}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers, params=params) as response:
                if response.status_code >= 400:
                    raw = (await response.aread())[:200].decode("utf-8", "ignore")
                    raise DshBridgeError(
                        f"DSH 返回 {response.status_code}" + (f"：{raw}" if raw else "")
                    )
                async for chunk in response.aiter_bytes():
                    yield chunk
    except httpx.HTTPError as exc:
        raise DshBridgeError(f"DSH 不可达：{exc}") from exc
