"""兑换码核销规则：一次性 / 不限人次 / 限期 / 每人一次。

以前核销只看 used_by，有效期根本没判——2026-11-01 管理页上一张 8 月就过期的码还显示
「可用」。现在规则收敛在 redemption_service，这里把四条规则和列表状态一起钉住。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed(db):
    from db_reset import clear

    await clear(db, "redemption_records", "redemption_codes", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type, ai_quota, api_credit) VALUES "
        "(1, '甲', 'x', 'human', 0, 0), (2, '乙', 'x', 'human', 0, 0), (3, '丙', 'x', 'human', 0, 0)"
    ))
    await db.commit()


async def _make_code(db, *, code, limit=1, amount=5, code_type="ai_quota", expires_in_days=30):
    """expires_in_days 传负数=已过期，传 None=永不过期"""
    from app.models.redemption import RedemptionCode

    obj = RedemptionCode(
        code=code,
        quota_amount=amount,
        code_type=code_type,
        expires_at=None if expires_in_days is None else _now() + timedelta(days=expires_in_days),
        created_by=1,
        usage_limit=limit,
    )
    db.add(obj)
    await db.commit()
    return obj


async def test_single_use_code_refuses_second_user(migrated_db):
    from app.database import async_session
    from app.services.infrastructure.redemption_service import RedemptionError, redeem

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-ONCE", limit=1, amount=5)

        out = await redeem(db, user_id=1, code="RC-ONCE")
        await db.commit()
        assert out["ai_quota"] == 5

        with pytest.raises(RedemptionError) as err:
            await redeem(db, user_id=2, code="RC-ONCE")
        assert str(err.value) == "兑换码已被使用"
        assert err.value.status == 400


async def test_unlimited_code_each_user_only_once(migrated_db):
    """活动码：不限人次，但同一个人第二次会被自己挡在门外"""
    from app.database import async_session
    from app.services.infrastructure.redemption_service import (
        RedemptionError, list_redemptions, redeemed_count, redeem,
    )

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-CAMPAIGN", limit=None, amount=3)

        await redeem(db, user_id=1, code="RC-CAMPAIGN")
        await redeem(db, user_id=2, code="RC-CAMPAIGN")
        await db.commit()
        assert await redeemed_count(db, "RC-CAMPAIGN") == 2

        with pytest.raises(RedemptionError) as err:
            await redeem(db, user_id=1, code="RC-CAMPAIGN")
        assert str(err.value) == "您已兑换过此码"
        assert err.value.status == 409

        rows = await list_redemptions(db, "RC-CAMPAIGN")
        assert {r["username"] for r in rows} == {"甲", "乙"}
        assert all(r["quota_amount"] == 3 for r in rows)


async def test_seat_limited_code_stops_at_limit(migrated_db):
    from app.database import async_session
    from app.services.infrastructure.redemption_service import RedemptionError, redeem

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-FIRST2", limit=2, amount=1)

        await redeem(db, user_id=1, code="RC-FIRST2")
        await redeem(db, user_id=2, code="RC-FIRST2")
        await db.commit()

        with pytest.raises(RedemptionError) as err:
            await redeem(db, user_id=3, code="RC-FIRST2")
        assert str(err.value) == "兑换码已兑完"


async def test_expired_code_is_refused_and_grants_nothing(migrated_db):
    from app.database import async_session
    from app.services.infrastructure.redemption_service import RedemptionError, redeem
    from app.models.user import User

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-OLD", limit=None, amount=9, expires_in_days=-1)

        with pytest.raises(RedemptionError) as err:
            await redeem(db, user_id=1, code="RC-OLD")
        assert str(err.value) == "兑换码已过期"

        user = await db.get(User, 1)
        assert (user.ai_quota or 0) == 0, "过期码不能发出任何额度"


async def test_list_codes_reports_expired_and_exhausted(migrated_db):
    """列表状态由后端算：8 月到期、没人兑过的码必须是 expired，不是「可用」"""
    from app.database import async_session
    from app.services.infrastructure.redemption_service import list_codes, redeem

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-OLD", limit=1, expires_in_days=-1)
        await _make_code(db, code="RC-CAMP", limit=None)
        await _make_code(db, code="RC-FULL", limit=1)
        await redeem(db, user_id=1, code="RC-FULL")
        await db.commit()

        listed = {c["code"]: c for c in await list_codes(db)}
        assert listed["RC-OLD"]["state"] == "expired"
        assert listed["RC-CAMP"]["state"] == "available"
        assert listed["RC-CAMP"]["usage_limit"] is None
        assert listed["RC-FULL"]["state"] == "exhausted"
        assert listed["RC-FULL"]["redeemed_count"] == 1
        assert listed["RC-CAMP"]["redeemed_count"] == 0


async def test_grant_goes_to_the_matching_pool(migrated_db):
    from app.database import async_session
    from app.services.infrastructure.redemption_service import redeem
    from app.models.user import User

    async with async_session() as db:
        await _seed(db)
        await _make_code(db, code="RC-CREDIT", limit=1, amount=7, code_type="api_credit")
        await _make_code(db, code="RC-FILE", limit=1, amount=100, code_type="file_quota")

        await redeem(db, user_id=1, code="RC-CREDIT")
        await redeem(db, user_id=1, code="RC-FILE")
        await db.commit()

        user = await db.get(User, 1)
        assert user.api_credit == 7
        assert user.file_quota_mb == 100
        assert user.file_quota_bonus_mb == 100, "文件配额要同时记在追加额度里（退订/回收要认它）"
        assert (user.ai_quota or 0) == 0
