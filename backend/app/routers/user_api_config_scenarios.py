"""用户默认配置的场景 —— HTTP 入口

薄路由：只做参数形状与鉴权，数据规则在 services/agent/api_scenario_service.py。
解析链读的是同一批表（api_config_resolver 的 _scenario_candidates），
所以「界面上关掉了一格」与「请求时不再尝试那一格」不可能各说各话。

设计依据：docs/dev/api_config_chain.md §3.4、§6.8。
"""
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.agent import api_scenario_service as svc
from app.utils.auth import get_current_user

router = APIRouter(prefix="/user/api-config/scenarios", tags=["用户 API 配置 · 场景"])


class ScenarioIn(BaseModel):
    """场景总开关"""

    enabled: bool


class RouteIn(BaseModel):
    """序列里的一格：挂哪条配置档、子开关"""

    profile_id: int
    enabled: bool = True


class RoutesIn(BaseModel):
    """整体保存：数组顺序即 position

    用整体保存而不是逐条增删改——排序、子开关、删除在界面上是一次操作，
    拆成多个请求就会出现「顺序存了一半」的中间态。
    """

    routes: list[RouteIn] = Field(default_factory=list)


@router.get("")
async def list_scenarios(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """场景清单：总开关、有序序列、每格最近一次尝试状态"""
    return {"scenarios": await svc.list_scenarios(db, current_user["user_id"])}


@router.put("/{key}")
async def set_scenario(
    key: str,
    body: ScenarioIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """开/关某个场景的默认配置兜底（关 = 第 ③ 格整体跳过，池与 AI 绑定不动）"""
    return await svc.set_scenario_enabled(db, current_user["user_id"], key, body.enabled)


@router.post("/{key}/routes")
async def add_route(
    key: str,
    body: RouteIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """在序列末尾追加一格（重复添加幂等）"""
    return await svc.add_route(db, current_user["user_id"], key, body.profile_id)


@router.put("/{key}/routes")
async def save_routes(
    key: str,
    body: RoutesIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """整体保存序列：顺序、子开关、增删一次落盘"""
    return {
        "routes": await svc.save_routes(
            db, current_user["user_id"], key, [r.model_dump() for r in body.routes],
        )
    }


@router.delete("/{key}/routes/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_route(
    key: str,
    profile_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从序列移除一格（不是关掉：关掉用子开关，移除是不要了）"""
    await svc.remove_route(db, current_user["user_id"], key, profile_id)


@router.post("/{key}/routes/{profile_id}/ack")
async def ack_status(
    key: str,
    profile_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """红点已读：只清 unread，历史 state 留着判断「下次还要不要发消息」"""
    return await svc.ack_status(db, current_user["user_id"], key, profile_id)
