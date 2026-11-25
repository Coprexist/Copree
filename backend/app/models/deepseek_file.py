"""DeepSeek Files API 上传索引：远端图片 × 引用方（会话）的登记表。

为什么单独一张表：
- `messages.attachments` 是 JSON 快照，写一次就不再更新；而远端 file_id 会**过期、
  被删、被重新上传**——这些是"会变的远端状态"，必须有自己的行，不能塞进快照。
- 云端**不做内容去重**（2026-11-25 核实：同一张图传两次是两个 file_id、占两份配额），
  所以去重只能我们自己按内容 sha256 做。

作用域口径（用户 2026-11-25 拍定）：
- `scope = sha256(base_url | api_key)`。**不同 key 一律不共享**——我们无法判断两把
  key 是否属于同一个 DeepSeek 账号，跨 key 猜归属只会误删别人的图。
- 同一把 key 内按内容共享：同一张图只传一份，靠 `remote_file_id` 在多行间复用。

一行 = 一个引用方。粒度是 `(scope, content_sha256, agent_id, context_ref)`：
同一张群图被 5 个 AI 看到就是 5 行，共用一个 remote_file_id。这样：
- 压缩掉某个会话时只删自己那行，不碰别人的；
- 某个 remote_file_id 的最后一行消失时才 `DELETE` 远端——不会误删正在用的图。
"""
from sqlalchemy import (
    BigInteger, Column, DateTime, Index, Integer, String, UniqueConstraint, func,
)
from app.database import Base


class DeepSeekUploadFile(Base):
    __tablename__ = "deepseek_upload_files"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 作用域：sha256(base_url + "|" + api_key)。不同 key 不共享是硬约束。
    scope = Column(String(64), nullable=False)
    # 引用方：哪个 AI、哪个会话（群 group:12 / 私信 dm_xxx）
    agent_id = Column(Integer, nullable=False)
    context_ref = Column(String(128), nullable=False)

    # 本地附件（FileMetadata.id）
    local_file_id = Column(Integer, nullable=True)
    # 去重键：图片字节的 sha256（不用 FileMetadata.content_hash——那个可能为空）
    content_sha256 = Column(String(64), nullable=False)
    # 远端 file_id（file-api-...）。多个引用方共用同一个值。
    remote_file_id = Column(String(128), nullable=False)

    mime_type = Column(String(100), nullable=True)
    bytes = Column(BigInteger, nullable=True)

    # 远端到期时间（naive UTC）：上传时 = created_at + expires_after（默认 7 天）。
    # 剩余寿命低于刷新余量（1h）时视为不可复用，重新上传。
    expires_at = Column(DateTime, nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        # 同一个引用方对同一份内容只登记一次：并发上传靠它兜底
        UniqueConstraint("scope", "content_sha256", "agent_id", "context_ref",
                         name="uq_deepseek_upload_ref"),
        # 复用查询：同 scope 同内容找可复用的 remote_file_id
        Index("ix_deepseek_upload_scope_content", "scope", "content_sha256"),
        # 清理入口：按 scope 扫 oldest
        Index("ix_deepseek_upload_scope_created", "scope", "created_at"),
    )
