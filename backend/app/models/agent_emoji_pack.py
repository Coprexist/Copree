"""AI 级表情包开关（平台资源的使用权）"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, Index, func

from app.database import Base


class AgentEmojiPack(Base):
    """一个 AI 对一个表情包的选择：用 / 不用

    默认白名单（见 settings.emoji_pack_mode）：没有行 = 这个 AI 不用这个包；
    黑名单模式反过来——没有行 = 能用，显式 enabled=False = 关掉。
    只记"用户的决定"，插件本身还是那堆资源文件，卸载插件不动这张表。
    """
    __tablename__ = "agent_emoji_packs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    pack_id = Column(String(80), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "pack_id", name="uq_agent_emoji_pack"),
        Index("ix_agent_emoji_packs_agent", "agent_id"),
    )
