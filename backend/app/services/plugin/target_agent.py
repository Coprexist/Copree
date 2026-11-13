"""通道背后的那个 AI：只按 id 认，名字不参与任何判断。

名字是用户可以随手改的内容（「鲸鱼娘 Deepseek」→「Deepseek 大肥鱼」），拿它当绑定键，
AI 一改名通道下次启动就"在群里找不到这个 AI"、start() 失败，整条通道静默不启动
（2026-11-02 线上，用户在 QQ 里只看到「机器人…的服务异常」）。

所以配置里只存 target_agent_id。target_agent（名字）仍由平台写进配置，但只是给人看的
标签——这里从不读它，改名不影响绑定；每次启动按 id 把当前名字刷一遍，免得标签过期误导排查。
"""
from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class Bound(NamedTuple):
    agent_id: int
    user_id: int
    name: str


async def bind(db: AsyncSession, *, agent_id: str, group_id: int = 0) -> Bound:
    """按 id 认下「这个通道背后是哪个 AI」。给了 group_id 就还要求它是这个群的 AI 成员。"""
    from app.models.agent import Agent
    from app.models.group import GroupMember

    if not agent_id.isdigit():
        raise ValueError("这个通道没有记录接的是哪个 AI，请回到 AI 页面重新保存一次通道")

    row = (await db.execute(
        select(Agent.id, Agent.user_id, Agent.name).where(Agent.id == int(agent_id))
    )).first()
    if row is None:
        # id 认不到只可能是 AI 被删了：名字再怎么对也不能悄悄换一个绑上
        raise ValueError(f"通道绑定的 AI（id={agent_id}）已经不存在，请回到 AI 页面重新接通道")

    if group_id:
        member = (await db.execute(
            select(GroupMember.group_id).where(
                GroupMember.group_id == group_id,
                GroupMember.member_type == "ai",
                GroupMember.member_id == row.user_id,
            )
        )).first()
        if member is None:
            raise ValueError(f"绑定的 AI「{row.name}」不在 Copree 群 #{group_id} 里")

    return Bound(int(row.id), int(row.user_id), str(row.name))


async def remember(plugin_id: str, instance: str, bound: Bound) -> None:
    """把名字标签刷成当前的（只写不读）：ID 已定，这里只为让库里的标签不说谎。"""
    from app.services.plugin.config import set_config

    await set_config(
        plugin_id,
        {"target_agent_id": str(bound.agent_id), "target_agent": bound.name},
        instance=instance,
        actor="system",
    )
