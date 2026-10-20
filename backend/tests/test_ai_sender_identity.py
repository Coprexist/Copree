"""AI 的发送身份：agents.id 与 users.id 是两个编号，不能混用

线上（2026-10-20）：群「CoExisten」(#59) 里 浮生（人物志1）普通聊天全部正常，调
`world_command` 却回「你不是该群成员，无法发送消息」。它在那个群的成员记录存的是
users.id=41（v2.0.0 起消息与成员表一律 users.id），而工具把 agents.id=25 原样当
sender_id 传进发送侧 —— 成员校验按编号查不到人，于是"明明在群里的人被当成外人"。

契约：成员判定只认 users.id；手里是 agents.id 的一侧用 `ai_sender_id` 换算一次。
两个 id 空间数值重叠（线上 20 条 AI 成员记录的 member_id 恰好也是另一个 agent 的 id），
所以**下游不许猜是哪套编号**，否则消息会被记到别的 AI 名下。

用例：换算 / 成员判定 / send_gm_message 的放行与拒绝 / world_command 全链路。
"""
import contextlib

import pytest
from sqlalchemy import text

from app.tools.chat_social.world_command import WorldCommand

pytestmark = pytest.mark.anyio

GROUP = 59
AGENT_ID, AI_USER_ID, HUMAN_ID = 24, 40, 1
GROUP_NAME = "CoExisten"


async def _seed(db):
    from db_reset import clear

    await clear(db, "pending_messages", "messages", "group_members", "groups",
                "agents", "world_bindings", "worlds", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        f"({HUMAN_ID}, '群主', 'x', 'human'), ({AI_USER_ID}, '浮生', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) VALUES "
        f"({AGENT_ID}, {HUMAN_ID}, '浮生（人物志1）', {AI_USER_ID}, true, '[]'::jsonb)"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        f"VALUES ({GROUP}, '{GROUP_NAME}', 'human', {HUMAN_ID}, 'default', true)"))
    # 成员表存 users.id —— 这就是 v2.0.0 之后全平台的写法
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        f"({GROUP}, 'ai', {AI_USER_ID}, 'member')"))
    await db.commit()


@contextlib.asynccontextmanager
async def _stub_dispatch(record: list):
    """消息出口换成只记账的假出口：这里测身份，不该真去唤醒世界程序/外部通道。"""
    import app.chat.outbound as outbound

    original = outbound.dispatch_group_message

    async def fake(db, group_id, message, source="user"):
        record.append({"group_id": group_id, "source": source,
                       "sender_type": message.sender_type, "sender_id": message.sender_id})

    outbound.dispatch_group_message = fake
    try:
        yield record
    finally:
        outbound.dispatch_group_message = original


async def test_ai_sender_id_converts_agents_id_to_users_id(migrated_db):
    """换算一次就够，重复调用/不是 agent 的编号原样返回（幂等，调用方不必判断来源）。"""
    from app.chat.gm import ai_sender_id
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert await ai_sender_id(db, AGENT_ID) == AI_USER_ID
        assert await ai_sender_id(db, AI_USER_ID) == AI_USER_ID
        assert await ai_sender_id(db, 999) == 999


async def test_membership_checks_only_know_users_id(migrated_db):
    """成员判定不接受 agents.id —— 换算必须在工具入口做，不能指望这里兜。

    这正是线上事故的分支：world_command 传了 agents.id，于是"在群里的 AI"被判成外人。
    """
    from app.chat.gm import is_group_member, is_member_of_group
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert await is_group_member(db, GROUP, "ai", AI_USER_ID) is True
        assert await is_group_member(db, GROUP, "ai", AGENT_ID) is False
        # 历史签名（chat_api 里那层）转调同一个判定，不许再各写一份查询
        assert await is_member_of_group(db, AI_USER_ID, "ai", GROUP) is True
        assert await is_member_of_group(db, AGENT_ID, "ai", GROUP) is False


async def test_send_gm_message_rejects_stranger_and_accepts_member(migrated_db):
    """不在群里照样拒（安全线不动），在群里的人一定放行，落库的 sender_id 是 users.id。"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    record: list = []
    async with _stub_dispatch(record), async_session() as db:
        await _seed(db)
        # 运行器没有 pytest.raises，按仓里既有写法手动判异常
        try:
            await send_gm_message(db, GROUP, "ai", 999, "我是外人")
        except ValueError as denied:
            assert "不是该群成员" in str(denied)
        else:
            raise AssertionError("外人发群消息应当被拒")
        msg = await send_gm_message(db, GROUP, "ai", AI_USER_ID, "在吗")
        await db.commit()
        assert msg.sender_id == AI_USER_ID
    assert [r["sender_id"] for r in record] == [AI_USER_ID]


async def test_world_command_sends_as_ai_user_id(migrated_db):
    """回归用例：群 AI 调 world_command 必须真的发得出去（2026-10-20 线上事故）。

    发出去的消息还要带 source="user" —— 群消息钩子只接这一路，世界程序才感知得到。
    """
    from app.database import async_session

    record: list = []
    async with _stub_dispatch(record), async_session() as db:
        await _seed(db)
        await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (45, 'CoExisten', 1)"))
        await db.execute(text(
            "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
            f"(45, 'group', {GROUP}, 'expedition')"))
        await db.commit()

        result = await WorldCommand().execute(db, AGENT_ID, GROUP, {"command": "签到"}, {})
        await db.commit()

        assert result.get("success") is True, result
        row = (await db.execute(text(
            "SELECT sender_type, sender_id, content FROM messages ORDER BY id DESC LIMIT 1"
        ))).mappings().first()
        assert (row["sender_type"], row["sender_id"], row["content"]) == ("ai", AI_USER_ID, "签到")

    assert record[-1] == {"group_id": GROUP, "source": "user",
                          "sender_type": "ai", "sender_id": AI_USER_ID}
