"""
AI 对话日志模型
存储 AI 每次 LLM 完整对话，供管理员和授权用户查看
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, ForeignKey, func,
)
from app.db_providers import json_column
from app.database import Base


class ConversationLogConfig(Base):
    """对话日志全局配置（单行表，id=1）"""
    __tablename__ = "conversation_log_config"

    id = Column(Integer, primary_key=True, default=1)
    # 系统硬上限（所有 AI 保留的最大对话数）
    max_conversation_logs = Column(Integer, default=30)
    # 新用户的默认保留数
    default_user_conversation_logs = Column(Integer, default=20)
    # 全局默认：用户是否可以查看 AI 对话日志
    default_user_log_access = Column(Boolean, default=False)
    # 全局默认：新创建的 AI 是否默认开启延迟回复功能
    default_delay_reply_enabled = Column(Boolean, default=False)
    # 上下文压缩阈值（占上下文窗口百分比，0.0-1.0，默认 0.60）
    compression_threshold = Column(Integer, default=60)  # 存整数 0-100，前端友好
    # 下面两列是「系数旋钮」，可空：**NULL = 用代码默认**（冷阈值系数 1/e、压后目标 20%）
    # 默认值只留在常量里一处，管理员改过的才落库 —— 免得同一个默认值抄两遍
    idle_threshold_percent = Column(Integer, nullable=True)   # T_idle 插值系数（1-99）
    compress_target_percent = Column(Integer, nullable=True)  # T_post = T_hot × 这个比例（1-99）

    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationLog(Base):
    """AI 单次完整对话记录"""
    __tablename__ = "ai_conversation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True, index=True, comment="普通 AI 记录；世界 AI 用量为空（记账走 user_id）")
    # 世界 AI 用量记账：不建占位 agent，直接记 user_id（世界主人）；普通 AI 记录此列为空
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True, comment="记账人（世界 AI 用量 = 世界主人）")
    # 对话发生的上下文：群聊或私信
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    session_id = Column(String(50), nullable=True)  # DM session_id
    conversation_type = Column(String(10), nullable=False, default="group")  # group | dm

    # 完整的 messages 数组（JSONB）
    messages = Column(json_column(), nullable=False)
    # 统计信息
    message_count = Column(Integer, default=0)  # messages 数组长度
    token_usage = Column(json_column(), nullable=True)  # {prompt_tokens, completion_tokens, total_tokens}
    # 是否有实际产出（AI 说了话或调了工具）
    has_output = Column(Boolean, default=False)
    # 使用的模型
    model = Column(String(50), nullable=True)
    # 是否启用了深度推理
    thinking_enabled = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now(), index=True)
