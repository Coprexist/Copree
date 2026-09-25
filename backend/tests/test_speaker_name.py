"""AI 上下文里的说话人名字不能是字面 "None"（用户 2026-09-25 实测）

症状：QQ 群里 AI 回「@None 在的在的，看到你了…」。
根因：本地消息的 sender_name 列是空的（网页端靠 sender_id 自己查名字），
      而 AI 的历史消息渲染直接取这个空值 → 上下文里说话人叫字面 "None"，
      模型就照着群里的 @ 习惯把一个叫 None 的人 @ 了出来。
"""
from sqlalchemy import text


async def test_format_message_never_renders_none():
    from app.utils.pure.prompting import format_message

    line = format_message(
        {"time": "Shanghai 09-25 11:04", "speaker_name": None, "speaker_id": 90,
         "content": "@化学老师 我是我"},
        "化学老师",
    )
    assert "None" not in line, line
    assert "未知（id=90）" in line

    assert "None" not in format_message({"speaker_name": None, "content": "x"}, "化学老师")


async def test_resolve_speaker_names_fills_local_messages(migrated_db):
    from app.ai.llm import _resolve_speaker_names
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "messages", "group_members", "groups", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, 'ShuAICFR', 'x', 'human'), (40, '化学老师', 'x', 'ai'), (90, 'QQ用户6682BD', 'x', 'external')"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (64, '化学老师少宇群', 'human', 1, 'default', true)"
        ))
        # 本地消息不写 sender_name（只有联邦消息才写），坑就是从这里来的
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content) VALUES "
            "(64, 'human', 90, '@化学老师 我是我'), (64, 'ai', 40, '你好')"
        ))
        await db.commit()

        rows = list((await db.execute(text(
            "SELECT sender_type, sender_id, sender_name, content FROM messages ORDER BY id"
        ))).all())
        names = await _resolve_speaker_names(db, rows)

        assert names[("human", 90)] == "QQ用户6682BD"
        assert names[("ai", 40)] == "化学老师"
        assert all(v and v != "None" for v in names.values())
