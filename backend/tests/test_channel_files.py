"""通道出站能力：这条通道带不带得了附件

QQ 官方机器人的接口没有发文件这个出口，附件只会留在站内——给 AI 的通道规矩与
send_file 的落点判定读同一份 channel.files_supported，少一处对齐就会"以为发出去了"。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db, *, group_id: int = 64):
    from db_reset import clear
    from app.services.plugin.channel import instance_of

    await clear(db, "plugin_configs", "plugins")
    await db.execute(text(
        "INSERT INTO plugins (id, name, category, enabled) "
        "VALUES ('qq-channel', 'QQ 通道', 'service', true)"))
    await db.execute(text(
        "INSERT INTO plugin_configs (plugin_id, instance, key, value) "
        "VALUES ('qq-channel', :inst, 'copree_group_id', :gid)"),
        {"inst": instance_of(24), "gid": str(group_id)})
    await db.commit()


async def test_declared_channel_without_file_exit(migrated_db):
    from app.database import async_session
    from app.services.plugin.channel import channel_rules, files_supported

    async with async_session() as db:
        await _seed(db)
        assert await files_supported(db, 64) is False          # qq-channel 声明 supports_files=false
        brief = await channel_rules(db, 64)
        assert "发不了文件" in brief, brief


async def test_group_without_channel_can_carry_files(migrated_db):
    """站内群没有通道：附件随便发，也不该多出一段通道规矩"""
    from app.database import async_session
    from app.services.plugin.channel import channel_rules, files_supported

    async with async_session() as db:
        await _seed(db)
        assert await files_supported(db, 999) is True
        assert await channel_rules(db, 999) == ""


async def test_send_file_tells_the_ai_that_qq_will_not_get_it(migrated_db):
    """文件仍落站内（站内看得见），但工具结果必须说清"那边收不到"——

    不说清 AI 就会以为发出去了，然后跟人约"文件发群里了"。
    """
    from sqlalchemy import text

    from app.database import async_session
    from app.tools.chat_social.send_file import SendFile

    async with async_session() as db:
        await _seed(db)
        from db_reset import clear
        await clear(db, "file_metadata", "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '主人', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
            "searchable, auto_approve_join, approve_invites) "
            "VALUES (64, '接了 QQ 的群', 'human', 1, 'default', true, true, true, true)"))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
            "VALUES (24, 1, '值班员', 2, true)"))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) "
            "VALUES (64, 'ai', 2, 'member')"))
        await db.execute(text(
            "INSERT INTO file_metadata (path, owner_type, owner_id, size, mime_type, collaboration_mode) "
            "VALUES ('workspace/a.md', 'ai', 24, 10, 'text/markdown', 'solo')"))
        await db.commit()   # 通道配置由 _seed 建好（qq-channel → 群 64）

        result = await SendFile().execute(
            db, 24, 64, {"file_path": "workspace/a.md", "content": "给你"}, {})
        assert result["success"], result
        assert "1 个文件" in result["message"] and "收不到" in result["message"], result["message"]
