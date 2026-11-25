"""跨情景经历索引 — AI 在别的会话里干过什么

与记忆表的分工（别混）：

- `rough_memories` 回答「我记不记得这个事实」——内容层，相关性召回，会衰减；
- `agent_experiences` 回答「我在哪儿干过什么」——指针层，只存一行摘要与场景，不做向量、
  不做焦段过滤、不参与衰减。它不替代记忆，也不进「## 记忆索引」。

归属用 `owner_type + owner_id`（与 rough_memories 同一套约定），因为主站 AI 与世界 AI
是两类实体（`agents` / `world_ais`），不共用 id 空间。目前的 owner_type：
- `ai`：主站的 AI（群聊 / 私信 / 插件帧都算它的场景）；
- `world_ai`：群视界的机器人（一个世界一个）。

写入侧由平台兜底（轮末 key_note、场景收口时的工具总账），AI 也可以自己写
（note_experience）；读取侧是投递：进过某个上下文就不再投（`exp:<id>` 那条 ref）。
"""
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)

from app.database import Base
from app.db_providers import json_column

# 一行的摘要上限：这是「指针」不是记录本体，写长了就会有人拿它当原文用
MAX_SUMMARY_CHARS = 400

OWNER_AI = "ai"
OWNER_WORLD_AI = "world_ai"


class AgentExperience(Base):
    __tablename__ = "agent_experiences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_type = Column(String(20), nullable=False, default=OWNER_AI, comment="ai | world_ai")
    owner_id = Column(Integer, nullable=False, comment="agents.id / world_ais.id")
    scene_key = Column(String(64), nullable=False, default="", comment="场景键（会话 / 帧的 context_ref）")
    scene_label = Column(String(100), nullable=False, default="", comment="场景名（群名 / 私信对方 / 世界会话）")
    frame_type = Column(String(20), nullable=False, default="", comment="group_chat | dm | 插件帧类型 ...")
    source = Column(
        String(20), nullable=False, default="key_note",
        comment="key_note=AI 轮末自述 | tool_ledger=场景收口的工具总账 | ai=note_experience",
    )
    summary = Column(Text, nullable=False, comment="一行：场景 + 干了什么（≤400 字）")
    tools = Column(Text, nullable=False, default="", comment="当轮工具总账（可选）")
    refs = Column(json_column(), default=dict, comment="关联：消息 id 范围 / 帧 id")
    started_at = Column(DateTime, nullable=True, comment="这一场的起点（UTC，无时区）")
    ended_at = Column(DateTime, server_default=func.now(), comment="写下这一行的时间（UTC，无时区）")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # 切场景时按 scene_key 取最新几行；也用于判断「这一场有没有写过」
        Index("ix_agent_experiences_scene", "owner_type", "owner_id", "scene_key", "id"),
        # 按语境召回时的默认排序
        Index("ix_agent_experiences_recent", "owner_type", "owner_id", "id"),
    )
