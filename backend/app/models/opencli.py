"""
OpenCLI 权限配置模型
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func,
)
from app.database import Base


class OpenCLIConfig(Base):
    """全局配置（单行表，id=1）"""
    __tablename__ = "opencli_config"

    id = Column(Integer, primary_key=True, default=1)
    global_enabled = Column(Boolean, default=False)
    default_rate_limit_per_minute = Column(Integer, default=5)
    timeout_seconds = Column(Integer, default=30)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class OpenCLIAgentWhitelist(Base):
    """AI OpenCLI 白名单"""
    __tablename__ = "opencli_agent_whitelist"

    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, default=False)
    rate_limit_override = Column(Integer, nullable=True)  # NULL = 继承全局
    created_at = Column(DateTime, server_default=func.now())


class OpenCLICommandWhitelist(Base):
    """命令白名单"""
    __tablename__ = "opencli_command_whitelist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(String(200), nullable=False)
    is_regex = Column(Boolean, default=False)
    description = Column(String(200))
    enabled = Column(Boolean, default=True)
    default_enabled = Column(Boolean, default=False)  # True=全部AI默认可用，跳过白名单
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, server_default=func.now())


class OpenCLIUsageLog(Base):
    """使用日志"""
    __tablename__ = "opencli_usage_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"))
    command = Column(Text, nullable=False)
    args = Column(Text)
    exit_code = Column(Integer)
    stdout_truncated = Column(Text)
    stderr_truncated = Column(Text)
    duration_ms = Column(Integer)
    executed_at = Column(DateTime, server_default=func.now())


class OpenCLIDeniedCommand(Base):
    """默认拒绝的命令黑名单"""
    __tablename__ = "opencli_denied_commands"

    pattern = Column(String(200), primary_key=True)
    reason = Column(String(200))
