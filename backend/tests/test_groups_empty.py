"""群列表的边界：一个没有任何消息的群，不能把整个列表打成 500

2026-11-23 外部反馈（复现步骤：新建群 → 不发消息 → 刷新聊天页）：
`revoked_name` 的赋值留在 `if last_msg:` 分支里，payload 却无条件用它，
于是列表里只要有一个空群，`GET /groups` 就是 UnboundLocalError。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '主人', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "searchable, auto_approve_join, approve_invites) VALUES "
        "(71, '空群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(71, 'human', 1, 'member')"))
    await db.commit()


async def test_empty_group_does_not_break_list(migrated_db):
    from app.chat.gm import list_user_groups
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        groups = await list_user_groups(db, 1)

        assert len(groups) == 1
        assert groups[0]["last_message_preview"] is None
        assert groups[0]["last_message_revoked_name"] is None


def test_last_message_summary_defaults_without_message():
    """没有最后一条消息：三个字段一起是 None——默认值跟着函数走，不靠调用方记得初始化"""
    from app.chat.gm import last_message_summary

    assert last_message_summary(None, sender_names_map={}, names={}) == (None, None, None)


def test_last_message_summary_marks_revoked_sender():
    """撤回：名字单独给出去（侧栏宽度不够先缩名字），预览是「谁撤回了一条消息」"""
    from types import SimpleNamespace

    from app.chat.gm import last_message_summary

    msg = SimpleNamespace(sender_type="ai", sender_id=41, created_at=None,
                          revoked_at="2026-11-23", content="<@!41> 在吗")
    preview, at, revoked = last_message_summary(
        msg, sender_names_map={("ai", 41): "甲AI"}, names={},
    )
    assert revoked == "甲AI"
    assert preview == "甲AI 撤回了一条消息"
    assert at is None


def test_last_message_summary_previews_normal_message():
    from types import SimpleNamespace

    from app.chat.gm import last_message_summary

    msg = SimpleNamespace(sender_type="human", sender_id=1, created_at=None,
                          revoked_at=None, content="晚上好")
    preview, at, revoked = last_message_summary(
        msg, sender_names_map={("human", 1): "主人"}, names={},
    )
    assert preview == "主人: 晚上好"
    assert revoked is None
    assert at is None
