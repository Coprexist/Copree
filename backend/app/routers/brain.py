"""
薄大脑 API — 设计文档 brain_controller_design.md 第十一节

端点：
  GET  /brain/health              获取 AI 健康状态（全部或指定）
  GET  /brain/state/{agent_id}    获取 AI 状态链
  POST /brain/heartbeat/{agent_id} 手动触发心跳
  POST /brain/switch-state/{agent_id} 切换 AI 状态
  GET  /brain/personality/{agent_id} 获取人格锚点（只读）
  POST /brain/personality/{agent_id} 创建/更新人格锚点（仅主人/管理员）
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.utils.auth import get_current_user
from app.services.agent.agent_service import get_agent
from app.routers.deps import require_agent_access as _require_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brain", tags=["薄大脑"])


# ═══════════════════════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════════════════════

class SwitchStateRequest(BaseModel):
    """切换 AI 状态请求"""
    target_state: str = Field(..., description="active|dnd|inactive|blocked")
    duration_hours: int | None = Field(default=None, ge=1, le=72, description="inactive/blocked 的持续时间（小时）")
    reason: str | None = None


class PersonalityAnchorRequest(BaseModel):
    """人格锚点创建/更新请求（不可被 AI 自身修改）"""
    name: str = Field(..., min_length=1, max_length=50)
    identity: str = Field(..., min_length=1, description="核心身份描述")
    personality: str = Field(default="", description="人格特征")
    core_values: list[str] = Field(default_factory=list, description="核心价值观（不可被修改）")
    consistency_coefficient: float = Field(default=0.7, ge=0.3, le=1.0, description="0.3=高度情境化 0.7=正常人 1.0=完全一致")


# ═══════════════════════════════════════════════════════════════
# 健康
# ═══════════════════════════════════════════════════════════════

@router.get("/health")
async def brain_health(
    agent_id: int | None = Query(default=None, description="指定 AI；缺省返回全部活跃 AI"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 健康状态（内存 / LLM 额度 / Skill 数）"""
    from app.services.brain.brain_controller import brain_controller

    if agent_id is not None:
        await _require_agent(agent_id, current_user, db)
        health = brain_controller.heartbeat_manager.get_health(agent_id)
        if health is None:
            health = await brain_controller.heartbeat_manager.heartbeat_check(agent_id)
        return health

    # 全部：只对管理员开放（避免泄露其他用户 AI 的额度信息）
    if current_user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可查看全部 AI 健康状态")
    return brain_controller.heartbeat_manager.get_all_health()


# ═══════════════════════════════════════════════════════════════
# 状态链
# ═══════════════════════════════════════════════════════════════

@router.get("/state/{agent_id}")
async def brain_state(
    agent: dict = Depends(_require_agent),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 状态链"""
    from app.services.brain.state_stack_manager import state_stack_manager

    stack = await state_stack_manager.get_state_stack(db, agent.id)
    return {"agent_id": agent.id, "state": agent.state, "stack": stack}


# ═══════════════════════════════════════════════════════════════
# 心跳
# ═══════════════════════════════════════════════════════════════

@router.post("/heartbeat/{agent_id}")
async def brain_heartbeat(
    agent: dict = Depends(_require_agent),
):
    """手动触发单个 AI 心跳检查"""
    from app.services.brain.brain_controller import brain_controller

    return await brain_controller.heartbeat_manager.heartbeat_check(agent.id)


# ═══════════════════════════════════════════════════════════════
# 状态切换
# ═══════════════════════════════════════════════════════════════

@router.post("/switch-state/{agent_id}")
async def brain_switch_state(
    req: SwitchStateRequest,
    agent: dict = Depends(_require_agent),
    db: AsyncSession = Depends(get_db),
):
    """切换 AI 状态（状态机：active/dnd/inactive/blocked）"""
    from app.services.agent.agent_service import switch_agent_state

    try:
        updated = await switch_agent_state(
            db,
            agent_id=agent.id,
            target_state=req.target_state,
            duration_hours=req.duration_hours,
            reason=req.reason,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {"agent_id": agent.id, "state": updated.state, "offline_until": updated.offline_until}


# ═══════════════════════════════════════════════════════════════
# 人格锚点（只读 — 不可被 Skill 修改）
# ═══════════════════════════════════════════════════════════════

@router.get("/personality/{agent_id}")
async def brain_personality(
    agent: dict = Depends(_require_agent),
    db: AsyncSession = Depends(get_db),
):
    """获取人格锚点"""
    from app.repositories.agent_repo import SQLAlchemyAgentRepository
    from app.services.brain.brain_controller import brain_controller

    anchor = await brain_controller.get_personality_anchor(SQLAlchemyAgentRepository(db), agent.id)
    if anchor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该 AI 尚未设置人格锚点")
    return anchor


@router.post("/personality/{agent_id}")
async def brain_personality_upsert(
    req: PersonalityAnchorRequest,
    agent: dict = Depends(_require_agent),
    db: AsyncSession = Depends(get_db),
):
    """创建/更新人格锚点（AI 自身不可调用，仅主人/管理员）"""
    from app.repositories.agent_repo import SQLAlchemyAgentRepository
    from app.services.brain.brain_controller import brain_controller

    anchor = await brain_controller.upsert_personality_anchor(
        SQLAlchemyAgentRepository(db),
        agent_id=agent.id,
        name=req.name,
        identity=req.identity,
        personality=req.personality,
        core_values=req.core_values,
        consistency_coefficient=req.consistency_coefficient,
    )
    await db.commit()
    return anchor
