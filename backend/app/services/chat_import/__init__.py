"""
从聊天记录导入 —— 把别处的对话搬进 Copree。

分层（见 docs/dev/chat_import.md）：

    统一结构 schema.py  ←  解析器 parsers/  ←  （外部 payload，由插件抓来）
            ↓
        导入器 importer.py  →  DM 历史（via='import'）

核心不碰网络：链接解析走插件，插件通过 fetchers.register 把能力挂过来。
没装插件时，粘贴 / 上传文件两条路照常可用。
"""
from app.services.chat_import.schema import AttachmentRef, Origin, Transcript, Turn

__all__ = ["AttachmentRef", "Origin", "Transcript", "Turn"]
