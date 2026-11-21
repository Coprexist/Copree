"""
兑换码服务：生成、核销、查询

核销的规则以前散在路由里（只看 used_by，过期根本没判——2026-11-01 管理页上 8 月就过期的
码还显示「可用」）。现在三条规则只有这一份实现：

  1. expires_at 过了            → 不能兑，管理端也据此显示「已过期」
  2. usage_limit 到顶           → 不能兑（NULL = 不限人次的活动码）
  3. 同一用户同一个码只能兑一次 → (code, user_id) 唯一约束兜底

并发：核销前对码行 SELECT ... FOR UPDATE，「数够不够」与「写流水」在同一事务里，
两个请求同时抢最后一个人次时后到的会被行锁挡住、再看到已满。
"""
import secrets
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.redemption import RedemptionCode, RedemptionRecord
from app.models.user import User

CODE_TYPES = ("ai_quota", "api_credit", "agent_bundle", "file_quota")
# v0.1.5 之前的类型名，写库/回填时统一成新名字
_LEGACY_TYPE_MAP = {"file_size": "file_quota"}

# 状态由后端算，前端只渲染：过期判断不该由每个调用方各写一遍
STATE_AVAILABLE = "available"
STATE_EXHAUSTED = "exhausted"
STATE_EXPIRED = "expired"


class RedemptionError(Exception):
    """业务性失败（码无效/过期/兑完/已兑过）——路由翻译成 HTTP 状态码"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def canonical_type(code_type: str | None) -> str:
    value = code_type or "ai_quota"
    return _LEGACY_TYPE_MAP.get(value, value)


def _now() -> datetime:
    """库里一律存 naive UTC（与全站其余时间字段一致）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def code_state(code_obj: RedemptionCode, redeemed_count: int,
               now: datetime | None = None) -> str:
    """过期优先于兑完：用户看到的第一个理由是「这张活动结束了」"""
    now = now or _now()
    if code_obj.expires_at and code_obj.expires_at < now:
        return STATE_EXPIRED
    if code_obj.usage_limit is not None and redeemed_count >= code_obj.usage_limit:
        return STATE_EXHAUSTED
    return STATE_AVAILABLE


# ══════════════════════════════════════════════════════════════
# 生成 / 查询
# ══════════════════════════════════════════════════════════════

async def create_code(
    db: AsyncSession,
    *,
    admin_id: int,
    quota_amount: int,
    code_type: str,
    expires_in_days: int,
    note: str | None = None,
    usage_limit: int | None = 1,
    max_usage: int | None = None,
    is_api_pool: bool = False,
) -> RedemptionCode:
    """usage_limit=None 即不限人次的活动码（每人仍限一次）"""
    code_obj = RedemptionCode(
        code="RC-" + secrets.token_hex(8).upper(),
        quota_amount=quota_amount,
        code_type=canonical_type(code_type),
        expires_at=_now() + timedelta(days=expires_in_days),
        created_by=admin_id,
        note=note,
        usage_limit=usage_limit,
        max_usage=max_usage,
        is_api_pool=is_api_pool,
    )
    db.add(code_obj)
    await db.flush()
    return code_obj


async def get_code(db: AsyncSession, code: str, *, for_update: bool = False) -> RedemptionCode | None:
    stmt = select(RedemptionCode).where(RedemptionCode.code == code)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def redeemed_count(db: AsyncSession, code: str) -> int:
    total = await db.scalar(
        select(func.count()).select_from(RedemptionRecord).where(RedemptionRecord.code == code)
    )
    return int(total or 0)


async def user_redeemed(db: AsyncSession, code: str, user_id: int) -> bool:
    found = await db.scalar(
        select(RedemptionRecord.id).where(
            RedemptionRecord.code == code, RedemptionRecord.user_id == user_id
        )
    )
    return found is not None


async def list_codes(db: AsyncSession) -> list[dict]:
    """一次聚合出每个码的已兑人次与最近兑换时间，避免逐行 N+1"""
    codes = (await db.execute(
        select(RedemptionCode).order_by(RedemptionCode.expires_at.desc())
    )).scalars().all()

    stats = {
        row.code: (int(row.total or 0), row.last_at)
        for row in (await db.execute(
            select(
                RedemptionRecord.code,
                func.count().label("total"),
                func.max(RedemptionRecord.redeemed_at).label("last_at"),
            ).group_by(RedemptionRecord.code)
        )).all()
    }

    now = _now()
    result = []
    for c in codes:
        total, last_at = stats.get(c.code, (0, None))
        result.append({
            "code": c.code,
            "quota_amount": c.quota_amount,
            "code_type": canonical_type(c.code_type),
            "expires_at": str(c.expires_at) if c.expires_at else None,
            "note": c.note,
            "max_usage": c.max_usage,
            "is_api_pool": bool(c.is_api_pool),
            "usage_limit": c.usage_limit,
            "redeemed_count": total,
            "last_redeemed_at": str(last_at) if last_at else None,
            "state": code_state(c, total, now=now),
            "created_at": str(c.created_at) if c.created_at else None,
        })
    return result


async def list_redemptions(db: AsyncSession, code: str, *, limit: int = 200) -> list[dict]:
    """某个码的兑换流水（管理端展开查看谁兑的）"""
    rows = (await db.execute(
        select(
            RedemptionRecord.user_id,
            User.username,
            RedemptionRecord.quota_amount,
            RedemptionRecord.code_type,
            RedemptionRecord.redeemed_at,
        )
        .join(User, User.id == RedemptionRecord.user_id, isouter=True)
        .where(RedemptionRecord.code == code)
        .order_by(RedemptionRecord.redeemed_at.desc())
        .limit(limit)
    )).all()
    return [
        {
            "user_id": r.user_id,
            "username": r.username,
            "quota_amount": r.quota_amount,
            "code_type": canonical_type(r.code_type),
            "redeemed_at": str(r.redeemed_at) if r.redeemed_at else None,
        }
        for r in rows
    ]


# ══════════════════════════════════════════════════════════════
# 核销
# ══════════════════════════════════════════════════════════════

def _grant(user: User, code_type: str, amount: int) -> str:
    """按类型发额度，返回给用户看的话术（与旧行为逐字一致）"""
    if code_type == "api_credit":
        user.api_credit = (user.api_credit or 0) + amount
        return f"兑换成功，获得 {amount} 通用 API 额度"
    if code_type == "agent_bundle":
        user.agent_bundle_credit = (user.agent_bundle_credit or 0) + amount
        return f"兑换成功，获得 {amount} AI 包断额度"
    if code_type == "file_quota":
        user.file_quota_mb = (user.file_quota_mb or 0) + amount
        user.file_quota_bonus_mb = (user.file_quota_bonus_mb or 0) + amount
        return f"兑换成功，获得 {amount} MB 文件存储配额"
    user.ai_quota = (user.ai_quota or 0) + amount
    return f"兑换成功，获得 {amount} AI 创建额度"


async def redeem(db: AsyncSession, *, user_id: int, code: str) -> dict:
    """核销一个码；失败抛 RedemptionError（含应返回的 HTTP 状态）"""
    code_obj = await get_code(db, code, for_update=True)
    if code_obj is None:
        raise RedemptionError("兑换码无效", 404)

    # 本人是否兑过先答：对已兑过的人说「已兑完」会让他以为码还有别人在抢
    if await user_redeemed(db, code_obj.code, user_id):
        raise RedemptionError("您已兑换过此码", 409)

    total = await redeemed_count(db, code_obj.code)
    state = code_state(code_obj, total)
    if state == STATE_EXPIRED:
        raise RedemptionError("兑换码已过期")
    if state == STATE_EXHAUSTED:
        # 一次性码沿用旧话术：对用户来说「被使用了」比「兑完了」更准确
        if code_obj.usage_limit == 1:
            raise RedemptionError("兑换码已被使用")
        raise RedemptionError("兑换码已兑完")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise RedemptionError("用户不存在", 404)

    code_type = canonical_type(code_obj.code_type)
    amount = code_obj.quota_amount
    message = _grant(user, code_type, amount)

    db.add(RedemptionRecord(
        code=code_obj.code, user_id=user_id, quota_amount=amount, code_type=code_type,
    ))
    try:
        await db.flush()
    except IntegrityError:
        # 行锁之外的兜底：同一个人两个请求撞在一起时，唯一约束说了算
        await db.rollback()
        raise RedemptionError("您已兑换过此码", 409)

    return {
        "message": message,
        "ai_quota": user.ai_quota,
        "api_credit": user.api_credit,
        "agent_bundle_credit": user.agent_bundle_credit,
        "file_quota_mb": user.file_quota_mb,
    }
