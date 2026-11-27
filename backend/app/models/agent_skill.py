"""
AI 思维 Skill 模型
每个 AI 可配置多个 Skill：延迟回复、打字指示器、场景匹配、提示词注入
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, func,
)
from app.db_providers import json_column
from app.database import Base


class AgentSkill(Base):
    __tablename__ = "agent_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False, comment="技能名称，如「延迟回复」「温柔模式」")
    skill_type = Column(String(30), nullable=False, comment="delay_reply | typing_indicator | scene_trigger | inject_prompt")
    is_enabled = Column(Boolean, default=True, comment="是否启用")
    config = Column(json_column(), nullable=False, default=dict, comment="技能配置JSON，各type不同schema")
    priority = Column(Integer, default=0, comment="优先级：数字越大越靠后")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())
