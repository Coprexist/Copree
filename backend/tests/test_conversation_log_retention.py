"""对话日志的保留策略：按状态老化程度分档，旋钮可在管理页配

规则（app/utils/pure/conversation_log.py）：3 天内动过留 20、3 天以上留 5、30 天以上留 2；
各档数值与两个阈值都能在管理页调、落 conversation_log_config（NULL = 用代码默认）。
这里锁住判定，再走一遍真实 IO（裁剪 + 配置读写）。
"""
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.anyio


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rows(count: int, state_key: str, *, age_days: int = 0, start_id: int = 1):
    """一桶日志：[{id, state_key, created_at}]，新 → 旧（与裁剪拿到的顺序一致）"""
    newest = _now() - timedelta(days=age_days)
    return [{"id": start_id + i, "state_key": state_key, "created_at": newest - timedelta(minutes=i)}
            for i in range(count)]


def _kept(rows, drop, state_key: str) -> int:
    dropped = set(drop)
    return len([r for r in rows if r["state_key"] == state_key and r["id"] not in dropped])


def test_state_key_round_trips():
    """帧身份的编码/解码是同一个口径（前端 LogState.tsx 也是这个写法）"""
    from app.utils.pure.state_stack import frame_of_state_key, state_key_of

    assert state_key_of({"type": "group_chat", "label": "群「测试」"}) == "group_chat|群「测试」"
    assert state_key_of({"type": "dm", "label": ""}) == "dm|"
    assert state_key_of({}) == "" and state_key_of(None) == ""
    assert frame_of_state_key("group_chat|群「测试」") == {"type": "group_chat", "label": "群「测试」"}
    assert frame_of_state_key("dm|") == {"type": "dm", "label": ""}
    assert frame_of_state_key("") == {} and frame_of_state_key(None) == {}


def test_active_states_all_keep_twenty():
    """3 天内动过的状态各留 20——没有「最多的那段」这种特殊待遇"""
    from app.utils.pure.conversation_log import LogRetention, plan_log_trim

    rows = _rows(40, "a", start_id=1) + _rows(9, "b", start_id=100)
    drop = plan_log_trim(rows, LogRetention(), now=_now())
    assert _kept(rows, drop, "a") == 20, "活跃状态留满 20"
    assert _kept(rows, drop, "b") == 9, "没超 20 就不删"


def test_idle_and_aged_states_drop_further():
    """3 天以上 → 5；30 天以上 → 2（各自按自己最后一次动静算）"""
    from app.utils.pure.conversation_log import LogRetention, plan_log_trim

    rows = (_rows(9, "fresh", start_id=1)
            + _rows(9, "idle", age_days=5, start_id=100)
            + _rows(9, "aged", age_days=40, start_id=200))
    drop = plan_log_trim(rows, LogRetention(), now=_now())
    assert _kept(rows, drop, "fresh") == 9, "3 天内 → 活跃档 20，没超不删"
    assert _kept(rows, drop, "idle") == 5
    assert _kept(rows, drop, "aged") == 2


def test_knobs_move_both_the_numbers_and_the_thresholds():
    """旋钮同时管档位数值与两个阈值（管理页配的就是这些）"""
    from app.utils.pure.conversation_log import LogRetention, plan_log_trim

    retention = LogRetention(active_keep=3, idle_keep=2, aged_keep=1, idle_days=10, aged_days=20)
    rows = (_rows(9, "d9", age_days=9, start_id=1)       # 还没到沉寂线 → 活跃档 3
            + _rows(9, "d10", age_days=10, start_id=100)  # 正好到沉寂线 → 2
            + _rows(9, "d20", age_days=20, start_id=200))  # 正好到老旧线 → 1
    drop = plan_log_trim(rows, retention, now=_now())
    assert _kept(rows, drop, "d9") == 3
    assert _kept(rows, drop, "d10") == 2
    assert _kept(rows, drop, "d20") == 1


def test_cap_per_state_limits_each_bucket():
    """列表侧：普通用户每段状态最多看几条（不封顶时原样返回）"""
    from app.utils.pure.conversation_log import cap_per_state

    items = [{"k": "a"}, {"k": "a"}, {"k": "b"}, {"k": "a"}, {"k": "b"}]
    assert cap_per_state(items, lambda i: i["k"], 2) == [{"k": "a"}, {"k": "a"}, {"k": "b"}, {"k": "b"}]
    assert cap_per_state(items, lambda i: i["k"], None) == items


async def _seed(db):
    from sqlalchemy import text
    from db_reset import clear

    await clear(db, "ai_conversation_logs", "conversation_log_config", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"))
    await db.commit()


def _add_logs(db, state_key: str, count: int, *, age_days: int = 0, ref_prefix: str = "m"):
    from app.models.conversation_log import ConversationLog

    newest = _now() - timedelta(days=age_days)
    for i in range(count):
        db.add(ConversationLog(
            agent_id=1, conversation_type="group",
            messages=[{"role": "user", "content": f"{ref_prefix}{i}"}],
            message_count=1, state_key=state_key,
            created_at=newest - timedelta(minutes=i),
        ))


async def test_trim_old_logs_buckets_by_state(migrated_db):
    """真实落库走一遍：活跃 40 → 20、活跃没超不删、40 天前的 → 2"""
    from sqlalchemy import func, select

    from app.database import async_session
    from app.models.conversation_log import ConversationLog
    from app.repositories.content_repo import SQLAlchemyContentRepository
    from app.services.content import conversation_log_service as cls

    async with async_session() as db:
        await _seed(db)
        _add_logs(db, "main", 40, ref_prefix="a")
        _add_logs(db, "side", 9, ref_prefix="b")
        _add_logs(db, "stale", 7, age_days=40, ref_prefix="c")
        await db.commit()

        await cls._trim_old_logs(SQLAlchemyContentRepository(db), 1)
        await db.commit()

        counts = dict((await db.execute(
            select(ConversationLog.state_key, func.count(ConversationLog.id))
            .group_by(ConversationLog.state_key)
        )).all())
        assert counts == {"main": 20, "side": 9, "stale": 2}, counts


async def test_knobs_persist_and_blank_clears_them(migrated_db):
    """旋钮落库（重启不丢）；显式留空 = 清除回代码默认，没提交的旋钮不动"""
    from app.database import async_session
    from app.repositories.content_repo import SQLAlchemyContentRepository
    from app.services.content import conversation_log_service as cls
    from app.utils.pure.conversation_log import IDLE_KEEP, IDLE_DAYS

    repo_of = lambda db: SQLAlchemyContentRepository(db)   # noqa: E731
    async with async_session() as db:
        await _seed(db)
        repo = repo_of(db)

        await cls.update_config(repo, updated_by=1, knobs={"idle_keep": 7, "idle_days": 5})
        await db.commit()
        cfg = await cls.get_config_dict(repo)
        assert cfg["idle_keep"] == 7 and cfg["idle_days"] == 5, cfg
        assert cfg["aged_keep"] is None, "没提交的旋钮保持未设置"

        # 落库的值真的参与判定
        retention = await cls._get_retention(repo, 1)
        assert retention.idle_keep == 7 and retention.idle_days == 5, retention

        # 只提一个旋钮：另一个不动
        await cls.update_config(repo, updated_by=1, knobs={"aged_keep": 3})
        await db.commit()
        assert (await cls.get_config_dict(repo))["idle_keep"] == 7

        # 留空 = 清除回代码默认（不是「不改」）——两个都清，才对得上默认值
        await cls.update_config(repo, updated_by=1, knobs={"idle_keep": None, "idle_days": None})
        await db.commit()
        assert (await cls.get_config_dict(repo))["idle_keep"] is None
        assert (await cls._get_retention(repo, 1)).idle_keep == IDLE_KEEP
        assert (await cls._get_retention(repo, 1)).idle_days == IDLE_DAYS