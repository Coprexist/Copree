"""设置面板提交的字段要真的落到 agent 上（AI 类型 / 可自改 / 可发现 / 额度成本 / 暂停）

来源：设置弹窗把这些字段塞进 PUT /agents/{id}/config，但请求 schema 里没有它们 ——
Pydantic 默认丢弃未知字段，于是界面上"改了保存了"，库里一点没变。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999021, 1, 999022


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '测试AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state, ai_type) "
        "VALUES (:a, :o, '测试AI', :u, 'active', 'semi_general')"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


async def test_settings_panel_fields_reach_the_agent(migrated_db):
    """切到共振（跨用户共享记忆）+ 可自改 + 不可发现 + 额度成本 + 暂停：一个 PUT 全落地。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.agent_service import update_agent_config

    async with async_session() as db:
        await _seed(db)
        await update_agent_config(db, AGENT_ID, OWNER, {
            "ai_type": "resonance",
            "is_ai_editable": True,
            "discoverable": False,
            "api_credit_cost": 3,
            "is_paused": True,
        }, is_admin=True)
        await db.commit()
        agent = await db.get(Agent, AGENT_ID)

    assert agent.ai_type == "resonance"
    assert agent.is_ai_editable is True
    assert agent.discoverable is False
    assert agent.api_credit_cost == 3
    assert agent.is_paused is True


async def test_unknown_ai_type_is_rejected(migrated_db):
    """类型只有三档：拼错的字符串不能静默写进库。"""
    from app.database import async_session
    from app.services.agent.agent_service import update_agent_config

    async with async_session() as db:
        await _seed(db)
        # 测试运行器只桩了 pytest.fixture / pytest.mark，这里不用 pytest.raises
        raised = False
        try:
            await update_agent_config(db, AGENT_ID, OWNER, {"ai_type": "shared"}, is_admin=True)
        except ValueError:
            raised = True
        assert raised
