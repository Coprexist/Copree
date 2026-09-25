"""外部身份模型 —— 不是本实例的人/AI，统一在这里各占一行

为什么单开一张表，而不是在 users 里建「影子账号」：
- users 是「本实例的真人 + AI」。外部身份混进去以后，搜人、加好友、注册引导
  （/auth/has-users 与「首个注册用户即管理员」）、用户统计全都要额外打一个 type
  补丁 —— 漏一处就把他当真人，而且他们还会消耗 users.id 的序号。
- 联邦把远端来的人挤在系统用户 0 号上，QQ 通道各自建影子账号：同一件事两种做法。
  统一到这里之后，QQ / 联邦 / 以后任何通道都只是「一行外部身份」，各带归属与放行状态。
- 外部身份不记账、不挂记忆、不占 users.id。它只回答三件事：
  这是谁（display_name）、从哪来（kind + owner_scope + origin）、放不放行（status）。
"""
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)

from app.database import Base

PENDING = "pending"
APPROVED = "approved"
BLOCKED = "blocked"


def _now() -> datetime:
    return datetime.utcnow()


class ExternalIdentity(Base):
    """一行 = 某个通道上的某个人（或某个远端实体）。"""

    __tablename__ = "external_identities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(80), nullable=False, comment="通道/来源种类，如 qq-channel、federation")
    owner_scope = Column(
        String(120), nullable=False, server_default="",
        comment="该通道下的归属实例：QQ 是 agent-<agentId>，联邦是对端公网 ID",
    )
    origin = Column(String(200), nullable=False, comment="通道侧的稳定标识：QQ 是 openid，联邦是远端实体 ID")
    display_name = Column(String(120), default="", comment="通道侧昵称，仅用于展示")
    avatar_url = Column(Text, default="")
    status = Column(String(16), default=PENDING, server_default=PENDING, comment="pending | approved | blocked")
    code = Column(String(12), default="", comment="配对码：用户在通道里收到，回平台核对")
    approved_at = Column(DateTime, nullable=True)
    # 他后来在本实例自己注册了账号时，把两边的身份绑起来（本人合并）；平时为 NULL
    bound_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        comment="绑定到本实例的真实账号（他后来自己注册了）",
    )
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    last_seen_at = Column(DateTime, nullable=True, comment="最近一次收到他的消息")

    __table_args__ = (
        UniqueConstraint("kind", "owner_scope", "origin", name="uq_external_identity"),
        Index("ix_external_identity_lookup", "kind", "owner_scope", "status"),
    )
