"""
系统指标快照模型

每 60 秒一条记录，JSONB 存储完整的指标快照。
自动清理超过 retention_days 的旧记录（默认 30 天）。
"""
from sqlalchemy import Column, Integer, DateTime, func
from app.db_providers import json_column
from app.database import Base


class AgentMetricsSnapshot(Base):
    """系统指标快照"""
    __tablename__ = "agent_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_data = Column(json_column(), nullable=False, comment="指标快照数据")
    created_at = Column(DateTime, server_default=func.now(), comment="快照时间")
