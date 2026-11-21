"""额度自动重置：窗口算术 + 跨窗口记账。

这条功能的正确性全在「窗口起点算得对不对」和「换窗口时账有没有归零」：
不跑定时任务，全靠读的时候算、写的时候顺手结旧账。所以两件都要钉死。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

TZ8 = timezone(timedelta(hours=8))


def _pure():
    from app.utils.pure import channel_token as pure
    return pure


# ── 纯函数：窗口算术 ──────────────────────────────────────────

def test_window_none_when_period_empty():
    pure = _pure()
    now = datetime(2026, 11, 1, 10, 30, tzinfo=TZ8)
    assert pure.window_start(now, "", "04:00") is None
    assert pure.window_end(now, "", "04:00") is None
    assert pure.window_start(now, "nonsense", "04:00") is None


def test_daily_window_respects_anchor():
    pure = _pure()
    # 04:00 重置：凌晨 03:00 还属于前一天那个窗口
    assert pure.window_start(datetime(2026, 11, 1, 3, 0, tzinfo=TZ8), "day", "04:00") == datetime(2026, 10, 31, 4, 0, tzinfo=TZ8)
    assert pure.window_start(datetime(2026, 11, 1, 5, 0, tzinfo=TZ8), "day", "04:00") == datetime(2026, 11, 1, 4, 0, tzinfo=TZ8)
    assert pure.window_end(datetime(2026, 11, 1, 5, 0, tzinfo=TZ8), "day", "04:00") == datetime(2026, 11, 2, 4, 0, tzinfo=TZ8)


def test_hourly_resets_on_the_hour():
    pure = _pure()
    # 按小时 = 每个整点换窗口；时间点（锚点）只对"每天及以上"有意义，这里不看它
    assert pure.window_start(datetime(2026, 11, 1, 10, 45, tzinfo=TZ8), "hour", "04:00") == datetime(2026, 11, 1, 10, 0, tzinfo=TZ8)
    assert pure.window_start(datetime(2026, 11, 1, 10, 0, tzinfo=TZ8), "hour", "04:00") == datetime(2026, 11, 1, 10, 0, tzinfo=TZ8)
    assert pure.window_end(datetime(2026, 11, 1, 10, 45, tzinfo=TZ8), "hour", "04:00") == datetime(2026, 11, 1, 11, 0, tzinfo=TZ8)


def test_two_day_window_is_aligned_to_epoch():
    pure = _pure()
    a = pure.window_start(datetime(2026, 11, 1, 6, 0, tzinfo=TZ8), "2days", "04:00")
    b = pure.window_start(datetime(2026, 11, 1, 23, 0, tzinfo=TZ8), "2days", "04:00")
    c = pure.window_start(datetime(2026, 11, 3, 5, 0, tzinfo=TZ8), "2days", "04:00")
    assert a == b, "同一窗口内两个时刻的起点必须一样"
    assert c - a == timedelta(days=2), "跨一个窗口正好 2 天"


def test_weekly_window_starts_on_monday():
    pure = _pure()
    # 2026-11-01 是周日 → 所属窗口起点是 10-26（周一）
    start = pure.window_start(datetime(2026, 11, 1, 10, 0, tzinfo=TZ8), "week", "04:00")
    assert start == datetime(2026, 10, 26, 4, 0, tzinfo=TZ8)
    assert start.weekday() == 0


def test_anchor_and_period_normalization():
    pure = _pure()
    assert pure.normalize_anchor("4:5") == "04:05"
    assert pure.normalize_anchor("24:00") == "00:00"
    assert pure.normalize_anchor("") == "00:00"
    assert pure.normalize_reset_period(" DAY ") == "day"
    assert pure.normalize_reset_period("monthly") == ""


# ── 服务层：跨窗口的读与写 ────────────────────────────────────

async def _seed(db):
    from db_reset import clear

    await clear(db, "channel_conversation_usage", "channel_token_policy", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '主人', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, ai_type) "
        "VALUES (1, 1, '测额度', 1, true, 'resonance')"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, origin_channel) "
        "VALUES (7, 'QQ 群', 'human', 1, 'default', true, 'qq')"))
    await db.commit()


async def test_window_change_reads_as_zero_and_charge_starts_fresh(migrated_db):
    from app.database import async_session
    from app.services.channel import token_quota

    async with async_session() as db:
        await _seed(db)
        await token_quota.set_policy(db, 1, "qq", limit_tokens=1000, reset_period="day", reset_anchor="04:00")
        await db.commit()

        # 先记一笔，让行存在
        await token_quota.charge(db, agent_id=1, group_id=7,
                                 usage={"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10})
        await db.commit()
        assert (await db.scalar(text(
            "SELECT used_billable FROM channel_conversation_usage WHERE agent_id=1"
        ))) == 10

        # 把这一行伪装成"上一个窗口的旧账"
        await db.execute(text(
            "UPDATE channel_conversation_usage SET used_total=999, used_billable=999, "
            "window_start = :old WHERE agent_id=1"
        ), {"old": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)})
        await db.commit()

        decision = await token_quota.decide(db, agent_id=1, channel_kind="qq", conversation="group:7")
        assert decision["used_tokens"] == 0, "换了窗口，旧账不算进这一轮"
        assert decision["allowed"] is True

        overview = await token_quota.overview(db, 1, "qq")
        assert overview["conversations"][0]["used_tokens"] == 0
        assert overview["reset"]["period"] == "day"
        assert overview["reset"]["anchor"] == "04:00"
        assert overview["reset"]["next_reset_at"], "界面要显示下次重置时间"

        # 跨窗口后的第一笔账：先把旧窗口结掉，再记这一笔
        await token_quota.charge(db, agent_id=1, group_id=7,
                                 usage={"prompt_tokens": 7, "completion_tokens": 0, "total_tokens": 7})
        await db.commit()
        row = (await db.execute(text(
            "SELECT used_total, used_billable, window_start FROM channel_conversation_usage WHERE agent_id=1"
        ))).mappings().first()
        assert row["used_billable"] == 7, "旧窗口的 999 必须被结掉"
        assert row["used_total"] == 7
        assert row["window_start"] is not None


async def test_manual_reset_aligns_window(migrated_db):
    from app.database import async_session
    from app.services.channel import token_quota

    async with async_session() as db:
        await _seed(db)
        await token_quota.set_policy(db, 1, "qq", limit_tokens=1000, reset_period="day", reset_anchor="04:00")
        await token_quota.charge(db, agent_id=1, group_id=7,
                                 usage={"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5})
        await db.commit()

        await token_quota.reset_usage(db, 1, "qq")
        await db.commit()
        row = (await db.execute(text(
            "SELECT used_billable, window_start FROM channel_conversation_usage WHERE agent_id=1"
        ))).mappings().first()
        assert row["used_billable"] == 0
        assert row["window_start"] is not None, "手动重置后要落在当前窗口，不然下一次读又当成旧账"
