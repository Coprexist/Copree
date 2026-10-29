"""
文件元数据 + 引用追踪 + 协作模式模型
"""
from sqlalchemy import (
    Column, Integer, String, BigInteger, DateTime, ForeignKey, func, CheckConstraint,
    UniqueConstraint, Index,
)
from app.db_providers import json_column
from app.database import Base


class FileMetadata(Base):
    """文件元数据（存储物理文件信息、权限、协作模式）"""
    __tablename__ = "file_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False)
    # 多态归属：owner_type='ai' 时 owner_id 必须是 agent.id（AI 本体），不是它的虚拟账号
    # users.id——文件是 AI 独有的东西；human → users.id，group → groups.id，system → 0。
    # 这列没有外键，语义靠写入方保证；读取侧一律经 file_service._owner_agent 归一，
    # 存量脏数据由 scripts/repair_ai_file_owner_ids.py 归正
    owner_type = Column(String(10), nullable=False)  # 'human' | 'ai' | 'group' | 'system'
    owner_id = Column(Integer, nullable=False)
    size = Column(BigInteger)
    mime_type = Column(String(100))
    content_hash = Column(String(64))  # SHA-256 哈希，用于文件去重
    permissions = Column(json_column())
    # 协作模式: solo(仅自己) | shared(指定协作者) | open(全群可见)
    collaboration_mode = Column(String(10), default="solo", nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('human', 'ai', 'group', 'system')",
            name="ck_file_owner_type",
        ),
        CheckConstraint(
            "collaboration_mode IN ('solo', 'shared', 'open')",
            name="ck_file_collaboration_mode",
        ),
        # 与线上库对齐：owner 检索索引（历史迁移创建，模型补声明）
        Index("idx_file_metadata_owner", "owner_type", "owner_id"),
    )


class FileReference(Base):
    """文件引用追踪：记录哪些 AI/消息引用了哪些文件

    用途：
    1. O(n) 通知：文件变更时通知所有引用方
    2. 依赖分析：查看文件被哪些 AI 依赖
    3. 协作判定：引用方自动成为协作者候选
    """
    __tablename__ = "file_references"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("file_metadata.id", ondelete="CASCADE"), nullable=False)
    referrer_type = Column(String(10), nullable=False)  # 'ai' | 'message' | 'group'
    referrer_id = Column(Integer, nullable=False)
    ref_type = Column(String(20), default="read")  # 'read' | 'write' | 'import' | 'share'
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "referrer_type IN ('human', 'ai', 'message', 'group')",
            name="ck_ref_referrer_type",
        ),
        CheckConstraint(
            "ref_type IN ('read', 'write', 'import', 'share', 'forward')",
            name="ck_ref_type",
        ),
        UniqueConstraint(
            "file_id", "referrer_type", "referrer_id",
            name="uq_file_ref",
        ),
    )


class FileCollaborator(Base):
    """文件协作者：显式指定的文件协作者（用于 shared 模式）

    当 collaboration_mode='shared' 时，此表中的 AI/用户拥有该文件的协作权限。
    """
    __tablename__ = "file_collaborators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey("file_metadata.id", ondelete="CASCADE"), nullable=False)
    collaborator_type = Column(String(10), nullable=False)  # 'ai' | 'user'
    collaborator_id = Column(Integer, nullable=False)
    role = Column(String(20), default="collaborator")  # 'collaborator' | 'viewer'
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "collaborator_type IN ('ai', 'user')",
            name="ck_collab_type",
        ),
        CheckConstraint(
            "role IN ('collaborator', 'viewer')",
            name="ck_collab_role",
        ),
        UniqueConstraint("file_id", "collaborator_type", "collaborator_id", name="uq_file_collab"),
    )
