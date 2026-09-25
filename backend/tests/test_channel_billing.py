"""通道来客的账单人：外部通道的会话记在 AI 主人头上

QQ 等通道来的用户是插件新建的影子账号（没有 Key、没有额度）。文档里的账单规则是
「群聊扣群主（group_owner_pays）/ 通用·半通用 AI 私聊扣聊天者」，所以必须靠
conversation_type 与 origin_channel 两件事，把账单落到真正能给钱的人身上。

踩过的坑：response_worker 调 _get_api_config 时没传 conversation_type，
群聊被当成私聊、账单落到 QQ 影子账号头上 → credit_source=none → AI 只发系统通知不回复。
"""
from sqlalchemy import text


async def test_billing_person_for_group_and_external_dm(migrated_db):
    from app.ai.executor import _get_api_config
    from app.database import async_session
    from app.models.agent import Agent
    from app.models.user import User
    from app.utils.auth import hash_password
    from app.utils.crypto import encrypt_api_key

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "agents", "users")
        owner = User(username="通道主人", password_hash=hash_password("x" * 12), email="o@test.local",
                     type="human", api_key_encrypted=encrypt_api_key("sk-owner"),
                     api_base_url="https://api.deepseek.com")
        stranger = User(username="QQ用户", password_hash=hash_password("x" * 12),
                        email="shadow@qq.bridge", type="external", origin_channel="qq")
        ai_user = User(username="化学老师", password_hash=hash_password("x" * 12),
                       email="ai@test.local", type="ai")
        db.add_all([owner, stranger, ai_user])
        await db.flush()
        agent = Agent(owner_id=owner.id, user_id=ai_user.id, name="化学老师", ai_type="semi_general")
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        # 1) 群聊：文档说扣群主 → 拿到主人的 Key
        key, _base, source, _pool, _info = await _get_api_config(
            db, agent, chatter_id=stranger.id, conversation_type="group")
        assert key == "sk-owner" and source == "user_key", (key, source)

        # 2) 外部通道私聊（bill_to_owner）：同样落到主人
        key, _base, source, _pool, _info = await _get_api_config(
            db, agent, chatter_id=stranger.id, conversation_type="dm", bill_to_owner=True)
        assert key == "sk-owner" and source == "user_key", (key, source)

        # 3) 站内陌生人私聊：按文档扣聊天者，他没 Key 就该是空（原行为，别改坏）
        key, _base, source, _pool, _info = await _get_api_config(
            db, agent, chatter_id=stranger.id, conversation_type="dm")
        assert key is None and source == "none", (key, source)

        # 4) 不传 conversation_type：老写法 —— 群聊也会被当成私聊，这正是那个坑
        key, _base, source, _pool, _info = await _get_api_config(db, agent, chatter_id=stranger.id)
        assert key is None, "不传 conversation_type 时群聊规则不生效（记录旧行为）"

        # 5) 非通用型 AI：无论如何都扣主人
        agent.ai_type = "resonance"
        await db.commit()
        key, _base, source, _pool, _info = await _get_api_config(
            db, agent, chatter_id=stranger.id, conversation_type="dm")
        assert key == "sk-owner", (key, source)
