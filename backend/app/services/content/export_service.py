"""
聊天记录导出服务
支持 JSON / TXT / HTML 三种格式
"""
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.message import Message
from app.models.user import User
from app.models.agent import Agent
from app.repositories.export_repo import ExportRepository


async def query_all_messages(
    export_repo: ExportRepository,
    group_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    查询群内所有消息，按时间升序排列。
    批量 resolve sender_name 和 reply_to 预览。
    """
    # 基础查询
    stmt = select(Message).where(Message.group_id == group_id)

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            stmt = stmt.where(Message.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            # date_to 是日期如 "2024-01-15"，取当天 23:59:59
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            stmt = stmt.where(Message.created_at <= dt_to)
        except ValueError:
            pass

    stmt = stmt.order_by(Message.created_at.asc())

    result = await export_repo.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        return []

    # 收集所有需要 resolve 的 sender ID
    human_ids = set()
    ai_ids = set()
    reply_to_ids = set()

    for msg in messages:
        if msg.sender_type == "human":
            human_ids.add(msg.sender_id)
        elif msg.sender_type == "ai":
            ai_ids.add(msg.sender_id)
        if msg.reply_to:
            reply_to_ids.add(msg.reply_to)

    # 批量查 sender 名称
    human_names: dict[int, str] = {}
    ai_names: dict[int, str] = {}

    if human_ids:
        r = await export_repo.execute(select(User.id, User.username).where(User.id.in_(human_ids)))
        for uid, uname in r.all():
            human_names[uid] = uname

    if ai_ids:
        r = await export_repo.execute(select(Agent.id, Agent.name).where(Agent.id.in_(ai_ids)))
        for aid, aname in r.all():
            ai_names[aid] = aname

    # 批量查 reply_to 原文（先留着：下面要把 <@!id> 换成名字再截断预览）
    reply_raw: dict[int, str] = {}
    if reply_to_ids:
        r = await export_repo.execute(
            select(Message.id, Message.content).where(Message.id.in_(reply_to_ids))
        )
        for mid, content in r.all():
            reply_raw[mid] = content or ""

    # 导出是**给人看**的：正文里的 <@!id> 换成名字（一次查全，别一条一条查）
    from app.utils.message_serializer import mention_names
    from app.utils.text import render_mention_names

    mention_map = await mention_names(
        export_repo, [m.content or "" for m in messages] + list(reply_raw.values())
    )
    reply_previews: dict[int, str] = {}
    for mid, raw in reply_raw.items():
        rendered = render_mention_names(raw, mention_map)
        reply_previews[mid] = rendered[:80] + ("..." if len(rendered) > 80 else "")

    # 组装结果
    result_list = []
    for msg in messages:
        sender_name = (
            human_names.get(msg.sender_id, f"用户#{msg.sender_id}")
            if msg.sender_type == "human"
            else ai_names.get(msg.sender_id, f"AI#{msg.sender_id}")
        )
        result_list.append({
            "id": msg.id,
            "sender_name": sender_name,
            "sender_type": msg.sender_type,
            "content": render_mention_names(msg.content or "", mention_map),
            "reply_to": msg.reply_to,
            "reply_preview": reply_previews.get(msg.reply_to) if msg.reply_to else None,
            "created_at": str(msg.created_at) if msg.created_at else "",
        })

    return result_list


def format_messages_json(messages: list[dict], group_name: str) -> str:
    """导出为结构化 JSON 格式"""
    import json

    result = {
        "group_name": group_name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def format_messages_txt(messages: list[dict], group_name: str) -> str:
    """导出为人类可读的 TXT 格式"""
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "=" * 72,
        f"群聊: {group_name}",
        f"导出时间: {exported_at}",
        f"消息总数: {len(messages)}",
        "=" * 72,
        "",
    ]

    for msg in messages:
        time_str = msg["created_at"][:19].replace("T", " ") if msg["created_at"] else "?"
        sender_tag = "👤" if msg["sender_type"] == "human" else "🤖"
        lines.append(f"[{time_str}] {sender_tag} {msg['sender_name']}: {msg['content']}")

        if msg.get("reply_preview"):
            lines.append(f"     ↳ 回复: \"{msg['reply_preview']}\"")

        lines.append("")

    return "\n".join(lines)


def format_messages_html(messages: list[dict], group_name: str) -> str:
    """导出为独立 HTML 页面（内嵌 CSS，离线可用）"""
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 生成消息气泡 HTML
    msg_html_parts = []
    for msg in messages:
        time_str = msg["created_at"][:19].replace("T", " ") if msg["created_at"] else "?"
        is_human = msg["sender_type"] == "human"

        bubble_class = "bubble-human" if is_human else "bubble-ai"
        sender_tag = "👤" if is_human else "🤖"
        sender_label = msg["sender_name"]

        reply_html = ""
        if msg.get("reply_preview"):
            reply_html = (
                f'<div class="reply-block">'
                f'↳ 回复: "{msg["reply_preview"]}"'
                f"</div>"
            )

        msg_html_parts.append(
            f'<div class="{bubble_class}">'
            f'<div class="bubble-header">'
            f'<span class="sender-tag">{sender_tag}</span>'
            f'<span class="sender-name">{sender_label}</span>'
            f'<span class="msg-time">{time_str}</span>'
            f"</div>"
            f'{reply_html}'
            f'<div class="bubble-content">{_escape_html(msg["content"])}</div>'
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聊天记录 - {_escape_html(group_name)}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif;
    background: #0f0f0f;
    color: #e0e0e0;
    max-width: 780px;
    margin: 0 auto;
    padding: 24px 16px;
    line-height: 1.6;
  }}
  .header {{
    text-align: center;
    padding: 24px 0 32px;
    border-bottom: 1px solid #2a2a2a;
    margin-bottom: 24px;
  }}
  .header h1 {{ font-size: 1.5rem; color: #f0f0f0; margin-bottom: 4px; }}
  .header p {{ font-size: 0.8rem; color: #888; }}
  .bubble-human, .bubble-ai {{
    margin-bottom: 12px;
    padding: 12px 16px;
    border-radius: 14px;
    max-width: 90%;
  }}
  .bubble-human {{
    background: #1a2740;
    border: 1px solid #253550;
  }}
  .bubble-ai {{
    background: #251a35;
    border: 1px solid #352545;
  }}
  .bubble-header {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
    font-size: 0.78rem;
  }}
  .sender-tag {{ font-size: 0.9rem; }}
  .sender-name {{ font-weight: 600; color: #c0c0c0; }}
  .msg-time {{ font-size: 0.7rem; color: #666; margin-left: auto; }}
  .bubble-content {{
    font-size: 0.92rem;
    white-space: pre-wrap;
    word-break: break-word;
    color: #d0d0d0;
  }}
  .reply-block {{
    font-size: 0.75rem;
    color: #777;
    border-left: 3px solid #444;
    padding-left: 10px;
    margin-bottom: 6px;
  }}
  .footer {{
    text-align: center;
    padding: 24px 0;
    border-top: 1px solid #2a2a2a;
    margin-top: 24px;
    font-size: 0.75rem;
    color: #555;
  }}
  @media print {{
    body {{ background: #fff; color: #222; }}
    .bubble-human {{ background: #e8f0fe; border-color: #c8d8f0; }}
    .bubble-ai {{ background: #f3e8ff; border-color: #d8c8f0; }}
    .bubble-content, .sender-name {{ color: #333; }}
    .header p, .msg-time, .reply-block, .footer {{ color: #888; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>💬 {_escape_html(group_name)}</h1>
  <p>导出时间: {exported_at} · 共 {len(messages)} 条消息</p>
</div>
{"".join(msg_html_parts)}
<div class="footer">
  由 AI 群聊社交网络导出 · Copree
</div>
</body>
</html>"""


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def query_all_dm_messages(
    export_repo: ExportRepository,
    session_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """查询私信会话内所有消息，按时间升序排列"""
    from app.models.dm import DMMessage

    stmt = select(DMMessage).where(DMMessage.session_id == session_id)

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            stmt = stmt.where(DMMessage.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            stmt = stmt.where(DMMessage.created_at <= dt_to)
        except ValueError:
            pass

    stmt = stmt.order_by(DMMessage.created_at.asc())

    result = await export_repo.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        return []

    # 批量查 sender 名称
    sender_ids = {m.sender_id for m in messages}
    sender_names: dict[int, str] = {}
    sender_types: dict[int, str] = {}
    if sender_ids:
        r = await export_repo.execute(select(User.id, User.username, User.type).where(User.id.in_(sender_ids)))
        for uid, uname, utype in r.all():
            sender_names[uid] = uname
            sender_types[uid] = utype or "human"

    # 导出是给人看的：正文里的 <@!id> 换成名字（一次查全）
    from app.utils.message_serializer import mention_names
    from app.utils.text import render_mention_names

    mention_map = await mention_names(export_repo, [m.content or "" for m in messages])

    result_list = []
    for msg in messages:
        result_list.append({
            "id": msg.id,
            "sender_name": sender_names.get(msg.sender_id, f"用户#{msg.sender_id}"),
            "sender_type": sender_types.get(msg.sender_id, "human"),
            "content": render_mention_names(msg.content or "", mention_map),
            "created_at": str(msg.created_at) if msg.created_at else "",
        })

    return result_list


async def export_dm_chat_history(
    export_repo: ExportRepository,
    session_id: str,
    fmt: str = "json",
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[bytes, str, str]:
    """编排：查询私信消息 → 格式化 → 返回 (content_bytes, media_type, filename)"""
    messages = await query_all_dm_messages(export_repo, session_id, date_from, date_to)
    chat_name = f"私信_{session_id}"

    if fmt == "txt":
        content = format_messages_txt(messages, chat_name)
        media_type = "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "html":
        content = format_messages_html(messages, chat_name)
        media_type = "text/html; charset=utf-8"
        ext = "html"
    else:
        content = format_messages_json(messages, chat_name)
        media_type = "application/json; charset=utf-8"
        ext = "json"

    date_label = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"dm_{session_id}_{date_label}.{ext}"

    return content.encode("utf-8"), media_type, filename


async def export_chat_history(
    export_repo: ExportRepository,
    group_id: int,
    fmt: str = "json",
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[bytes, str, str]:
    """
    编排：查询消息 → 格式化 → 返回 (content_bytes, media_type, filename)
    """
    from app.chat.gm import get_group

    group = await get_group(export_repo.session, group_id)
    group_name = group.name if group else f"群聊#{group_id}"

    messages = await query_all_messages(export_repo, group_id, date_from, date_to)

    if fmt == "txt":
        content = format_messages_txt(messages, group_name)
        media_type = "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "html":
        content = format_messages_html(messages, group_name)
        media_type = "text/html; charset=utf-8"
        ext = "html"
    else:  # json
        content = format_messages_json(messages, group_name)
        media_type = "application/json; charset=utf-8"
        ext = "json"

    date_label = datetime.now(timezone.utc).strftime("%Y%m%d")
    # 文件名安全化
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in group_name)[:40]
    filename = f"chat_{safe_name}_{date_label}.{ext}"

    return content.encode("utf-8"), media_type, filename
