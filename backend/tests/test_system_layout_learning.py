"""布局结论的自纠正（#7）：对端报「system 必须在开头」时学一次，下一轮生效。

三层各管一件事：llm 只识别（抛类型化异常、不碰库）、channel_layout 只学习
（唯一写 system_layout 的地方）、executor 只触发（不重放请求）。
"""
import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.anyio

BASE = "https://llm.example.com/v1"
LEADING_ERROR = "400 Bad Request: System message must be at the beginning"


def test_only_the_leading_error_is_recognized():
    from app.utils.pure.chat_template import is_system_layout_error

    assert is_system_layout_error(LEADING_ERROR)
    assert is_system_layout_error("system message must be at the beginning")
    assert is_system_layout_error("Error: System message must be at the beginning (Jinja)")
    # 其它 400 不触发：参数错、模型不存在都不该改布局
    assert not is_system_layout_error("Invalid request: messages[0].role")
    assert not is_system_layout_error("")


def test_classification_routes_the_layout_error_separately():
    from app.ai.llm import (
        KeyFatalError, RateLimitError, ServerError, SystemLayoutError,
        _raise_classified_error,
    )

    with pytest.raises(SystemLayoutError) as caught:
        _raise_classified_error(400, LEADING_ERROR, base_url=BASE)
    assert caught.value.base_url == BASE

    with pytest.raises(Exception) as plain:
        _raise_classified_error(400, "Invalid request: temperature")
    assert not isinstance(plain.value, SystemLayoutError)

    with pytest.raises(ServerError):
        _raise_classified_error(503, "overloaded")
    with pytest.raises(RateLimitError):
        _raise_classified_error(429, "slow down")
    with pytest.raises(KeyFatalError):
        _raise_classified_error(401, "bad key")


async def _seed_provider(db, base_url: str = BASE, *, layout: str = "anywhere",
                         source: str = "preset"):
    from db_reset import clear

    await clear(db, "user_api_providers", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) "
        "VALUES (1, 'layout-user', 'x', 'human')"
    ))
    await db.execute(text(
        "INSERT INTO user_api_providers "
        "(user_id, kind, name, base_url, system_layout, system_layout_source) "
        "VALUES (1, 'llm', '自建', :base_url, :layout, :source)"
    ), {"base_url": base_url, "layout": layout, "source": source})
    await db.commit()


async def test_learn_writes_leading_once(migrated_db):
    from app.database import async_session
    from app.models.api_profile import UserApiProvider
    from app.services.agent.channel_layout import _learned_at, learn_from_error

    _learned_at.clear()
    async with async_session() as db:
        await _seed_provider(db)
        # 调用点传的是 chat 请求的 base（provider.base_url 同形），不是完整 chat URL
        assert await learn_from_error(db, BASE, LEADING_ERROR) is True

        row = (await db.execute(select(UserApiProvider))).scalars().first()
        assert (row.system_layout, row.system_layout_source) == ("leading", "detected")

        # 内存桶拦第二次调用（同一通道 10 分钟只学一次）
        assert await learn_from_error(db, BASE, LEADING_ERROR) is False
        # 桶清掉后仍不写：DB 已经是 leading + detected（幂等）
        _learned_at.clear()
        assert await learn_from_error(db, BASE, LEADING_ERROR) is False


async def test_learn_ignores_other_errors_and_unknown_base(migrated_db):
    from app.database import async_session
    from app.services.agent.channel_layout import _learned_at, learn_from_error

    _learned_at.clear()
    async with async_session() as db:
        await _seed_provider(db)
        assert await learn_from_error(db, BASE, "Invalid request: temperature") is False
        assert await learn_from_error(db, "https://other.example.com/v1", LEADING_ERROR) is False
