"""通俗模式（ui_prefs.plain_language）必须真的落库 —— 用户 2026-09-23「滑钮打不开」。

前端只做两件事：PUT /user/settings 写偏好 → GET /auth/me 读回来。
所以这里的判据也必须是"另一个会话从库里读得到"，而不是看接口返回值——
JSON 列的原地 update 不被 SQLAlchemy 跟踪时，返回值对、库里没有，正是滑块回弹的样子。
"""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

USER_ID = 9


async def _seed_user() -> None:
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, ui_prefs) "
            "VALUES (:uid, '通俗自测', 'x', 'human', CAST(:prefs AS json))"
        ), {"uid": USER_ID, "prefs": json.dumps({"theme_color": "blue"})})
        await db.commit()


async def test_plain_language_survives_a_fresh_session(migrated_db):
    """写进去的 plain_language，换一个会话还得读得到（别的键也不能被清掉）。"""
    from app.database import async_session
    from app.repositories.user_repo import SQLAlchemyUserRepository
    from app.services.infrastructure.auth_service import update_user_settings

    await _seed_user()
    async with async_session() as db:
        info = await update_user_settings(
            user_id=USER_ID,
            user_repo=SQLAlchemyUserRepository(db),
            api_key_pool_repo=None,
            ui_prefs={"plain_language": True},
        )
        assert info["ui_prefs"].get("plain_language") is True, f"返回值就不对：{info['ui_prefs']}"
        await db.commit()

    async with async_session() as db:
        saved = (await db.execute(
            text("SELECT ui_prefs FROM users WHERE id = :uid"), {"uid": USER_ID}
        )).scalar_one()
        assert saved.get("plain_language") is True, f"没落库（滑钮会回弹）：{saved}"
        assert saved.get("theme_color") == "blue", f"顺手的键被清掉了：{saved}"
