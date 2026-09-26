"""群聊上下文：群设置存在与否都必须走同一段账本

2026-09-26 把折叠默认值从 256 换成 FOLD_LIMIT 时，max_len 的赋值被顶出了
`if not vector_accelerated:`，紧接着的 `if max_len is None:` 把后面整段历史同步
吞成了自己的 body：群只要设过展示上限（生产库迁移后每个群都是 2048），AI 收到的
上下文里就一条历史都没有。编译能过、316 个用例全绿——因为在那之前没有任何用例
跑过 build_messages 的群聊分支。这里补上：只断言账本条目真的进了 messages。
"""
import pytest

pytestmark = pytest.mark.anyio

LONG = "长" * 3000


async def _seed(db, display_len):
    """一个群、一个 AI、两条历史；display_len=None 就是「从没设过」"""
    from sqlalchemy import text
    from db_reset import clear

    await clear(db, "pending_messages", "messages", "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"
    ))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "max_msg_display_len) VALUES (59, 'CoExisten', 'human', 1, 'default', true, :dl)"
    ), {"dl": display_len})
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(59, 'ai', 2, 'member'), (59, 'human', 1, 'owner')"
    ))
    await db.execute(text(
        "INSERT INTO messages (id, group_id, sender_type, sender_id, content) VALUES "
        "(1, 59, 'human', 1, '第一句'), (2, 59, 'human', 1, :long)"
    ), {"long": LONG})
    await db.commit()


async def _context_body(db) -> str:
    """真库跑一遍群聊上下文构建，把 messages 的正文拼起来看"""
    from sqlalchemy import select
    from app.ai.llm import build_messages
    from app.models.agent import Agent

    agent = (await db.execute(select(Agent).where(Agent.id == 1))).scalar_one()
    messages = await build_messages(db, agent, 59)
    return "\n".join(m["content"] for m in messages)


async def test_group_with_a_configured_limit_still_gets_its_history(migrated_db):
    from app.database import async_session

    async with async_session() as db:
        await _seed(db, 2048)
        body = await _context_body(db)

    assert "第一句" in body, "群设过展示上限时账本必须照走（回归：整段历史被 if 吞掉）"


async def test_group_without_a_limit_folds_at_the_default(migrated_db):
    from app.database import async_session

    async with async_session() as db:
        await _seed(db, None)
        body = await _context_body(db)

    assert "第一句" in body, "没设过的群也要有历史"
    assert "…（中间省略" in body, "没设过的群按 FOLD_LIMIT 折叠，3000 字那条要折"
