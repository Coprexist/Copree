"""开发者决定这条变更怎么告知：自动改变量 / 只报结构变化 / 自己写文案 / 不通知

唯一入口 capability_versioning.notice_policy（世界配置的 tool_notice 走它）；
存储口径只有一条：**摘要空 = 不告知**——起点版本本就如此，silent 与 structural（整版只剩措辞）
也走同一条。声明写错一律按 auto 处理并告警：宁可多通知，也不能因为一个拼错的字段静默沉默。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

SRC = "test-notice-src"


def _defs(name: str, desc: str = "", *extra: str) -> list:
    """一个主能力（可带说明）+ 若干只给名字的附加能力（用来造结构变化）"""
    def one(n: str, d: str = "") -> dict:
        return {"type": "function", "function": {
            "name": n, "description": d,
            "parameters": {"type": "object", "properties": {}, "required": []}}}

    return [one(name, desc)] + [one(n) for n in extra]


def _shape(raw) -> tuple:
    """声明解析结果的形状（text / notify / structural_only），测试只比这一个三元组"""
    from app.services.capability_versioning import notice_policy

    p = notice_policy(raw)
    return (p.text, p.notify, p.structural_only)


async def _repo(db):
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    return SQLAlchemyCapabilityRepository(db)


async def _reset(db, *sources: str):
    for s in sources:
        await db.execute(text("DELETE FROM capability_versions WHERE source = :s"), {"s": s})
    await db.commit()


async def _changelog(db, source: str, version: int) -> str:
    return (await db.execute(text(
        "SELECT changelog FROM capability_versions WHERE source = :s AND version = :v"),
        {"s": source, "v": version})).scalar_one()


def test_auto_is_the_default_for_everything_unrecognised():
    for raw in (None, "", "auto", {"mode": "auto"}, {"mode": "胡说"}, 123, [], {"mode": "custom"}):
        assert _shape(raw) == (None, True, False), raw


def test_silent_is_explicit_and_never_the_default():
    for raw in (False, "silent", {"mode": "silent"}):
        assert _shape(raw) == (None, False, False), raw


def test_structural_is_the_third_choice_a_plugin_author_can_declare():
    """作者可选「措辞微调不推」：结构变化照发，不给参数就什么都不改（默认仍是 auto）"""
    for raw in ("structural", {"mode": "structural"}):
        assert _shape(raw) == (None, True, True), raw


def test_a_plain_sentence_is_the_changelog():
    """最省心的写法：写一句人话就是文案（保留词已在前面接住）"""
    assert _shape("新增了查天气能力") == ("新增了查天气能力", True, False)
    assert _shape({"text": "顺手修了个错别字"}) == ("顺手修了个错别字", True, False)
    assert _shape({"mode": "custom", "text": "重写了参数说明"}) == ("重写了参数说明", True, False)


async def test_notice_false_writes_the_version_but_says_nothing(migrated_db):
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)   # v1 起点
        await ensure_source_version(repo, SRC, _defs("t1", "改了说明"), "测试源",
                                    scope=SCOPE_ALL, notice=False)                       # v2 静默
        assert await _changelog(db, SRC, 2) == "", "不通知 = 摘要留空（与起点同一条口径）"

        holder = {}
        assert await build_change_notice(repo, holder, [SRC], state="group:1") is None, \
            "声明不通知 → 不落条目"
        assert holder["cap_known_versions"]["group:1|" + SRC] == 2, "但仍要与最新结清，免得每轮重扫"


async def test_structural_reports_structure_but_not_wording(migrated_db):
    """只改了措辞 → 摘要留空（不告知）；加了参数或能力 → 照发"""
    from app.database import async_session
    from app.services.capability_versioning import ensure_source_version, SCOPE_ALL

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1", "第一版说明"), "测试源", scope=SCOPE_ALL)

        # 只重写说明文字：这一版对模型「怎么调」没有任何影响，不推
        await ensure_source_version(repo, SRC, _defs("t1", "第一版说明（换了个说法）"), "测试源",
                                    scope=SCOPE_ALL, structural_only=True)
        assert await _changelog(db, SRC, 2) == "", "纯措辞 → 摘要留空，与 silent 同一条口径"

        # 多一个能力：结构变化，照发
        await ensure_source_version(repo, SRC, _defs("t1", "第一版说明（换了个说法）", "t2"), "测试源",
                                    scope=SCOPE_ALL, structural_only=True)
        log = await _changelog(db, SRC, 3)
        assert "新增能力 t2" in log and "更新能力 t1" not in log, log


async def test_structural_declaration_flows_through_ensure_world_version(migrated_db):
    """世界作者在世界配置里写 tool_notice=structural，这条源就只会报结构变化"""
    from app.database import async_session
    from app.services.capability_versioning import ensure_world_version

    src = "world-78"
    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, src)
        await ensure_world_version(repo, 78, _defs("w1", "说明"))
        await ensure_world_version(repo, 78, _defs("w1", "说明（改）"), notice="structural")
        await ensure_world_version(repo, 78, _defs("w1", "说明（改）", "w2"), notice="structural")

        assert await _changelog(db, src, 2) == "", "措辞改动不推"
        assert "新增能力 w2" in await _changelog(db, src, 3), "结构变化照发"


async def test_custom_changelog_is_used_instead_of_the_auto_diff(migrated_db):
    from app.database import async_session
    from app.services.capability_versioning import (
        build_change_notice, ensure_source_version, SCOPE_ALL,
    )

    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, SRC)
        await ensure_source_version(repo, SRC, _defs("t1"), "测试源", scope=SCOPE_ALL)
        await ensure_source_version(repo, SRC, _defs("t1", "改了说明"), "测试源",
                                    scope=SCOPE_ALL, changelog="补了 t1 的说明")

        # 先有过进度（v1）才会收到 v2 的通知；首见的处理见 test_capability_scope
        holder = {"cap_known_versions": {"group:1|" + SRC: 1}}
        notice = await build_change_notice(repo, holder, [SRC], state="group:1")
        assert notice and "补了 t1 的说明" in notice, notice
        assert "+ " not in notice, "给了文案就不再自动算改变量"


async def test_world_declaration_flows_through_ensure_world_version(migrated_db):
    """世界配置 tool_notice：几种选择都能到那条源上（声明怎么读只有 notice_policy 一处）"""
    from app.database import async_session
    from app.services.capability_versioning import ensure_world_version

    src = "world-77"
    async with async_session() as db:
        repo = await _repo(db)
        await _reset(db, src)
        await ensure_world_version(repo, 77, _defs("w1"))
        await ensure_world_version(repo, 77, _defs("w1", "加了一条"), notice="新增了 w1")
        await ensure_world_version(repo, 77, _defs("w1", "又改了一版"), notice={"mode": "silent"})
        await ensure_world_version(repo, 77, _defs("w1", "第三版"), notice={"mode": "silent"})

        assert await _changelog(db, src, 2) == "[世界77 v2] 新增了 w1"
        assert await _changelog(db, src, 3) == "", "silent → 空摘要"
        assert await _changelog(db, src, 4) == "", "一直是 silent 就一直不告知"
