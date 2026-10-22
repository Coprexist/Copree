"""群助手服务 — 世界在群里的发言身份

一个群助手 = group_assistants 表里一行：无账号、不入群成员表（与群视界 AI 同形态）。
它在消息里的身份是 (sender_type="assistant", sender_id=group_assistants.id)——sender_type
就是"这个 id 属于哪张表"，和 human/ai 指向 users 是同一套读法；群助手的 id 与 users.id
不共用号段，所以消息发送者一律带类型认人（display_name.sender_names）。

世界程序、世界 AI、世界 skill 在绑定群里说话，一律走 say_in_group：发言者就是该群的
群助手，不再借群主或普通成员的身份。借真人身份的代价不只是"看着像群主在说"：
AI 读群历史时也会把世界的话当成那个人说的，等于世界假扮了他。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.models.world import GroupAssistant, WorldBinding

logger = logging.getLogger(__name__)

# 助手名与列宽一致：世界的名字直接当助手名（群成员 @ 得出来），超了截断
_NAME_LIMIT = 50


def default_prompt(world, group_id: int, group_type_slug: str | None = None) -> str:
    """群助手的默认人设：一个群一个助手，由世界调度。类型化的群带一句类型说明。

    世界在这个群说的话由它代言，所以人设里必须写清"你是谁派来的"，
    否则它被 @ 到时只会以通用助手口吻答话，跟世界对不上。
    """
    text = f"你是群「{group_id}」的助手，由世界「{world.name}」统一调度管理。"
    if group_type_slug:
        text += f"\n\n类型：{group_type_slug}"
    return text


async def create_assistant(db, world, group_id: int, *, name: str,
                           group_type_slug: str | None = None,
                           system_prompt: str | None = None) -> GroupAssistant:
    """建一行群助手 —— 建行的唯一入口。

    调用方只决定名字与提示词（类型模板要编号、世界的代言用世界的名字），
    字段怎么填、日志怎么记都在这里，免得再有人手抄一份字段清单。
    """
    ga = GroupAssistant(
        group_id=int(group_id),
        world_id=int(world.id),
        group_type_slug=group_type_slug,
        name=(str(name or "").strip() or "群助手")[:_NAME_LIMIT],
        system_prompt=(system_prompt if system_prompt is not None
                       else default_prompt(world, group_id, group_type_slug)),
        config={},
    )
    db.add(ga)
    await db.flush()
    logger.info(f"🤖 群 {group_id} 创建群助手「{ga.name}」(group_assistant {ga.id}，世界 #{world.id})")
    return ga


async def _binding_slug(db, world_id: int, group_id: int) -> str | None:
    """这个群绑到世界时用的类型（没类型就是 None——历史绑定大多如此）"""
    return (await db.execute(
        select(WorldBinding.group_type_slug).where(
            WorldBinding.world_id == int(world_id),
            WorldBinding.entity_type == "group",
            WorldBinding.entity_id == int(group_id),
        )
    )).scalars().first() or None


async def ensure_group_assistant(db, world, group_id: int) -> GroupAssistant:
    """世界在这个群里的发言人：有就用（取 id 最小的那个，身份保持稳定），没有就按世界的名字建一个。

    类型化绑定（group_types.json 的 assistant_spec）在绑定时已按模板建好助手；
    历史绑定没有类型、也就没有助手，这里在第一次发言时补建。一个世界绑几个群，
    就是每群一个助手——身份跟着群走，不会把 A 群的话记到 B 群的助手头上。
    """
    rows = (await db.execute(
        select(GroupAssistant).where(
            GroupAssistant.world_id == int(world.id),
            GroupAssistant.group_id == int(group_id),
        ).order_by(GroupAssistant.id)
    )).scalars().all()
    if rows:
        return rows[0]
    return await create_assistant(
        db, world, group_id,
        name=world.name,
        group_type_slug=await _binding_slug(db, world.id, group_id),
    )


async def say_in_group(db, world, group_id: int, content: str, *, reply_to: int | None = None):
    """以本群群助手身份发一条世界消息（落库 + 实时广播）—— 世界在群里说话的唯一入口。

    source="world"：不回流给世界程序（防死循环）；
    allow_non_member：助手不是群成员表里的人，成员校验对它不适用（群绑定校验在调用方）。

    广播推完整消息视图而不是 id+正文：前端按 sender_id 反查发送者，而群助手的 id
    不在用户表里，只推 id 会让这条消息显示成一个没有名字的人。
    """
    from app.chat.gm import gm_message_to_dict, send_gm_message
    from app.chat.group_delivery import broadcast_group_message

    ga = await ensure_group_assistant(db, world, group_id)
    msg = await send_gm_message(
        db, int(group_id), "assistant", int(ga.id), content,
        reply_to=reply_to, source="world", allow_non_member=True,
    )
    await db.flush()
    await broadcast_group_message(int(group_id), gm_message_to_dict(msg, sender_name=ga.name))
    logger.info(f"🌐 世界 #{world.id} 以群助手「{ga.name}」在群 {group_id} 发言（消息 {msg.id}）")
    return msg
