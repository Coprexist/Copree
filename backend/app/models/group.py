"""
群聊模型
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, CheckConstraint, func, Text, text,
    ForeignKey, PrimaryKeyConstraint, Index, UniqueConstraint,
)
from app.database import Base


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    owner_type = Column(String(10), nullable=False)  # 'human' | 'ai'
    owner_id = Column(Integer, nullable=False)
    is_vector_accelerated = Column(Boolean, default=False)
    announcement = Column(Text, nullable=True)
    announcement_updated_at = Column(DateTime, nullable=True)
    speak_limit_per_minute = Column(Integer, default=0)  # 0 = 不限制
    speak_limit_window_seconds = Column(Integer, default=120)  # 时间窗口（秒）
    concurrent_ai_limit = Column(Integer, default=3)  # 同群同时 LLM 调用上限，NULL/0=默认3
    # 群聊单条消息展示上限（超长折成前 75% + 后 25%，见 utils/pure/history.fold_text）；0 = 不截断
    max_msg_display_len = Column(Integer, default=2048)
    is_paused = Column(Boolean, default=False)  # 群管理暂停 AI 触发
    avatar_mode = Column(String(20), nullable=False, default="default")  # 'default' | 'members' | 'custom'
    avatar_url = Column(String(500), nullable=True)  # 自定义头像 URL
    include_ai_in_avatar = Column(Boolean, nullable=False, default=True)  # members 模式下是否包含 AI
    # 发现与入群三开关（默认值＝保持 2026-09-21 之前的行为：搜不到、直接进、邀请免审）
    searchable = Column(Boolean, nullable=False, default=False, server_default=text("false"),
                        comment="是否可被搜索到（默认关，群主主动开）")
    auto_approve_join = Column(Boolean, nullable=False, default=True, server_default=text("true"),
                               comment="加群是否自动通过；关＝需要群主/管理员审批")
    approve_invites = Column(Boolean, nullable=False, default=False, server_default=text("false"),
                             comment="群成员邀请是否需要审批；群主/管理员的邀请免审")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('human', 'ai')",
            name="ck_group_owner_type",
        ),
    )


class MemberSilence(Base):
    """按人静音：某个 AI 在这个群里不听某个人的消息（连 @ 都不唤醒）

    为什么单开一张表：group_members 记的是「谁在这个群里」、dnd_until 记的是「整个群要不要
    静音」；「只对某个人静音」是第三种关系，塞进成员表没处挂。

    until_at 与 remaining_count 各自可空：空 = 那一维不限制（都空 = 永久静音）；
    谁先用完/先到点都算失效——用户 2026-09-26 要的正是「多少条内 / 多少分钟内不再唤醒」。
    """
    __tablename__ = "member_silences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    target_user_id = Column(Integer, nullable=False)
    until_at = Column(DateTime, nullable=True)
    remaining_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "group_id", "target_user_id", name="uq_member_silence"),
        Index("ix_member_silences_lookup", "agent_id", "group_id", "target_user_id"),
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    member_type = Column(String(10), nullable=False)  # 'human' | 'ai'
    member_id = Column(Integer, nullable=False)
    role = Column(String(20), default="member")  # owner|admin|member
    # NULL/过去 = 没设免打扰；有值且在将来 = 免打扰中；"永久"存 2099-12-31（见 chat/delivery.py）
    # 免打扰挡的是常规消息：@/@all/公告/特别关心都穿透
    dnd_until = Column(DateTime, nullable=True)
    muted_until = Column(DateTime, nullable=True)  # 屏蔽截止时间，期间 @/公告也不穿透
    last_read_at = Column(DateTime, nullable=True)  # 用户上次查看群聊的时间
    joined_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("group_id", "member_type", "member_id"),
        CheckConstraint(
            "member_type IN ('human', 'ai')",
            name="ck_group_member_type",
        ),
    )


class GroupInvitation(Base):
    """群邀请记录（仅人类走邀请流程，AI 直接入群）"""
    __tablename__ = "group_invitations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    inviter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    invitee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending | accepted | rejected
    # 群 approve_invites=true 时，成员邀请先卡审批：pending → 群主/管理员批准后才通知被邀请人。
    # 存量记录与群主/管理员的邀请一律 approved（默认值就是它，避免历史数据被拦）。
    approval_status = Column(String(20), default="approved", nullable=False,
                             server_default=text("'approved'"))  # pending | approved | rejected
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    message = Column(Text, nullable=True)  # 附言
    dm_session_id = Column(String(64), nullable=True)  # 关联的 DM 会话
    dm_message_id = Column(Integer, nullable=True)     # 关联的卡片消息 ID
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_group_invitation_status",
        ),
        # 与线上库对齐：邀请检索索引（历史迁移创建，模型补声明）
        Index("idx_group_invitations_group", "group_id"),
        Index("idx_group_invitations_invitee", "invitee_id", "status"),
    )


class GroupJoinRequest(Base):
    """入群申请：群 auto_approve_join=false 时，申请入群先落这里，
    群主/管理员在「申请列表」里批准或拒绝（与好友申请同一处审批、同一个红点口径）。"""
    __tablename__ = "group_join_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pending | approved | rejected
    message = Column(Text, nullable=True)  # 申请附言
    resolver_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 审批人
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_group_join_requests_group_status", "group_id", "status"),
        Index("ix_group_join_requests_user_status", "user_id", "status"),
    )
