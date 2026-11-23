"""
自习室学习记录模型 — 每人每天一条（分钟累计）
"""
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func,
)
from app.database import Base


class StudyRecord(Base):
    """学习时长记录：user_id + date 唯一，minutes 累计。"""
    __tablename__ = "study_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 不单独建 user_id 索引：uq_study_user_date 的前缀就是它，再建一条是冗余
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date = Column(String(10), nullable=False, comment="YYYY-MM-DD")
    minutes = Column(Integer, nullable=False, default=0, comment="当日累计学习分钟")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_study_user_date"),
    )
