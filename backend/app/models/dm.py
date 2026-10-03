"""
私信（DM）模型
- dm_sessions: 会话表，session_id 为排序拼接的 "min_id_max_id"
- dm_messages: 消息表，read_at 记录对方阅读时间
"""
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint,
    Index, text,
)
from app.database import Base


class DMSession(Base):
    """私信会话（1对1）"""
    __tablename__ = "dm_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False)  # "min_id_max_id"
    user1_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user2_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # per-session 免打扰（每方独立）
    user1_dnd_until = Column(DateTime)
    user2_dnd_until = Column(DateTime)

    last_message_id = Column(Integer)
    last_message_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user1_id", "user2_id", name="uq_dm_session_users"),
        # 「我参与的私信」按 user1_id=我 或 user2_id=我 取：user1 侧上面那条唯一约束的前缀
        # 已经能用，这里补 user2 侧
        Index("ix_dm_sessions_user2", "user2_id"),
    )


class DMMessage(Base):
    """私信消息"""
    __tablename__ = "dm_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(64),
        ForeignKey("dm_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    reply_to = Column(Integer)  # 回复的消息 ID
    attachments = Column("attachments", Text)  # JSON array of {file_id, name, size, mime_type}
    message_type = Column(String(30), default="normal", nullable=False)  # normal | group_invitation

    # 对方阅读时间：发送时为 NULL，用户查看会话后批量标记
    read_at = Column(DateTime)

    # 联邦来源：NULL=本地消息，非空=来自对应 public_id 的远程实例
    source_public_id = Column(String(50), nullable=True)

    # 撤回（与群消息同一套语义，见 app/chat/revoke.py）
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(Integer, nullable=True)
    channel_msg_id = Column(Text, nullable=True)
    channel_ref_idx = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # 会话里的消息按 (session_id, created_at) 取、按 created_at 排
        Index("ix_dm_messages_session_created", "session_id", "created_at"),
        # 未读只占少数：部分索引只收 read_at IS NULL 的行，
        # 未读计数与「标记已读」都走它（别的方言退化成普通索引）
        Index("ix_dm_messages_unread", "session_id", postgresql_where=text("read_at IS NULL")),
    )
