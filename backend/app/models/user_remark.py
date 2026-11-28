"""我给某个群 / 某个人起的备注（会话列表与资料卡优先显示它）

为什么单独一张表，而不是塞进 user_group_preferences 或 users：
- 它是「我对这个对象的称呼」，所有者一定是一个人，一个对象至多一条 → 天然是
  (owner, target) 的键；
- 群备注与用户备注是同一件事，两种目标用两个可空外键 + CHECK 互斥表达，
  「删群 / 删账号时备注跟着走」交给数据库，不靠业务代码逐路径清理。
"""
from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func,
)

from app.database import Base

# 备注是个称呼，不是简介：32 字够装下「三班的张老师（夜班）」这种
REMARK_MAX_CHARS = 32


class UserRemark(Base):
    __tablename__ = "user_remarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # 两种目标恰好有一个非空（CHECK 保证）：group_id 是群备注，target_user_id 是人备注
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    remark = Column(String(REMARK_MAX_CHARS), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL) <> (target_user_id IS NOT NULL)",
            name="ck_user_remark_single_target",
        ),
        UniqueConstraint("owner_id", "group_id", name="uq_user_remark_group"),
        UniqueConstraint("owner_id", "target_user_id", name="uq_user_remark_user"),
    )
