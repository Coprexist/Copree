"""
AI 配置模型 — AI 的运行时配置
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, Text, DateTime, ForeignKey, func
from app.db_providers import json_column
from app.database import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    
    config_type = Column(String(20), default="default")
    config_data = Column(json_column(), default=dict)
    
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())