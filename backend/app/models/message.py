"""
消息模型
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func,
    CheckConstraint,
)
from app.config import settings
from app.db_providers import vector_column, json_column
from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    sender_type = Column(String(10), nullable=False)  # 'human' | 'ai'
    sender_id = Column(Integer, nullable=False)
    sender_name = Column(String(100), nullable=True)  # 联邦消息的发送者名称（本地消息为 NULL，由关联查询获取）
    content = Column(Text, nullable=False)
    reply_to = Column(Integer, nullable=True)
    source_public_id = Column(String(50), nullable=True)  # 远程消息来源实例公网 ID（NULL=本地）
    via = Column(String(16), nullable=True)  # 消息入口通道：NULL=站内；qq=QQ 通道（联邦来源另见 source_public_id）
    sender_avatar_url = Column(Text, nullable=True, default='')  # 联邦消息的发送者头像 URL（本地消息为 NULL）
    attachments = Column(json_column(), nullable=True)  # [{file_id, path, name, size, mime_type}, ...]
    # 撤回：站内 2 分钟内可撤；原文留在库里但任何渲染都不显示（撤回通知见 utils/pure/history.revoked_notice）
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(Integer, nullable=True)  # 谁撤的（users.id），审计用
    # 通道侧那条消息的 id（QQ 撤回要它）：NULL = 没经通道，或通道没回 id
    channel_msg_id = Column(Text, nullable=True)
    # 通道侧的**引用索引** REFIDX（QQ 的 msg_idx / ext_info.ref_idx）：精准引用某条时用它
    channel_ref_idx = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "sender_type IN ('human', 'ai')",
            name="ck_message_sender_type",
        ),
    )


class PendingMessage(Base):
    """暂存消息表（AI 离线/免打扰/暂停期间的消息积压）"""
    __tablename__ = "pending_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class GroupMessageEmbedding(Base):
    """向量加速消息表"""
    __tablename__ = "group_message_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(vector_column(settings.embedding_dimension))  # 嵌入向量，维度随配置
    created_at = Column(DateTime, server_default=func.now())
    metadata_ = Column("metadata", json_column())
