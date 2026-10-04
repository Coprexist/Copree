"""
AI 个人工作区服务
追踪 AI 当前任务、处理中断、注入恢复上下文。
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.workspace import AgentWorkspace
from app.repositories.agent_repo import AgentRepository, SQLAlchemyAgentRepository
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyAgentRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyAgentRepository(db_or_repo)
    return db_or_repo

# 中断后多久内算"需要恢复"（超过这个时间就当 AI 已经做完了）
RECOVERY_WINDOW_MINUTES = 30


async def save_current_task(
    db: AsyncSession,
    agent_id: int,
    task: str,
) -> None:
    """保存 AI 的当前任务。每次 tool_call_loop 结束时调用。"""
    db = _ensure_repo(db)
    now = utc_now()
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()

    if ws:
        ws.current_task = task[:500]  # 截断
        ws.current_task_at = now
        ws.interrupted_at = None  # 新的任务开始，清除旧的中断标记
        ws.interruption_reason = None
        ws.updated_at = now
    else:
        ws = AgentWorkspace(
            agent_id=agent_id,
            current_task=task[:500],
            current_task_at=now,
            updated_at=now,
        )
        db.add(ws)
    await db.flush()


async def mark_interrupted(
    db: AsyncSession,
    agent_id: int,
    reason: str,
) -> None:
    """标记 AI 的当前任务被中断（有人发消息来了）"""
    db = _ensure_repo(db)
    now = utc_now()
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()

    if ws and ws.current_task:
        ws.interrupted_at = now
        ws.interruption_reason = reason[:200]
        ws.updated_at = now
        await db.flush()
        logger.info(f"📋 AI({agent_id}) 任务被中断: 「{ws.current_task[:50]}」→ 原因: {reason}")


async def get_workspace_status(db: AsyncSession, agent_id: int) -> dict:
    """获取 AI 的当前工作区状态"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()

    if ws is None or ws.current_task is None:
        return {
            "has_task": False,
            "current_task": None,
            "interrupted": False,
        }

    return {
        "has_task": True,
        "current_task": ws.current_task,
        "current_task_at": ws.current_task_at.isoformat() if ws.current_task_at else None,
        "interrupted": ws.interrupted_at is not None,
        "interrupted_at": ws.interrupted_at.isoformat() if ws.interrupted_at else None,
        "interruption_reason": ws.interruption_reason,
    }


async def get_current_task_text(db: AsyncSession, agent_id: int) -> str | None:
    """
    获取当前任务的纯文本——直接注在系统提示词里。
    如果 AI 有进行中的任务，返回一行提示；如果被打断过，额外说明。
    """
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()

    if ws is None or ws.current_task is None:
        return None

    lines = [f"\n\n## 📋 你的当前任务\n**「{ws.current_task}」**"]

    if ws.current_task_at:
        lines.append(f"- 开始于: {ws.current_task_at.strftime('%H:%M:%S')}")

    if ws.interrupted_at:
        now = utc_now()
        if now - ws.interrupted_at < timedelta(minutes=RECOVERY_WINDOW_MINUTES):
            lines.append(f"- ⚠️ 在 {ws.interrupted_at.strftime('%H:%M:%S')} 被「{ws.interruption_reason or '新消息'}」打断")
            lines.append("- 你可以：继续之前的任务，或者调用 clear_current_task 放弃，或者更新你的计划")

    lines.append("- 如果需要停止这个任务，调用 clear_current_task 工具")
    return "\n".join(lines)


async def clear_task(db: AsyncSession, agent_id: int) -> None:
    """清除当前任务（AI 完成了或放弃了）"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()
    if ws:
        ws.current_task = None
        ws.current_task_at = None
        ws.interrupted_at = None
        ws.interruption_reason = None
        ws.updated_at = utc_now()
        await db.flush()


async def _get_or_create_ws(db: AsyncSession, agent_id: int) -> AgentWorkspace:
    """获取或创建工作区记录"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentWorkspace).where(AgentWorkspace.agent_id == agent_id)
    )
    ws = result.scalar_one_or_none()
    if ws is None:
        ws = AgentWorkspace(agent_id=agent_id, updated_at=utc_now())
        db.add(ws)
        await db.flush()
    return ws


async def get_workspace_file(db: AsyncSession, agent_id: int, file_type: str) -> str:
    """读取单个工作区文件"""
    db = _ensure_repo(db)
    ws = await _get_or_create_ws(db, agent_id)
    content = getattr(ws, file_type, None)
    return content or ""


async def set_workspace_file(db: AsyncSession, agent_id: int, file_type: str, content: str) -> None:
    """写入单个工作区文件"""
    db = _ensure_repo(db)
    if file_type not in ("todo", "plan", "journal"):
        raise ValueError(f"无效的文件类型: {file_type}")
    ws = await _get_or_create_ws(db, agent_id)
    setattr(ws, file_type, content)
    ws.updated_at = utc_now()
    await db.flush()
    logger.info(f"📝 AI({agent_id}) 更新工作区 {file_type}: {len(content)} 字符")


async def get_all_workspace_files(db: AsyncSession, agent_id: int) -> dict:
    """获取所有工作区文件"""
    db = _ensure_repo(db)
    ws = await _get_or_create_ws(db, agent_id)
    return {
        "todo": ws.todo or "",
        "plan": ws.plan or "",
        "journal": ws.journal or "",
    }
