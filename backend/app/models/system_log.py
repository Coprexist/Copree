"""
审计日志模型 — 企业级操作记录，含哈希链防篡改
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, func
from app.db_providers import json_column
from app.database import Base


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = {"comment": "审计日志（哈希链防篡改）"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    log_type = Column(String(50), nullable=False, comment="操作类型: config|user|ai|api|file|permission|system")
    operator_type = Column(String(10), nullable=False, comment="操作者类型: human|ai|system")
    operator_id = Column(Integer, nullable=False, comment="操作者 ID")
    target_type = Column(String(50), nullable=False, comment="操作目标类型")
    target_id = Column(Integer, nullable=True, comment="操作目标 ID")

    # 企业审计字段
    success = Column(Boolean, nullable=False, default=True, comment="操作成功/失败")
    error_message = Column(Text, nullable=True, comment="失败原因")
    ip_address = Column(String(45), nullable=True, comment="客户端 IP（IPv4/IPv6）")
    old_value = Column(json_column(), nullable=True, comment="变更前值")
    new_value = Column(json_column(), nullable=True, comment="变更后值")

    # 额外上下文
    details = Column(json_column(), nullable=True, comment="其他上下文")

    # 哈希链防篡改
    prev_hash = Column(String(64), nullable=True, comment="上一条日志的 SHA256")
    hash = Column(String(64), nullable=False, comment="本条日志的 SHA256（含 prev_hash）")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True, comment="记录时间")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "log_type": self.log_type,
            "operator_type": self.operator_type,
            "operator_id": self.operator_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "success": self.success,
            "error_message": self.error_message,
            "ip_address": self.ip_address,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "details": self.details,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
