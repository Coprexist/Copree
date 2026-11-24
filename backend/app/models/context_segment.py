"""上下文压缩段：被折叠的原文快照。

为什么单独一张表：
- 压缩只留了一句「中间 N 条已折叠」的提示。AI 真要去翻时**没有原文可翻**——
  之前那句「用 expand_context 工具可展开」指向一个根本不存在的工具（2026-11-25 发现）。
- 不能塞进 ai_conversation_logs：那个表按「每个会话保留 20 条」裁剪，压缩段需要
  更长的存活期；而且它出现在管理员/用户的日志列表里会被当成一次真实对话。

保留策略与去重：
- 段不参与任何投递/注入，只在 AI 主动展开时读一次。
- expires_at 到期由 context_segment_service.purge_expired 清掉（压缩后 7 天）。
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Index
from app.db_providers import json_column
from app.database import Base


class ContextSegment(Base):
    __tablename__ = "context_segments"

    # AI 拿它当 ref 调工具，短一点：8 位十六进制（碰撞概率可忽略，主键兜底）
    id = Column(String(16), primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    # 场景键（与账本同一套 context_ref）：群 group:12 / 私信 dm_xxx / 插件帧 / 世界会话
    context_ref = Column(String(128), nullable=False, index=True)
    conversation_type = Column(String(32), nullable=False, default="group")
    group_id = Column(Integer, nullable=True)
    session_id = Column(String(50), nullable=True)
    # 被折叠了多少：条数 + 估算 token（只用于提示文案与裁剪，不参与计费）
    message_count = Column(Integer, nullable=False, default=0)
    token_count = Column(Integer, nullable=False, default=0)
    # 折叠前的 messages 原文（含 tool 调用链，展开时原样还给 AI）
    payload = Column(json_column(), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    expires_at = Column(DateTime, nullable=False, index=True)

    __table_args__ = (
        Index("ix_context_segments_agent_created", "agent_id", "created_at"),
    )
