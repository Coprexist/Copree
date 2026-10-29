"""文件访问权限：AI 文件归属按 agent.id 解析，虚拟账号 id 撞车不能炸

check_file_access 曾用 `agents.id == owner_id OR agents.user_id == owner_id` 一次
取归属 AI。agents.user_id 是 AI 自己的虚拟用户账号，数值和另一个 agent 的 id 撞上
就同时命中两行，scalar_one_or_none() 抛 MultipleResultsFound——文件预览与下载整片
500（线上实例：agent 29 的虚拟账号是 user 49，文件归属 agent 49 时两条都命中）。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agent_collaborators", "file_metadata", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (2, '合作者', 'x', 'human'), "
        "(49, '路人', 'x', 'human'), (120, '鲸鱼娘账号', 'x', 'ai')"))
    # agent 49 属于 user 1，虚拟账号是 user 120；agent 29 的虚拟账号恰好是 user 49
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(49, 1, '鲸鱼娘', 120, true), (29, 49, '1号', 49, true)"))
    await db.execute(text(
        "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type, collaboration_mode) VALUES "
        "(1, 'agents/49/shu_ai/graph_ai.py', 'ai', 49, 9569, 'text/x-python', 'solo'), "
        # 早期数据：owner_id 存的是 AI 的虚拟账号 id，而不是 agent.id
        "(2, 'agents/49/shu_ai/README.md', 'ai', 120, 2714, 'text/markdown', 'solo')"))
    await db.commit()


async def test_owner_reads_ai_file_when_agent_id_collides_with_another_virtual_account(migrated_db):
    from app.database import async_session
    from app.services.content.file_service import check_file_access

    async with async_session() as db:
        await _seed(db)
        metadata = await check_file_access(db, 1, "human", 1)
        assert metadata is not None and metadata.id == 1


async def test_unrelated_user_is_denied_not_crashed(migrated_db):
    """user 49 是 agent 29 的主人，与 agent 49 的文件无关：判拒绝，不是抛异常"""
    from app.database import async_session
    from app.services.content.file_service import check_file_access

    async with async_session() as db:
        await _seed(db)
        assert await check_file_access(db, 1, "human", 49) is None


async def test_legacy_owner_id_stored_as_virtual_account_id(migrated_db):
    from app.database import async_session
    from app.services.content.file_service import check_file_access

    async with async_session() as db:
        await _seed(db)
        metadata = await check_file_access(db, 2, "human", 1)
        assert metadata is not None and metadata.id == 2


async def test_agent_collaborator_is_matched_by_agent_id(migrated_db):
    from app.database import async_session
    from app.services.content.file_service import check_file_access

    async with async_session() as db:
        await _seed(db)
        await db.execute(text(
            "INSERT INTO agent_collaborators (agent_id, user_id, can_edit) "
            "VALUES (49, 2, true)"))
        await db.commit()
        metadata = await check_file_access(db, 1, "human", 2)
        assert metadata is not None and metadata.id == 1
