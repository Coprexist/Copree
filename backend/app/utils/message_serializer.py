"""
通用消息序列化器
将 Message(群聊)和 DMMessage(私信)ORM 对象统一转为 dict,
消除 dm_service 和 group_service 中的重复序列化逻辑。

v2.0: 支持统一 Sender 模型
"""
import json

from app.models.sender import Sender


import re


def normalize_attachments(attachments: list | str | None) -> list | None:
    """统一归一化 attachments：Text 列（JSON 字符串）转 list。"""
    if isinstance(attachments, str):
        try:
            return json.loads(attachments)
        except (json.JSONDecodeError, TypeError):
            return None
    return attachments


async def mention_names(db, contents) -> dict[int, str]:
    """把若干段正文里出现过的 <@!id> 一次查成名字。

    只查真正出现过的 id（通常 0~2 个），不为此拉整张成员表；导出时一次查全，别一条一条查。
    """
    from sqlalchemy import select

    from app.models.user import User
    from app.utils.text import iter_mention_ids

    ids = {uid for content in contents for uid in iter_mention_ids(content or "")}
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()
    return {int(uid): str(name or "") for uid, name in rows}


def make_preview(content: str | None, attachments: list | str | None = None, max_len: int = 50) -> str:
    """生成消息预览文本。

    纯附件无文字：
      - 单个 → 图片显示 [图片]，其余显示文件名或 [文件]
      - 多个 → [N个文件]
    有文字 → 截取前 max_len 个字符（去除 HTML 标签）。

    attachments 兼容 list（群聊 JSONB）和 str（私信 Text 列存 JSON）。
    """
    if content:
        plain = re.sub(r'<[^>]+>', '', content)
        preview = plain[:max_len]
        if len(plain) > max_len:
            preview += "..."
        return preview

    atts = normalize_attachments(attachments)
    if not atts:
        return ""

    count = len(atts)
    if count == 1:
        a = atts[0]
        if isinstance(a, dict):
            mime = a.get('mime_type', '') or ''
            if mime.startswith('image/'):
                return "[图片]"
            name = a.get('name', '')
            if name:
                return name[:max_len] + ("..." if len(name) > max_len else "")
        return "[文件]"
    return f"[{count}个文件]"


def serialize_message(message, *,
                      sender_name=None,
                      sender_type=None,
                      sender_avatar_url=None,
                      sender_state=None,
                      conversation_key='group_id',
                      include_read_at=False) -> dict:
    """将消息 ORM 对象序列化为字典。

    兼容 Message (群聊) 和 DMMessage (私信) 两种模型,
    通过 getattr 鸭子类型访问字段,用参数处理模型差异。

    Args:
        message: Message 或 DMMessage ORM 实例
        sender_name: 发送者名称(ORM 字段为 None 时使用)
        sender_type: 发送者类型(ORM 字段为 None 时使用)
        sender_avatar_url: 发送者头像 URL(ORM 字段为 None 时使用)
        conversation_key: 会话 ID 键名,群聊用 'group_id',私信用 'session_id'
        include_read_at: 是否包含 read_at 字段(私信有,群聊无)
    """
    # sender_name: 参数优先于 ORM 字段(联邦消息通过 ORM 字段存储)
    effective_name = sender_name or getattr(message, 'sender_name', None)

    # sender_type: 参数优先于 ORM 字段(DMMessage 无此字段,靠调用方传入)
    effective_type = sender_type or getattr(message, 'sender_type', None)

    # sender_avatar_url: ORM 优先于参数(Message 模型 ORM 存储联邦权威值),空值统一归一到 None
    # 注意:与 sender_name/type 的参数优先策略不同--avatar 在联邦场景下由 _download_remote_avatar
    # 后台下载后写入 DB,DB 值比消息中的临时 URL 更权威。
    effective_avatar = getattr(message, 'sender_avatar_url', None) or sender_avatar_url or None

    # attachments: JSONB 自动反序列化,Text 列需手动 json.loads
    attachments = normalize_attachments(getattr(message, 'attachments', None))

    conversation_value = getattr(message, conversation_key, None)

    # 撤回：正文不再下发（原文只在库里），只把"撤回过"这个事实给前端
    revoked = bool(getattr(message, "revoked_at", None))

    result = {
        "id": message.id,
        conversation_key: conversation_value,
        "sender_type": effective_type,
        "sender_id": message.sender_id,
        "sender_name": effective_name,
        "sender_avatar_url": effective_avatar,
        "content": "" if revoked else message.content,
        "revoked": revoked,
        "reply_to": getattr(message, 'reply_to', None),
        "source_public_id": getattr(message, 'source_public_id', None),
        "via": getattr(message, 'via', None),
        "sender_state": sender_state,
        "attachments": attachments,
        "created_at": str(message.created_at) if message.created_at else None,
    }

    # message_type(DMMessage 有,Message 没有,默认 'normal')
    mt = getattr(message, 'message_type', None)
    if mt:
        result["message_type"] = mt

    if include_read_at:
        m_read_at = getattr(message, 'read_at', None)
        result["read_at"] = str(m_read_at) if m_read_at else None

    return result


def serialize_message_with_sender(message, sender: Sender, *,
                                  sender_state=None,
                                  conversation_key='group_id',
                                  include_read_at=False) -> dict:
    """使用统一 Sender 模型序列化消息。

    Args:
        message: Message 或 DMMessage ORM 实例
        sender: 统一发送者模型
        sender_state: 发送者状态(在线/离线等)
        conversation_key: 会话 ID 键名,群聊用 'group_id',私信用 'session_id'
        include_read_at: 是否包含 read_at 字段
    """
    attachments = normalize_attachments(getattr(message, 'attachments', None))

    conversation_value = getattr(message, conversation_key, None)

    revoked = bool(getattr(message, "revoked_at", None))

    result = {
        "id": message.id,
        conversation_key: conversation_value,
        "sender": sender.to_dict(),
        "content": "" if revoked else message.content,
        "revoked": revoked,
        "reply_to": getattr(message, 'reply_to', None),
        "source_public_id": getattr(message, 'source_public_id', None),
        "via": getattr(message, 'via', None),
        "sender_state": sender_state,
        "attachments": attachments,
        "created_at": str(message.created_at) if message.created_at else None,
    }

    mt = getattr(message, 'message_type', None)
    if mt:
        result["message_type"] = mt

    if include_read_at:
        m_read_at = getattr(message, 'read_at', None)
        result["read_at"] = str(m_read_at) if m_read_at else None

    return result
