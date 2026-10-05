"""创建 AI 的草稿 —— 辅助填写助手的一侧账本。

一次「创建」拆成两半，各自用现成设施：
- 表单快照与状态：本表（form 就是前端 AgentForm 的原样 JSON，camelCase，不做字段翻译）
- 请求体（对话）：ai_conversation_logs，conversation_type="creator"，session_id = 草稿 id

草稿列表因此天然就是「继续上一次的创建 / 选择未完成的创建」的列表，不必另造消息表。
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, func

from app.database import Base
from app.db_providers import json_column


class AgentCreationDraft(Base):
    """一次创建 AI 的进行中会话（表单 + 状态；对话在对话日志里）"""

    __tablename__ = "agent_creation_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True, comment="创建者")
    title = Column(String(100), default="", nullable=False, comment="草稿名（取首句描述，列表展示用）")
    form = Column(json_column(), default=dict, nullable=False, comment="创建表单快照（前端 AgentForm 原样，camelCase）")
    preset = Column(String(30), nullable=True, comment="档位 key（chat|immersive|digital_life）")
    sub = Column(String(40), nullable=True, comment="子档 id（唯一来源见 frontend/src/components/agent-create/presets.ts）")
    status = Column(String(16), default="open", nullable=False, comment="open=未完成 | created=已建号 | abandoned=已放弃")
    agent_id = Column(Integer, nullable=True, comment="建成后的 AI id（status=created 时）")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_agent_creation_drafts_user_status", "user_id", "status", "updated_at"),
    )
