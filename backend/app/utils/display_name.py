"""显示名的唯一入口：AI 取 agents.name，群助手取 group_assistants.name，其余取 users.username。

AI 建号时会在 users 里另开一行，username 抄一份当时的名字（重名还要加后缀），
而改名只写 agents.name——两份名字从此各自漂移。任何"把 id 渲染成人或 AI 看的
名字"的地方若直接读 users.username，同一个 AI 就会在会话列表里是旧名、在消息
气泡里是新名。所以显示名一律从这里取。

两套号段，键不能混：
- display_names()：users 号段（人和 AI 都在 users 表里，sender_type = human | ai）
- sender_names()：消息发送者，键是 (sender_type, sender_id)。群助手不是 users 行，
  它的 id 是 group_assistants 的主键；只看 id 会把「群助手 3」认成「用户 3」——
  世界在群里说的话，AI 读历史时就变成了那个人在说。
"""
from sqlalchemy import select

from app.models.agent import Agent
from app.models.user import User

SenderKey = tuple[str, int]


def _fallback(user_id: int) -> str:
    """查不到名字时的兜底：界面宁可见「用户{id}」也不要 None。"""
    return f"用户{user_id}"


async def display_names(db, user_ids, *, with_channel: bool = False) -> dict[int, str]:
    """批量：users 号段 id → 显示名。

    db 可以是 Session，也可以是暴露了 execute 的仓库（导出、邀请等路径就是这么传的）。
    一次 join 查完，避免列表逐条查名字。

    with_channel=True 给**给 AI 看**的那条路（会话账本、记忆检索）：外部通道来的人在名字后带上
    通道标记（如「书爱[QQ]」）——AI 才知道他不在这侧。站内界面不要这个标记：那边本来就有"来自 QQ"
    的展示位（前端自己画），名字里再塞一次就重了。
    """
    ids = {int(uid) for uid in user_ids if uid is not None}
    if not ids:
        return {}

    marks: dict[str, str] = {}        # 通道标识 → 展示名：一批里按 kind 只查一次 manifest
    names: dict[int, str] = {}
    rows = (await db.execute(
        select(User.id, User.username, User.type, User.origin_channel, Agent.name)
        .outerjoin(Agent, Agent.user_id == User.id)
        .where(User.id.in_(ids))
    )).all()
    for uid, username, user_type, origin_channel, agent_name in rows:
        # agent.name 只对 AI 有意义；人类用户没有 agent 行，取 username
        name = (agent_name or "").strip() if user_type == "ai" else ""
        name = name or (username or "").strip() or _fallback(int(uid))
        if with_channel and origin_channel:
            if origin_channel not in marks:
                from app.services.plugin.catalog import channel_label

                marks[origin_channel] = channel_label(str(origin_channel))
            name = f"{name}[{marks[origin_channel]}]"
        names[int(uid)] = name

    for uid in ids - names.keys():
        names[uid] = _fallback(uid)
    return names


async def sender_names(db, keys, *, with_channel: bool = False) -> dict[SenderKey, str]:
    """批量：(sender_type, sender_id) → 显示名。消息发送者一律走这个。

    群助手（sender_type='assistant'）的 id 属于 group_assistants 号段，单独查；
    其余（human / ai）都在 users 号段，交给 display_names。
    """
    keys = {(str(sender_type or ""), int(sender_id)) for sender_type, sender_id in keys
            if sender_id is not None}
    if not keys:
        return {}

    assistant_keys = {k for k in keys if k[0] == "assistant"}
    person_keys = keys - assistant_keys
    names = await display_names(db, {k[1] for k in person_keys}, with_channel=with_channel) if person_keys else {}
    out: dict[SenderKey, str] = {k: names[k[1]] for k in person_keys}

    if assistant_keys:
        from app.models.world import GroupAssistant

        rows = (await db.execute(
            select(GroupAssistant.id, GroupAssistant.name)
            .where(GroupAssistant.id.in_({k[1] for k in assistant_keys}))
        )).all()
        for ga_id, ga_name in rows:
            out[("assistant", int(ga_id))] = (ga_name or "").strip() or _fallback(int(ga_id))

    for key in keys - out.keys():
        out[key] = _fallback(key[1])
    return out


async def display_name(db, user_id) -> str:
    """单个：查不到给「用户{id}」。"""
    if user_id is None:
        return ""
    return (await display_names(db, [user_id])).get(int(user_id)) or _fallback(int(user_id))
