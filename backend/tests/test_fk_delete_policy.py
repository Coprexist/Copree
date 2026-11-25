"""删号语义的门禁：指向 users / agents 的外键必须按 fk_policy 声明 ondelete

原先这些外键大多没写 ondelete（PostgreSQL 默认 NO ACTION），「先删谁后删谁」只能靠
业务代码里的注释维持，换一行顺序或新开一条删除路径就是 500。现在删除语义进了数据库，
这个文件守住三件事：

1. 模型层：每个指向 users/agents 的列都要在 app/models/fk_policy.py 登记，且 ondelete
   与登记一致——新增一个裸外键，这里先失败，不会等到线上删号 500 才发现；
2. 迁移层：最新一条带 FROZEN_POLICY 的迁移与策略文件一致（改了语义必须配迁移）；
3. 行为层：真删一次 AI、真删一个账号，从属数据级联清干净、事实记录留成 NULL 而不是
   跟着消失。
"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio


def _all_user_and_agent_fks():
    import app.models  # noqa: F401  确保全部模型注册进 metadata
    from app.database import Base

    found = {}
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.target_fullname in ("users.id", "agents.id"):
                    found[(table_name, column.name)] = (fk.ondelete, column.nullable)
    return found


def test_metadata_matches_policy():
    from app.models.fk_policy import FK_POLICY

    found = _all_user_and_agent_fks()

    unregistered = sorted(set(found) - set(FK_POLICY))
    assert not unregistered, (
        f"这些列指向 users/agents，但没在 fk_policy.py 登记删除语义：{unregistered}。"
        "先想清楚它是从属数据（CASCADE）还是事实记录（SET NULL），登记后再改模型列。"
    )

    vanished = sorted(set(FK_POLICY) - set(found))
    assert not vanished, f"fk_policy.py 里登记了、但模型里已经没有这些外键：{vanished}"

    mismatched = {
        key: {"model": found[key][0], "policy": FK_POLICY[key]}
        for key in found
        if found[key][0] != FK_POLICY[key]
    }
    assert not mismatched, f"ondelete 与策略不一致：{mismatched}"

    not_null = sorted(key for key, (_od, nullable) in found.items() if _od == "SET NULL" and not nullable)
    assert not not_null, f"SET NULL 的列必须允许为空，这些还是 NOT NULL：{not_null}"


def test_latest_migration_snapshot_matches_policy():
    from app.models.fk_policy import FK_POLICY

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    snapshots = {}
    for path in sorted(versions.glob("*.py")):
        if "FROZEN_POLICY" not in path.read_text(encoding="utf-8"):
            continue
        spec = importlib.util.spec_from_file_location(f"_fk_snapshot_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        snapshots[module.revision] = (path.name, dict(module.FROZEN_POLICY))

    assert snapshots, "没有任何迁移带 FROZEN_POLICY：删除语义的迁移快照丢了"
    latest_revision = max(snapshots)
    latest_name, latest = snapshots[latest_revision]

    diff = {
        key: {"migration": latest.get(key), "policy": FK_POLICY.get(key)}
        for key in set(latest) | set(FK_POLICY)
        if latest.get(key) != FK_POLICY.get(key)
    }
    assert not diff, (
        f"最新快照 {latest_name} 与 fk_policy.py 不一致：{diff}。"
        "改删除语义的流程是：策略文件 → 模型列 → 新迁移（并带上新的 FROZEN_POLICY）。"
    )


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_delete_agent_cascades_and_keeps_facts(migrated_db):
    """删 AI：从属数据跟着走，用量/日志留成 NULL。

    这条链以前是「顺序即正确性」：dm_messages / dm_sessions 的 NO ACTION 外键会挡住
    删账号，必须先手工清私信再删（2026-11-15 的线上 500）。现在由数据库兜底，
    这里顺便把「留下的东西确实是 NULL 而不是整行消失」也钉住。
    """
    from db_reset import clear
    from app.database import async_session
    from app.models.agent import Agent
    from app.models.agent_config import AgentConfig
    from app.models.api_usage_log import ApiUsageLog
    from app.models.conversation_log import ConversationLog
    from app.models.dm import DMMessage, DMSession
    from app.models.friendship import Friendship, FriendshipRequest
    from app.models.memory import RoughMemory
    from app.models.user import User
    from app.services.agent.agent_service import delete_agent

    async with async_session() as db:
        # friendship_requests 没有外键，clear 的 users 闭包带不走别的用例留下的行；
        # 下面断言的是全表计数，所以这里显式点名清干净
        await clear(db, "agents", "users", "friendships", "friendship_requests")

        owner = User(username="删号测试主人", password_hash="x", type="human")
        ai_user = User(username="待删AI", password_hash="x", type="ai")
        db.add_all([owner, ai_user])
        await db.flush()

        agent = Agent(owner_id=owner.id, user_id=ai_user.id, name="待删AI")
        db.add(agent)
        await db.flush()

        session_id = f"{min(owner.id, ai_user.id)}_{max(owner.id, ai_user.id)}"
        db.add_all([
            AgentConfig(agent_id=agent.id),
            RoughMemory(owner_type="ai", owner_id=agent.id, title="称呼", user_id=ai_user.id),
            DMSession(session_id=session_id, user1_id=min(owner.id, ai_user.id),
                      user2_id=max(owner.id, ai_user.id)),
            # 好友关系/申请没有外键可依赖（friend_id 是多态裸 id）：删号后不清就是「未知N」
            Friendship(user_id=owner.id, friend_type="ai", friend_id=ai_user.id),
            Friendship(user_id=ai_user.id, friend_type="human", friend_id=owner.id),
            FriendshipRequest(requester_id=owner.id, target_type="ai", target_id=ai_user.id),
            FriendshipRequest(requester_id=ai_user.id, target_type="human", target_id=owner.id),
        ])
        # 先落会话再插消息：两者没有 ORM 关系，flush 顺序不会自己排好
        await db.flush()
        db.add_all([
            ApiUsageLog(user_id=owner.id, agent_id=agent.id, tokens_used=10),
            ConversationLog(agent_id=agent.id, user_id=owner.id, messages=[]),
            DMMessage(session_id=session_id, sender_id=ai_user.id, content="在的"),
        ])
        await db.commit()

        agent_id, ai_user_id = agent.id, ai_user.id
        await delete_agent(db, agent_id, operator_id=owner.id)
        await db.commit()

        assert (await db.execute(select(User).where(User.id == ai_user_id))).scalar_one_or_none() is None
        assert (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none() is None

        # 从属数据：跟着一起没（friendships 没有外键，必须由 delete_agent 显式清）
        for model in (AgentConfig, RoughMemory, DMSession, DMMessage, Friendship, FriendshipRequest):
            assert await _count(db, model) == 0, f"{model.__tablename__} 应随删号清空"

        # 事实记录：行还在，主体列变 NULL
        usage = (await db.execute(select(ApiUsageLog))).scalar_one()
        assert usage.agent_id is None and usage.user_id == owner.id
        log = (await db.execute(select(ConversationLog))).scalar_one()
        assert log.agent_id is None and log.user_id == owner.id


async def test_delete_account_cascades_and_keeps_facts(migrated_db):
    """删普通账号：私信/邀请/记忆跟着走，兑换码与商城作品留成无主。"""
    from db_reset import clear
    from app.database import async_session
    from app.models.dm import DMMessage, DMSession
    from app.models.group import Group, GroupInvitation
    from app.models.memory import RoughMemory
    from app.models.redemption import RedemptionCode
    from app.models.study_record import StudyRecord
    from app.models.user import User
    from app.models.world import WorldMarketItem

    async with async_session() as db:
        await clear(db, "agents", "users")

        leaver = User(username="要注销的人", password_hash="x", type="human")
        stayer = User(username="留下的人", password_hash="x", type="human")
        db.add_all([leaver, stayer])
        await db.flush()

        group = Group(name="测试群", owner_type="human", owner_id=stayer.id)
        db.add(group)
        await db.flush()

        session_id = f"{min(leaver.id, stayer.id)}_{max(leaver.id, stayer.id)}"
        db.add_all([
            DMSession(session_id=session_id, user1_id=min(leaver.id, stayer.id),
                      user2_id=max(leaver.id, stayer.id)),
            GroupInvitation(group_id=group.id, inviter_id=leaver.id, invitee_id=stayer.id),
            RoughMemory(owner_type="group", owner_id=group.id, title="记一笔", user_id=leaver.id),
            StudyRecord(user_id=leaver.id, date="2026-11-23", minutes=30),
            RedemptionCode(code="TESTCODE01", quota_amount=10, created_by=leaver.id),
            WorldMarketItem(kind="world", title="作品", author_id=leaver.id,
                            author_name="要注销的人", package_path="market/1.zip"),
        ])
        await db.flush()
        db.add(DMMessage(session_id=session_id, sender_id=leaver.id, content="拜拜"))
        await db.commit()

        await db.delete(leaver)
        await db.commit()

        for model in (DMSession, DMMessage, GroupInvitation, RoughMemory, StudyRecord):
            assert await _count(db, model) == 0, f"{model.__tablename__} 应随账号删除清空"

        assert await _count(db, RedemptionCode) == 1
        assert (await db.execute(select(RedemptionCode))).scalar_one().created_by is None
        assert await _count(db, WorldMarketItem) == 1
        market = (await db.execute(select(WorldMarketItem))).scalar_one()
        assert market.author_id is None
        assert market.author_name == "要注销的人", "作者名是冗余列，删号后仍要能显示"
