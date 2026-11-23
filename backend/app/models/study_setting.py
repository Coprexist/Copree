"""
自习室用户状态模型 — 时长设置 + 今日周期进度（每用户一行）
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from app.database import Base


class StudySetting(Base):
    """自习室用户状态：番茄钟时长设置 + 今日周期进度，跨设备同步。"""
    __tablename__ = "study_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # unique=True 已经建了唯一索引，index=True 只是同一条索引的第二份声明
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    # 时长设置
    focus = Column(Integer, nullable=False, default=25, comment="专注时长（分钟）")
    short = Column(Integer, nullable=False, default=5, comment="短休息时长（分钟）")
    long = Column(Integer, nullable=False, default=15, comment="长休息时长（分钟）")
    interval = Column(Integer, nullable=False, default=4, comment="长休息间隔（专注次数，1~12）")
    # 今日周期进度（换设备续上）
    cycle_date = Column(String(10), comment="周期进度所属日期 YYYY-MM-DD")
    cycles = Column(Integer, nullable=False, default=0, comment="当前轮已完成专注数")
    sessions = Column(Integer, nullable=False, default=0, comment="今日累计完成专注数")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
