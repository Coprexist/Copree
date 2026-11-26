"""
一次「从聊天记录导入」的记账。

为什么单独一张表：导入的消息混在 DM 历史里，光看消息分不出「哪一批」——
要能整批撤销、要在会话里标一句「这段来自导入」、要统计「多少个附件没有原文件」，
都得有一个批次锚点。表里只存来源与统计，正文永远只在 messages 里（单一来源）。
"""
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Text, func, Index,
)
from app.database import Base


class ChatImport(Base):
    __tablename__ = "chat_imports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    creator_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(
        String(64), ForeignKey("dm_sessions.session_id", ondelete="CASCADE"), nullable=False
    )

    platform = Column(String(32), nullable=False, default="unknown")
    parser = Column(String(64), nullable=False, default="unknown")
    parser_version = Column(Integer, nullable=False, default=1)
    source_url = Column(Text, nullable=True)
    title = Column(String(200), nullable=True)

    message_count = Column(Integer, nullable=False, default=0)
    attachment_count = Column(Integer, nullable=False, default=0)
    missing_attachment_count = Column(Integer, nullable=False, default=0)
    first_at = Column(DateTime, nullable=True)
    last_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_chat_imports_agent", "agent_id"),
        Index("ix_chat_imports_session", "session_id"),
    )
