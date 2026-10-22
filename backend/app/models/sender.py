"""
统一发送者模型 — 消息发送者的标准化表示

定义统一的 Sender 数据结构，消除 sender_type + sender_id 的零散处理。
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SenderType(str, Enum):
    HUMAN = "human"
    AI = "ai"
    # 群助手（世界在群里的代言）：独立实体、无账号，sender_id 是 group_assistants.id
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Sender:
    sender_type: SenderType
    sender_id: int
    name: str = ""
    avatar_url: Optional[str] = None
    language: str = "zh"

    @classmethod
    def from_db(cls, sender_type: str, sender_id: int, name: str = "", avatar_url: Optional[str] = None, language: str = "zh") -> "Sender":
        return cls(
            sender_type=SenderType(sender_type),
            sender_id=sender_id,
            name=name,
            avatar_url=avatar_url,
            language=language,
        )

    @classmethod
    def from_message(cls, message) -> "Sender":
        return cls(
            sender_type=SenderType(message.sender_type),
            sender_id=message.sender_id,
            name="",
        )

    def to_dict(self) -> dict:
        return {
            "sender_type": self.sender_type.value,
            "sender_id": self.sender_id,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "language": self.language,
        }

    def is_human(self) -> bool:
        return self.sender_type == SenderType.HUMAN

    def is_ai(self) -> bool:
        return self.sender_type == SenderType.AI

    def is_assistant(self) -> bool:
        return self.sender_type == SenderType.ASSISTANT

    def is_system(self) -> bool:
        return self.sender_type == SenderType.SYSTEM


SYSTEM_SENDER = Sender(
    sender_type=SenderType.SYSTEM,
    sender_id=0,
    name="系统",
)