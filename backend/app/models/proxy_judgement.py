"""代理 LLM 判定留痕 —— 每次「要不要拉起本尊」一行

为什么落库：94% 的停用线、影子期的正确拦截/误杀、按帧聚合都要读它。
日志里 grep 出来的数不可靠，也不便按窗口算。一行一个判断，量与消息数成正比；
账本只收那句 why，明细留在这张表里，两处不混。
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
    UniqueConstraint, func,
)
from app.database import Base


class ProxyJudgement(Base):
    """一次代理判定的结果"""

    __tablename__ = "proxy_judgements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)

    # 帧身份记 type + context_ref，不记帧实例 id：帧 id 每次 push 都会换，统计会碎成一地
    frame_type = Column(String(40), nullable=False, default="", comment="group_chat / dm / …")
    context_ref = Column(String(120), nullable=False, default="", comment="如 group:81")

    message_id = Column(BigInteger, nullable=True, comment="触发这条判断的消息；幂等键的一半")
    wake = Column(Boolean, nullable=False, comment="True = 拉起本尊")
    reason = Column(String(200), nullable=False, default="", comment="代理给的短理由（≤20 字）")
    urgency = Column(String(10), nullable=False, default="normal", comment="low / normal / high")

    # 超时或出错时记录回退：这一行说明「本该代理判，实际是启发式判的」，不参与停用统计
    fallback = Column(Boolean, nullable=False, default=False)
    model = Column(String(80), nullable=False, default="")
    latency_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # 同一条消息对同一个 AI 只判一次（重投/重排时直接读旧结论）
        UniqueConstraint("agent_id", "message_id", name="uq_proxy_judgement_message"),
        Index("ix_proxy_judgement_agent_created", "agent_id", "created_at"),
    )
