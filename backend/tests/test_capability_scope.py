"""能力版本进度的作用域：通知按状态各记一份，生效按状态各锁一份

病根（修前）：告知进度 known 挂 agent 级，谁先构建提示词谁把版本推平——别的状态再也收不到那条
变更；记忆索引正文又冻结在 effective 快照上，于是那个状态里"AI 自己写下的规则"连正文都不存在。

钉住三件事：
1. 一条作用域=全部的变更，**每个状态各收一次**（这才是"实现 agent 的全部通知"）；
2. 生效（effective）按状态各锁一份：一个状态 compact 不许改别的状态的前缀字节；
3. 版本作用域不覆盖我 → 不发内容，但要与我最新的版本"结清"（否则每轮重扫）。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

SRC = "test-scope-src"


def _defs(*names: str) -> list:
    return [{"type": "function", "function": {"name": n, "description": ""}} for n in names]


async def _repo(db):
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    return SQLAlchemyCapabilityRepository(db)


async def _reset(db, *sources: str):
    for s in sources:
        await db.execute(text("DELETE FROM capability_versions WHERE source = :s"), {"s": s})
    await db.commit()


async def test_every_state_gets_its_own_notice(migrated_db):
    """作用域=全部 → 两个状态各收一次；同一个状态不重复收

    首见只看**变更**：起点版本（v1）没有 changelog，"从无到有"不是变更（内容是它前缀里
    本来就有的东西）。所以这里先造出一次真实变更（v2），两个状态才各有一条可收的。
    """
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)

        fresh = {}
        assert await build_change_notice(repo, fresh, [SRC], state="group:0") is None, \
            "只有起点版本时没有可告知的变更（内容就在它眼前的前缀里）"
        assert fresh["cap_known_versions"] == {"group:0|" + SRC: 1}, "但要与最新结清，免得每轮重扫"

        await ensure_source_version(repo, SRC, _defs("t1", "t2"), "测试源", scope=SCOPE_ALL)  # v2

        a, b = {}, {}
        assert await build_change_notice(repo, a, [SRC], state="group:1") is not None
        assert await build_change_notice(repo, b, [SRC], state="group:2") is not None, \
            "另一个状态也必须收到——修前它会被先构建的那个状态替它勾掉"

        assert a["cap_known_versions"] == {"group:1|" + SRC: 2}
        assert b["cap_known_versions"] == {"group:2|" + SRC: 2}, "两个状态各记一份键"
        assert await build_change_notice(repo, a, [SRC], state="group:1") is None, "同一状态不重复发"


async def test_legacy_flat_key_is_adopted_once_per_state(migrated_db):
    """迁移口径：旧的 agent 级键作为每个状态的**起点**继承，不重放历史全量 changelog"""
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t2"), "测试源", scope=SCOPE_ALL)  # 到 v2

        legacy = {"cap_known_versions": {SRC: 2}}
        assert await build_change_notice(repo, legacy, [SRC], state="group:1") is None, \
            "旧键已是最新 → 不该补发历史通知"

        await ensure_source_version(repo, SRC, _defs("t3"), "测试源", scope=SCOPE_ALL)  # v3
        holder = {"cap_known_versions": {SRC: 2}}
        notice = await build_change_notice(repo, holder, [SRC], state="group:2")
        assert notice is not None and "v2→v3" in notice, "新变更仍要送到每个状态"
        assert "v0→" not in notice, "不能把 v1/v2 的历史全量重放一遍"


async def test_scope_outside_state_settles_without_content(migrated_db):
    """作用域不覆盖我 → 不发内容，但我要与该版本结清（否则每轮重扫）"""
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t2"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t3"), "测试源", scope="group:9")

        other = {"cap_known_versions": {SRC: 2}}
        assert await build_change_notice(repo, other, [SRC], state="group:1") is None, \
            "v3 只发给 group:9，不该发给 group:1"
        # 旧键留着当别的状态的起点，本状态自己的键推到最新
        assert other["cap_known_versions"]["group:1|" + SRC] == 3, \
            "已知进度要推到最新，避免每轮重扫同一版本"


async def test_effective_is_locked_per_state(migrated_db):
    """一个状态 compact 只换它自己的前缀字节，别的状态照旧读旧快照"""
    from app.database import async_session
    from app.services.capability_versioning import (
        apply_pending_changes, ensure_text_source_version, get_effective_text, SCOPE_ALL,
    )

    src = SRC + "-text"
    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, src)
        await ensure_text_source_version(repo, src, "第一版", "测试文本", scope=SCOPE_ALL)

        holder = {}
        one = "group:1"
        two = "group:2"
        assert await get_effective_text(repo, holder, src, "兜底", state=one) == "第一版"
        assert await get_effective_text(repo, holder, src, "兜底", state=two) == "第一版"

        await ensure_text_source_version(repo, src, "第二版", "测试文本", scope=SCOPE_ALL)
        assert await get_effective_text(repo, holder, src, "兜底", state=one) == "第一版", "锁定态不换字节"
        assert await get_effective_text(repo, holder, src, "兜底", state=two) == "第一版", "锁定态不换字节"

        await apply_pending_changes(repo, holder, [src], state=one)  # 只有 group:1 解锁
        assert await get_effective_text(repo, holder, src, "兜底", state=one) == "第二版"
        assert await get_effective_text(repo, holder, src, "兜底", state=two) == "第一版", \
            "别的会话没有 compact，它的前缀字节不许跟着换"

        await ensure_text_source_version(repo, src, "第三版", "测试文本", scope=SCOPE_ALL)
        await apply_pending_changes(repo, holder, [src], state=one)
        assert await get_effective_text(repo, holder, src, "兜底", state=one) == "第三版"
        assert await get_effective_text(repo, holder, src, "兜底", state=two) == "第一版"


async def test_unlock_aligns_memory_index_for_the_unlocking_state(migrated_db):
    """解锁源必须含记忆索引：漏了它，索引正文永远停在第一版（AI 写进去的规则换不进来）"""
    from app.ai.executor import _unlock_context
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.capability_versioning import (
        ensure_text_source_version, memory_index_source, SCOPE_ALL,
    )

    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
            "VALUES (1, 1, '测试AI', 2, true) ON CONFLICT (id) DO UPDATE SET name = '测试AI'"
        ))
        await db.commit()

        src = memory_index_source(1)
        repo = await _repo(db)
        await _reset(db, src)
        await ensure_text_source_version(repo, src, "## 记忆索引\npeople/\n", "AI1记忆索引", scope=SCOPE_ALL)

        agent = await db.get(Agent, 1)
        agent.cap_effective_versions = {}
        await db.commit()

        await _unlock_context(db, agent, group_id=999003, session_id=None,
                              conversation_type="group", summary="[摘要] 测试")
        await db.commit()

        keys = agent.cap_effective_versions or {}
        assert "group:999003|" + src in keys, f"解锁要按本状态对齐记忆索引，实际 {keys}"
