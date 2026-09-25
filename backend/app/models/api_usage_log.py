"""
API 用量日志模型
记录每次 LLM 调用的额度消耗，用于用户端展示和审计。
"""
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, func
from app.database import Base


class ApiUsageLog(Base):
    """每次 LLM 调用扣除的额度记录"""
    __tablename__ = "api_usage_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="哪个用户消耗的")
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True, comment="哪个 AI 产生的调用")
    pool_key_id = Column(Integer, ForeignKey("api_key_pool.id", ondelete="SET NULL"), nullable=True,
                         comment="使用的池 Key（NULL=用户自有 Key 或该 Key 已删除）")
    source = Column(String(20), nullable=False, default="user_key", comment="来源：user_key | pool_key")
    tokens_used = Column(Integer, nullable=False)
    credit_spent = Column(Numeric(6, 2), nullable=False, default=0)
    model = Column(String(50), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
