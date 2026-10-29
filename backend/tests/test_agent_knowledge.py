"""AI 知识库：可见、可开关、可改的那一侧（人看界面，AI 看提示词索引 + file_read）

正文落在 AI 的文件空间（data/agents/{id}/knowledge/），元数据落 agent_knowledge；
索引进系统提示时与记忆索引同段、一起走能力版本链冻结。
"""
import shutil

import pytest
from sqlalchemy import text
from app.paths import agent_dir

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999001, 1, 999002


async def _seed(db):
    from db_reset import clear

    # 用例共用同一个 AGENT_ID：只清库不清文件的话，上一轮写下的 manual.md 还在，
    # 这边 add_entry 就会落成 manual-3.md，清单断言随之错位。
    shutil.rmtree(agent_dir(AGENT_ID), ignore_errors=True)
    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '测试AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state) VALUES (:a, :o, '测试AI', :u, 'active')"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


async def test_documents_are_visible_and_listed(migrated_db):
    """加两份文档：清单里看得到标题/摘要/大小，正文写进它自己的文件空间。"""
    from app.database import async_session
    from app.paths import agent_dir
    from app.services.agent.knowledge_service import add_entry, list_entries

    async with async_session() as db:
        await _seed(db)
        first = await add_entry(db, AGENT_ID, title="用户手册", summary="怎么用平台",
                                filename="manual.md", content="# 用户手册\n正文")
        second = await add_entry(db, AGENT_ID, title="排错指南", summary="常见故障",
                                 filename="manual.md", content="# 排错\n正文")
        rows = await list_entries(db, AGENT_ID)

    assert [r["title"] for r in rows] == ["用户手册", "排错指南"]
    assert rows[0]["size"] > 0 and rows[0]["enabled"] is True
    # 同名文件不覆盖：第二份自动加 -2
    assert first["path"] == "manual.md" and second["path"] == "manual-2.md"
    assert (agent_dir(AGENT_ID) / "knowledge" / "manual.md").exists()
    assert (agent_dir(AGENT_ID) / "knowledge" / "manual-2.md").exists()


async def test_index_lists_only_enabled_documents(migrated_db):
    """开关只管"进不进索引"：关掉之后正文还在，能读，但提示词索引里不再出现。"""
    from app.database import async_session
    from app.services.agent.knowledge_service import (
        add_entry, knowledge_index_text, read_entry, update_entry,
    )

    async with async_session() as db:
        await _seed(db)
        entry = await add_entry(db, AGENT_ID, title="群视界 FAQ", summary="世界怎么用",
                                filename="faq.md", content="世界的入口是群")
        before = await knowledge_index_text(db, AGENT_ID)
        await update_entry(db, AGENT_ID, entry["id"], enabled=False)
        after = await knowledge_index_text(db, AGENT_ID)
        full = await read_entry(db, AGENT_ID, entry["id"])

    assert "群视界 FAQ" in before and "file_read" in before
    assert "群视界 FAQ" not in after
    assert full["content"] == "世界的入口是群"


async def test_upload_decodes_text_and_names_itself(migrated_db):
    """上传走的是同一条入库路径：GB18030 的中文文件也解得开，标题缺省取文件名。"""
    from app.database import async_session
    from app.paths import agent_dir
    from app.services.agent.knowledge_service import add_entry_from_upload, read_entry

    async with async_session() as db:
        await _seed(db)
        content = "# 平台说明" + chr(10) + "群视界怎么玩"
        entry = await add_entry_from_upload(
            db, AGENT_ID, filename="平台说明.md", data=content.encode("gb18030"),
        )
        full = await read_entry(db, AGENT_ID, entry["id"])

    assert entry["title"] == "平台说明" and entry["path"] == "平台说明.md"
    assert (agent_dir(AGENT_ID) / "knowledge" / "平台说明.md").exists()
    assert full["content"].startswith("# 平台说明") and "群视界怎么玩" in full["content"]


async def test_upload_rejects_binary_and_oversize(migrated_db):
    """只收文本：二进制（PDF 头）与超 2MB 的文件当场拒绝，不留半个文件。"""
    from app.database import async_session
    from app.services.agent.knowledge_service import add_entry_from_upload, MAX_BYTES

    async with async_session() as db:
        await _seed(db)
        for name, data in (
            ("手册.pdf", b"%PDF-1.7" + bytes([0, 1, 2])),
            ("大文件.md", b"a" * (MAX_BYTES + 1)),
        ):
            raised = False
            try:
                await add_entry_from_upload(db, AGENT_ID, filename=name, data=data)
            except ValueError:
                raised = True
            assert raised, name


async def test_documents_are_registered_files(migrated_db):
    """正文是"登记在册"的普通文件：AI 的 file_list 看得到，删文档时登记行一起消失。"""
    from app.database import async_session
    from app.services.agent.knowledge_service import add_entry, delete_entry
    from app.services.content.file_service import ai_list_files

    async with async_session() as db:
        await _seed(db)
        entry = await add_entry(db, AGENT_ID, title="手册", filename="manual.md", content="正文")
        listed = [f["path"] for f in await ai_list_files(db, AGENT_ID, "knowledge")]
        await delete_entry(db, AGENT_ID, entry["id"])
        after = [f["path"] for f in await ai_list_files(db, AGENT_ID, "knowledge")]

    assert "knowledge/manual.md" in listed
    assert after == []


async def test_move_relocates_file_record_and_guards(migrated_db):
    """移动/改名：文件与记录一起走；只给目录保留原文件名；同名不覆盖；爬不出 knowledge/。"""
    from app.database import async_session
    from app.paths import agent_dir
    from app.services.agent.knowledge_service import add_entry, move_entry

    async with async_session() as db:
        await _seed(db)
        doc_a = await add_entry(db, AGENT_ID, title="甲", filename="a.md", content="甲的正文")
        doc_b = await add_entry(db, AGENT_ID, title="乙", filename="b.md", content="乙的正文")
        moved = await move_entry(db, AGENT_ID, doc_b["id"], "平台/B.md")
        by_dir = await move_entry(db, AGENT_ID, doc_b["id"], "归档")
        clash = await move_entry(db, AGENT_ID, doc_a["id"], "归档/B.md")
        escaped = None
        try:
            await move_entry(db, AGENT_ID, doc_a["id"], "../escape.md")
        except ValueError as e:
            escaped = str(e)

    root = agent_dir(AGENT_ID) / "knowledge"
    assert moved["path"] == "平台/B.md"
    assert by_dir["path"] == "归档/B.md"
    assert clash["path"] == "归档/B-2.md"          # 同名不覆盖，自动 -2
    assert escaped                                  # 越界路径被拒
    assert not (root / "a.md").exists() and not (root / "b.md").exists()
    assert (root / "归档" / "B.md").read_text(encoding="utf-8") == "乙的正文"
    assert (root / "归档" / "B-2.md").read_text(encoding="utf-8") == "甲的正文"


async def test_delete_removes_the_file_too(migrated_db):
    """删文档 = 删元数据 + 删文件；留下孤儿文件比少一条记录更糟。"""
    from app.database import async_session
    from app.paths import agent_dir
    from app.services.agent.knowledge_service import add_entry, delete_entry

    async with async_session() as db:
        await _seed(db)
        entry = await add_entry(db, AGENT_ID, title="临时", filename="tmp.md", content="x")
        path = agent_dir(AGENT_ID) / "knowledge" / entry["path"]
        assert path.exists()
        await delete_entry(db, AGENT_ID, entry["id"])

    assert not path.exists()
