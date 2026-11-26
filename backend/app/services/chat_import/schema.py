"""
统一结构（transcript）—— 导入侧唯一的契约。

只描述「谁在什么时候说了什么」，不含灵魂档案（配置 / 记忆 / 知识库）：
那是 Copree 到 Copree 的完整搬运，有自己的 v2.0 格式，硬套对话序列只会丢字段。

约定：
- role 只有 user / assistant 两个值，说话人名字只用于展示；
- at 可空——拿不到时间就不编；
- attachments[].available=False 表示只有元数据，没有原文件（界面显示「无原文件」）；
- 解析器不认的字段进 warnings，不静默丢。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["user", "assistant"]

# 一条消息的正文上限：不是给正常对话设限，是拦住把整个文件当一段塞进来的情况。
MAX_CONTENT_CHARS = 200_000
# 一次导入的条数上限：再多应该分批，而不是一次请求卡死。
MAX_MESSAGES = 20_000


class Origin(BaseModel):
    """这段对话从哪来。写进 chat_imports，也是将来「一键撤销」的依据。"""

    platform: str = "unknown"
    source_url: str | None = None
    parser: str = "unknown"
    parser_version: int = 1
    fetched_at: datetime | None = None


class AttachmentRef(BaseModel):
    """一个附件。available=False 表示我们只有元数据。"""

    name: str | None = None
    size: int | None = None
    mime_type: str | None = None
    type: str = "file"          # image / audio / file
    available: bool = True
    reason: str | None = None   # no_source / too_large / fetch_failed
    # 已经落到站内文件时才有
    file_id: int | None = None
    path: str | None = None


class Turn(BaseModel):
    role: Role
    content: str = ""
    at: datetime | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _clamp(cls, v: str) -> str:
        if len(v) > MAX_CONTENT_CHARS:
            return v[:MAX_CONTENT_CHARS]
        return v


class Transcript(BaseModel):
    version: int = 1
    origin: Origin = Field(default_factory=Origin)
    title: str | None = None
    participants: dict[str, str] = Field(default_factory=dict)  # {user: "我", assistant: "DeepSeek"}
    messages: list[Turn]
    warnings: list[str] = Field(default_factory=list)
    # 「初步摘要注入」的正文：导入时写进账本的一条 kind="summary"，
    # 位置在开场之后、导入的消息之前——AI 一开口就知道「我们之前在别处聊过」。
    # 将来真压缩时会被新算出的摘要顶掉（rewrite_context 只留摘要 + 尾部），就是「释放」。
    # 空 = 不注入，不写一条没内容的摘要占位。
    summary: str | None = None

    @field_validator("messages")
    @classmethod
    def _non_empty(cls, v: list[Turn]) -> list[Turn]:
        if not v:
            raise ValueError("这段对话里没有可导入的消息")
        if len(v) > MAX_MESSAGES:
            raise ValueError(f"一次最多导入 {MAX_MESSAGES} 条消息，请分批")
        return v

    def suggested_summary(self) -> str:
        """给「初步摘要」起一句话的草稿——不调模型，只用已知的事实。

        为什么不在这里调用模型：智能整理的配置校验（池额度 / 用户默认配置）是另一批事，
        还没定；把摘要生成挂在那儿之前，先给一份**一定可用**的草稿，让用户改一句话就够了。
        模型摘要将来接上时，只是换掉这个草稿的来源，落点不变。
        """
        total = len(self.messages)
        if not total:
            return ""
        who = self.participants or {}
        theirs = who.get("assistant") or "它"
        mine = who.get("user") or "你"

        when = ""
        times = sorted(m.at for m in self.messages if m.at)
        if times:
            span = times[0].strftime("%Y-%m-%d")
            if times[-1].strftime("%Y-%m-%d") != span:
                span += " 到 " + times[-1].strftime("%Y-%m-%d")
            when = span + " "

        first = next((m.content.strip().replace("\n", " ") for m in self.messages
                      if m.role == "user" and m.content.strip()), "")
        if len(first) > 40:
            first = first[:40] + "…"

        platform = {"deepseek": "DeepSeek", "pasted": "别处"}.get(self.origin.platform, "别处")
        head = f"{when}我们在{platform}聊过一段（共 {total} 条，{mine} {sum(1 for m in self.messages if m.role == 'user')} 条、"
        head += f"{theirs} {sum(1 for m in self.messages if m.role == 'assistant')} 条）"
        if first:
            head += f"，从「{first}」说起"
        head += "。完整记录已经导入这段私信，具体细节可以翻聊天记录。"
        return head

    def stats(self) -> dict:
        """给前端预览用的一行统计。"""
        files = [a for m in self.messages for a in m.attachments]
        missing = [a for a in files if not a.available]
        times = [m.at for m in self.messages if m.at]
        return {
            "messages": len(self.messages),
            "user_messages": sum(1 for m in self.messages if m.role == "user"),
            "assistant_messages": sum(1 for m in self.messages if m.role == "assistant"),
            "attachments": len(files),
            "missing_attachments": len(missing),
            "first_at": min(times).isoformat() if times else None,
            "last_at": max(times).isoformat() if times else None,
        }
