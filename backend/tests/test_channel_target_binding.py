"""通道与 AI 的绑定：按 id 认，名字只用来显示

2026-11-02 线上：AI 从「鲸鱼娘 Deepseek」改名成「Deepseek 大肥鱼」之后，QQ 通道再启动
就按名字找不到它、start() 失败，整条通道静默不启动，用户在 QQ 里只看到「机器人…的服务异常」。
名字是用户随时能改的内容，不能当绑定的主键——绑定只认 id。
"""
from sqlalchemy import text

OWNER_ID = 901
AGENT_ID = 7
AGENT_USER_ID = 902
GROUP_ID = 990901
EMPTY_GROUP_ID = 990902


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        for table in ("users", "agents", "groups"):
            await db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1000)"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            f"({OWNER_ID}, '群主', 'x', 'human'), ({AGENT_USER_ID}, '浮生', 'x', 'ai') "
            "ON CONFLICT DO NOTHING"
        ))
        await db.execute(text(
            f"INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
            f"VALUES ({AGENT_ID}, {OWNER_ID}, '浮生', {AGENT_USER_ID}, true) "
            "ON CONFLICT DO NOTHING"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) VALUES "
            f"({GROUP_ID}, '改名测试群', 'human', {OWNER_ID}, 'default', true), "
            f"({EMPTY_GROUP_ID}, '空群', 'human', {OWNER_ID}, 'default', true) "
            "ON CONFLICT DO NOTHING"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            f"({GROUP_ID}, 'human', {OWNER_ID}, 'owner'), ({GROUP_ID}, 'ai', {AGENT_USER_ID}, 'member') "
            "ON CONFLICT DO NOTHING"
        ))
        await db.commit()


async def test_bind_reads_name_from_id(migrated_db):
    """绑定只按 id：AI 改名之后照样认得上，名字是现读的（线上就死在按名字找）"""
    from app.database import async_session
    from app.services.plugin.target_agent import bind

    await _seed()
    async with async_session() as db:
        await db.execute(text("UPDATE agents SET name='Deepseek 大肥鱼' WHERE id=:a"), {"a": AGENT_ID})
        await db.commit()

    async with async_session() as db:
        bound = await bind(db, agent_id=str(AGENT_ID), group_id=GROUP_ID)
        assert bound.agent_id == AGENT_ID
        assert bound.name == "Deepseek 大肥鱼", "名字应按 id 读当前的，而不是配置里那份旧的"


async def test_bind_requires_id(migrated_db):
    """配置里没有 AI 的 id：如实说"重新保存一次通道"，不要退回去按名字猜"""
    from app.database import async_session
    from app.services.plugin.target_agent import bind

    await _seed()
    async with async_session() as db:
        try:
            await bind(db, agent_id="", group_id=GROUP_ID)
        except ValueError as e:
            assert "重新保存" in str(e), str(e)
        else:
            raise AssertionError("没有 id 时不该猜出一个 AI 绑上")


async def test_bind_refuses_agent_outside_group(migrated_db):
    """id 认得到、但那个 AI 不在这个群里：如实报错，不悄悄换一个绑上"""
    from app.database import async_session
    from app.services.plugin.target_agent import bind

    await _seed()
    async with async_session() as db:
        try:
            await bind(db, agent_id=str(AGENT_ID), group_id=EMPTY_GROUP_ID)
        except ValueError as e:
            assert "不在" in str(e), str(e)
        else:
            raise AssertionError("AI 不在这个群里时不该绑上")
