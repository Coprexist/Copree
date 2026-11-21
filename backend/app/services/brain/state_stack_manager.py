"""
状态链管理器（兼容层）

已委托给 state_stack_service.py，此处仅做兼容导出。
"""

from app.services.agent.state_stack_service import (
    get_state_stack_summary,
    push_state,
    pop_state,
    close_state,
    list_states,
)


class StateStackManager:
    async def push_state(self, db, agent_id: int, frame: dict) -> None:
        await push_state(db, agent_id, frame)

    async def pop_state(self, db, agent_id: int) -> dict | None:
        stack, _ = await pop_state(db, agent_id)
        return stack[-1] if stack else None

    async def close_state(self, db, agent_id: int, frame_id: str) -> None:
        await close_state(db, agent_id, frame_id)

    async def get_state_stack(self, db, agent_id: int) -> list[dict]:
        return await list_states(db, agent_id)

    async def resume_state(self, db, agent_id: int) -> dict | None:
        stack = await list_states(db, agent_id)
        # "挂着等回来"只有一个语义，两种历史写法（paused 是改名前）
        for frame in reversed(stack):
            if frame.get("status") in ("paused", "suspended"):
                return frame
        return None


state_stack_manager = StateStackManager()