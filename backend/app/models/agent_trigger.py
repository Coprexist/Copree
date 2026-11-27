"""
触发器模型 — AI 的事件触发器
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func, Text
from app.db_providers import json_column
from app.database import Base


class AgentTrigger(Base):
    __tablename__ = "agent_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    
    trigger_type = Column(String(20), nullable=False)  # time | event | semantic | relational | state | composite
    task = Column(Text, nullable=False)
    
    status = Column(String(20), default="pending")  # pending | fired | cancelled
    
    expires_at = Column(DateTime, nullable=True)
    max_fires = Column(Integer, default=-1)  # -1 = 无限制
    fire_count = Column(Integer, default=0)
    
    condition = Column(json_column(), default=dict)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())