"""撤回回收（§5.6）：撤回不当场删字节，只把「什么时候可以删」定下来

为什么放弃当场删：撤回后消息不显示，但同一份文件可能还被别人的消息引用（转发）；删盘还
要能失败重试、要能凑批。于是撤回事务只做两件事——清消息快照、定 reclaim_after（全平台
唯一的一条删除时钟，§3.5）——真正的删除交给每分钟扫一次的回收器。
"""
import os
import tempfile
from datetime import datetime, timezone

from sqlalchemy import text

HUMAN, AI_USER, AI_AGENT, GROUP = 1, 40, 24, 64
PLAIN_FILE, FORWARDED_FILE = 900, 901


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_references", "file_metadata", "messages",
                "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:human, '群主', 'x', 'human'), (:ai, '化学老师账号', 'x', 'ai')"
    ), {"human": HUMAN, "ai": AI_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(:a, :owner, '化学老师', :ai_user, true)"
    ), {"a": AI_AGENT, "owner": HUMAN, "ai_user": AI_USER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '撤回测试群', 'human', :owner, 'default', true)"
    ), {"g": GROUP, "owner": HUMAN})
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(:g, 'human', :owner, 'owner'), (:g, 'ai', :ai_user, 'member')"
    ), {"g": GROUP, "owner": HUMAN, "ai_user": AI_USER})
    for fid, name in ((PLAIN_FILE, "pic.png"), (FORWARDED_FILE, "doc.png")):
        await db.execute(text(
            # physical_key 非空＝字节确实在这条路径上：回收器据此才去删盘（§5.7）
            "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type,"
            " collaboration_mode, physical_key) "
            "VALUES (:id, :path, 'human', :owner, 5, 'image/png', 'solo', 'i:0:0')"
        ), {"id": fid, "path": f"users/1/chat/{name}", "owner": HUMAN})
    for mid, fid, name in ((1358, PLAIN_FILE, "pic.png"), (1359, FORWARDED_FILE, "doc.png")):
        await db.execute(text(
            "INSERT INTO messages (id, group_id, sender_type, sender_id, content, attachments) "
            "VALUES (:mid, :g, 'human', :owner, '看这个', CAST(:att AS jsonb))"
        ), {"mid": mid, "g": GROUP, "owner": HUMAN,
            "att": f'[{{"file_id": {fid}, "path": "users/1/chat/{name}", "name": "{name}"}}]'})
    await db.commit()


def _with_data_dir():
    from app.config import settings

    data_dir = tempfile.mkdtemp(prefix="revoke-")
    original = settings.data_dir
    settings.data_dir = data_dir
    for name in ("pic.png", "doc.png"):
        path = os.path.join(data_dir, "users/1/chat", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"bytes")
    return data_dir, original


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def test_revoke_clears_the_snapshot_and_queues_the_bytes(migrated_db):
    """撤回：快照清空、文件排进一分钟后的回收，消息本身仍留着（审计要看得到）"""
    from app.chat.revoke import revoke_group_message
    from app.config import settings
    from app.database import async_session
    from app.models.file import FileMetadata
    from app.models.message import Message

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            message = await db.get(Message, 1358)
            result = await revoke_group_message(db, message, actor_id=HUMAN)
            await db.commit()
            await db.refresh(message)
            meta = await db.get(FileMetadata, PLAIN_FILE)

            assert result["revoked"] is True
            assert result["attachments_queued"] == 1, result
            assert message.attachments is None, "撤回的消息不该再带着附件快照"
            assert message.revoked_at is not None
            assert meta.reclaim_after is not None, "没被别人认领的附件要排进回收"
            delay = (meta.reclaim_after - _now()).total_seconds()
            assert 30 <= delay <= 90, delay
    finally:
        settings.data_dir = original


async def test_revoke_keeps_bytes_that_someone_else_forwarded(migrated_db):
    """被转发过的文件不排回收：别人的新消息还指着同一份字节"""
    from app.chat.revoke import revoke_group_message
    from app.config import settings
    from app.database import async_session
    from app.models.file import FileMetadata
    from app.models.message import Message

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            # AI 把这份文件转发到自己的会话：file_references 里落一行 forward
            await db.execute(text(
                "INSERT INTO file_references (file_id, referrer_type, referrer_id, ref_type) "
                "VALUES (:fid, 'ai', :aid, 'forward')"
            ), {"fid": FORWARDED_FILE, "aid": AI_AGENT})
            await db.commit()

            message = await db.get(Message, 1359)
            result = await revoke_group_message(db, message, actor_id=HUMAN)
            await db.commit()
            meta = await db.get(FileMetadata, FORWARDED_FILE)

            assert result["attachments_queued"] == 0, result
            assert meta.reclaim_after is None, "有人认领过的字节不能排进回收"
            assert os.path.exists(os.path.join(data_dir, "users/1/chat/doc.png"))
    finally:
        settings.data_dir = original


async def test_the_sweeper_takes_queued_revoke_bytes_away(migrated_db):
    """到点之后由回收器连字节带行删掉（撤回自己不动盘）"""
    from app.chat.revoke import revoke_group_message
    from app.config import settings
    from app.database import async_session
    from app.models.message import Message
    from app.services.content.file_service import reclaim_unsent_attachments

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            message = await db.get(Message, 1358)
            await revoke_group_message(db, message, actor_id=HUMAN)
            await db.commit()

            assert os.path.exists(os.path.join(data_dir, "users/1/chat/pic.png")), \
                "撤回当场不该删字节（删盘交给回收器）"

            # 把删除时刻拨到过去，回收器这一轮就该带走它
            await db.execute(text(
                "UPDATE file_metadata SET reclaim_after = now() - interval '1 second' WHERE id = :fid"
            ), {"fid": PLAIN_FILE})
            await db.commit()
            removed = await reclaim_unsent_attachments(db)
            await db.commit()

            assert removed == 1, removed
            assert not os.path.exists(os.path.join(data_dir, "users/1/chat/pic.png"))
            assert (await db.execute(text(
                "SELECT count(*) FROM file_metadata WHERE id = :fid"
            ), {"fid": PLAIN_FILE})).scalar_one() == 0
    finally:
        settings.data_dir = original
