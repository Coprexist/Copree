"""
兑换码模型

两张表：
- RedemptionCode  码的「定义」：类型、额度、有效期、最多能用几次
- RedemptionRecord 兑换「流水」：谁、用哪个码、兑了多少、什么时候

拆成两张表是这次扩展的关键：以前「用过没有」只能靠 used_by 单列表达，一个码只能给
一个人；现在「已兑人次」「本人兑过没有」都从流水里数，(code, user_id) 唯一约束让
「每人一次」由数据库兜底——不限人次的活动码和一次性码走同一条判断，不需要两套分支。
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Index, func,
)
from app.database import Base


class RedemptionCode(Base):
    __tablename__ = "redemption_codes"

    code = Column(String(32), primary_key=True)
    quota_amount = Column(Integer, nullable=False)
    code_type = Column(String(20), default="ai_quota")  # 'ai_quota' | 'api_credit' | 'agent_bundle' | 'file_quota'
    expires_at = Column(DateTime)
    # v0.1.5 的单人字段：0100 已把已有数据回填进 redemption_records，
    # 新流程只读流水、不再读写这两列；保留列是为了老库回滚时数据不丢。
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True, comment="已废弃：见 redemption_records")
    used_at = Column(DateTime, comment="已废弃：见 redemption_records")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # v0.1.5: 兑换码增强
    note = Column(Text, nullable=True, comment="管理员备注（不暴露给用户）")
    max_usage = Column(Integer, nullable=True, comment="此码最多可用多少 credit（NULL=一次性全额）")
    is_api_pool = Column(Boolean, default=False, comment="是否使用 API 池额度")
    created_at = Column(DateTime, server_default=func.now())
    # 最多可兑换人次：NULL=不限人次（活动码）、1=一次性（历史默认）、N=前 N 名
    usage_limit = Column(Integer, nullable=True, comment="最多可兑换人次（NULL=不限，每人仍限一次）")


class RedemptionRecord(Base):
    """兑换流水（已兑人次与「每人一次」的唯一真相）"""

    __tablename__ = "redemption_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), ForeignKey("redemption_codes.code", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # 发放快照：码被删/改之后，流水仍能自证当时发了什么
    quota_amount = Column(Integer, nullable=False)
    code_type = Column(String(20), nullable=False)
    redeemed_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("code", "user_id", name="uq_redemption_record_user"),
        Index("ix_redemption_records_code", "code"),
        Index("ix_redemption_records_user", "user_id"),
    )
