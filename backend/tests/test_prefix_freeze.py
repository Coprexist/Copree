"""前缀快照：锁的是发出去的**字节**，不是渲染代码的自觉

2026-11-14 实测：send_voice / file_fetch / 大肥鱼表情包三次改动，版本号与工具名单都没变
（那是另一把锁，锁的是"输入"），但前缀里的技能目录是每轮现算出来的文本 → 活跃会话从第 0
字节失效 7 次，多付 283k 未命中。这里钉住三件事：

1. 锁定态：渲染结果再怎么变，发出去的都是首见那份字节；
2. 解锁点：清空之后才会换新（compact/clear 是唯一换字节的地方）；
3. 解锁清单里有这一步，且排在版本对齐之后（否则清干净了也还是拼旧版）。
"""

from app.services.capability_versioning import (
    clear_frozen_prefix, freeze_prefix, frozen_prefix,
)


def test_prefix_snapshot_pins_the_rendered_bytes():
    holder = {}
    assert frozen_prefix(holder, "group:1") is None

    first = freeze_prefix(holder, "group:1", "## 技能目录\n可用工具：93 个")
    assert first == "## 技能目录\n可用工具：93 个"
    assert frozen_prefix(holder, "group:1") == first
    # 存的是文本 + 指纹（排查用：字节变了没有）
    stored = holder["cap_effective_prefix"]["group:1|prefix"]
    assert stored["hash"] and len(stored["hash"]) == 64

    # 渲染代码改了（新增工具行、加了个表情包）→ 发出去的仍是首见那份
    again = freeze_prefix(holder, "group:1", "## 技能目录\n可用工具：94 个\n+ send_voice")
    assert again == first
    assert frozen_prefix(holder, "group:1") == first


def test_prefix_snapshot_is_per_conversation():
    """前缀按会话拼：一个会话解锁不该换别的会话的字节"""
    holder = {}
    freeze_prefix(holder, "group:1", "群1")
    assert frozen_prefix(holder, "group:2") is None
    assert freeze_prefix(holder, "group:2", "群2") == "群2"


def test_persona_variants_have_their_own_snapshot():
    """人格覆盖按说话的人不同 → 字节本来就该不同：两个版本各钉一份，B 不能读到 A 的"""
    holder = {}
    assert freeze_prefix(holder, "group:1", "本体人格", variant="agent-prompt-1") == "本体人格"
    assert frozen_prefix(holder, "group:1", variant="agent-prompt-1-u7") is None
    assert freeze_prefix(holder, "group:1", "用户7的覆盖", variant="agent-prompt-1-u7") == "用户7的覆盖"
    # 各自锁定，互不覆盖
    assert frozen_prefix(holder, "group:1", variant="agent-prompt-1") == "本体人格"
    # 解锁一次全作废（所有变体）
    clear_frozen_prefix(holder, "group:1")
    assert frozen_prefix(holder, "group:1", variant="agent-prompt-1") is None
    assert frozen_prefix(holder, "group:1", variant="agent-prompt-1-u7") is None


def test_unlock_clears_then_refreezes():
    holder = {}
    freeze_prefix(holder, "group:1", "旧目录")
    clear_frozen_prefix(holder, "group:1")
    assert frozen_prefix(holder, "group:1") is None
    assert freeze_prefix(holder, "group:1", "新目录") == "新目录"


async def test_mark_prefix_fresh_clears_the_snapshot():
    """"前缀换新"只有一个入口：谁宣布换新，谁的旧快照就得作废

    否则锁定态拒绝覆盖（这条是对的，防断缓存），解锁之后旧字节会被原样端回来——
    test_personality_versioning 里"解锁后前缀换新人格"就是这么红的。
    """
    from app.services import capability_versioning as cv

    holder = {}
    cv.freeze_prefix(holder, "group:59", "旧人格")
    assert cv.frozen_prefix(holder, "group:59") == "旧人格"

    class _Repo:
        async def execute(self, _q):
            class _Res:
                def scalar_one_or_none(self):
                    return None
            return _Res()

    await cv.mark_prefix_fresh(_Repo(), holder, ["agent-prompt-1"], state="group:59")
    assert cv.frozen_prefix(holder, "group:59") is None, "换新之后旧快照必须作废"

async def test_build_messages_keeps_the_same_bytes_when_the_catalog_changes(migrated_db):
    """端到端：目录渲染变了，同一会话的第二轮请求字节必须一模一样

    2026-11-14 的现场就是这样：工具数组没变、版本号没变，只有 _build_tools_segment
    多了一行（send_voice/file_fetch/大肥鱼表情包），活跃会话的整段前缀从第 0 字节失效。
    """
    from sqlalchemy import select

    from app.ai import llm
    from app.ai.llm import build_messages
    from app.database import async_session
    from app.models.agent import Agent

    async with async_session() as session:
        await _seed_agent(session)

        agent = (await session.execute(select(Agent).where(Agent.id == 1))).scalar_one()
        first = await build_messages(session, agent, 59)
        await session.commit()
        assert "## 技能目录" in first[0]["content"]

        # 平台加了个工具：目录段渲染结果当场变了（这就是今天的现场）
        real = llm._build_tools_segment

        async def _new_catalog(_session, _agent, is_dm: bool = False, emoji_note: str = ""):
            # 用只在本用例出现的标记，别拿真工具名当探针：send_voice 已经是真目录里的一员
            return ("## 技能目录\n你的状态：在线　可用工具：95 个\n"
                    "+ __snapshot_probe_tool(x)  这是本用例才有的工具名")

        llm._build_tools_segment = _new_catalog
        try:
            agent = (await session.execute(select(Agent).where(Agent.id == 1))).scalar_one()
            second = await build_messages(session, agent, 59)
        finally:
            llm._build_tools_segment = real

        assert second[0]["content"] == first[0]["content"], "锁定态发出去的前缀字节必须一模一样"
        assert "__snapshot_probe_tool" not in second[0]["content"], (
            "新目录要等 compact/clear 解锁才进前缀")


async def _seed_agent(session):
    """造一份最小会话：群主 + AI 账号 + 一只 AI + 一个群。

    一律走 ORM 造数据，不写裸 SQL：桌面版跑 SQLite，塞进 PG 方言的语句当场就崩；
    而且裸 execute(text(...)) 绕过仓储的事务边界，造出来的数据与业务路径不是同一种。
    """
    from app.models.agent import Agent
    from app.models.group import Group
    from app.models.user import User
    from db_reset import clear

    await clear(session, "capability_versions", "agent_history_entries",
                "group_members", "groups", "agents", "users")
    session.add_all([
        User(id=1, username="群主", password_hash="x", type="human"),
        User(id=2, username="测试AI账号", password_hash="x", type="ai"),
    ])
    await session.flush()   # 先落账号：agents.owner_id / user_id 都指回 users
    session.add_all([
        Agent(id=1, owner_id=1, name="测试AI", user_id=2, discoverable=True, ai_type="resonance"),
        Group(id=59, name="CoExisten", owner_type="human", owner_id=1),
    ])
    await session.commit()
