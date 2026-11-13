"""帧事件的插件钩子：帧被切进来之后，插件可以补一次「我不在的时候那边发生了什么」。

为什么需要这条钩子：插件帧的上下文与群 / 私信是分开的——他在「刷 B 站」那一帧里看不到
B 站站内的新回复与私信（那属于另一个系统的收件箱）。钩子给插件一次机会把这些**读进帧的账本**。

是**注入**，不是唤醒：平台不因这条钩子开新轮，也不替插件判断「要不要回」——
插件只负责把消息写进账本，回不回、什么时候回，由 AI 自己看上下文决定。

失败只记日志：钩子是锦上添花，补不到消息不该让「进这一帧」失败。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# 钩子可能要出网（B 站收件箱就是几个 HTTP）：进帧不该被外部接口的延迟拖住，
# 超时就当这次没补上——下一次进来还会再补（游标只在成功时前进）。
HOOK_TIMEOUT_SECONDS = 8.0


async def entered(db, agent, frame: dict | None) -> None:
    """帧成了当前帧之后调一次：交给它所属插件的 on_frame_enter（没声明就跳过）。"""
    from app.services.agent import frame_budget
    from app.services.plugin.channel import instance_of

    plugin_id = frame_budget.plugin_of(frame)
    if not plugin_id:
        return
    try:
        from app.services.infrastructure.plugin_registry import get_by_owner

        instance = get_by_owner(plugin_id, instance_of(int(agent.id)))
        hook = getattr(instance, "on_frame_enter", None)
        if not callable(hook):
            return
        # 插件上下文由 plugin_registry 在注册时套过（见 _CONTEXT_BOUND_METHODS）
        await asyncio.wait_for(hook(db, agent, frame), timeout=HOOK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("插件 %s 的 on_frame_enter 超时 %.0fs，这次不补（不影响进帧）",
                    plugin_id, HOOK_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001 —— 钩子失败只影响"补消息"，不影响进帧
        logger.warning("插件 %s 的 on_frame_enter 失败（不影响进帧）: %s", plugin_id, e)
