"""
API Key 池模型
管理员可管理系统级共享 API Key 池，用户通过兑换码获取额度后从池中分配 Key。
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, func
from app.database import Base
from app.db_providers import json_column


class ApiKeyPool(Base):
    """管理员管理的 API Key 池"""
    __tablename__ = "api_key_pool"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="管理员命名（如 DeepSeek 主号）")
    api_base_url = Column(Text, nullable=True, comment="API 地址，NULL=继承全局")
    api_key_encrypted = Column(Text, nullable=False, comment="Fernet 加密的 API Key")
    is_active = Column(Boolean, default=True, comment="管理员可禁用")
    priority = Column(Integer, default=0, comment="越高越优先分配")
    concurrent_limit = Column(Integer, nullable=True, comment="并发上限，NULL=按模型默认(pro=500,flash=2500)")
    provider_name = Column(String(100), nullable=True, comment="关联 provider_config[].name，NULL=使用第一个/默认供应商")
    # 「地址」和「能用哪些模型」是同一件事的两半：只有地址不知道名字清单，就会把别家的模型名
    # 发到这家（2026-10-19：grok-4.6 发进 DeepSeek 池，每轮 400，用户那边什么都看不到）
    models = Column(json_column(), nullable=True,
                    comment="待选模型 id 列表；NULL=按 provider_name/api_base_url 关联的供应商预设")
    default_model = Column(String(100), nullable=True,
                           comment="通道不认 AI 自选模型时用它；NULL=供应商预设的 chat_model")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class UserApiAssignment(Base):
    """用户→池 Key 绑定缓存（O(1) 查找，避免每次扫描全池）"""
    __tablename__ = "user_api_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    pool_key_id = Column(Integer, ForeignKey("api_key_pool.id", ondelete="CASCADE"), nullable=False)
    assigned_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, server_default=func.now())
