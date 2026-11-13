"""通道身份目录（平台表）—— 查询口径与"插件不直接碰表"这条契约

这张表归平台：一张表认所有通道的人，放行状态、锚点账号、跨应用认亲都在它上面。
插件只经 app/services/channel/identity.py 与 anchor.py 读写；插件自己去 SELECT
意味着表结构与放行状态机成了两份契约，哪天表改了口径就得挨个插件改。
"""
import re
from pathlib import Path

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


async def test_lookups_answer_only_what_was_asked(migrated_db):
    """四个只读口径：有没有这一行、接的哪个账号、跨应用标识、@ 翻译用的联系人"""
    from app.database import async_session
    from app.services.channel import identity

    from db_reset import clear

    from app.models.user import User
    from app.utils.auth import hash_password

    async with async_session() as db:
        await clear(db, "external_identities", "users")
        anchor = User(username="锚点小绿", password_hash=hash_password("x" * 12),
                      email="ANCHOR@qq.bridge", type="external")
        db.add(anchor)
        await db.flush()
        await identity.ensure(
            db, kind="qq", owner_scope="bot-a", origin="OPENID-A",
            display_name="小明", commit=False,
        )
        record = await identity.ensure(
            db, kind="qq", owner_scope="bot-a", origin="OPENID-B",
            display_name="小红", commit=False,
        )
        record.union_id = "UNION-1"
        record.user_id = int(anchor.id)
        await db.commit()
        anchor_id = int(anchor.id)

        # 有这一行 / 没这一行；别的实例下的同一个地址不算
        assert await identity.has(db, kind="qq", owner_scope="bot-a", origin="OPENID-A") is True
        assert await identity.has(db, kind="qq", owner_scope="bot-a", origin="OPENID-Z") is False
        assert await identity.has(db, kind="qq", owner_scope="bot-b", origin="OPENID-A") is False

        # 接的账号：没接上是 0，不是 None（调用方拿它直接当 id 用）
        assert await identity.user_id_of(db, kind="qq", owner_scope="bot-a", origin="OPENID-B") == anchor_id
        assert await identity.user_id_of(db, kind="qq", owner_scope="bot-a", origin="OPENID-A") == 0

        # 跨应用标识：没记过是空串
        assert await identity.union_id_of(db, kind="qq", owner_scope="bot-a", origin="OPENID-B") == "UNION-1"
        assert await identity.union_id_of(db, kind="qq", owner_scope="bot-a", origin="OPENID-A") == ""

        # 出站 @ 翻译：只回被点名的那个，且只在问的这个实例下
        assert await identity.contacts(
            db, kind="qq", owner_scope="bot-a", user_ids=[anchor_id, 7],
        ) == {anchor_id: "OPENID-B"}
        assert await identity.contacts(db, kind="qq", owner_scope="bot-b", user_ids=[anchor_id]) == {}
        assert await identity.contacts(db, kind="qq", owner_scope="bot-a", user_ids=[]) == {}


async def test_plugins_do_not_read_the_identity_table_directly():
    """插件只经平台服务读写身份目录：直接 import 模型或裸 SQL 都算越界"""
    offenders = []
    raw_sql = re.compile(r"(?i)\b(?:from|into|update|join)\s+external_identities\b")
    for path in sorted(PLUGINS.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "ExternalIdentity" in text:
            offenders.append(f"{path.relative_to(PLUGINS.parent)}: 直接 import 了身份模型")
        elif raw_sql.search(text):
            offenders.append(f"{path.relative_to(PLUGINS.parent)}: 裸 SQL 查了身份表")
    assert not offenders, "插件不该直接碰通道身份表，改走 app/services/channel/identity.py：" + "; ".join(offenders)
