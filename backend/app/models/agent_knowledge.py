"""
AI 知识库 — 每个 AI 自己的文档库（界面可见、可配置）
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.database import Base


class AgentKnowledge(Base):
    """知识库条目（一份文档一行）

    正文落在 AI 自己的文件空间（data/agents/{id}/knowledge/<path>）：文档动辄几十 KB，
    塞进 DB 只会在提示词里挤爆；AI 本来就有 file_read 工具，索引里给标题/摘要/路径、
    细节按需去读。这张表是"界面上看得到、能开关、能改"的唯一来源。
    """

    __tablename__ = "agent_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(120), nullable=False, comment="文档标题（进索引）")
    summary = Column(Text, default="", comment="一句话说明（进索引；空 = 只列标题）")
    path = Column(String(200), nullable=False, comment="相对 knowledge/ 的路径")
    size = Column(Integer, default=0, comment="正文字节数")
    enabled = Column(Boolean, default=True, comment="关掉后不进索引（正文保留）")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "path", name="uq_agent_knowledge_path"),
    )
