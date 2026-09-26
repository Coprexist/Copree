"""
目录级结构记忆（双重记忆架构的系统2）

与 rough_memories（向量记忆）互补：
- 向量记忆：语义搜索，适合"我记不记得这个事实？"
- 结构记忆：精确键值存取，适合"学生1的有机化学水平是什么？"

目录结构：{category}/{sub_key}/{field} → value
UNIQUE(agent_id, category, sub_key, field) 实现 upsert
"""
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint,
)
from app.database import Base
from app.db_providers import json_column


class StructuredRecord(Base):
    __tablename__ = "structured_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(100), nullable=False)
    sub_key = Column(String(200), nullable=False)
    field = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    # 设定权值与焦段锚点（v1.1）：与向量记忆同一套语义（见 utils/pure/memory_weight.py）
    value_score = Column(Integer, default=3, comment="设定权值 1-5")
    mem_type = Column(String(20), default="daily",
                      comment="类型: person|relationship|promise|event|preference|daily")
    session_refs = Column(json_column(), default=list)
    session_foci = Column(json_column(), default=list)
    semantic_foci = Column(json_column(), default=list)
    last_touched_at = Column(DateTime, nullable=True)
    last_touched_call = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "category", "sub_key", "field", name="uq_sr_path"),
    )
