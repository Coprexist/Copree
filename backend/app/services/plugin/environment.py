"""
会话环境 —— 取值、判定、落通知的唯一入口。

插件只报事实（ServicePlugin.environment），平台在这里做三件事：问一圈、比一次、变了落一条通知。
判定只有一条路径（canonical 相等），所以插件返回布尔、指纹还是快照都不影响这里；
缺这一层，「谁在什么时候比」就会散到两条请求链路和每个插件里。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

# 环境取值的时间闸：契约要求「廉价」，但契约拦不住写错的插件，平台强制一个上限；
# 超时按「丢弃本次」处理（与异常同路径）——卡住的插件不该拖住整条消息链路。
ENV_TIMEOUT_SECONDS = 1.0

from app.utils.pure.plugin_env import EnvContractError, changed, normalize, render_environment

logger = logging.getLogger(__name__)


async def current_environment(db: AsyncSession, group_id: int) -> dict | None:
    """返回该会话当前的环境；没有任何通道提供环境时返回 None。

    多个通道同时服务一个群属于罕见情形，此处按先声明者合并，而非并列展开；确需并列说明的
    插件应以 text 覆盖整句。插件抛出异常或返回值违约时丢弃该条并记 error，不降级为
    「无环境」——降级会伪造出一次环境消失。
    """
    from app.services.infrastructure.plugin_registry import get_by_owner
    from app.services.plugin.channel import served_instances
    from app.utils.pure.channel_landing import session_ref

    merged: dict = {}
    for plugin_id, instance, _found in await served_instances(db, group_id):
        plugin = get_by_owner(plugin_id, instance)
        probe = getattr(plugin, "environment", None)
        if not callable(probe):
            continue
        # origin = 这个会话的标识（平台总能给出；插件自己解析成它的对端对象）
        try:
            raw = await asyncio.wait_for(
                probe(origin=session_ref(group_id)), timeout=ENV_TIMEOUT_SECONDS,
            )
            env = normalize(raw)
        except EnvContractError as e:
            logger.error(f"🌐 插件 {plugin_id}/{instance} 环境返回值违约（丢弃本次）: {e}")
            continue
        except asyncio.TimeoutError:
            logger.error(
                f"🌐 插件 {plugin_id}/{instance} 环境取值超时（>{ENV_TIMEOUT_SECONDS}s，丢弃本次）"
            )
            continue
        except Exception as e:  # noqa: BLE001 —— 插件故障不该打断这一轮对话
            logger.error(f"🌐 插件 {plugin_id}/{instance} 环境取值异常（丢弃本次）: {type(e).__name__}: {e}")
            continue
        if not env:
            continue
        for key, value in env.items():
            merged.setdefault(key, value)
    return merged or None


async def sync_environment(db: AsyncSession, agent, group_id: int) -> dict | None:
    """取值、判定，并建立基线或落一条通知；返回落下的通知文本，无变化时返回空串。

    会话帧首次取值只建立基线、不发通知（见 read_frame_env）。发生变更时先推进已告知值
    （去重依据），写进前缀的那一份保持不变——锁定态只落通知，解锁点
    （UNLOCK_STEPS 的 apply_environment）才将两者对齐。返回的条目由调用方与其它当轮事件
    一起 append，因此本函数不落库、不提交事务。「紧跟历史」是账本条目的纪律。
    """
    from app.services.agent.state_stack_service import read_frame_env, write_frame_env
    from app.services.history.context_sync import context_ref
    from app.utils.pure.history import make_entry

    ref = context_ref(group_id=group_id)
    current = await current_environment(db, group_id)
    baseline, locked, notified = await read_frame_env(db, agent.id, ref)
    if not baseline:
        await write_frame_env(db, agent.id, ref, locked=current, notified=current)
        return None
    if not changed(notified, current):
        return None
    await write_frame_env(db, agent.id, ref, locked=locked, notified=current)
    text = f"【环境变化】{render_environment(current)}"
    return make_entry("notice", text, flags={"drop_on_unlock": True})


async def locked_environment_text(db: AsyncSession, agent_id: int, context_ref: str) -> str:
    """返回锁定态写进前缀的环境文本；会话帧缺失、未建立基线或环境为空时返回空串。"""
    from app.services.agent.state_stack_service import read_frame_env

    have, locked, _notified = await read_frame_env(db, agent_id, context_ref)
    if not have or not locked:
        return ""
    return render_environment(locked)
