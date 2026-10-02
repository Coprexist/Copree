"""表情包插件：载荷解析、图片白名单、全局开关。

表情包的核心不是图，是「写法 → 长相」这张表：写法 [表情:名字] 是唯一真源
（QQ 来消息也归一成同一个写法），所以解析要严——不合法的单条丢掉、包目录外的文件一律不发。
"""
import json
import tempfile
from pathlib import Path


def _write_pack(faces, *, category="emojipack", files=()):
    """造一个磁盘上的表情包目录，返回它的 manifest（不碰真插件目录）"""
    root = Path(tempfile.mkdtemp(prefix="emoji-pack-"))
    (root / "emoji.json").write_text(
        json.dumps({"usage": "测试用", "faces": faces}, ensure_ascii=False), encoding="utf-8",
    )
    for rel, data in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"id": "test-pack", "name": "测试包", "category": category,
            "_dir": str(root), "entry": "emoji.json"}


def test_pack_parsing_keeps_what_is_usable_and_drops_the_rest():
    from app.services.plugin import catalog

    manifest = _write_pack([
        {"id": "shy", "name": "害羞", "emoji": "😳"},
        {"id": "cat", "name": "猫猫", "file": "cat.png"},
        {"id": "cat", "name": "重复的猫", "emoji": "🐱"},            # 同 id：后来的丢掉
        {"id": "Bad Id", "name": "坏 id", "emoji": "🙂"},
        {"id": "noname", "emoji": "🙂"},
        {"id": "escape", "name": "越狱", "file": "../plugin.json"},  # 想跳目录：当没有这张图
        {"id": "script", "name": "脚本", "file": "x.svg"},           # 非光栅后缀：拒绝
        {"id": "empty", "name": "什么都没有"},
    ])
    pack = catalog.get_emoji_pack(manifest)

    assert [f["id"] for f in pack["faces"]] == ["shy", "cat"], pack["faces"]
    assert pack["faces"][0]["emoji"] == "😳"
    assert pack["faces"][1]["file"] == "cat.png"


def test_pack_faces_are_capped():
    from app.services.plugin import catalog

    manifest = _write_pack([
        {"id": f"f{i}", "name": f"表情{i}", "emoji": "🙂"}
        for i in range(catalog.EMOJI_FACE_MAX + 20)
    ])
    assert len(catalog.get_emoji_pack(manifest)["faces"]) == catalog.EMOJI_FACE_MAX


def test_only_declared_raster_assets_are_served():
    from app.services.plugin import catalog

    manifest = _write_pack(
        [{"id": "cat", "name": "猫猫", "file": "cat.png"}],
        files=[("cat.png", b"\x89PNG"), ("unlisted.png", b"\x89PNG")],
    )

    served = catalog.emoji_asset_path(manifest, "cat.png")
    assert served is not None and served.name == "cat.png"
    assert catalog.emoji_asset_path(manifest, "unlisted.png") is None, "没写进 emoji.json 的文件不发"
    assert catalog.emoji_asset_path(manifest, "../plugin.json") is None


def test_other_categories_are_not_emoji_packs():
    from app.services.plugin import catalog

    manifest = _write_pack([{"id": "shy", "name": "害羞", "emoji": "😳"}], category="skin")
    assert catalog.get_emoji_pack(manifest) == {}


def test_builtin_qq_pack_is_well_formed():
    """内置包随代码走，AI 照它的名字发表情——名字写错就会显示成文字"""
    from app.services.plugin import catalog

    pack = catalog.get_emoji_pack(catalog.scan_disk()["emoji-qq"])

    assert len(pack["faces"]) >= 40, len(pack["faces"])
    assert pack["usage"], "插件要自带用法说明，否则只能靠模型自己猜场合"
    assert len({f["id"] for f in pack["faces"]}) == len(pack["faces"])
    assert all(f["name"] and (f["emoji"] or f["file"]) for f in pack["faces"])
    assert any(f["name"] == "害羞" for f in pack["faces"]), "QQ 来的 [表情:害羞] 要能对上"


async def test_enabled_packs_follow_the_global_switch(migrated_db):
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog
    from db_reset import clear

    async with async_session() as db:
        await clear(db, "plugins", "user_plugin_prefs")
        await catalog.sync_plugins_to_db(db)
        catalog.invalidate_caches()           # 换库/换目录后要清缓存，生产由 toggle/装卸钩子负责
        assert any(p["id"] == "emoji-qq" for p in await catalog.enabled_emoji_packs(db))

        row = await db.get(Plugin, "emoji-qq")
        row.enabled = False
        await db.commit()
        assert any(p["id"] == "emoji-qq" for p in await catalog.enabled_emoji_packs(db)), \
            "30 秒缓存内不该读到关闭——失效钩子才是真相变化的入口"

        catalog.invalidate_caches()
        assert not any(p["id"] == "emoji-qq" for p in await catalog.enabled_emoji_packs(db))


def test_resolve_emoji_text_leaves_code_untouched():
    """代码里的 :x: 是在举例。

    定界按"等长 run 配对"（CommonMark 的思路），不是数反引号奇偶：正文里一个落单的反引号
    会把后面整段代码块翻成"代码外"，Sphinx 风格的 :param: 就会被当表情吃掉。
    """
    from app.services.plugin import catalog

    packs = [{"id": "demo", "name": "演示", "usage": "",
              "faces": [{"id": "shy", "name": "害羞", "emoji": "😳", "file": ""}]}]

    fence = "```python\n# 用法：:shy: 表示笑脸\n```"
    assert catalog.resolve_emoji_text(fence, packs) == fence

    stray = "正文里一个落单的反引号 ` 然后\n" + fence
    assert catalog.resolve_emoji_text(stray, packs) == stray, "落单反引号不该把代码块翻出来"

    inline = "举例 `:shy:` 与 :shy: 都要看"
    assert catalog.resolve_emoji_text(inline, packs) == "举例 `:shy:` 与 😳 都要看"


def test_long_emoji_is_ignored_instead_of_truncated():
    """组合序列（ZWJ + 肤色）远超一个字符：截断会渲染成几个分开的表情，比忽略更糟"""
    from app.services.plugin import catalog

    # 带肤色的四口之家 11 个 codepoint：旧实现按 8 字符截断，正好切在 ZWJ 上
    family = "👨🏽‍👩🏽‍👧🏽‍👦🏽"
    assert len(family) > 8
    assert catalog._emoji_face({"id": "fam", "name": "一家", "emoji": family})["emoji"] == family

    too_long = "🙂" * 40
    assert catalog._emoji_face({"id": "long", "name": "太长", "emoji": too_long}) is None
    kept = catalog._emoji_face({"id": "long2", "name": "太长", "emoji": too_long, "file": "x.png"})
    assert (kept["emoji"], kept["file"]) == ("", "x.png"), kept
    assert catalog._emoji_face({"id": "space", "name": "带空格", "emoji": "a b"}) is None


def test_skin_vars_reject_non_hex():
    """皮肤值直接写进 CSS 变量，只判 startswith('#') 等于把无效样式放进前端"""
    import json
    import tempfile
    from pathlib import Path

    from app.services.plugin import catalog

    root = Path(tempfile.mkdtemp(prefix="skin-pack-"))
    (root / "skin.json").write_text(json.dumps({
        "light": {"primary_500": "#10B981", "accent_500": "#zzz", "mint_400": "#", "rose_400": "#12"},
        "dark": {"primary_500": "#059669"},
    }), encoding="utf-8")
    manifest = {"id": "skin-x", "category": "skin", "entry": "skin.json", "_dir": str(root)}

    assert catalog.get_skin_vars(manifest) == {
        "light": {"primary_500": "#10B981"},
        "dark": {"primary_500": "#059669"},
    }


async def test_cached_packs_are_handed_out_as_copies(migrated_db):
    """缓存共享对象时，调用方一个 append 就能污染 30 秒；交付的必须是副本"""
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog
    from db_reset import clear

    async with async_session() as db:
        await clear(db, "plugins", "user_plugin_prefs")
        await catalog.sync_plugins_to_db(db)
        catalog.invalidate_caches()
        first = await catalog.enabled_emoji_packs(db)

    first.clear()
    first_pack = await catalog.enabled_emoji_packs(db)
    assert first_pack, "清空返回的列表不该影响缓存"

    first_pack[0]["faces"].clear()
    assert (await catalog.enabled_emoji_packs(db))[0]["faces"], "清空 faces 不该影响缓存"

    # 磁盘扫描同样交副本：调用方 pop 掉一个插件不该影响下一次扫描
    disk = catalog.scan_disk()
    disk.pop("emoji-qq", None)
    assert "emoji-qq" in catalog.scan_disk(), "scan_disk 也要交副本"


async def test_sync_plugins_takes_both_session_and_repository(migrated_db):
    """同步只依赖 PluginRepository 声明的方法，传 session 或传 repo 结果必须一致

    调用方（启动、路由）传的都是 AsyncSession，服务内部经兼容层落到 repo 上；
    两种入参一旦分叉，替换 repo 实现时就会有一半路径静默不走新实现。
    """
    from app.database import async_session
    from app.repositories.plugin_repo import SQLAlchemyPluginRepository
    from app.services.plugin import catalog
    from db_reset import clear

    async with async_session() as db:
        await clear(db, "plugins", "user_plugin_prefs")
        catalog.invalidate_caches()
        via_repo = await catalog.sync_plugins_to_db(SQLAlchemyPluginRepository(db))

    async with async_session() as db:
        await clear(db, "plugins", "user_plugin_prefs")
        catalog.invalidate_caches()
        via_session = await catalog.sync_plugins_to_db(db)

    assert via_repo > 0, "清空后首次同步应写入全部磁盘插件"
    assert via_session == via_repo, f"两种入参结果分叉：repo={via_repo} session={via_session}"


def test_resolve_emoji_text_follows_the_contract():
    """正文里只允许两种写法：unicode 字符（标准表情）与 :id: 短码（自定义表情）。

    [表情:名字] 只是兼容输入 —— 它不该再出现在库里、AI 上下文里、导出的文件里。
    """
    from app.services.plugin import catalog

    packs = [{
        "id": "demo", "name": "演示", "usage": "",
        "faces": [
            {"id": "shy", "name": "害羞", "emoji": "😳", "file": ""},
            {"id": "cat", "name": "猫猫", "emoji": "", "file": "cat.png"},
        ],
    }]

    assert catalog.resolve_emoji_text("[表情:害羞]", packs) == "😳", "旧写法在入口换成字符"
    assert catalog.resolve_emoji_text(":shy:", packs) == "😳"
    assert catalog.resolve_emoji_text(":害羞:", packs) == ":害羞:", \
        "短码限 ASCII（业界惯例）；中文名只在兼容写法 [表情:害羞] 里当别名"
    assert catalog.resolve_emoji_text("[表情:猫猫]", packs) == ":cat:", "只有图的规范成短码"
    assert catalog.resolve_emoji_text(":cat:", packs) == ":cat:"
    assert catalog.resolve_emoji_text(":没这个:", packs) == ":没这个:", "认不出就原样留着"
    inline = "行内代码 \u0060[表情:害羞]\u0060 不动，外面的 [表情:害羞] 要动"
    assert catalog.resolve_emoji_text(inline, packs) == "行内代码 \u0060[表情:害羞]\u0060 不动，外面的 😳 要动"
    assert catalog.resolve_emoji_text("时间 12:30:45 不该被当成短码", packs) == "时间 12:30:45 不该被当成短码"
    assert catalog.resolve_emoji_text("没有表情", packs) == "没有表情"


async def test_entry_normalizes_emoji_before_storing(migrated_db):
    """入口归一：落库的正文只有字符或标准短码，下游（AI 上下文、导出、通道出口）不用再解析"""
    from sqlalchemy import text

    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog
    from db_reset import clear

    async with async_session() as db:
        await clear(db, "plugins", "messages", "group_members", "groups", "users", "agents")
        for table in ("users", "groups", "messages"):
            await db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 900)"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES (900, '归一', 'x', 'human')"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (900, '归一测试群', 'human', 900, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) "
            "VALUES (900, 'human', 900, 'owner')"
        ))
        await catalog.sync_plugins_to_db(db)
        row = await db.get(Plugin, "emoji-qq")
        row.enabled = True
        await db.commit()
        catalog.invalidate_caches()

        both = await send_gm_message(
            db, group_id=900, sender_type="human", sender_id=900,
            content="[表情:害羞] 和 :qq_shy:",
        )
        unknown = await send_gm_message(
            db, group_id=900, sender_type="human", sender_id=900, content=":没这个:",
        )
        await db.commit()

        assert both.content == "😳 和 😳", both.content
        assert unknown.content == ":没这个:", unknown.content
