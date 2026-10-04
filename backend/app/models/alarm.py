"""
Agent 闹钟模型
AI 可以为自己设定闹钟，到时间后自动唤醒并执行预设任务
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from app.database import Base


class AgentAlarm(Base):
    __tablename__ = "agent_alarms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    wake_at = Column(DateTime(timezone=True), nullable=False, comment="唤醒时间")
    task = Column(Text, nullable=False, comment="唤醒后要执行的任务描述")
    # 计划到点是在某个状态里执行的（唤醒时构建的就是那个状态的请求体），所以记住"该唤醒谁"；
    # 帧 id 活在 agents.state_stack 的 JSON 里、会被 pop/重建，所以另记一个稳定的"谁拉起的"（会话轴）
    frame_id = Column(String(12), nullable=True, comment="唤醒时恢复的目标状态帧")
    origin_context_ref = Column(String(64), nullable=True, comment="排这条计划的会话（帧会重建，会话轴不会）")
    status = Column(String(20), default="pending", comment="pending / fired / cancelled")
    created_at = Column(DateTime, default=None)
    fired_at = Column(DateTime(timezone=True), default=None, comment="实际触发时间")
