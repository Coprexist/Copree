"""
enter_state 工具 —— 进入一条已经存在的状态帧（沉睡的、已结束的都行）

和 push_state 的分工必须说清，否则他会拿 push_state 造一帧新的去顶替：
- push_state：**新建**一帧，用于换会话/换任务；
- enter_state：**回到**已经存在的那一帧（别人替他预建的、或他以前结束掉的），
  帧上的工具白名单、技能、额度都跟着生效，身份（id / type）一点不变。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class EnterState(ToolPlugin):
    name = "enter_state"
    description = (
        "进入一条已存在的状态帧（沉睡 / 已结束的都算），工具换成那一帧的白名单；出来用 pop_state。\n"
        "插件在通道页预建的状态（如「刷 B 站」）就用它进；push_state 是新建一帧，别拿它顶替。"
    )
    segment = "self_management"
    parameters = {
        "frame_id": {"type": "string", "description": "要进入的状态帧 id（list_states 里能看到）"},
        "doing": {
            "type": "string", "nullable": True,
            "description": "进来之后在做什么（可选；不传就沿用帧上原来的）",
        },
    }
    required = ["frame_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "AI 进入一条已存在的状态帧（插件预建的或自己以前结束的），工具白名单随之生效"
    trigger_condition = "AI 想进入某个已存在的状态（如插件预建的「刷 B 站」）时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent.state_stack_service import pull_frame

        frame_id = str(arguments.get("frame_id") or "").strip()
        if not frame_id:
            return {"error": True, "message": "要给它 frame_id（list_states 里能看到）"}

        from app.models.agent import Agent
        from sqlalchemy import select

        agent_obj = (await db.execute(
            select(Agent).where(Agent.id == int(agent_id)))).scalars().first()
        if agent_obj is None:
            return {"error": True, "message": "找不到这个 AI"}

        doing = str(arguments.get("doing") or "").strip()
        # leave="suspend"：原来那帧挂着，pop 之后回到它（与 pop_state 只差这一个参数）
        frame = await pull_frame(
            db, agent_id, frame_id,
            leave="suspend",
            updates={"doing": doing} if doing else None,
        )
        if not frame:
            return {"error": True,
                    "message": f"没有找到状态帧 {frame_id}：先 list_states 看一眼 id 对不对"}
        # 进帧之后交给它所属插件补一次「我不在的时候那边发生了什么」（可选钩子，注入不唤醒）
        from app.services.plugin import frame_hooks

        await frame_hooks.entered(db, agent_obj, frame)
        await db.commit()
        logger.info(f"Agent({agent_id}) enter_state {frame_id} [{frame.get('type')}]")
        tools = frame.get("tools") or []
        # 进来这一刻就把这帧的预算念一遍：建议单次时长、这一小时/今天还剩几次都在这句里
        from app.services.agent import frame_budget

        budget = await frame_budget.describe(db, agent_id)
        return {
            "success": True,
            "message": (
                f"已进入 [{frame.get('type')}]: {frame.get('doing') or frame.get('why') or ''}"
                + (f"（这个状态的工具白名单有 {len(tools)} 个）" if tools else "")
                + (f"\n预算: {budget}" if budget else "")
                + "\n出来用 pop_state。"
            ),
            "frame_id": frame.get("id"),
            "type": frame.get("type"),
        }


ToolRegistry.register(EnterState)
