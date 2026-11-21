"""附件生命周期四列：物理键、归档时间、最近引用、可改性

四列一起测的理由：它们只服务同一件事——"这个文件现在归谁、还算不算在用、清掉能不能真的省盘"。
单独看任何一列都读不出它们为什么必须同时存在：
- 没有 physical_key，一份硬链接进来的字节会被算成两份用量，清掉原件也释放不了磁盘；
- 没有 archived_to_message_at，发出去的附件永远留在「我的文件」里，删也删不动；
- 没有 last_referenced_at，三年前上传、昨天被转发的文件会被当成最老的先清掉。

设计：docs/dev/message_storage_and_cleanup.md §3.2、§3.3、§5.2、§5.7
"""
import json
import os
import tempfile

from sqlalchemy import text

AI_AGENT, AI_USER, HUMAN, GROUP = 49, 120, 1, 5


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_references", "file_metadata", "messages", "dm_messages", "dm_sessions",
               "group_members", "groups", "users", "agents")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:human, '主人', 'x', 'human'), (:ai, '鲸鱼娘账号', 'x', 'ai')"
    ), {"human": HUMAN, "ai": AI_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(:a, :owner, '鲸鱼娘', :ai_user, true)"
    ), {"a": AI_AGENT, "owner": HUMAN, "ai_user": AI_USER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '测试群', 'human', :owner, 'default', true)"
    ), {"g": GROUP, "owner": HUMAN})
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(:g, 'human', :owner, 'owner'), (:g, 'ai', :ai_user, 'member')"
    ), {"g": GROUP, "owner": HUMAN, "ai_user": AI_USER})
    await db.commit()
    # 显式 id 不推进序列：水位挪开，免得同一次运行里别的用例上传附件时撞主键
    await db.execute(text("SELECT setval(pg_get_serial_sequence('file_metadata', 'id'), 100)"))
    await db.commit()


async def _human_file_with_message(db, *, name: str, body: bytes, data_dir: str) -> int:
    """造一份"人发的、附在群消息上"的文件（物理文件 + 元数据 + 消息）"""
    rel = f"attachments/qq/{name}"
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    from app.services.content.file_service import physical_key_of

    fid = (await db.execute(text(
        "INSERT INTO file_metadata (path, owner_type, owner_id, size, mime_type, collaboration_mode,"
        " physical_key) VALUES (:path, 'human', :owner, :size, :mime, 'solo', :key) RETURNING id"
    ), {"path": rel, "owner": HUMAN, "size": len(body), "mime": "text/plain",
        "key": physical_key_of(rel)})).scalar_one()
    await db.execute(text(
        "INSERT INTO messages (group_id, sender_type, sender_id, content, attachments) "
        "VALUES (:g, 'human', :owner, '[文件]', CAST(:att AS JSONB))"
    ), {"g": GROUP, "owner": HUMAN,
        "att": json.dumps([{"file_id": fid, "path": rel, "name": name}])})
    await db.commit()
    return fid


async def test_fetched_attachment_shares_physical_key(migrated_db):
    """硬链接进来的附件与新原件同键：两行元数据、一份字节"""
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="lifecycle-key-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            fid = await _human_file_with_message(db, name="笔记.txt", body="同一份字节".encode(),
                                                 data_dir=data_dir)
            fetched = await file_service.ai_fetch_attachment(db, AI_AGENT, fid)
            await db.commit()

            src_key = (await db.execute(text(
                "SELECT physical_key FROM file_metadata WHERE id = :id"
            ), {"id": fid})).scalar_one()
            dst_key = (await db.execute(text(
                "SELECT physical_key FROM file_metadata WHERE id = :id"
            ), {"id": fetched["file_id"]})).scalar_one()

        assert src_key and src_key == dst_key, "硬链接的两行必须是同一个物理键，否则一份文件被算两次"
        async with async_session() as db:
            rows, keys = (await db.execute(text(
                "SELECT count(*), count(DISTINCT physical_key) FROM file_metadata"
            ))).one()
        assert rows == 2 and keys == 1
    finally:
        settings.data_dir = original


async def test_message_archives_attachment_and_keeps_first_archive_time(migrated_db):
    """发出即归档；再发一次只刷新"最近引用"，归档时间记的是第一次被带走"""
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="lifecycle-arch-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            meta = await file_service.upload_file(
                db, "attachments/qq", "图.png", b"bytes-here", "image/png", "human", HUMAN)
            await db.commit()
            fid = int(meta.id)
            assert meta.archived_to_message_at is None, "刚上传还没发出去，不该是已归档"

        async with async_session() as db:
            await file_service.track_attachments(db, [{"file_id": fid}], "human", HUMAN)
            await db.commit()
            first = (await db.execute(text(
                "SELECT archived_to_message_at, last_referenced_at FROM file_metadata WHERE id = :id"
            ), {"id": fid})).one()
            assert first[0] is not None, "发出去了就该归档：它归消息，不再算发送者的文件"

        # 隔一个事务再引用一次（func.now() 是事务起点，同一事务里两次取到的是同一个值）
        async with async_session() as db:
            await file_service.track_attachments(db, [{"file_id": fid}], "human", HUMAN)
            await db.commit()
            second = (await db.execute(text(
                "SELECT archived_to_message_at, last_referenced_at FROM file_metadata WHERE id = :id"
            ), {"id": fid})).one()

        assert second[0] == first[0], "归档时间记的是第一次被带走的那一刻，重发不刷新"
        assert second[1] > first[1], "重发必须让文件重新变'新'，否则它会被当成最老的先清掉"
    finally:
        settings.data_dir = original


async def test_attachments_given_as_json_string_are_tracked(migrated_db):
    """私信的 attachments 是 Text 列（JSON 字符串）：入口收口后照样记账"""
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="lifecycle-str-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            meta = await file_service.upload_file(
                db, "attachments/qq", "语音.amr", b"amr-bytes", "audio/amr", "human", HUMAN)
            await db.commit()
            fid = int(meta.id)

        async with async_session() as db:
            await file_service.track_attachments(
                db, json.dumps([{"file_id": fid, "name": "语音.amr"}]), "human", HUMAN)
            await db.commit()
            archived = (await db.execute(text(
                "SELECT archived_to_message_at FROM file_metadata WHERE id = :id"
            ), {"id": fid})).scalar_one()
        assert archived is not None, "字符串形式的附件漏了记账，等于私信发的文件永不归档"
    finally:
        settings.data_dir = original

async def test_overwriting_a_path_transfers_the_physical_key(migrated_db):
    """同一路径覆盖出新版本：旧行必须交出物理键

    覆盖是原地截断，inode 不变——不清旧行的话，同一个路径的历史版本会全都指向同一份新字节，
    用量把一份文件算成 N 份，清理时又因为"同组还有活行"而一个字节都不释放。
    """
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="lifecycle-overwrite-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            first = await file_service.upload_file(
                db, "attachments/qq", "图.png", b"version-one", "image/png", "human", HUMAN)
            first_id = int(first.id)
            await db.commit()

        async with async_session() as db:
            # 同名同路径、内容不同 → 新建一行（内容相同的话会走去重分支，不建新行）
            second = await file_service.upload_file(
                db, "attachments/qq", "图.png", b"version-two-longer", "image/png", "human", HUMAN)
            second_id = int(second.id)
            await db.commit()
            assert second_id != first_id

        async with async_session() as db:
            keys = dict((await db.execute(text(
                "SELECT id, physical_key FROM file_metadata WHERE id IN (:a, :b)"
            ), {"a": first_id, "b": second_id})).all())
        assert keys[first_id] is None, "旧版本的字节已经被覆盖掉了，不该再持有物理键"
        assert keys[second_id], "当前这一行必须持有物理键"
    finally:
        settings.data_dir = original
