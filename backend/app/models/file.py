"""
文件元数据 + 引用追踪 + 协作模式模型
"""
from sqlalchemy import (
    Boolean, Column, Integer, String, BigInteger, DateTime, ForeignKey, func, CheckConstraint,
    UniqueConstraint, Index, Text, text,
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
    # 语音说了什么（**唯一来源**）：AI 的语音是它自己的文字合成的，人的语音由 ASR 补。
    # 存这里而不是塞进消息的 attachments 快照——它是**会变**的文件属性（先没有、转写完才有、
    # 重转还会改），进了 N 份快照就有 N 份不一致。消息侧只留 file_id，读时按 id 取。
    transcript = Column(Text)
    # 音频时长（毫秒，不可变属性）：会话列表要写「[语音 0'42"]」。文件内容定了就不会变，
    # 所以存一次、随消息快照走（与 transcript 不同——那个会变，只存这一处）。
    # 写入方：file_service.upload_file 上传音频时探测一次（utils/audio_probe，不引解码器）。
    duration_ms = Column(Integer)
    permissions = Column(json_column())
    # 协作模式: solo(仅自己) | shared(指定协作者) | open(全群可见)——只管「谁能看」，不管「能不能改」
    collaboration_mode = Column(String(10), default="solo", nullable=False)

    # 内容可不可变。可见性说"谁能看"，这一列说"内容还能不能改"：以前 collaboration_mode
    # 同时被当成两个意思，但写工具从来没真的落到别人的文件上（ai_write_file 只写自己的
    # agents/{id}/），所以拆成独立的一列。默认 true＝可改，与历史行为一致。
    mutable = Column(Boolean, nullable=False, server_default=text("true"))

    # 被消息带走的时间（NULL＝还在「我的文件」里）。发出去的附件归**消息**所有，不再算发送者的
    # 文件、也删不动它。物理路径不搬家——ai_fetch_attachment 的硬链接依赖路径稳定（§3.2）。
    archived_to_message_at = Column(DateTime, nullable=True)

    # 最近一次被消息引用（发送、转发都算）。清理梯度按它排新旧，**不按 created_at**：
    # 三年前上传、昨天被转发的文件，比十分钟前上传、消息立刻被撤回的更"活"（§5.2）。
    # 撤回时回退成「剩余引用里最大的那个」，否则撤回来的文件永远排不到清理队列前头。
    last_referenced_at = Column(DateTime, nullable=False, server_default=func.now())

    # 硬链接组标识（i:<dev>:<ino>；拿不到 inode 时 f:<源行id>）。同一份物理字节只算一次用量，
    # 删字节前也靠它查同组还有没有活行：ai_fetch_attachment 用 os.link 收附件时会多建一行、
    # size 照抄原件——不按物理键去重就把一份文件算成两份，清掉原件还释放不了磁盘（§5.7）。
    physical_key = Column(String(64), nullable=True)

    # 这行什么时候会被回收（NULL＝不回收）。目前只有「上传了但没发」的附件有值：上传时按
    # 24 小时算，发出时清空——进了消息就不归这条规则管了，清理梯度接手（§3.5）。
    # 用一列而不是拿 created_at + 24h 现算：存量迁移时给过一次宽限（7 天），两者不一样；
    # 界面上的「X 小时后清除」也直接读它，不各算各的。
    reclaim_after = Column(DateTime, nullable=True, index=True)

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
        # 计量：按物理键去重后按 owner 汇总（§5.7）
        Index("idx_file_metadata_owner_physical", "owner_type", "owner_id", "physical_key"),
        # 清理候选：已归档 + 按最近引用排新旧（§5.3）
        Index("idx_file_metadata_archive_ref", "archived_to_message_at", "last_referenced_at"),
        # 删字节前查「同一份物理文件还有没有别的活行」
        Index("idx_file_metadata_physical", "physical_key"),
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
