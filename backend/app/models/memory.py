"""
两层记忆模型
"""
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, func,
    CheckConstraint,
)
from app.config import settings
from app.db_providers import json_column, vector_column
from app.database import Base

class RoughMemory(Base):
    """粗略记忆（标题层）"""
    __tablename__ = "rough_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_type = Column(String(10), nullable=False)  # 'ai' | 'group'
    owner_id = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    embedding = Column(vector_column(settings.embedding_dimension))  # 标题向量（维度随配置）
    scope = Column(String(10), default="private")  # private | group | cross_user
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    # v0.1.3: per-user 记忆隔离（共振 AI 为 NULL，通用/半通用填触发用户 ID）
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # v0.1.4: 延迟归档字段
    status = Column(String(20), default="active", comment="active | pending_archive | discarded")
    # 设定权值 1-5（v1.1）：1=日常流水, 3=一般, 5=人物与关系定位。
    # 时间权值不落库——读取时按「最近一次被召回」现算，设定权值始终保持不动
    value_score = Column(Integer, default=5, comment="设定权值 1-5: 1=日常流水, 3=一般, 5=人物与关系定位")
    mem_type = Column(String(20), default="daily",
                      comment="类型: person|relationship|promise|event|preference|daily")
    # 焦段锚点：适用范围。三组各自可空，全空 = 当前会话 + 当前语义焦段
    session_refs = Column(json_column(), default=list)
    session_foci = Column(json_column(), default=list)
    semantic_foci = Column(json_column(), default=list)
    # 时间权值的基准：最近一次被召回的时刻与当时的调用刻度
    last_touched_at = Column(DateTime, nullable=True)
    last_touched_call = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('ai', 'group')",
            name="ck_rough_owner_type",
        ),
        CheckConstraint(
            "status IN ('active', 'pending_archive', 'discarded')",
            name="ck_rough_status",
        ),
    )


class DetailMemory(Base):
    """详细记忆（内容层）"""
    __tablename__ = "detail_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rough_id = Column(Integer, ForeignKey("rough_memories.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(vector_column(settings.embedding_dimension))  # 可选，用于深度检索
    created_at = Column(DateTime, server_default=func.now())
