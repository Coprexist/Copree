"""附件收进 AI 文件空间：群聊/私信都能收、任何文件类型都能收、且不复制字节（硬链接）

为什么要有这条工具：AI 在上下文里"看得见"附件（多模态/文本注入按路径直接读，不看归属），
但那份文件属于发它的人；文件工具只在自己的空间里工作。想留下/转发/二次加工就显式收一次——
这是平台侧的能力，与哪条通道（QQ / NapCat / 站内）无关。
"""
import os
import tempfile

from sqlalchemy import text

AI_AGENT, OTHER_AGENT = 49, 29
AI_USER, HUMAN = 120, 1
GROUP = 5


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_references", "file_metadata", "messages", "dm_messages", "dm_sessions",
               "group_members", "groups", "users", "agents")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:human, '主人', 'x', 'human'), (:ai, '鲸鱼娘账号', 'x', 'ai'), (7, '路人', 'x', 'human')"
    ), {"human": HUMAN, "ai": AI_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(:a, :owner, '鲸鱼娘', :ai_user, true), (:b, 7, '别人家的', 7, true)"
    ), {"a": AI_AGENT, "b": OTHER_AGENT, "owner": HUMAN, "ai_user": AI_USER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '测试群', 'human', :owner, 'default', true)"
    ), {"g": GROUP, "owner": HUMAN})
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(:g, 'human', :owner, 'owner'), (:g, 'ai', :ai_user, 'member')"
    ), {"g": GROUP, "owner": HUMAN, "ai_user": AI_USER})
    await db.commit()
    # 显式 id 不会推进序列：把水位挪开，免得同一次运行里别的用例上传附件时撞主键
    await db.execute(text(
        "SELECT setval(pg_get_serial_sequence('file_metadata', 'id'), 100)"
    ))
    await db.commit()


async def _group_file(db, *, file_id: int, name: str, mime: str, body: bytes, data_dir: str):
    """造一份"附在群消息上、属于别人"的附件（物理文件 + 元数据 + 消息）"""
    rel = f"attachments/qq/{name}"
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    await db.execute(text(
        "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type, collaboration_mode) "
        "VALUES (:id, :path, 'human', :owner, :size, :mime, 'solo')"
    ), {"id": file_id, "path": rel, "owner": HUMAN, "size": len(body), "mime": mime})
    await db.execute(text(
        "INSERT INTO messages (id, group_id, sender_type, sender_id, content, attachments) "
        "VALUES (:mid, :g, 'human', :owner, '[文件]', CAST(:att AS JSONB))"
    ), {"mid": 900 + file_id, "g": GROUP, "owner": HUMAN,
        "att": f'[{{"file_id": {file_id}, "path": "{rel}", "name": "{name}", "mime_type": "{mime}"}}]'})
    await db.commit()
    return rel


async def test_group_attachment_is_fetched_as_a_hardlink(migrated_db):
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="fetch-group-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            rel = await _group_file(db, file_id=51, name="笔记.txt", mime="text/plain",
                                    body="第一行\n第二行".encode(), data_dir=data_dir)
            fetched = await file_service.ai_fetch_attachment(db, AI_AGENT, 51)
            await db.commit()
            content = await file_service.ai_read_file(db, AI_AGENT, fetched["path"])
            meta = await db.get(file_service.FileMetadata, fetched["file_id"])

        assert fetched["path"] == "inbox/笔记.txt", fetched
        assert meta.owner_type == "ai" and meta.owner_id == AI_AGENT
        assert content.startswith("第一行")
        src = os.path.join(data_dir, rel)
        dst = os.path.join(data_dir, meta.path)
        assert os.stat(src).st_ino == os.stat(dst).st_ino, "同一份物理文件（硬链接），不是第二份磁盘"
    finally:
        settings.data_dir = original


async def test_dm_attachment_is_fetched_too(migrated_db):
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="fetch-dm-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            rel = f"attachments/qq/猫.png"
            path = os.path.join(data_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"png-bytes")
            await db.execute(text(
                "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type, collaboration_mode) "
                "VALUES (52, :path, 'human', :owner, 9, 'image/png', 'solo')"
            ), {"path": rel, "owner": HUMAN})
            session_id = f"{HUMAN}_{AI_USER}"
            await db.execute(text(
                "INSERT INTO dm_sessions (session_id, user1_id, user2_id) VALUES (:sid, :a, :b)"
            ), {"sid": session_id, "a": HUMAN, "b": AI_USER})
            await db.execute(text(
                "INSERT INTO dm_messages (session_id, sender_id, content, attachments, message_type) "
                "VALUES (:sid, :sender, '[图片]', :att, 'normal')"
            ), {"sid": session_id, "sender": HUMAN, "att": f'[{{"file_id": 52, "path": "{rel}", "name": "猫.png", "mime_type": "image/png"}}]'})
            await db.commit()

            fetched = await file_service.ai_fetch_attachment(db, AI_AGENT, 52)
            await db.commit()

        assert fetched["path"] == "inbox/猫.png", fetched
        assert os.path.isfile(os.path.join(data_dir, "agents", str(AI_AGENT), "inbox", "猫.png"))
    finally:
        settings.data_dir = original


async def _group_files(db, *, items, data_dir):
    """一条消息挂多个附件（file_id, name, mime, body）——编号场景要的就是这个"""
    import json

    atts = []
    for fid, name, mime, body in items:
        rel = f"attachments/qq/{name}"
        path = os.path.join(data_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(body)
        await db.execute(text(
            "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type, collaboration_mode) "
            "VALUES (:id, :path, 'human', :owner, :size, :mime, 'solo')"
        ), {"id": fid, "path": rel, "owner": HUMAN, "size": len(body), "mime": mime})
        atts.append({"file_id": fid, "path": rel, "name": name, "size": len(body), "mime_type": mime})
    await db.execute(text(
        "INSERT INTO messages (id, group_id, sender_type, sender_id, content, attachments) "
        "VALUES (:mid, :g, 'human', :owner, '[文件]', CAST(:att AS JSONB))"
    ), {"mid": 990, "g": GROUP, "owner": HUMAN, "att": json.dumps(atts)})
    await db.commit()


async def test_fetch_by_indexes_takes_the_named_one(migrated_db):
    """[图片 2] 就是 indexes=[2]：编号必须落在同一张图上。"""
    from app.config import settings
    from app.database import async_session
    from app.tools.file_operations.file_fetch import FileFetch

    data_dir = tempfile.mkdtemp(prefix="fetch-index-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            await _group_files(db, data_dir=data_dir, items=[
                (61, "第一张.png", "image/png", b"one"),
                (62, "第二张.png", "image/png", b"two"),
            ])
            result = await FileFetch().execute(db, AI_AGENT, GROUP, {"indexes": [2]}, {})
            await db.commit()

        assert result["count"] == 1, result
        assert result["fetched"][0]["path"] == "inbox/第二张.png", result
    finally:
        settings.data_dir = original


async def test_fetch_without_arguments_takes_the_whole_message(migrated_db):
    """不传参数 = 收整条消息的全部附件（旧实现只收第一个，那是漏收）。"""
    from app.config import settings
    from app.database import async_session
    from app.tools.file_operations.file_fetch import FileFetch

    data_dir = tempfile.mkdtemp(prefix="fetch-all-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            await _group_files(db, data_dir=data_dir, items=[
                (63, "甲.txt", "text/plain", b"a"),
                (64, "乙.txt", "text/plain", b"b"),
            ])
            result = await FileFetch().execute(db, AI_AGENT, GROUP, {}, {})
            await db.commit()

        assert result["count"] == 2, result
        assert [f["path"] for f in result["fetched"]] == ["inbox/甲.txt", "inbox/乙.txt"]
    finally:
        settings.data_dir = original


async def test_missing_index_says_which_ones_exist(migrated_db):
    """缺号要报清楚，否则模型会反复猜编号。"""
    from app.config import settings
    from app.database import async_session
    from app.tools.file_operations.file_fetch import FileFetch

    data_dir = tempfile.mkdtemp(prefix="fetch-missing-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            await _group_files(db, data_dir=data_dir, items=[
                (65, "唯一.txt", "text/plain", b"x"),
            ])
            result = await FileFetch().execute(db, AI_AGENT, GROUP, {"indexes": [2]}, {})

        assert result["error"] is True
        assert "2" in result["message"] and "1" in result["message"], result
    finally:
        settings.data_dir = original


async def test_other_ais_attachment_is_refused(migrated_db):
    from app.config import settings
    from app.database import async_session
    from app.services.content import file_service

    data_dir = tempfile.mkdtemp(prefix="fetch-deny-")
    original = settings.data_dir
    settings.data_dir = data_dir
    try:
        async with async_session() as db:
            await _seed(db)
            await _group_file(db, file_id=53, name="别人的.txt", mime="text/plain",
                              body=b"x", data_dir=data_dir)
            try:
                await file_service.ai_fetch_attachment(db, OTHER_AGENT, 53)
            except ValueError:
                pass
            else:
                raise AssertionError("不在那个群的 AI 不该收得到这份附件")
    finally:
        settings.data_dir = original
