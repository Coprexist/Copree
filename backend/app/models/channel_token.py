"""
通道对话额度 — 每个 QQ 群 / QQ 私聊最多用多少 token

两张表分工：
  channel_token_policy        每个「AI × 通道种类」一份默认（口径 + 额度 + 超限自动回复）
  channel_conversation_usage  每个对话一行（可单独覆盖口径/额度，并记已用量）

为什么默认值与对话分开：用户既要「统一调这个 AI 的默认」，又要「单独调某一个对话」。
两者混在一张表里，就得给"默认"造一行哨兵记录（conversation='*'），
然后每个查询都要记得把这行排除掉——迟早漏一处。
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func,
)

from app.database import Base


class ChannelTokenPolicy(Base):
    """某个 AI 在某类通道上的默认额度策略"""

    __tablename__ = "channel_token_policy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    # 通道种类来自插件 manifest 的 channel.kind（qq / napcat / …），不是一个平台常量表
    channel_kind = Column(String(20), nullable=False, default="qq")
    # total = 全部 token（含缓存命中）；billable = 只算未命中的输入 + 输出
    meter_mode = Column(String(10), nullable=False, default="billable",
                        comment="计费口径：total（全部 token）| billable（未命中输入 + 输出）")
    limit_tokens = Column(BigInteger, nullable=False, default=1_000_000,
                          comment="每个对话的默认额度（token）")
    blocked_reply_enabled = Column(Boolean, nullable=False, default=True,
                                   comment="超限后自动回一句话（默认开）")
    blocked_reply_text = Column(Text, nullable=True,
                                comment="自动回复正文；NULL = 平台默认的官方口吻那一句")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "channel_kind", name="uq_channel_token_policy"),
    )


class ChannelConversationUsage(Base):
    """一个通道对话的额度与已用量（可覆盖默认策略）"""

    __tablename__ = "channel_conversation_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    channel_kind = Column(String(20), nullable=False, default="qq")
    # group:<Copro 群 id> / dm:<私信会话 id>：额度是"这个对话"的账，与 QQ 侧 openid 解耦，
    # 换绑、改名都不该把它清零（键的构造见 utils/pure/channel_token.conversation_key）
    conversation = Column(String(120), nullable=False)
    # 给人看的名字（QQ 群名 / QQ 昵称），只是展示用，随时可以刷
    label = Column(String(120), nullable=False, default="")
    # 从对话反查到这条账的两条路：群走 copree_group_id，私聊走 dm_session_id
    copree_group_id = Column(Integer, nullable=True)
    dm_session_id = Column(String(64), nullable=True)

    # 单独的覆盖：NULL = 跟随该 AI 在该通道上的默认策略
    limit_override = Column(BigInteger, nullable=True, comment="单独额度；NULL = 跟随默认")
    meter_mode_override = Column(String(10), nullable=True, comment="单独口径；NULL = 跟随默认")

    # 两个计数器都留着：用户换口径时不丢历史，随时可以换回去继续看
    used_total = Column(BigInteger, nullable=False, default=0, comment="按 total 口径累计")
    used_billable = Column(BigInteger, nullable=False, default=0, comment="按 billable 口径累计")

    # 超限自动回复的节流时间：空 = 这个对话还没被提醒过
    blocked_notice_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "channel_kind", "conversation", name="uq_channel_conversation_usage"),
        # 记账时按群 / 按私信会话反查：两条索引各自对应一条 SQL
        Index("ix_channel_conv_usage_group", "agent_id", "copree_group_id"),
        Index("ix_channel_conv_usage_dm", "agent_id", "dm_session_id"),
    )
