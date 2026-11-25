"""存储空间的口径：路径即归属（§3.1、§5.7、§7）

几件事一起测，因为它们只服务同一个问题：**这个数字到底代表谁的多少字节**。
- 一个文件在哪一栏，由它躺在哪个目录决定（users/{uid}/files 或 users/{uid}/chat）；
- 用量必须按 physical_key 去重：同一范围里一份字节被登记成两行，不能算两次；
- 跨账户的硬链接各算各的（AI 抓进 inbox 的那份在 AI 的账里），见 measure_usage 注释；
- 只算 physical_key 非空的行：同路径被覆盖写掉的历史版本（字节已不在）不能继续计费；
- 「收进我的文件」是硬链接：同一份字节同时出现在两栏，但不占两份配额；
- 收文件一律经 _adopt_into：撞名让后缀（绝不覆盖已有字节）、文稿与时长跟着走一份快照；
- /fs/list 按目录分栏：AI 的文件属于 AI，不会混进人的列表。
"""
import os
import tempfile

from sqlalchemy import text

HUMAN, AI_AGENT, AI_USER, GROUP = 1, 49, 120, 5


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
    # 显式 id 不推进序列：水位挪开，免得同一次运行里别的用例撞主键
    await db.execute(text("SELECT setval(pg_get_serial_sequence('file_metadata', 'id'), 100)"))
    await db.commit()


async def _add_file(db, data_dir, *, rel, owner_type, owner_id, size,
                    physical_key="auto", hardlink_from=None, reclaim_after=None):
    """造一份「物理文件 + 元数据行」

    physical_key="auto" 时对着磁盘 stat 出真键；hardlink_from 给一个已存在的相对路径，
    就 os.link 过去（模拟「收进我的文件」与 AI 的 file_fetch）。
    """
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if hardlink_from:
        if not os.path.exists(path):
            os.link(os.path.join(data_dir, hardlink_from), path)
    else:
        with open(path, "wb") as f:
            f.write(b"x" * size)
    if physical_key == "auto":
        from app.services.content.file_service import physical_key_of
        physical_key = physical_key_of(rel)
    fid = (await db.execute(text(
        "INSERT INTO file_metadata (path, owner_type, owner_id, size, mime_type,"
        " collaboration_mode, physical_key, reclaim_after)"
        " VALUES (:path, :ot, :oid, :size, 'text/plain', 'solo', :pk, :reclaim) RETURNING id"
    ), {"path": rel, "ot": owner_type, "oid": owner_id, "size": size,
        "pk": physical_key, "reclaim": reclaim_after})).scalar_one()
    await db.commit()
    return fid


def _with_data_dir():
    data_dir = tempfile.mkdtemp(prefix="metering-")
    from app.config import settings
    original = settings.data_dir
    settings.data_dir = data_dir
    return data_dir, original


async def test_same_scope_hardlink_group_is_counted_once(migrated_db):
    """同一范围里同一份字节登记成两行：只算一次"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import measure_usage, user_chat_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            first = await _add_file(db, data_dir, rel="users/1/chat/qq/shared.bin",
                                    owner_type="human", owner_id=HUMAN, size=1000)
            key = (await db.execute(text(
                "SELECT physical_key FROM file_metadata WHERE id = :id"
            ), {"id": first})).scalar_one()
            await _add_file(db, data_dir, rel="users/1/chat/qq/shared-copy.bin",
                            owner_type="human", owner_id=HUMAN, size=1000,
                            physical_key=key)

            usage = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            assert usage[HUMAN] == {"bytes": 1000, "files": 1}
    finally:
        settings.data_dir = original


async def test_cross_account_hardlink_counts_in_each_account(migrated_db):
    """跨账户硬链接各算各的：原件在人的对话目录，AI 抓的副本在 AI 自己的账里"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import measure_usage, user_chat_dir, user_space_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/chat/qq/voice.silk",
                            owner_type="human", owner_id=HUMAN, size=500)
            await _add_file(db, data_dir, rel=f"agents/{AI_AGENT}/inbox/voice.silk",
                            owner_type="ai", owner_id=AI_AGENT, size=500,
                            hardlink_from="users/1/chat/qq/voice.silk")

            mine = await measure_usage(db, "human", [HUMAN], path_prefix=user_space_dir(HUMAN))
            produced = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            agent = await measure_usage(db, "ai", [AI_AGENT])
            assert mine == {}, "发出去的语音不占「我的文件」"
            assert produced[HUMAN] == {"bytes": 500, "files": 1}
            assert agent[AI_AGENT] == {"bytes": 500, "files": 1}
    finally:
        settings.data_dir = original


async def test_only_rows_whose_bytes_are_on_disk_are_counted(migrated_db):
    """physical_key 为空 = 字节已不在盘上（历史版本 / 路径已缺失），不计用量"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import measure_usage, user_chat_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/chat/qq/live.bin", owner_type="human",
                            owner_id=HUMAN, size=100)
            # 同一路径的旧版本：physical_key 留空，代表它的字节已被覆盖写掉
            await _add_file(db, data_dir, rel="users/1/chat/qq/live.bin", owner_type="human",
                            owner_id=HUMAN, size=999, physical_key=None)

            usage = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            assert usage[HUMAN] == {"bytes": 100, "files": 1}
    finally:
        settings.data_dir = original


async def test_space_and_chat_are_separate_buckets(migrated_db):
    """「我的文件」与「我在对话里产生的」各算各的：配额只认前者（§3.1、§6.1）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import measure_usage, user_space_dir, user_chat_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/files/mine.bin", owner_type="human",
                            owner_id=HUMAN, size=100)
            await _add_file(db, data_dir, rel="users/1/chat/qq/sent.bin", owner_type="human",
                            owner_id=HUMAN, size=200)

            mine = await measure_usage(db, "human", [HUMAN], path_prefix=user_space_dir(HUMAN))
            produced = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            assert mine[HUMAN] == {"bytes": 100, "files": 1}
            assert produced[HUMAN] == {"bytes": 200, "files": 1}
    finally:
        settings.data_dir = original


async def test_starring_into_my_files_counts_once(migrated_db):
    """收进我的文件 = 硬链接：两栏都看得见，但配额只多一份（§7.3）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import measure_usage, user_space_dir, user_chat_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/chat/qq/kept.png", owner_type="human",
                            owner_id=HUMAN, size=300)
            await _add_file(db, data_dir, rel="users/1/files/kept.png", owner_type="human",
                            owner_id=HUMAN, size=300,
                            hardlink_from="users/1/chat/qq/kept.png")

            mine = await measure_usage(db, "human", [HUMAN], path_prefix=user_space_dir(HUMAN))
            produced = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            assert mine[HUMAN] == {"bytes": 300, "files": 1}
            assert produced[HUMAN] == {"bytes": 300, "files": 1}
    finally:
        settings.data_dir = original


async def test_fs_list_split_by_space(migrated_db):
    """人类的两栏按目录切片：AI 的文件不混进来，历史版本不再出现"""
    from app.database import async_session
    from app.config import settings
    from app.routers.files import list_directory
    from app.services.content.file_service import list_files

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/files/mine.bin", owner_type="human",
                            owner_id=HUMAN, size=10)
            # 同一路径的旧版本（字节已被覆盖）：列表里不该再出现一次
            await _add_file(db, data_dir, rel="users/1/files/mine.bin", owner_type="human",
                            owner_id=HUMAN, size=999, physical_key=None)
            await _add_file(db, data_dir, rel="users/1/chat/qq/sent.bin", owner_type="human",
                            owner_id=HUMAN, size=20)
            await _add_file(db, data_dir, rel=f"agents/{AI_AGENT}/work.md", owner_type="ai",
                            owner_id=AI_AGENT, size=30)

            visible = await list_directory(scope="mine",
                                           current_user={"user_id": HUMAN}, db=db)
            assert {f["path"] for f in visible} == {"users/1/files/mine.bin"}

            produced = await list_directory(scope="produced",
                                            current_user={"user_id": HUMAN}, db=db)
            assert {f["path"] for f in produced} == {"users/1/chat/qq/sent.bin"}

            # AI 自己的 file_list 仍看得见自己工作区的文件，否则发完就"消失"了
            seen = await list_files(db, "agents", "ai", AI_AGENT)
            paths = {f["path"] for f in seen}
            assert f"agents/{AI_AGENT}/work.md" in paths
    finally:
        settings.data_dir = original


async def test_viewed_scope_keeps_cleaned_entries(migrated_db):
    """浏览过的：只列别人的文件；已被清理的条目仍在，但标成打不开"""
    from app.database import async_session
    from app.config import settings
    from app.routers.files import list_directory

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            live = await _add_file(db, data_dir, rel="users/2/chat/qq/peer-live.png",
                                   owner_type="human", owner_id=2, size=300)
            gone = await _add_file(db, data_dir, rel="users/2/chat/qq/peer-gone.png",
                                   owner_type="human", owner_id=2, size=400,
                                   physical_key=None)
            own = await _add_file(db, data_dir, rel="users/1/files/mine.bin",
                                  owner_type="human", owner_id=HUMAN, size=10)
            for fid in (live, gone, own):
                await db.execute(text(
                    "INSERT INTO file_references (file_id, referrer_type, referrer_id, ref_type)"
                    " VALUES (:f, 'human', :u, 'read')"
                ), {"f": fid, "u": HUMAN})
            await db.commit()

            viewed = await list_directory(scope="viewed",
                                          current_user={"user_id": HUMAN}, db=db)
            assert {v["id"] for v in viewed} == {live, gone}, "自己的文件不算「浏览」"
            assert {v["id"]: v["is_available"] for v in viewed} == {live: True, gone: False}
    finally:
        settings.data_dir = original


def test_target_of_maps_attachments_to_owner_dirs():
    """迁移口径（纯函数）：attachments/ 下的行各回各家，非个人空间的不动"""
    from scripts.migrate_owner_paths import target_of

    assert target_of("human", 7, "attachments/qq/a.png") == "users/7/chat/qq/a.png"
    assert target_of("ai", 9, "attachments/voice/b.mp3") == "agents/9/voice/b.mp3"
    assert target_of("ai", 9, "attachments/c.md") == "agents/9/chat/c.md"
    # 已经在自己的目录里、或不是个人空间的文件（表情包）搬不动
    assert target_of("human", 7, "users/7/files/d.bin") is None
    assert target_of("human", 7, "agents/9/e.bin") is None
    assert target_of("system", 0, "emoji/f.png") is None

async def test_quota_refuses_write_into_my_files(migrated_db):
    """「我的文件」装不下就拒写（§6.1）：删点旧的可以，平台不替你删"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import (
        StorageQuotaExceeded, upload_file, user_space_dir,
    )

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("UPDATE users SET file_quota_mb = 1 WHERE id = :u"), {"u": HUMAN})
            await db.commit()

            ok = await upload_file(db, user_space_dir(HUMAN), "small.txt", b"x" * 400_000,
                                   "text/plain", "human", HUMAN)
            assert ok.id
            try:
                await upload_file(db, user_space_dir(HUMAN), "big.txt", b"y" * 800_000,
                                  "text/plain", "human", HUMAN)
                raise AssertionError("超配额必须抛 StorageQuotaExceeded")
            except StorageQuotaExceeded as e:
                assert e.quota == 1024 * 1024
                assert e.used == 400_000
    finally:
        settings.data_dir = original


async def test_quota_does_not_watch_conversation_attachments(migrated_db):
    """配额只管「我的文件」：对话里产生的附件不占它（§3.1）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import upload_file, user_chat_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("UPDATE users SET file_quota_mb = 1 WHERE id = :u"), {"u": HUMAN})
            await db.commit()

            meta = await upload_file(db, user_chat_dir(HUMAN), "voice.mp3", b"z" * 2_000_000,
                                     "audio/mpeg", "human", HUMAN)
            assert meta.path == "users/1/chat/voice.mp3"
    finally:
        settings.data_dir = original


async def test_quota_replacing_same_name_deducts_the_old_bytes(migrated_db):
    """同名覆盖是替换不是新增：老字节先减掉，别把「替换」误判成「写第二份」"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import upload_file, user_space_dir

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("UPDATE users SET file_quota_mb = 1 WHERE id = :u"), {"u": HUMAN})
            await db.commit()

            await upload_file(db, user_space_dir(HUMAN), "a.txt", b"a" * 600_000,
                              "text/plain", "human", HUMAN)
            # 换内容再传同名、**显式要求覆盖**：总量仍是 600 KB，装得下
            again = await upload_file(db, user_space_dir(HUMAN), "a.txt", b"b" * 600_000,
                                      "text/plain", "human", HUMAN, overwrite=True)
            assert again.path == "users/1/files/a.txt"
    finally:
        settings.data_dir = original

async def test_upload_stamps_unsent_deadline_only_in_chat(migrated_db):
    """上传时只有「我在对话里产生的」那一栏带回收时刻；发出即清空（§3.5）"""
    from datetime import datetime, timezone
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import (
        track_attachments, upload_file, user_chat_dir, user_space_dir,
    )

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            chat = await upload_file(db, user_chat_dir(HUMAN), "draft.png", b"x" * 10,
                                    "image/png", "human", HUMAN)
            space = await upload_file(db, user_space_dir(HUMAN), "kept.png", b"y" * 10,
                                     "image/png", "human", HUMAN)
            assert chat.reclaim_after is not None
            assert space.reclaim_after is None, "我的文件不该有回收时刻"
            hours = (chat.reclaim_after - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600
            assert 23 < hours <= 24, hours

            await track_attachments(db, [{"file_id": chat.id}], "human", HUMAN)
            await db.refresh(chat)
            assert chat.reclaim_after is None, "发出去了就不归 24 小时回收管"
            assert chat.archived_to_message_at is not None
    finally:
        settings.data_dir = original


async def test_reclaim_removes_unsent_and_spares_the_rest(migrated_db):
    """到点的没发送附件连字节带行删掉；我的文件、已发出的、还没到点的都不动（§3.5）"""
    from datetime import datetime, timedelta, timezone
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import reclaim_unsent_attachments, disk_path

    data_dir, original = _with_data_dir()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    try:
        async with async_session() as db:
            await _seed(db)
            due = await _add_file(db, data_dir, rel="users/1/chat/due.png", owner_type="human",
                                 owner_id=HUMAN, size=100, reclaim_after=past)
            later = await _add_file(db, data_dir, rel="users/1/chat/later.png", owner_type="human",
                                    owner_id=HUMAN, size=200, reclaim_after=future)
            await _add_file(db, data_dir, rel="users/1/files/kept.png", owner_type="human",
                            owner_id=HUMAN, size=300)
            import os as _os
            assert _os.path.exists(disk_path("users/1/chat/due.png"))

            removed = await reclaim_unsent_attachments(db)
            await db.commit()

            assert removed == 1
            assert not _os.path.exists(disk_path("users/1/chat/due.png")), "字节也要走"
            left = {r[0] for r in (await db.execute(text("SELECT id FROM file_metadata"))).all()}
            assert due not in left
            assert {later} <= left
    finally:
        settings.data_dir = original


async def test_reclaim_spares_bytes_shared_with_another_owner(migrated_db):
    """硬链接：回收人的那份只摘掉一个目录项，AI 收进 inbox 的字节还在（§5.7）"""
    from datetime import datetime, timedelta, timezone
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import reclaim_unsent_attachments, disk_path
    import os as _os

    data_dir, original = _with_data_dir()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    try:
        async with async_session() as db:
            await _seed(db)
            await _add_file(db, data_dir, rel="users/1/chat/qq/shared.gif", owner_type="human",
                            owner_id=HUMAN, size=400, reclaim_after=past)
            await _add_file(db, data_dir, rel=f"agents/{AI_AGENT}/inbox/shared.gif",
                            owner_type="ai", owner_id=AI_AGENT, size=400,
                            hardlink_from="users/1/chat/qq/shared.gif")

            assert await reclaim_unsent_attachments(db) == 1
            await db.commit()
            kept = disk_path(f"agents/{AI_AGENT}/inbox/shared.gif")
            # 别的 owner 指着同一份字节，回收不能把它带走
            assert _os.path.exists(kept)
            assert _os.path.getsize(kept) == 400
    finally:
        settings.data_dir = original

async def test_keep_hardlinks_into_my_files(migrated_db):
    """收进我的文件：硬链接一份、占一格配额、原件不动（§7.3）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import (
        keep_to_my_files, measure_usage, user_chat_dir, user_space_dir,
    )

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            src = await _add_file(db, data_dir, rel="users/1/chat/photo.png",
                                 owner_type="human", owner_id=HUMAN, size=500)

            kept = await keep_to_my_files(db, HUMAN, src)
            assert kept["already"] is False
            assert kept["path"] == "users/1/files/photo.png"
            assert kept["linked"] is True, "同一卷上必须是硬链接，不能复制"

            space = await measure_usage(db, "human", [HUMAN], path_prefix=user_space_dir(HUMAN))
            chat = await measure_usage(db, "human", [HUMAN], path_prefix=user_chat_dir(HUMAN))
            assert space[HUMAN] == {"bytes": 500, "files": 1}
            assert chat[HUMAN] == {"bytes": 500, "files": 1}

            # 幂等：再点一次还是同一行，不许多一份配额
            again = await keep_to_my_files(db, HUMAN, src)
            assert again["already"] is True
            assert again["linked"] is None, "这一次没建链接，不能回报 True"
            assert again["file_id"] == kept["file_id"]
            space2 = await measure_usage(db, "human", [HUMAN], path_prefix=user_space_dir(HUMAN))
            assert space2[HUMAN] == space[HUMAN]

            # 两份路径指向同一个 inode：清掉对话那份，我的文件里字节还在
            from app.services.content.file_service import physical_key_of
            assert physical_key_of("users/1/files/photo.png") == physical_key_of("users/1/chat/photo.png")
    finally:
        settings.data_dir = original


async def test_keep_refuses_when_quota_is_full(migrated_db):
    """配额满了就收不进来（413 那条路的业务侧）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import (
        StorageQuotaExceeded, keep_to_my_files, upload_file, user_chat_dir,
    )

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("UPDATE users SET file_quota_mb = 1 WHERE id = :u"), {"u": HUMAN})
            await db.commit()
            big = await upload_file(db, user_chat_dir(HUMAN), "big.mp4", b"q" * 2_000_000,
                                  "video/mp4", "human", HUMAN)
            try:
                await keep_to_my_files(db, HUMAN, big.id)
                raise AssertionError("配额不够时必须拒绝")
            except StorageQuotaExceeded:
                pass
    finally:
        settings.data_dir = original


async def test_keep_brings_the_transcript_along(migrated_db):
    """收进来的语音在「我的文件」里点开还有文字：文稿与时长跟着内容走一份快照"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import keep_to_my_files

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            src = await _add_file(db, data_dir, rel="users/1/chat/voice-1.silk",
                                  owner_type="human", owner_id=HUMAN, size=800)
            await db.execute(text(
                "UPDATE file_metadata SET transcript = :t, duration_ms = 4200 WHERE id = :i"
            ), {"t": "第一句，第二句", "i": src})
            await db.commit()

            kept = await keep_to_my_files(db, HUMAN, src)
            await db.commit()
            row = (await db.execute(text(
                "SELECT transcript, duration_ms FROM file_metadata WHERE id = :i"
            ), {"i": kept["file_id"]})).one()

        assert row[0] == "第一句，第二句", row
        assert row[1] == 4200, row
    finally:
        settings.data_dir = original


async def test_adopt_never_overwrites_a_name_already_on_disk(migrated_db):
    """目标目录里已有同名文件（哪怕没登记元数据）时让后缀：绝不覆盖已有字节"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import keep_to_my_files

    data_dir, original = _with_data_dir()
    try:
        async with async_session() as db:
            await _seed(db)
            # 我自己的空间里先躺着一份同名文件，但没有元数据行（迁移前的遗留就是这种形状）
            squat = os.path.join(data_dir, "users/1/files/photo.png")
            os.makedirs(os.path.dirname(squat), exist_ok=True)
            with open(squat, "wb") as f:
                f.write(b"old-bytes")

            src = await _add_file(db, data_dir, rel="users/1/chat/photo.png",
                                  owner_type="human", owner_id=HUMAN, size=9)
            kept = await keep_to_my_files(db, HUMAN, src)
            await db.commit()

            with open(squat, "rb") as f:
                assert f.read() == b"old-bytes", "已经存在的字节被覆盖了"
        assert kept["path"] == f"users/1/files/photo.{src}.png", kept
    finally:
        settings.data_dir = original
