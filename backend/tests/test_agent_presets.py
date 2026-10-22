"""预设档位切换：数字生命档要把该开的都开上，预览只读不写

这组用例来自一个真 bug：档位切换依赖的参数表搬进 utils/pure/presets.py 后没跟着导入，
点「切换档位」直接 NameError（预览与实写都崩），界面上这个功能等于不可用。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999011, 1, 999012


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '测试AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state, config_profile, "
        "current_temperature, thinking_enabled, memory_load_mode, memory_recent_count, max_tool_rounds) "
        "VALUES (:a, :o, '测试AI', :u, 'active', 'chat', 0.4, false, 'index_only', 0, 20)"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


async def test_switching_to_digital_life_applies_the_whole_bundle(migrated_db):
    """升级方向按「取大/取或」合并：用户已经拉高的值保留（20 轮），其余换成数字生命的档位值。"""
    from app.database import async_session
    from app.services.agent.agent_service import apply_config_profile

    async with async_session() as db:
        await _seed(db)
        agent = await apply_config_profile(db, AGENT_ID, "digital_life", operator_id=OWNER)
        await db.commit()
        await db.refresh(agent)

    assert agent.config_profile == "digital_life"
    assert agent.current_temperature == 1.1
    assert agent.thinking_enabled is True
    assert agent.force_alarm_on_end is True
    assert agent.plan_injection_enabled is True
    assert agent.retire_handover_self is True
    assert agent.memory_load_mode == "index_plus_semantic"
    assert agent.memory_recent_count == 5
    # 已经是 20 的轮次不被降回去
    assert agent.max_tool_rounds == 20


async def test_preview_reports_the_direction_without_writing(migrated_db):
    """预览必须只读：界面上先看「会改哪几项」，点确认才落库。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.agent_service import apply_config_profile

    async with async_session() as db:
        await _seed(db)
        preview = await apply_config_profile(db, AGENT_ID, "digital_life", operator_id=OWNER, dry_run=True)
        agent = await db.get(Agent, AGENT_ID)
        assert preview["direction"] == "upgrade"
        assert preview["changed_fields"]["memory_load_mode"]["new"] == "index_plus_semantic"
        assert agent.config_profile == "chat" and agent.memory_load_mode == "index_only"
