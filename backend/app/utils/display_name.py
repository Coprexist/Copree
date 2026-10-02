"""显示名的唯一入口：AI 取 agents.name，其余取 users.username。

AI 建号时会在 users 里另开一行，username 抄一份当时的名字（重名还要加后缀），
而改名只写 agents.name——两份名字从此各自漂移。任何"把 id 渲染成人或 AI 看的
名字"的地方若直接读 users.username，同一个 AI 就会在会话列表里是旧名、在消息
气泡里是新名。所以显示名一律从这里取。
"""
from sqlalchemy import select

from app.models.agent import Agent
from app.models.user import User


def _fallback(user_id: int) -> str:
    """查不到名字时的兜底：界面宁可见「用户{id}」也不要 None。"""
    return f"用户{user_id}"


async def display_names(db, user_ids) -> dict[int, str]:
    """批量：id → 显示名。

    db 可以是 Session，也可以是暴露了 execute 的仓库（导出、邀请等路径就是这么传的）。
    一次 join 查完，避免列表逐条查名字。
    """
    ids = {int(uid) for uid in user_ids if uid is not None}
    if not ids:
        return {}

    rows = (await db.execute(
        select(User.id, User.username, User.type, Agent.name)
        .outerjoin(Agent, Agent.user_id == User.id)
        .where(User.id.in_(ids))
    )).all()

    names: dict[int, str] = {}
    for uid, username, user_type, agent_name in rows:
        # agent.name 只对 AI 有意义；人类用户没有 agent 行，取 username
        name = (agent_name or "").strip() if user_type == "ai" else ""
        names[int(uid)] = name or (username or "").strip() or _fallback(int(uid))

    for uid in ids - names.keys():
        names[uid] = _fallback(uid)
    return names


async def display_name(db, user_id) -> str:
    """单个：查不到给「用户{id}」。"""
    if user_id is None:
        return ""
    return (await display_names(db, [user_id])).get(int(user_id)) or _fallback(int(user_id))
