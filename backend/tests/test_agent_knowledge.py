"""AI 知识库：可见、可开关、可改的那一侧（人看界面，AI 看提示词索引 + file_read）

正文落在 AI 的文件空间（data/agents/{id}/knowledge/），元数据落 agent_knowledge；
索引进系统提示时与记忆索引同段、一起走能力版本链冻结。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999001, 1, 999002


async def _seed(db):
    from db_reset import clear

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
