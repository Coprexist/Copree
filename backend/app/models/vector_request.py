"""
向量加速申请模型
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, func,
)
from app.database import Base


class VectorAccelerationRequest(Base):
    __tablename__ = "vector_acceleration_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    requester_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), default="pending")  # pending|approved|rejected
    approver_type = Column(String(10))  # human|ai|system
    approver_id = Column(Integer)
    auto_handled = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime)
