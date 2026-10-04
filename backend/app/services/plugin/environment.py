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

from app.utils.pure.plugin_env import (
    ENV_RULES_KEY,
    EnvContractError,
    canonical,
    changed,
    normalize,
    render_channel_rules,
    render_environment,
)

logger = logging.getLogger(__name__)

# 环境取值的时间闸：契约要求「廉价」，但契约拦不住写错的插件，平台强制一个上限；
# 超时按「丢弃本次」处理（与异常同路径）——卡住的插件不该拖住整条消息链路。
ENV_TIMEOUT_SECONDS = 1.0


async def current_environment(db: AsyncSession, group_id: int, *, served: list | None = None) -> dict | None:
    """返回该会话当前的环境；没有任何通道提供环境时返回 None。

    多个通道同时服务一个群属于罕见情形，此处按先声明者合并，而非并列展开；确需并列说明的
    插件应以 text 覆盖整句。插件抛出异常或返回值违约时丢弃该条并记 error，不降级为
    「无环境」——降级会伪造出一次环境消失。
    """
    from app.services.infrastructure.plugin_registry import get_by_owner
    from app.services.plugin.channel import served_instances
    from app.utils.pure.channel_landing import session_ref

    merged: dict = {}
    for plugin_id, instance, _found in (served if served is not None else await served_instances(db, group_id)):
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


async def environment_snapshot(db: AsyncSession, group_id: int, *, served: list | None = None) -> dict | None:
    """这个会话当前的环境快照 = 插件报的环境 + 平台自己那条（通道规矩）。

    两处取值——每轮的 environment_turn 与解锁点的 apply_environment——必须拿到同一份：
    解锁点直接问 current_environment 的话，对齐时会把通道规矩这条平台自己的事实抹掉。
    """
    from app.services.plugin.channel import channel_rules

    env = await current_environment(db, group_id, served=served)
    rules = await channel_rules(db, group_id, served=served)
    if not rules:
        return env
    return {**(env or {}), ENV_RULES_KEY: rules}


def _change_text(locked: dict | None, current: dict | None) -> str:
    """变更通知：说清到底变了什么。

    为什么要把通道规矩整段带上：写进前缀的那份锁定态不变，AI 若只看到"环境有变化"，
    就不知道是群名人数在动还是"这个群不再接 QQ 了"这种要改行为的事。通知是账本条目，只付一次。
    """

    def facts(value: dict | None) -> dict | None:
        return {k: v for k, v in (value or {}).items() if k != ENV_RULES_KEY} or None

    lines: list[str] = []
    if canonical(facts(locked)) != canonical(facts(current)):
        lines.append(f"【环境变化】{render_environment(facts(current))}")
    rules_before, rules_now = render_channel_rules(locked), render_channel_rules(current)
    if rules_before != rules_now:
        lines.append("【通道变化】" + (
            f"这个群的通道规矩变了，以这条为准：\n{rules_now}" if rules_now
            else "这个群不再接外部通道了，之前那些通道规矩不用再管。"
        ))
    return "\n".join(lines) or f"【环境变化】{render_environment(current)}"


async def environment_turn(
    db: AsyncSession, agent, group_id: int | None, *, served: list | None = None,
) -> tuple[dict | None, dict | None]:
    """本轮的环境：返回 (写进前缀的那份, 要落的通知条目)。

    一次读帧、一次问通道，两处用途（渲染前缀段、判定要不要通知）共用这一次取值——
    分成两个函数各自读一遍状态栈，每轮就多一次 DB 往返，而这是每轮都付的钱。
    快照里除了插件报的环境，还有平台自己那条通道规矩（见 environment_snapshot）。

    会话帧首次取值只建立基线、不发通知（见 read_frame_env）。发生变更时先推进已告知值
    （去重依据），写进前缀的那一份保持不变——锁定态只落通知，解锁点
    （UNLOCK_STEPS 的 apply_environment）才将两者对齐。私信会话（group_id 为空）没有环境
    来源，直接返回空。本函数不提交事务：与注入同一次提交才构成同事务。
    """
    if not group_id:
        return None, None
    from app.services.agent.state_stack_service import read_frame_env, write_frame_env
    from app.services.history.context_sync import context_ref
    from app.utils.pure.history import make_entry

    ref = context_ref(group_id=group_id)
    current = await environment_snapshot(db, group_id, served=served)
    established, locked, notified = await read_frame_env(db, agent.id, ref)
    if not established:
        await write_frame_env(db, agent.id, ref, locked=current, notified=current)
        return current, None
    if not changed(notified, current):
        return locked, None
    await write_frame_env(db, agent.id, ref, locked=locked, notified=current)
    return locked, make_entry("notice", _change_text(locked, current), flags={"drop_on_unlock": True})
