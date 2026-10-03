"""
统一插件目录服务 — 目录即插件（DSH skills 目录同款思路）

约定：
- 插件 = 一个目录 + plugin.json（manifest），目录名即插件 id
- 扫描两个位置（同名 id 后者覆盖前者）：
    1. backend/plugins/          内置插件（随代码走，git 跟踪）
    2. <数据根>/plugins/         用户安装插件（持久化目录，覆盖内置同名）
- category: skin | skill | emojipack | world | other
  - skin     插件 entry=skin.json   → {light:{var:hex}, dark:{var:hex}} 变量覆盖
  - skill    插件 entry=skill.json  → {skills:[{type,name,category,description,config_schema}]}
  - emojipack 插件 entry=emoji.json → {usage, faces:[{id,name,emoji,file}]} 表情资源
- 两级开关：plugins.enabled（管理员全局）+ user_plugin_prefs（用户个人），生效 = 两者都开
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.paths import PLUGINS_DIR
from app.repositories.plugin_repo import PluginRepository, SQLAlchemyPluginRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyPluginRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyPluginRepository(db_or_repo)
    return db_or_repo


# backend/app/services/plugin/catalog.py → backend/
BACKEND_ROOT = Path(__file__).resolve().parents[3]
BUILTIN_PLUGIN_DIR = BACKEND_ROOT / "plugins"
# 用户安装的插件目录：数据根的子目录，布局只在 app.paths 定义（用例可整体替换它）
USER_PLUGIN_DIR = PLUGINS_DIR

MANIFEST_NAME = "plugin.json"

# 类别 → entry 载荷文件名
ENTRY_FILE = {
    "skin": "skin.json",
    "skill": "skill.json",
    "emojipack": "emoji.json",
}

# 表情包：一个表情 = 写法 + 长相。写法 [表情:名字] 是唯一真源（QQ 来消息也归一成同一个写法，
# 见 qq-channel 插件的 humanize_faces）；长相优先用 unicode 字符（各通道都看得见），
# 其次才是包里的图片（只在站内渲染）。
EMOJI_FACE_MAX = 256               # 单包上限：再多翻不完，还每轮占 AI 的清单
# id 是表情的全局短码（正文写成 :id:）。首字符限字母：短码处于全局命名空间，包作者按
# <包>_<脸> 命名（内置包为 qq_shy）即可避开冲突；纯数字写法也因此不可能是表情。
EMOJI_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
# 只发这些后缀：SVG 在同源下带出脚本执行面，表情用光栅图足够
EMOJI_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# emoji 字段的上限按 codepoint 计：组合序列远不止一个字符（一家四口 7 个、再加肤色 11 个），
# 上限只用来挡住明显的垃圾输入，正常表情不该被截断
EMOJI_CHARS_MAX = 32

# 表情在正文里的两种规范写法，新增表达方式前先读这段：
#   标准表情  unicode 字符本身，各通道天然可用，无需解析；
#   自定义表情 ASCII 短码 :id:（Slack / Mastodon / Misskey 同款）。
# [表情:名字] 为早期写法，仅作兼容输入：入口归一后只留上面两种，历史消息渲染仍认。
EMOJI_SHORTCODE_RE = re.compile(r":([a-z][a-z0-9_-]{0,39}):")
EMOJI_LEGACY_RE = re.compile(r"\[表情:([^\]\n]{1,24})\]")
_CODE_RUN_RE = re.compile(r"`+")

# 皮肤值必须是合法 hex：皮肤插件可由用户安装，值会直接写进 CSS 变量，
# 只判 startswith("#") 等于把无效样式放进前端（#zzz 会静默失效）
SKIN_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")

# 皮肤变量 key（与前端 THEME_COLOR_KEYS 一致，另加 bubble）
SKIN_KEYS = [
    "primary_400", "primary_500", "primary_600",
    "accent_400", "accent_500",
    "mint_400", "mint_500",
    "rose_400", "rose_500",
    "bubble",
]


def _scan_dir(root: Path, builtin: bool) -> dict[str, dict[str, Any]]:
    """扫描一个插件根目录，返回 {id: manifest}"""
    found: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        manifest_file = entry / MANIFEST_NAME
        if not manifest_file.exists():
            continue
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"插件 manifest 解析失败 {manifest_file}: {e}")
            continue
        if not isinstance(manifest, dict):
            continue
        plugin_id = manifest.get("id") or entry.name
        manifest["id"] = plugin_id
        manifest.setdefault("name", plugin_id)
        manifest.setdefault("description", "")
        manifest.setdefault("category", "other")
        manifest.setdefault("version", "1.0.0")
        manifest.setdefault("author", "")
        manifest.setdefault("icon", "")
        manifest.setdefault("entry", ENTRY_FILE.get(manifest["category"], ""))
        manifest.setdefault("default_enabled", True)
        manifest["builtin"] = builtin
        manifest["_dir"] = str(entry)
        found[plugin_id] = manifest
    return found


_CACHE_TTL = 30.0
_disk_cache: dict[str, Any] = {"at": 0.0, "sig": None, "plugins": {}}


def _source_signature() -> tuple:
    """两个插件根目录的"当前长相"：子目录及其下钻一层的 name + mtime。

    缓存以它为准而不是只看时间：安装、卸载、改 manifest 都会让签名变化，一变就重扫——
    否则新装的插件要等 TTL 才可见，测试与本地开发都会把它当成 bug。
    """
    sig: list[tuple] = []
    for root in (BUILTIN_PLUGIN_DIR, USER_PLUGIN_DIR):
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            try:
                children = sorted((child.name, child.stat().st_mtime_ns) for child in entry.iterdir())
                sig.append((str(root), entry.name, entry.stat().st_mtime_ns, tuple(children)))
            except OSError:
                continue
    return tuple(sig)


def scan_disk() -> dict[str, dict[str, Any]]:
    """扫描磁盘全部插件：内置 + 用户（用户覆盖内置同名）。

    manifest 在热路径上被反复读取（通道类型、皮肤变量、表情目录），所以按目录签名缓存；
    签名没变又没过 TTL 就直接复用，省掉逐个读 JSON 解析。返回的是浅拷贝：外层字典与每个
    manifest 各一份，但嵌套块（channel / guide 等）仍与缓存共用同一个对象——现有调用方
    全部只读，不要在返回值里改写嵌套块，否则会污染缓存到签名或 TTL 变化为止。
    """
    import time as _time

    signature = _source_signature()
    if _disk_cache["sig"] != signature or _time.time() - float(_disk_cache["at"]) >= _CACHE_TTL:
        plugins: dict[str, dict[str, Any]] = {}
        plugins.update(_scan_dir(BUILTIN_PLUGIN_DIR, builtin=True))
        plugins.update(_scan_dir(USER_PLUGIN_DIR, builtin=False))
        _disk_cache.update({"at": _time.time(), "sig": signature, "plugins": plugins})
    return {pid: dict(manifest) for pid, manifest in _disk_cache["plugins"].items()}


def channel_kind(plugin_id: str) -> str:
    """插件声明的外部身份类别（manifest 的 channel.kind）

    类别名由插件自带 —— 第三方通道插件不该等我们在平台里加一个常量。
    没声明就退回插件 id：老插件不用改也能跑（kind 只是身份的归类标签，不改身份本身）。
    """
    manifest = scan_disk().get(plugin_id) or {}
    block = manifest.get("channel")
    kind = str((block or {}).get("kind") or "").strip() if isinstance(block, dict) else ""
    return kind or plugin_id


def channels() -> list[dict[str, Any]]:
    """声明了 channel 块的插件 —— 「有哪些外部通道」的唯一来源

    通道卡片、路由、身份类别都读这里：第三方通道插件装上就出现、卸载就消失，
    平台侧不再维护插件 id 常量表。
    """
    result: list[dict[str, Any]] = []
    for plugin_id, manifest in sorted(scan_disk().items()):
        block = manifest.get("channel")
        if not isinstance(block, dict) or not block:
            continue
        name = str(manifest.get("name") or plugin_id)
        label = str(block.get("label") or name)
        desc = str(block.get("desc") or manifest.get("description") or "")
        guide = block.get("guide")
        result.append({
            "plugin_id": plugin_id,
            "kind": str(block.get("kind") or "").strip() or plugin_id,
            "name": name,
            # 通道名与说明都是插件的产品文案，三语由插件自带：不该在平台的 i18n 表里再抄一遍
            "label": label,
            "label_en": str(block.get("label_en") or label),
            "label_ja": str(block.get("label_ja") or label),
            "desc": desc,
            "desc_en": str(block.get("desc_en") or desc),
            "desc_ja": str(block.get("desc_ja") or desc),
            # 开通指引进 manifest：q.qq.com 这类链接是插件自己的知识，平台不该替它记
            "guide": [g for g in (guide if isinstance(guide, list) else []) if isinstance(g, dict) and g.get("text")],
            # 能力与限制：同样是插件自己的知识（腾讯下线主动推送、私聊额度、封号风险…），
            # 卡片直接照着显示，平台不替它总结
            "limits": [x for x in (block.get("limits") if isinstance(block.get("limits"), list) else []) if isinstance(x, dict) and x.get("text")],
            "pairing": bool(block.get("pairing", False)),
            "supports_group": bool(block.get("supports_group", False)),
            "default_enabled": bool(manifest.get("default_enabled", True)),
        })
    return result


def channel_plugin(plugin_id: str) -> dict[str, Any] | None:
    """按 id 取一条已声明的通道；没声明返回 None（路由拿它挡掉乱传的 plugin_id）"""
    return next((c for c in channels() if c["plugin_id"] == plugin_id), None)


def load_entry_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """读取插件 entry 载荷（skin.json / skill.json），无则返回 {}"""
    entry = manifest.get("entry")
    if not entry:
        return {}
    payload_file = Path(manifest["_dir"]) / entry
    if not payload_file.exists():
        return {}
    try:
        data = json.loads(payload_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"插件载荷解析失败 {payload_file}: {e}")
        return {}


def get_skin_vars(manifest: dict[str, Any]) -> dict[str, Any]:
    """skin 插件 → {light:{key:hex}, dark:{key:hex}}（只保留合法 key）"""
    if manifest.get("category") != "skin":
        return {}
    payload = load_entry_payload(manifest)
    result: dict[str, Any] = {"light": {}, "dark": {}}
    for mode in ("light", "dark"):
        src = payload.get(mode) or {}
        for key, hex_val in src.items():
            if key in SKIN_KEYS and isinstance(hex_val, str) and SKIN_HEX_RE.match(hex_val):
                result[mode][key] = hex_val
    return result


def get_skill_defs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """skill 插件 → 技能类型定义列表"""
    if manifest.get("category") != "skill":
        return []
    payload = load_entry_payload(manifest)
    skills = payload.get("skills") or []
    return [s for s in skills if isinstance(s, dict) and s.get("type")]


def _emoji_face(raw: Any) -> dict[str, str] | None:
    """一条表情 → 规范形状；不合法的单条丢掉，不让一个坏表情带崩整包"""
    if not isinstance(raw, dict):
        return None
    face_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()[:24]
    if not EMOJI_ID_RE.match(face_id) or not name:
        return None
    emoji = str(raw.get("emoji") or "").strip()
    if len(emoji) > EMOJI_CHARS_MAX or any(ch.isspace() for ch in emoji):
        # 截断会把 ZWJ 序列切成半个表情（渲染成几个分开的字符），比明确忽略更糟
        logger.warning(f"表情 {face_id} 的 emoji 字段不合法（{len(emoji)} 个字符），已忽略该字段")
        emoji = ""
    file_name = str(raw.get("file") or "").strip()
    if file_name:
        rel = PurePosixPath(file_name)
        safe = (
            not rel.is_absolute()
            and ".." not in rel.parts
            and not any(part.startswith(".") for part in rel.parts)
            and file_name.lower().endswith(EMOJI_ASSET_SUFFIXES)
        )
        file_name = file_name if safe else ""       # 图名就是白名单，不安全的当没有
    if not emoji and not file_name:
        return None
    return {"id": face_id, "name": name, "emoji": emoji, "file": file_name}


def get_emoji_pack(manifest: dict[str, Any]) -> dict[str, Any]:
    """emojipack 插件 → {usage, faces:[{id,name,emoji,file}]}；不是这类插件返回 {}"""
    if manifest.get("category") != "emojipack":
        return {}
    payload = load_entry_payload(manifest)
    faces: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in (payload.get("faces") or [])[:EMOJI_FACE_MAX]:
        face = _emoji_face(raw)
        if face and face["id"] not in seen:
            seen.add(face["id"])
            faces.append(face)
    if not faces:
        return {}
    return {"usage": str(payload.get("usage") or "").strip()[:200], "faces": faces}


def emoji_asset_path(manifest: dict[str, Any], file_name: str) -> Path | None:
    """渲染层要的那张图 → 磁盘路径；只认 emoji.json 里声明过的文件。

    白名单来自载荷而不是"目录里有什么就发什么"：写进 emoji.json 的才算资产，
    顺带把目录穿越堵死——没声明的名字连解析都不做。
    """
    declared = {f["file"] for f in get_emoji_pack(manifest).get("faces", []) if f["file"]}
    if file_name not in declared:
        return None
    root = Path(manifest["_dir"]).resolve()
    try:
        path = (root / file_name).resolve()
    except OSError:
        return None
    return path if path.is_file() and root in path.parents else None


def emoji_index(packs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """短码 / 中文名 → 表情（附所属包）。同名冲突先到先得。

    这里的 setdefault 是硬约束，不能改成覆盖赋值：短码处于全局命名空间，覆盖会让后启用的包
    改写历史消息的语义。包作者按 <包>_<脸> 命名可从源头避免撞名（内置包为 qq_ 前缀）。
    """
    index: dict[str, dict[str, str]] = {}
    for pack in packs:
        for face in pack["faces"]:
            for key in (face["id"], face["name"]):
                index.setdefault(key, {**face, "pack": pack["id"]})
    return index


def _split_code(content: str) -> list[tuple[bool, str]]:
    """把正文切成 (是否代码, 片段)，调用方只改代码外的部分。

    按 CommonMark 的定界思路配对反引号 run：一个 run 必须能在后面找到**等长**的 run 才算定界符，
    否则当普通字符。仅按奇偶切分不够——正文里一个落单的反引号就会把后面整个代码块翻成"代码外"，
    代码里的 :param: / :return: 会被当表情吃掉。
    """
    segments: list[tuple[bool, str]] = []
    pos = scan = 0
    while True:
        match = _CODE_RUN_RE.search(content, scan)
        if match is None:
            segments.append((False, content[pos:]))
            return segments
        fence = match.group(0)
        end = content.find(fence, match.end())
        if end < 0:
            scan = match.end()          # 没有等长收尾：当普通字符，继续往后找
            continue
        segments.append((False, content[pos:match.start()]))
        segments.append((True, content[match.start():end + len(fence)]))
        pos = scan = end + len(fence)


def resolve_emoji_text(content: str, packs: list[dict[str, Any]]) -> str:
    """正文里的表情写法归一：有 unicode 的换成字符，仅图片的规范成 :id:。

    只在入口做一次（手打、AI 输出、QQ 入站都经此处），下游（AI 上下文、搜索、导出、
    联邦、通道出口）拿到的即字符或标准短码，无需重复解析。
    """
    if not content or ("[表情:" not in content and ":" not in content):
        return content
    index = emoji_index(packs)
    if not index:
        return content

    def one(key: str, whole: str) -> str:
        face = index.get(key.strip())
        if face is None:
            return whole
        return face["emoji"] or f":{face['id']}:"

    # 只动代码外：代码里的 :x: 是在举例，不是发表情
    out: list[str] = []
    for is_code, segment in _split_code(content):
        if is_code:
            out.append(segment)
            continue
        segment = EMOJI_LEGACY_RE.sub(lambda m: one(m.group(1), m.group(0)), segment)
        out.append(EMOJI_SHORTCODE_RE.sub(lambda m: one(m.group(1), m.group(0)), segment))
    return "".join(out)


_emoji_cache: dict[str, Any] = {"at": 0.0, "packs": []}


def invalidate_caches() -> None:
    """插件目录或开关变化后清缓存：安装、卸载、开关、重扫都要调，入口归一与选择器立刻生效"""
    _disk_cache.update({"at": 0.0, "sig": None})
    _emoji_cache["at"] = 0.0


def _copy_packs(packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """缓存数据的对外副本：共享同一份对象时，调用方一个 append 就污染缓存 30 秒"""
    return [
        {"id": p["id"], "name": p["name"], "usage": p["usage"],
         "faces": [dict(face) for face in p["faces"]]}
        for p in packs
    ]


async def enabled_emoji_packs(db: AsyncSession, *, cached: bool = True) -> list[dict[str, Any]]:
    """全局启用且载荷可解析的表情包（AI 说明、入口归一、前端选择器共用一个来源）。

    只看管理员级开关，不看个人偏好：AI 是平台级发言者，不应因某个用户关掉选择器而失去表情；
    个人偏好只决定"我自己的输入框里显不显示"。
    带 30 秒缓存：入口归一每条消息都要查询，读盘与解析 JSON 不必逐条执行；返回的始终是副本，
    调用方怎么改都不会污染缓存。
    """
    import time as _time

    from sqlalchemy import select

    from app.models.plugin import Plugin

    if cached and _time.time() - float(_emoji_cache["at"]) < _CACHE_TTL:
        return _copy_packs(_emoji_cache["packs"])
    repo = _ensure_repo(db)
    rows = (await repo.execute(
        select(Plugin).where(Plugin.category == "emojipack", Plugin.enabled.is_(True))
    )).scalars().all()
    disk = scan_disk()
    packs = []
    for row in rows:
        pack = get_emoji_pack(disk.get(row.id) or {})
        if pack:
            packs.append({
                "id": row.id, "name": row.name,
                "usage": pack["usage"], "faces": pack["faces"],
            })
    _emoji_cache.update({"at": _time.time(), "packs": packs})
    return _copy_packs(packs)


async def normalize_emoji_text(db: AsyncSession, content: str) -> str:
    """入口归一：把表情写法换成字符或标准短码（见 resolve_emoji_text）。

    取包失败按原文入库：表情不应阻断消息发送。
    """
    if not content or ("[表情:" not in content and ":" not in content):
        return content
    try:
        packs = await enabled_emoji_packs(db)
    except Exception as e:
        logger.warning(f"取表情包失败，正文按原样处理：{type(e).__name__}: {e}")
        return content
    return resolve_emoji_text(content, packs)


async def sync_plugins_to_db(db) -> int:
    """磁盘 → DB 同步：新增插入、存在更新、磁盘消失删除（幂等，返回变更数）"""
    # 落成 repo 后再用：后面的 execute / delete / add / commit 全在 PluginRepository 契约里，
    # 变量名留在 db 上会让人以为一半走仓库、一半走 session 原生方法
    repo = _ensure_repo(db)
    from sqlalchemy import select
    from app.models.plugin import Plugin

    disk = scan_disk()
    changed = 0
    result = await repo.execute(select(Plugin))
    db_plugins = {p.id: p for p in result.scalars().all()}

    # 磁盘消失 → 删除（卸载语义）
    for pid in list(db_plugins.keys()):
        if pid not in disk:
            await repo.delete(db_plugins[pid])
            logger.info(f"插件卸载（目录消失）: {pid}")
            changed += 1

    # 新增 / 更新
    for pid, m in disk.items():
        defaults = dict(
            name=m.get("name", pid)[:120],
            description=str(m.get("description", ""))[:2000],
            category=m.get("category", "other"),
            version=str(m.get("version", "1.0.0"))[:20],
            author=str(m.get("author", ""))[:80],
            icon=m.get("icon", "")[:40],
            builtin=bool(m.get("builtin", False)),
        )
        existing = db_plugins.get(pid)
        if existing is None:
            is_skin = defaults.get('category') == 'skin'
            # 默认值必须与 _scan_dir 的 setdefault 一致：两边不一致时，谁删掉自己那份都会让
            # 所有插件的默认开关静默翻转
            enabled = False if is_skin else bool(m.get("default_enabled", True))
            repo.add(Plugin(id=pid, enabled=enabled, **defaults))
            logger.info(f"插件发现: {pid} ({defaults['name']})")
            changed += 1
        else:
            dirty = any(getattr(existing, k) != v for k, v in defaults.items())
            if dirty:
                for k, v in defaults.items():
                    setattr(existing, k, v)
                changed += 1
    if changed:
        await repo.commit()
    return changed
