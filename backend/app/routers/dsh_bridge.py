"""
DSH 桥接 API —— Copree 管理端与 DSH 本体会话之间的唯一入口。

- POST /dsh-bridge/register   DSH 侧 dsh-copree 插件的心跳（密钥认证，非管理员）
- POST /dsh-bridge/forget     管理员断开（清掉注册表，等下一次心跳）
- GET  /admin/dsh/status      桥接状态：是否检测到、是否可达、插件版本
- GET  /admin/dsh/sessions    DSH 会话列表（跨工作区）
- POST /admin/dsh/prompt      往 DSH 会话投一条消息（无 session_id 则新建会话）
- GET  /admin/dsh/stream      SSE 中继 DSH 会话事件（前端 fetch 流式读）
- POST /admin/dsh/cancel      取消 DSH 会话当前轮次
- POST /admin/dsh/answer      回答 DSH 会话里的审批 / 提问（人在 Copree 页面点的那一下）
- GET  /dsh-bridge/attachment/{file_id}  DSH 插件按 id 取附件字节（密钥认证）
"""
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.content.file_service import _get_physical_path, get_file
from app.services.dsh import bridge
from app.utils.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["DSH 桥接"])


class RegisterRequest(BaseModel):
    """DSH 插件心跳体（字段名与插件一致，避免两端各起一套命名）。"""

    advertise_url: str = Field(default="", alias="advertiseUrl")
    version: str = ""
    plugin: str = bridge.PLUGIN_ID

    model_config = {"populate_by_name": True}


class PromptRequest(BaseModel):
    text: str
    session_id: str | None = Field(default=None, alias="sessionId")
    cwd: str | None = None
    """附件引用 [{fileId, name, mime}]：字节不塞进正文，由 DSH 插件按 id 回取。"""
    attachments: list[dict] | None = None
    """'steer' = 插到 DSH 最近一个步骤边界（正在跑工具时插队，空闲时开新一轮）。"""
    mode: str | None = None

    model_config = {"populate_by_name": True}


class SessionRequest(BaseModel):
    session_id: str = Field(alias="sessionId")

    model_config = {"populate_by_name": True}


class AnswerRequest(BaseModel):
    """一次回答：审批给 approve，提问给 answers（结构与 DSH 的答案一致，不做二次翻译）。"""

    id: str
    approve: bool | None = None
    answers: list[dict] | None = None


def _bridge_error(exc: bridge.DshBridgeError) -> HTTPException:
    """桥接不可用统一 503：前端据 detail 显示可读原因。"""
    return HTTPException(status_code=503, detail=str(exc))


@router.post("/dsh-bridge/register")
async def register(
    payload: RegisterRequest,
    x_copree_bridge_token: str | None = Header(default=None),
):
    """DSH 插件心跳（密钥认证）。

    心跳本身已经是「DSH 同意」的证明：没在 DSH 设置里点同意接入，插件不会发这个请求；
    再来时密钥不对一律 401。所以这里不需要第二道同意。
    """
    if not bridge.verify_secret(x_copree_bridge_token):
        raise HTTPException(status_code=401, detail="桥接密钥不匹配")
    try:
        registration = bridge.record_registration(payload.advertise_url, payload.version)
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc
    return {"ok": True, "seen_at": registration.seen_at}


@router.get("/dsh-bridge/attachment/{file_id}")
async def bridge_attachment(
    file_id: int,
    x_copree_bridge_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """把附件字节直接交给 DSH 插件（插件落盘到会话工作目录，会话里的 AI 读文件）。

    为什么不走「前端把 base64 放进正文」：一张截图就能把请求撑到几 MB，链路上任何一跳
    （反向代理默认 1MB 等）都会先把它掐掉 —— 实测丢图就是这么丢的。改成按 id 回取后，
    正文永远只有一行标记，再没有体积上限，也不用在前端做画布重编码。
    """
    if not bridge.verify_secret(x_copree_bridge_token):
        raise HTTPException(status_code=401, detail="桥接密钥不匹配")
    metadata = await get_file(db, file_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    physical_path = _get_physical_path(metadata.path)
    if not os.path.exists(physical_path):
        raise HTTPException(status_code=404, detail="物理文件不存在")
    return FileResponse(
        physical_path,
        media_type=metadata.mime_type or "application/octet-stream",
        filename=os.path.basename(metadata.path),
    )


@router.post("/dsh-bridge/forget")
async def forget(_: dict = Depends(require_admin)):
    """管理员断开桥接：清掉注册表（DSH 侧仍会在下一个心跳重新登记）。"""
    bridge.forget_registration()
    return {"ok": True}


@router.get("/admin/dsh/status")
async def bridge_status(_: dict = Depends(require_admin)):
    """状态卡数据：本地注册状态 + 一次真实探活（DSH 不可达时报 unreachable）。"""
    registration = bridge.current_registration()
    if registration is None:
        return {
            "detected": False,
            "state": "offline",
            "secret_configured": bool(bridge.settings.dsh_bridge_secret),
            "last_seen_seconds": bridge.last_seen_seconds(),
        }
    try:
        probe = await bridge.status()
    except bridge.DshBridgeError as exc:
        return {
            "detected": True,
            "state": "unreachable",
            "version": registration.version,
            "last_seen_seconds": bridge.last_seen_seconds(),
            "detail": str(exc),
        }
    return {
        "detected": True,
        "state": "online",
        "version": probe.get("version") or registration.version,
        "last_seen_seconds": bridge.last_seen_seconds(),
    }


@router.get("/admin/dsh/sessions")
async def bridge_sessions(_: dict = Depends(require_admin)):
    """DSH 会话列表（所有工作区）。"""
    try:
        return {"items": await bridge.list_sessions()}
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc


@router.post("/admin/dsh/prompt")
async def bridge_prompt(payload: PromptRequest, _: dict = Depends(require_admin)):
    """投一条消息给 DSH 会话；返回真正落点（可能是 DSH 新建的会话）。"""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    try:
        return await bridge.prompt(payload.text, payload.session_id, payload.cwd, payload.attachments, payload.mode)
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc


@router.post("/admin/dsh/cancel")
async def bridge_cancel(payload: SessionRequest, _: dict = Depends(require_admin)):
    """取消当前轮次（会话与历史保留）。"""
    try:
        await bridge.cancel(payload.session_id)
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc
    return {"ok": True}


@router.post("/admin/dsh/answer")
async def bridge_answer(payload: AnswerRequest, _: dict = Depends(require_admin)):
    """把人在页面上的回答交回 DSH（审批/提问共用一条，DSH 侧按内容分辨）。"""
    body: dict = {"id": payload.id}
    if payload.approve is not None:
        body["approve"] = payload.approve
    if payload.answers is not None:
        body["answers"] = payload.answers
    try:
        return await bridge.answer(body)
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc


@router.get("/admin/dsh/stream")
async def bridge_stream(
    session_id: str = Query(..., alias="sessionId"),
    after_seq: int = Query(-1, alias="afterSeq"),
    _: dict = Depends(require_admin),
):
    """SSE 中继：DSH 的帧原样过桥，Copree 不解释内容。

    桥接层出错时补一帧 DSH 词表里的 error（此时响应头已发出，只能用流内错误表达）。
    """
    try:
        bridge._target("/stream")  # 未注册/未同意 → 提前 503，而不是开了流再报错
    except bridge.DshBridgeError as exc:
        raise _bridge_error(exc) from exc

    async def relay():
        try:
            async for chunk in bridge.stream(session_id, after_seq):
                yield chunk
        except bridge.DshBridgeError as exc:
            yield f'data: {{"k":"error","message":{str(exc)!r}}}\n\n'.encode()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
    )
