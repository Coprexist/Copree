"""人格段的版本链：本体与 per-user 覆盖都进链；改了发通知，解锁才换前缀

这条链曾经在线上**一次都没跑过**（见 docs/dev/capability_lazy_loading.md「已修：人格段的版本链
在线上从没跑过」）：调用点把"解析好的本体人格"当 per-user 覆盖传下去，取提示词那层据此直接返回。
坏掉时没有任何一处会失败——只会静默把该 AI 所有会话的前缀一起作废。
所以这份用例除了行为，还钉住那条接线本身。
"""
import pathlib

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"

_OLD = "你是甲，说话简短。\n回答前先想一步。\n不确定就说不知道。" + "（补白）" * 20
_NEW = "你是甲，说话简短。\n回答前先想两步再开口。\n不确定就说不确定，并给出怎么查。" + "（补白）" * 20


async def _seed(db, prompt: str = _OLD):
    from db_reset import clear

    # capability_versions 也要清：版本行是全局的（按源累计），不清会串到下一条用例
    await clear(db, "capability_versions", "agent_history_entries", "agent_user_configs",
                "agent_alarms", "pending_messages", "messages", "group_members", "groups",
                "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '测试AI账号', 'x', 'ai'), (7, '乙', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, current_system_prompt, "
        "ai_type) VALUES (1, 1, '测试AI', 2, true, :p, 'resonance')"), {"p": prompt})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"))
    await db.commit()


async def _msgs(db, **kw):
    from sqlalchemy import select

    from app.ai.llm import build_messages
    from app.models.agent import Agent

    agent = (await db.execute(select(Agent).where(Agent.id == 1))).scalar_one()
    return await build_messages(db, agent, 59, **kw)


async def _versions(db, source: str) -> list[str]:
    rows = (await db.execute(text(
        "SELECT version FROM capability_versions WHERE source = :s ORDER BY version"), {"s": source})).all()
    return [r[0] for r in rows]


async def _set_prompt(db, agent_id: int, prompt: str):
    await db.execute(text("UPDATE agents SET current_system_prompt = :p WHERE id = :i"),
                     {"p": prompt, "i": agent_id})
    await db.commit()


async def test_body_prompt_change_notifies_and_only_unlock_applies_it(migrated_db):
    """本体人格：改 → 写新版本 + 账本里出现行级 diff 的通知；前缀等解锁才换"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)

        first = await _msgs(db)
        assert "回答前先想一步" in first[0]["content"], "第一版人格进了锁定段"
        assert await _versions(db, "agent-prompt-1") == [1], "首次构建建立基线（不通知）"

        await _set_prompt(db, 1, _NEW)
        second = await _msgs(db)
        assert await _versions(db, "agent-prompt-1") == [1, 2], "改了要写新版本"
        assert "回答前先想一步" in second[0]["content"], "锁定态前缀仍是旧人格"
        notice = [m["content"] for m in second if "能力变更通知" in (m.get("content") or "")]
        assert notice and "回答前先想两步再开口" in notice[0], notice
        assert "- 回答前先想一步。" in notice[0] and "+ 回答前先想两步再开口。" in notice[0], notice[0]

        # 解锁（compact / clear）才换前缀
        from app.models.agent import Agent
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import apply_pending_changes

        await apply_pending_changes(SQLAlchemyCapabilityRepository(db), await db.get(Agent, 1),
                                    ["agent-prompt-1"], state="group:59")
        await db.commit()
        third = await _msgs(db)
        assert "回答前先想两步再开口" in third[0]["content"], "解锁后前缀换成新人格"
        assert "回答前先想一步。" not in third[0]["content"]
        # 通知只发一次：账本里那条会一直躺在历史里（本轮不该再发一条新的）
        from app.services.capability_versioning import read_progress

        assert read_progress(await db.get(Agent, 1), "cap_known_versions", "group:59",
                             "agent-prompt-1") == 2


async def test_per_user_override_gets_its_own_source(migrated_db):
    """覆盖是另一份文本、另一个源：本体源不动，覆盖改了只动它自己的源"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)

        first = await _msgs(db, system_prompt_override="你是乙，只跟乙说话。", prompt_owner=7)
        assert "只跟乙说话" in first[0]["content"]
        assert await _versions(db, "agent-prompt-1-u7") == [1]
        assert await _versions(db, "agent-prompt-1") == [], "有人格覆盖时不碰本体源"

        second = await _msgs(db, system_prompt_override="你是乙，只跟乙说话，且先报时间。", prompt_owner=7)
        assert await _versions(db, "agent-prompt-1-u7") == [1, 2]
        assert "只跟乙说话。" in second[0]["content"], "锁定态仍是旧覆盖"
        notice = [m["content"] for m in second if "能力变更通知" in (m.get("content") or "")]
        assert notice and "用户7的覆盖" in notice[0], notice


async def test_effective_config_exposes_the_raw_override(migrated_db):
    """配置层把"覆盖本身"与"解析后的值"分开：只给解析后的值，覆盖判定等于做废"""
    from app.database import async_session
    from app.services.agent.agent_service import get_effective_config

    async with async_session() as db:
        await _seed(db)
        await db.execute(text(
            "INSERT INTO agent_user_configs (agent_id, user_id, system_prompt_override) "
            "VALUES (1, 7, '你是乙')"))
        await db.execute(text("UPDATE agents SET ai_type = 'semi_general' WHERE id = 1"))
        await db.commit()

        mine = await get_effective_config(db, 1, 7)
        assert mine["system_prompt"] == "你是乙" and mine["system_prompt_override"] == "你是乙"
        other = await get_effective_config(db, 1, 1)
        assert other["system_prompt"].startswith("你是甲")
        assert other["system_prompt_override"] is None, "没有覆盖时必须给 None，别给解析后的值"


def test_callers_pass_the_raw_override_not_the_resolved_prompt():
    """源码闸门：接线退回"传解析后的值"，人格又会绕过版本链（坏了半年没人发现）"""
    worker = (_APP / "ai" / "response_worker.py").read_text(encoding="utf-8")
    assert worker.count('system_prompt_override=effective_cfg.get("system_prompt_override")') == 2
    assert 'system_prompt_override=effective_cfg.get("system_prompt")' not in worker
    llm = (_APP / "ai" / "llm.py").read_text(encoding="utf-8")
    assert "if system_prompt_override:" not in llm, "取提示词那层不许再对覆盖早退（那正是绕过版本链的口子）"
