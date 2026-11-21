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
    from app.utils.display_name import display_names
    from app.utils.text import iter_mention_ids

    ids = {uid for content in contents for uid in iter_mention_ids(content or "")}
    if not ids:
        return {}
    return await display_names(db, ids)


# 撤回的会话预览：与账本占位（history.revoked_text）同一语义，但列表要短。
# 一律写"谁撤回了一条消息"（完整名字，不截）：私信也一样——不再分"你/对方"，
# 少一种特判就少一处跟消息里的灰色胶囊对不齐的机会。
REVOKED_PREVIEW = "撤回了一条消息"


def revoked_preview(sender_name: str | None = None) -> str:
    """撤回那条在会话列表里怎么写：有名字就带上，没有就退回中性说法，别猜。"""
    return f"{sender_name} {REVOKED_PREVIEW}" if sender_name else REVOKED_PREVIEW


def voice_label(duration_ms: int | None) -> str:
    """语音在列表里的写法：[语音] / [语音 12"] / [语音 1'42"]

    时长缺失（老消息的快照里没有、或探测失败）就退回不带时长的写法，不显示 0'00"。
    """
    if not duration_ms or duration_ms <= 0:
        return "[语音]"
    total = int(round(duration_ms / 1000))
    if total < 60:
        return f'[语音 {total}"]'
    return f"[语音 {total // 60}'{total % 60:02d}]"


def make_preview(content: str | None, attachments: list | str | None = None, max_len: int = 50) -> str:
    """生成消息预览文本。

    纯附件无文字：
      - 单个 → 图片 [图片]、语音 [语音]，其余显示文件名或 [文件]
      - 多个 → [N个文件]
    有文字 → 截取前 max_len 个字符（去除 HTML 标签）；彩色标签与 Markdown 先在
    text_preview 里脱掉，这里只做截断与附件占位。

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
            # 语音给占位而不是文件名：voice_20261114104640_1.mp3 在列表里既长又没信息
            if mime.startswith('audio/'):
                return voice_label(a.get('duration_ms'))
            name = a.get('name', '')
            if name:
                return name[:max_len] + ("..." if len(name) > max_len else "")
        return "[文件]"
    return f"[{count}个文件]"


def text_preview(
    content: str | None,
    attachments: list | str | None = None,
    *,
    names: dict[int, str] | None = None,
    max_len: int = 50,
    keep_url: bool = True,
) -> str:
    """一条消息 → 给人看的一行：@ 令牌换名字 → 去 Markdown 与彩色标签 → 截断。

    会话列表与站内浮窗共用这一份拼法（浮窗传 keep_url=False 省地方）：
    两处各写一套，就会出现「列表里露着 [blue]、浮窗里是干净的」这种对不齐。
    [blue]…[/blue] 是站内自定义文法，不脱掉的话侧栏直接显示字面量。
    """
    from app.utils.text import plainify_markdown, render_mention_names

    plain = plainify_markdown(render_mention_names(content or "", names or {}), keep_url=keep_url)
    return make_preview(plain, attachments, max_len=max_len)


def preview_of(message, names: dict[int, str] | None = None, max_len: int = 50) -> str:
    """会话列表那一行预览：撤回的只留占位，不把原文透出去。

    撤回是「这条没了」，原文只留在库里（审计/排障）。消息渲染按 revoked_at 走占位，
    列表预览也得走同一个口径——否则撤回后侧栏还挂着原话，刷新也甩不掉。
    """
    if getattr(message, "revoked_at", None):
        return REVOKED_PREVIEW
    return text_preview(
        getattr(message, "content", None),
        getattr(message, "attachments", None),
        names=names,
        max_len=max_len,
    )


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
