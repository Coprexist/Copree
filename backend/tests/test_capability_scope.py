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
    """作用域=全部 → 变更按状态各收一次，谁先构建也替不了别人勾掉

    首见（状态还没拼过前缀）不补发历史——它拿到的就是最新内容，见
    test_first_sight_settles_silently。这里测的是"变更发生之后，每个已存在的状态各收一次"。
    """
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)

        a = {}
        assert await build_change_notice(repo, a, [SRC], state="group:1") is None, \
            "只有起点版本时没有可告知的变更（内容就在它眼前的前缀里）"
        assert a["cap_known_versions"] == {"group:1|" + SRC: 1}, "但要与最新结清，免得每轮重扫"

        await ensure_source_version(repo, SRC, _defs("t1", "t2"), "测试源", scope=SCOPE_ALL)  # v2

        first = await build_change_notice(repo, a, [SRC], state="group:1")
        assert first is not None and "v1→v2" in first, "已存在的状态收到这次变更"
        assert a["cap_known_versions"] == {"group:1|" + SRC: 2}

        await ensure_source_version(repo, SRC, _defs("t1", "t3"), "测试源", scope=SCOPE_ALL)  # v3

        b = {"cap_known_versions": {"group:2|" + SRC: 2}}
        assert await build_change_notice(repo, a, [SRC], state="group:1") is not None, "a 收到 v3"
        late = await build_change_notice(repo, b, [SRC], state="group:2")
        assert late is not None and "v2→v3" in late, \
            "另一个状态也收到——修前它会被先构建的那个状态替它勾掉"
        assert await build_change_notice(repo, a, [SRC], state="group:1") is None, "同一状态不重复发"


async def test_first_sight_settles_silently(migrated_db):
    """新建的 AI / 新建的会话不重放历史：首见只结清 known，之后的新变更照常送到

    实测口径：库里的 platform 源已攒到 v57、changelog 合计 8.2k 字，按旧写法（known 当 0）
    新建的 AI 第一轮就会把 v1→v57 全念一遍。
    """
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t2"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t3"), "测试源", scope=SCOPE_ALL)  # v3

        fresh = {}
        assert await build_change_notice(repo, fresh, [SRC], state="group:new") is None, \
            "新状态的前缀本来就用最新内容，历史 changelog 对它没有信息量"
        assert fresh["cap_known_versions"]["group:new|" + SRC] == 3, "首见也要结清，免得每轮重扫"

        await ensure_source_version(repo, SRC, _defs("t4"), "测试源", scope=SCOPE_ALL)  # v4
        notice = await build_change_notice(repo, fresh, [SRC], state="group:new")
        assert notice is not None and "v3→v4" in notice, "首见之后的新变更照常通知"


async def test_legacy_flat_key_is_not_a_baseline(migrated_db):
    """旧的 agent 级扁平键不再参与判定：它只说"别人收到过 v几"，说不出这个状态的前缀装着哪版。

    旧口径把它当每个状态的起点继承——后果是**新建的会话**第一轮就收到一条 v44→v60 的合并通知，
    而那段前缀本来就是按最新拼的；更糟的是 get_effective_* 走同一个回落，老状态的前缀会被永久
    钉在出土版本上（浮生 platform effective 停在 v12：58 个工具，连 enter_world 都没有）。
    """
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t2"), "测试源", scope=SCOPE_ALL)  # 到 v2
        await ensure_source_version(repo, SRC, _defs("t3"), "测试源", scope=SCOPE_ALL)  # v3

        legacy = {"cap_known_versions": {SRC: 1}}
        assert await build_change_notice(repo, legacy, [SRC], state="group:new") is None,             "本状态没有任何进度 = 前缀还没拼过（首见）→ 结清，不补发扁平键描述过的历史"
        assert legacy["cap_known_versions"]["group:new|" + SRC] == 3, "首见也要结清，免得每轮重扫"

        await ensure_source_version(repo, SRC, _defs("t4"), "测试源", scope=SCOPE_ALL)  # v4
        notice = await build_change_notice(repo, legacy, [SRC], state="group:new")
        assert notice is not None and "v3→v4" in notice, "首见之后的新变更照常送到"


async def test_effective_counts_as_a_baseline(migrated_db):
    """前缀按哪版拼的，就等于"看到过哪版"：effective 也算基线，否则解锁后必挨一条多余的旧通知"""
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, apply_pending_changes, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t2"), "测试源", scope=SCOPE_ALL)  # v2

        # 解锁：前缀换成 v2，known/effective 一起推到 2
        holder = {}
        await apply_pending_changes(repo, holder, [SRC], state="group:1")
        assert await build_change_notice(repo, holder, [SRC], state="group:1") is None,             "刚解锁就补发一条「变更已生效」的通知 = 通知说的和前缀正好相反"

        await ensure_source_version(repo, SRC, _defs("t3"), "测试源", scope=SCOPE_ALL)  # v3
        notice = await build_change_notice(repo, holder, [SRC], state="group:1")
        assert notice is not None and "v2→v3" in notice

        # 丢了 known 的状态（迁移动过键）退回 effective 当前缀基线：从它前缀那版算起，
        # 既不会把更早的历史全量重放，也不会因为它"忘了"就一条都不给
        holder["cap_known_versions"] = {}
        await ensure_source_version(repo, SRC, _defs("t4"), "测试源", scope=SCOPE_ALL)  # v4
        notice = await build_change_notice(repo, holder, [SRC], state="group:1")
        assert notice is not None and "v2→v4" in notice and "v0→" not in notice


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
        # 旧扁平键不再被读，本状态自己的键推到最新
        assert other["cap_known_versions"]["group:1|" + SRC] == 3, \
            "已知进度要推到最新，避免每轮重扫同一版本"


async def test_legacy_flat_effective_does_not_pin_the_state(migrated_db):
    """旧 agent 级扁平 effective 不许钉住老状态。

    实测（2026-10-21）：浮生 cap_effective_versions 里只有扁平 platform=12，而平台已到 v60；
    v12 只有 58 个工具、连 enter_world 都没有——同一个回落让它的工具列表永久停在出土版本。
    """
    from app.database import async_session
    from app.services.capability_versioning import (
        ensure_source_version, get_effective_definitions, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("old"), "测试源", scope=SCOPE_ALL)  # v1
        await ensure_source_version(repo, SRC, _defs("new"), "测试源", scope=SCOPE_ALL)  # v2

        holder = {"cap_effective_versions": {SRC: 1}}    # 迁移期留下的扁平键
        defs = await get_effective_definitions(repo, holder, SRC, [], state="group:9")
        assert defs == _defs("new"), "老状态要用最新定义：扁平键说的是别的状态钉在哪版"
        assert holder["cap_effective_versions"]["group:9|" + SRC] == 2, "并记下本状态的 effective"

        await ensure_source_version(repo, SRC, _defs("third"), "测试源", scope=SCOPE_ALL)  # v3
        assert await get_effective_definitions(repo, holder, SRC, [], state="group:9") == _defs("new"),             "记下来之后又是锁定态：新的 v3 要等这个状态自己解锁"


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
