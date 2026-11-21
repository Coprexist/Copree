"""AI 级表情包开关：默认白名单，创建者给每个 AI 勾选；黑名单模式反过来

平台装好包（插件）只是"有这个资源"；能不能用是这个 AI 的事——插件不知道部署里有哪些 AI，
所以权限只在这里算（catalog.packs_for_agent），入口归一按"谁在说"取。
"""
import os
import tempfile

from sqlalchemy import text

AGENT, AGENT_USER, HUMAN, GROUP = 7, 120, 1, 5
PACK = "emoji-dafeiyu"
FACE = ":happy_deyi:"


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_references", "file_metadata", "messages", "group_members", "groups",
               "users", "agents", "plugins", "agent_emoji_packs")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:human, '群主', 'x', 'human'), (:ai, '鲸鱼娘账号', 'x', 'ai')"
    ), {"human": HUMAN, "ai": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES (:a, :owner, '鲸鱼娘', :u, true)"
    ), {"a": AGENT, "owner": HUMAN, "u": AGENT_USER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '测试群', 'human', :owner, 'default', true)"
    ), {"g": GROUP, "owner": HUMAN})
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(:g, 'human', :owner, 'owner'), (:g, 'ai', :u, 'member')"
    ), {"g": GROUP, "owner": HUMAN, "u": AGENT_USER})
    await db.commit()
    _install_test_pack()
    from app.services.plugin import catalog

    await catalog.sync_plugins_to_db(db)      # 测试数据目录里的包变成一行插件
    catalog.invalidate_caches()


def _install_test_pack() -> None:
    """在测试数据目录里装一个最小的图片表情包

    conftest 把数据根指向临时目录（别写真实数据卷），所以这里现场造一个包：
    plugin.json + emoji.json + 一张图，够 catalog 扫描与发图用。
    """
    import json

    from app.paths import PLUGINS_DIR

    pack = PLUGINS_DIR / PACK
    (pack / "happy").mkdir(parents=True, exist_ok=True)
    (pack / "happy" / "deyi.png").write_bytes(b"png-bytes")
    (pack / "plugin.json").write_text(json.dumps({
        "id": PACK, "name": "大肥鱼（测试）", "category": "emojipack",
        "version": "1.0.0", "default_enabled": True,
    }, ensure_ascii=False), encoding="utf-8")
    (pack / "emoji.json").write_text(json.dumps({
        "usage": "写 :happy_deyi: 发这张图",
        "faces": [{
            "id": "happy_deyi", "name": "得意", "file": "happy/deyi.png",
            "caption": "得意闭眼拳头", "keywords": "得意 好耶", "mood": "happy",
        }],
    }, ensure_ascii=False), encoding="utf-8")


def _has_pack(packs, pack_id=PACK):
    return any(p["id"] == pack_id for p in packs)


async def test_whitelist_means_off_until_the_owner_turns_it_on(migrated_db):
    from app.database import async_session
    from app.services.plugin import catalog

    async with async_session() as db:
        await _seed(db)
        assert _has_pack(await catalog.enabled_emoji_packs(db)), "包本身要装好并全局启用"
        assert not _has_pack(await catalog.packs_for_agent(db, AGENT)), "默认白名单：没勾 = 不可用"

        await catalog.set_agent_pack(db, AGENT, PACK, True)
        assert _has_pack(await catalog.packs_for_agent(db, AGENT)), "勾上之后才可用"


async def test_blacklist_mode_defaults_on_and_can_be_turned_off(migrated_db):
    from app.config import settings
    from app.database import async_session
    from app.services.plugin import catalog

    original = settings.emoji_pack_mode
    settings.emoji_pack_mode = "blacklist"
    try:
        async with async_session() as db:
            await _seed(db)
            assert _has_pack(await catalog.packs_for_agent(db, AGENT)), "黑名单：没表态 = 可用"
            await catalog.set_agent_pack(db, AGENT, PACK, False)
            assert not _has_pack(await catalog.packs_for_agent(db, AGENT)), "显式关掉才不可用"
    finally:
        settings.emoji_pack_mode = original


async def test_ai_message_only_attaches_allowed_packs(migrated_db):
    """入口按"谁在说"取包：没被允许的 AI 写 :happy_deyi: 不会变成附件"""
    from app.chat.gm import send_gm_message
    from app.config import settings
    from app.database import async_session
    from app.services.plugin import catalog

    workdir = tempfile.mkdtemp(prefix="emoji-pack-")
    original_dir = settings.data_dir
    settings.data_dir = workdir
    try:
        async with async_session() as db:
            await _seed(db)
            denied = await send_gm_message(
                db, group_id=GROUP, sender_type="ai", sender_id=AGENT_USER,
                content=f"看这个 {FACE}",
            )
            await db.commit()
            assert not denied.attachments, "没被允许的包不该发出去"

            await catalog.set_agent_pack(db, AGENT, PACK, True)
            allowed = await send_gm_message(
                db, group_id=GROUP, sender_type="ai", sender_id=AGENT_USER,
                content=f"看这个 {FACE}",
            )
            await db.commit()

        assert allowed.attachments, "允许之后要变成图片附件"
        att = allowed.attachments[0]
        assert att["mime_type"] == "image/png", att
        assert att["path"].startswith("emoji/"), att
        assert os.path.isfile(os.path.join(workdir, att["path"])), "图要落到数据目录"
    finally:
        settings.data_dir = original_dir
