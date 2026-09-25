"""我的 AI · 通道 — 用户给自己的 AI 接上外部聊天软件（QQ 官方 / NapCat / 以后别的）

通道清单由插件声明（manifest 的 channel 块）——路由上的 {plugin_id} 就是插件 id，
平台侧没有"通道常量表"：插件装上就多一条，卸载就少一条。

权限模型：通道属于 AI 的所有者（agent.user_id），入口就在那个 AI 的页面上。
管理员那份插件配置接口仍然保留，用来排障；日常增删改走这里。

为什么不给 plugin_configs 加"归属人"列：实例 id 是 agent-<agentId>，
归属从 agents 表读出来即可，少一列就少一处能对不上的地方。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.plugin import channel
from app.services.plugin.runtime_control import StartFailed, UnknownInstance
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me/agents", tags=["我的 AI · 通道"])


class SaveChannelRequest(BaseModel):
    """只收配置值：目标 AI 由路径上的 agent_id 决定，不给用户填名字的机会"""
    values: dict = {}


class PairApproveRequest(BaseModel):
    pairing_id: int | None = None
    code: str | None = None


class OriginRequest(BaseModel):
    """通道侧的那个人的标识（QQ 官方是 openid，NapCat 是 QQ 号）"""
    origin: str


class LandingGroupRequest(BaseModel):
    """另一个落点选项：不接回现有群，而是在 Copree 新建一个"""
    name: str = ""


async def _owned(db: AsyncSession, agent_id: int, user: dict):
    """归属检查：不是自己的 AI 一律 403（NotOwned 是 PermissionError，不转成 HTTP 会变成 500）"""
    try:
        return await channel.owned_agent(db, agent_id, int(user["user_id"]))
    except channel.NotOwned as e:
        raise HTTPException(403, str(e))


def _declared(plugin_id: str) -> dict:
    """未知通道 404：插件没声明 channel 块，或插件根本不存在"""
    try:
        return channel.declared(plugin_id)
    except channel.UnknownChannel as e:
        raise HTTPException(404, str(e))


@router.get("/{agent_id}/channels")
async def list_channels(
    agent_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """这个 AI 的全部通道（含配置、运行态、待批准与已配对的名单）"""
    await _owned(db, agent_id, user)
    return {"channels": await channel.views(db, agent_id, int(user["user_id"]))}


@router.put("/{agent_id}/channels/{plugin_id}")
async def save_channel(
    agent_id: int,
    plugin_id: str,
    req: SaveChannelRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存通道配置并让它生效（写配置 → 建实例 → 启动）"""
    _declared(plugin_id)
    agent = await _owned(db, agent_id, user)
    try:
        result = await channel.save(
            db, plugin_id=plugin_id, agent_id=agent_id, user_id=int(user["user_id"]),
            values=req.values or {}, actor=str(user.get("username") or user["user_id"]),
            target_agent_name=agent.name,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": "已保存", **result}


@router.post("/{agent_id}/channels/{plugin_id}/landing-group")
async def create_landing_group(
    agent_id: int,
    plugin_id: str,
    req: LandingGroupRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """在 Copree 新建一个群当外部消息的落点（用户是群主，这个 AI 是成员）"""
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    try:
        return await channel.create_landing_group(
            db, agent_id=agent_id, user_id=int(user["user_id"]), name=req.name
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{agent_id}/channels/{plugin_id}/start")
async def start_channel(
    agent_id: int,
    plugin_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    try:
        return await channel.start(db, plugin_id=plugin_id, agent_id=agent_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except UnknownInstance:
        raise HTTPException(404, "通道还没配置")
    except StartFailed as e:
        raise HTTPException(500, str(e))


@router.post("/{agent_id}/channels/{plugin_id}/stop")
async def stop_channel(
    agent_id: int,
    plugin_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    try:
        return await channel.stop(db, plugin_id=plugin_id, agent_id=agent_id)
    except UnknownInstance:
        raise HTTPException(404, "通道还没配置")


@router.post("/{agent_id}/channels/{plugin_id}/pairings/approve")
async def approve_pairing(
    agent_id: int,
    plugin_id: str,
    req: PairApproveRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批准一条待配对：点界面上的「批准」，或把通道里收到的配对码抄回来"""
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    try:
        return {"message": "已批准", **await channel.approve(
            db, plugin_id=plugin_id, agent_id=agent_id, pairing_id=req.pairing_id, code=req.code
        )}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{agent_id}/channels/{plugin_id}/pairings/block")
async def block_pairing(
    agent_id: int,
    plugin_id: str,
    req: OriginRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拉黑一个人：不再回话，也不再发配对码"""
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    ok = await channel.block_pairing(db, plugin_id=plugin_id, agent_id=agent_id, origin=req.origin)
    if not ok:
        raise HTTPException(404, "没有这条配对记录")
    return {"message": "已拉黑", "origin": req.origin}


@router.post("/{agent_id}/channels/{plugin_id}/pairings/forget")
async def forget_pairing(
    agent_id: int,
    plugin_id: str,
    req: OriginRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解除配对：对方重新变回陌生人，下次私聊重新领码"""
    _declared(plugin_id)
    await _owned(db, agent_id, user)
    ok = await channel.forget_pairing(db, plugin_id=plugin_id, agent_id=agent_id, origin=req.origin)
    if not ok:
        raise HTTPException(404, "没有这条配对记录")
    return {"message": "已解除"}
