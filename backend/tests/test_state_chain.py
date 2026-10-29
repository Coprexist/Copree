"""状态链：运行集合的双向链表（节点只存 id）

两件事分开测：
1. **纯指针算术**（attach / move_to_tail / detach / window / reconcile）——一个字典，不碰 DB；
2. **接上服务之后**：老 AI 首建链、切会话 = 把帧拉到链尾、pop/close = 下链、执行窗口可调。

改造前的行为由 test_frame_lifecycle.py 钉住（12 条全绿），这里只钉新结构。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


def _chain(*frame_ids):
    """造一条链：节点号 1..n，帧 id 按给的顺序。"""
    from app.utils.pure import state_chain as sc

    chain = sc.empty_chain()
    for node_id, frame_id in enumerate(frame_ids, start=1):
        sc.attach_tail(chain, node_id, frame_id)
    return chain


# ═══════════════════════════════════════════════════════════════
# 纯函数
# ═══════════════════════════════════════════════════════════════

def test_attach_tail_points_both_ways():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C")
    assert sc.frames_by_chain(chain) == ["A", "B", "C"]
    assert chain["head"] == 1 and chain["tail"] == 3
    assert sc.executing_frames(chain) == ["C"], "只执行链尾"
    assert sc.problems(chain) == []


def test_move_to_tail_relinks_neighbours_and_then_hangs_on_the_old_tail():
    """用户定的算法：前后帧相连 → 本帧接到上一个最末帧之后 → 它成了新的最末帧。"""
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C")
    patches = sc.move_to_tail(chain, 2)                   # 把中间的 B 拉到最末

    assert sc.frames_by_chain(chain) == ["A", "C", "B"], "B 落到末尾，A、C 相连"
    assert chain["tail"] == 2
    assert sc.executing_frames(chain) == ["B"]
    assert sc.problems(chain) == []
    # 最多 4 行：前驱、后继、旧尾、本帧（此处后继就是旧尾，去重后 3 行）
    assert len(patches) <= 4, f"只该动常数个节点，实际 {len(patches)}: {patches}"


def test_move_to_tail_is_constant_work_regardless_of_chain_length():
    """O(1) 的实证：50 帧的链把中间一帧拉到最末，改动的节点数不随链长增长。"""
    from app.utils.pure import state_chain as sc

    chain = _chain(*[f"F{i}" for i in range(50)])
    patches = sc.move_to_tail(chain, 25)

    assert len(patches) <= 4, f"链长 50 也只动 4 行，实际 {len(patches)}"
    assert sc.frames_by_chain(chain)[-1] == "F24"
    assert sc.problems(chain) == []


def test_move_to_tail_of_the_head_and_idempotence():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C")
    sc.move_to_tail(chain, 1)                             # 链头也要能拉
    assert sc.frames_by_chain(chain) == ["B", "C", "A"]
    assert chain["head"] == 2 and chain["tail"] == 1
    assert sc.problems(chain) == []

    assert sc.move_to_tail(chain, 1) == [], "已经在最末：幂等，一条补丁都不出"
    assert sc.frames_by_chain(chain) == ["B", "C", "A"]


def test_detach_links_the_neighbours():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C")
    sc.detach(chain, 2)
    assert sc.frames_by_chain(chain) == ["A", "C"]
    assert sc.problems(chain) == []
    assert sc.node_of_frame(chain, "B") is None, "下链就得从帧索引里消失"

    sc.detach(chain, 1)                                   # 摘链头
    assert sc.frames_by_chain(chain) == ["C"] and chain["head"] == 3
    sc.detach(chain, 3)                                   # 摘到空
    assert sc.frames_by_chain(chain) == []
    assert chain["head"] is None and chain["tail"] is None
    assert sc.problems(chain) == []


def test_window_follows_max_active():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C", "D")
    assert sc.executing_frames(chain) == ["D"], "默认窗口 1：只跑链尾"
    sc.set_max_active(chain, 3)
    assert sc.executing_frames(chain) == ["D", "C", "B"], "窗口 3：链尾起三帧（尾在前）"
    assert sc.set_max_active(chain, 0) == 1, "非法值回落"
    assert sc.set_max_active(chain, 99) == sc.MAX_ACTIVE_CAP


def test_reconcile_plans_appends_and_drops():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B", "C")
    plan = sc.reconcile(chain, ["A", "C", "NEW"])
    assert plan == {"append": ["NEW"], "drop": [2]}, plan

    plan = sc.reconcile(chain, ["A", "B", "C"])
    assert plan == {"append": [], "drop": []}


def test_reconcile_keeps_the_node_closest_to_the_tail():
    """同一个帧挂了两个节点：留靠近链尾的那个（最近的位次才算数）。"""
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B")
    # 手工造出"同一个帧挂了两个节点"的坏链（正常写入路径被 attach_tail 拦住了）
    chain["nodes"][3] = {"frame": "A", "prev": 2, "next": None}
    chain["nodes"][2]["next"] = 3
    chain["tail"] = 3
    chain["index"]["A"] = 3
    plan = sc.reconcile(chain, ["A", "B"])
    assert plan["drop"] == [1], plan


def test_problems_reports_a_cycle():
    from app.utils.pure import state_chain as sc

    chain = _chain("A", "B")
    chain["nodes"][2]["next"] = 1
    assert any("链上有环" in p for p in sc.problems(chain)), sc.problems(chain)


def test_order_by_chain_puts_history_first_and_current_last():
    from app.utils.pure import state_chain as sc

    chain = _chain("B", "A")
    frames = [
        {"id": "A", "status": "active"},
        {"id": "OLD", "status": "ended"},
        {"id": "B", "status": "paused"},
    ]
    assert [f["id"] for f in sc.order_by_chain(frames, chain)] == ["OLD", "B", "A"], \
        "历史区在前、运行区按链序、当前帧（链尾）在末尾"


# ═══════════════════════════════════════════════════════════════
# 接上服务之后
# ═══════════════════════════════════════════════════════════════

async def _seed(db, state_stack: str = "[]"):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, 'u', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) "
        "VALUES (1, 1, '测试AI', NULL, true, :stack)"), {"stack": state_stack})
    await db.commit()


async def _open(db, n: int):
    from app.services.agent.state_stack_service import ensure_active_frame

    await ensure_active_frame(db, 1, "group_chat", f"group:{n}", f"群{n}", "某人")
    await db.commit()


async def _queue(db):
    """链上帧 id，尾在前（= 要执行的顺序）。"""
    from app.services.agent.state_stack_service import get_chain, get_frames

    view = await get_chain(db, 1)
    return view["queue"]


async def _node_count(db) -> int:
    return (await db.execute(text(
        "SELECT count(*) FROM agent_state_chain_node WHERE agent_id = 1"))).scalar_one()


async def test_first_write_builds_the_chain(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import get_chain, get_frames

    async with async_session() as db:
        await _seed(db)
        assert await _node_count(db) == 0

        await _open(db, 1)
        await _open(db, 2)

        frames = await get_frames(db, 1)
        view = await get_chain(db, 1)
        assert await _node_count(db) == 2, "两帧都该上链"
        assert view["materialized"] is True
        assert view["queue"] == [frames[-1]["id"], frames[0]["id"]], "尾在前"
        assert view["current"] == frames[-1]["id"], "当前帧 = 链尾"
        assert view["max_active"] == 1

        row = (await db.execute(text(
            "SELECT head_id, tail_id, max_active FROM agent_state_chain WHERE agent_id = 1"))).first()
        assert row is not None and row[0] != row[1]


async def test_switching_back_pulls_that_frame_to_the_chain_tail(migrated_db):
    """切回一段会话 = 把它拉到链尾；帧身份不变（闹钟之类记的 id 长期有效）。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)
        opening = {f["context_ref"]: f["id"] for f in await get_frames(db, 1)}
        assert await _queue(db) == [opening["group:3"], opening["group:2"], opening["group:1"]]

        await _open(db, 1)                     # 切回第一段会话

        frames = await get_frames(db, 1)
        assert await _queue(db) == [opening["group:1"], opening["group:3"], opening["group:2"]]
        assert frames[-1]["id"] == opening["group:1"], "当前帧仍在数组末尾"
        assert frames[-1]["context_ref"] == "group:1"
        assert sum(1 for f in frames if f["context_ref"] == "group:1") == 1, "同会话不叠帧"


async def test_pop_detaches_the_node_but_keeps_the_frame(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import get_chain, get_frames, pop_state

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)

        before = await _queue(db)
        await pop_state(db, 1)
        await db.commit()

        assert await _node_count(db) == 1, "弹出的帧下链（记录还在存储里）"
        assert await _queue(db) == [before[1]]
        assert (await get_chain(db, 1))["current"] == before[1]
        assert len(await get_frames(db, 1)) == 2, "帧记录不删"


async def test_close_detaches_the_last_node(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import close_state, get_chain, get_frames

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)

        await close_state(db, 1)
        await db.commit()

        assert await _node_count(db) == 0, "关掉的帧下链"
        view = await get_chain(db, 1)
        assert view["queue"] == [] and view["current"] is None
        assert len(await get_frames(db, 1)) == 1, "记录保留（等他交接后事）"


async def test_alarm_wakeup_pulls_a_sleeping_frame_to_the_chain_tail(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import get_chain, get_frames, restore_frame

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)
        opening = {f["context_ref"]: f["id"] for f in await get_frames(db, 1)}

        await restore_frame(db, 1, opening["group:1"], "")
        await db.commit()

        assert await _queue(db) == [opening["group:1"], opening["group:2"]]
        assert (await get_chain(db, 1))["current"] == opening["group:1"]


async def test_execution_window_is_a_column_on_the_chain(migrated_db):
    """「并发执行帧数」就是链上一列：改它不必动任何帧。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        get_chain, get_frames, set_chain_max_active,
    )

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)

        assert await set_chain_max_active(db, 1, 2) == 2
        await db.commit()

        view = await get_chain(db, 1)
        frames = await get_frames(db, 1)
        assert view["max_active"] == 2
        assert view["current"] == frames[-1]["id"]
        assert await set_chain_max_active(db, 1, 99) == 8, "上限截断"
        assert await set_chain_max_active(db, 1, 0) == 1, "非法值回落"
        await db.commit()
        assert (await db.execute(text(
            "SELECT max_active FROM agent_state_chain WHERE agent_id = 1"))).scalar_one() == 1


async def test_a_legacy_agent_gets_its_chain_built_in_the_stored_order(migrated_db):
    """改造前没有链的 AI：第一次写栈时按当时的运行集合现建，当前帧不变。"""
    import json

    from app.database import async_session
    from app.services.agent.state_stack_service import get_chain, get_frames

    legacy = [
        {"id": "old1", "type": "group_chat", "context_ref": "group:1",
         "doing": "旧会话一", "status": "paused"},
        {"id": "old2", "type": "group_chat", "context_ref": "group:2",
         "doing": "旧会话二", "status": "active"},
        {"id": "gone", "type": "dm", "context_ref": "dm:9", "status": "ended"},
    ]

    async with async_session() as db:
        await _seed(db, json.dumps(legacy))
        assert await _node_count(db) == 0

        view = await get_chain(db, 1)                  # 读接口不建链（只读不写）
        assert view["materialized"] is False
        assert view["current"] == "old2" and view["queue"] == ["old2", "old1"], "现算也得对"
        assert await _node_count(db) == 0, "读接口不该顺手写库"

        await _open(db, 3)                             # 第一次写栈：建链 + 挂新帧

        assert await _node_count(db) == 3, "老的两帧 + 新的一帧"
        assert (await get_chain(db, 1))["queue"][1:] == ["old2", "old1"], "老顺序不变，新帧在尾"
        frames = await get_frames(db, 1)
        assert [f["id"] for f in frames][0] == "gone", "已结束的帧留在历史区"
        assert frames[-1]["context_ref"] == "group:3", "当前帧在末尾"


async def test_merged_duplicate_frames_leave_the_chain(migrated_db):
    """同一会话的重复帧在写入点被归并 → 被并掉的那帧连同它的节点一起下链。"""
    import json

    from app.database import async_session
    from app.services.agent.state_stack_service import get_chain, get_frames, touch_active_frame

    dupes = [
        {"id": "dupA", "type": "group_chat", "context_ref": "group:9",
         "doing": "旧的那份", "status": "paused"},
        {"id": "dupB", "type": "group_chat", "context_ref": "group:9",
         "doing": "新的那份", "status": "active"},
    ]
    async with async_session() as db:
        await _seed(db, json.dumps(dupes))

        await touch_active_frame(db, 1)                 # 任意一次写栈都会归并
        await db.commit()

        frames = await get_frames(db, 1)
        assert [f["id"] for f in frames].count("dupB") == 1, frames
        assert next(f for f in frames if f["id"] == "dupA")["status"] == "ended"
        assert await _node_count(db) == 1, "被归并的帧下链"
        assert (await get_chain(db, 1))["queue"] == ["dupB"], "在位次上只剩幸存者"


async def test_disposing_a_group_detaches_its_frames(migrated_db):
    """群解散走的是绕开 _save 的那条路（dispose_context_frames）：链得在它自己里对上。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        dispose_context_frames, get_chain, get_frames,
    )

    async with async_session() as db:
        await _seed(db)                                 # 这档 AI 不接手后事 → 平台代销
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 1)                              # 回到群 1
        group2 = next(f["id"] for f in await get_frames(db, 1) if f["context_ref"] == "group:2")

        out = await dispose_context_frames(db, "group:1")
        await db.commit()

        assert out["dropped"] == 1, out
        frames = await get_frames(db, 1)
        assert [f["context_ref"] for f in frames] == ["group:2"], "死会话的帧已代销"
        assert await _node_count(db) == 1, "节点跟着下链"
        view = await get_chain(db, 1)
        assert view["queue"] == [group2] and view["current"] == group2, "剩下的那帧接位"


async def test_a_broken_chain_is_rebuilt_at_the_write_point(migrated_db):
    """链被并发写拧断（这里手工造一个"头还有前驱"）→ 下一次写栈整条重建，不留半吊子。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        _load_chain, get_chain, touch_active_frame,
    )
    from app.utils.pure import state_chain

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)
        before = await _queue(db)                       # 尾在前

        head, tail = (await db.execute(text(
            "SELECT head_id, tail_id FROM agent_state_chain WHERE agent_id = 1"))).first()
        await db.execute(text(
            "UPDATE agent_state_chain_node SET prev_id = :t WHERE id = :h"),
            {"t": tail, "h": head})                     # 链头被接上了前驱 = 坏链
        await db.commit()
        assert state_chain.problems(await _load_chain(db, 1)), "先得真是一条坏链"

        await touch_active_frame(db, 1)                 # 任意一次写栈都会对账
        await db.commit()

        chain = await _load_chain(db, 1)
        assert state_chain.problems(chain) == [], state_chain.problems(chain)
        assert await _node_count(db) == 3, "重建不丢帧"
        assert await _queue(db) == before, "还读得出的位次保持不变"
        assert (await get_chain(db, 1))["current"] == before[0]


async def test_list_states_tool_hands_the_ai_the_chain(migrated_db):
    """工具输出跟着链走：哪帧在跑、队列顺序、窗口大小直接给，AI 不必自己看 status 猜。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames
    from app.tools.self_management.list_states import ListStates

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)
        frames = await get_frames(db, 1)

        out = await ListStates().execute(db, 1, None, {}, {})

        assert out["success"] and out["depth"] == 2, out
        assert [f["id"] for f in out["frames"] if f["executing"]] == [frames[-1]["id"]]
        assert out["queue"] == [frames[-1]["id"], frames[0]["id"]], "尾在前"
        assert out["max_active"] == 1
