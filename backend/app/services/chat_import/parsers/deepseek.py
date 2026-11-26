"""
DeepSeek —— 两种离线格式：

1. 分享页的第一方 JSON（插件抓来的就是这个，也是 /api/v0/share/content 的 data.biz_data）
2. 我们自己的导出结构 {origin, meta, transcript}（用户手上可能已经有这份文件）

两者都只是「一个消息数组 + 父子引用」，解析方式相同：沿 parent/child 还原主分支
（分享里可能留着重新生成产生的分叉，只取最后一条子链——那才是页面上看到的那条）。

附件一律只记元数据：分享里的原图要过平台审核才给下载，我们**不伪装**去取。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.chat_import.parsers import Parser, register
from app.services.chat_import.schema import AttachmentRef, Origin, Transcript, Turn


def _ts(value) -> datetime | None:
    """DeepSeek 的 inserted_at 是秒级时间戳。"""
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _is_share(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("messages"), list) and any(
        isinstance(m, dict) and ("fragments" in m or "parent_id" in m) for m in doc["messages"][:5]
    )


def _is_export(doc) -> bool:
    return isinstance(doc, dict) and isinstance(doc.get("transcript"), list)


def detect(payload: str, filename: str | None = None) -> float:
    text = payload.lstrip()
    if not text.startswith("{"):
        return 0.0
    try:
        doc = json.loads(payload)
    except Exception:
        return 0.0
    if isinstance(doc, dict) and isinstance(doc.get("data"), dict):
        doc = doc["data"].get("biz_data") or doc["data"]
    if _is_share(doc):
        return 0.98
    if _is_export(doc):
        return 0.9
    return 0.0


def _branch(msgs: list[dict]) -> list[dict]:
    """沿「每个 parent 的最后一条子节点」还原主分支。"""
    by_id = {m.get("message_id", i): m for i, m in enumerate(msgs)}
    children: dict = {}
    for m in msgs:
        children.setdefault(m.get("parent_id"), []).append(m.get("message_id", id(m)))
    roots = [m.get("message_id", i) for i, m in enumerate(msgs) if not m.get("parent_id")]
    out: list[dict] = []
    for root in roots:
        cur = root
        while cur is not None:
            m = by_id.get(cur)
            if m is not None:
                out.append(m)
            kids = children.get(cur) or []
            cur = kids[-1] if kids else None
    return out


def _turn_from_fragments(m: dict) -> Turn:
    text: list[str] = []
    files: list[AttachmentRef] = []
    for f in m.get("fragments") or []:
        kind = f.get("type")
        content = (f.get("content") or "").strip()
        if kind in ("REQUEST", "RESPONSE") and content:
            text.append(content)
        elif kind == "FILE":
            for fl in f.get("files") or []:
                name = fl.get("file_name") or "附件"
                files.append(AttachmentRef(
                    name=name,
                    size=fl.get("file_size"),
                    mime_type=fl.get("content_type") or None,
                    type="image" if (fl.get("content_type") or "").startswith("image/") else "file",
                    available=False,
                    reason="no_source",
                ))
    return Turn(
        role="user" if m.get("role") == "USER" else "assistant",
        content="\n\n".join(text),
        at=_ts(m.get("inserted_at")),
        attachments=files,
    )


def _parse_share(doc: dict, source_url: str | None) -> Transcript:
    msgs = doc.get("messages") or []
    turns = [_turn_from_fragments(m) for m in _branch(msgs)]
    turns = [t for t in turns if t.content or t.attachments]
    missing = sum(1 for t in turns for a in t.attachments if not a.available)
    warnings = []
    if missing:
        warnings.append(f"{missing} 个附件在分享里拿不到原文件，只保留文件名")
    for t in turns:
        if not t.content and t.attachments:
            t.content = "（只有附件，正文为空）"
    return Transcript(
        origin=Origin(platform="deepseek", parser="deepseek.share", parser_version=1,
                      source_url=source_url),
        title=doc.get("title"),
        participants={"user": "用户", "assistant": "DeepSeek"},
        messages=turns,
        warnings=warnings,
    )


_ATT_FIELDS = set(AttachmentRef.model_fields)


def _attachment(raw) -> AttachmentRef:
    """导出的 files 里可能带我们统一结构没有的字段（如 kind / signed_path）：
    只取认得的，其余忽略——不因为多一个字段就整份导入失败。"""
    if isinstance(raw, AttachmentRef):
        return raw
    data = {k: v for k, v in (raw or {}).items() if k in _ATT_FIELDS}
    data.setdefault("available", False)
    data.setdefault("reason", "no_source")
    return AttachmentRef(**data)


def _parse_export(doc: dict) -> Transcript:
    turns = []
    for item in doc.get("transcript") or []:
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        at = item.get("at")
        turns.append(Turn(
            role=role,
            content=item.get("text") or "",
            at=datetime.fromisoformat(at).replace(tzinfo=None) if at else None,
            attachments=[_attachment(a) for a in (item.get("files") or [])],
        ))
    return Transcript(
        origin=Origin(platform="deepseek", parser="deepseek.export", parser_version=1,
                      source_url=(doc.get("origin") or {}).get("url")),
        title=(doc.get("meta") or {}).get("title"),
        participants={"user": "用户", "assistant": "DeepSeek"},
        messages=[t for t in turns if t.content or t.attachments],
    )


def parse(payload: str, filename: str | None = None) -> Transcript:
    doc = json.loads(payload)
    if isinstance(doc, dict) and isinstance(doc.get("data"), dict):
        url = None
        doc = {"title": doc["data"].get("biz_data", {}).get("title"),
               **doc["data"].get("biz_data", {})}
    if _is_export(doc):
        return _parse_export(doc)
    return _parse_share(doc, None)


register(Parser(name="deepseek", platform="DeepSeek", version=1,
                parse=parse, detect=detect))
