"""
统一插件目录服务 — 目录即插件（DSH skills 目录同款思路）

约定：
- 插件 = 一个目录 + plugin.json（manifest），目录名即插件 id
- 扫描两个位置（同名 id 后者覆盖前者）：
    1. backend/plugins/          内置插件（随代码走，git 跟踪）
    2. DATA_DIR/plugins/         用户安装插件（持久化目录，覆盖内置同名）
- category: skin | skill | world | other
  - skin  插件 entry=skin.json   → {light:{var:hex}, dark:{var:hex}} 变量覆盖
  - skill 插件 entry=skill.json  → {skills:[{type,name,category,description,config_schema}]}
- 两级开关：plugins.enabled（管理员全局）+ user_plugin_prefs（用户个人），生效 = 两者都开
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

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
USER_PLUGIN_DIR = Path(os.environ.get("DATA_DIR", "data")) / "plugins"

MANIFEST_NAME = "plugin.json"

# 类别 → entry 载荷文件名
ENTRY_FILE = {
    "skin": "skin.json",
    "skill": "skill.json",
}

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


def scan_disk() -> dict[str, dict[str, Any]]:
    """扫描磁盘全部插件：内置 + 用户（用户覆盖内置同名）"""
    plugins: dict[str, dict[str, Any]] = {}
    plugins.update(_scan_dir(BUILTIN_PLUGIN_DIR, builtin=True))
    plugins.update(_scan_dir(USER_PLUGIN_DIR, builtin=False))
    return plugins


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
            if key in SKIN_KEYS and isinstance(hex_val, str) and hex_val.startswith("#"):
                result[mode][key] = hex_val
    return result


def get_skill_defs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """skill 插件 → 技能类型定义列表"""
    if manifest.get("category") != "skill":
        return []
    payload = load_entry_payload(manifest)
    skills = payload.get("skills") or []
    return [s for s in skills if isinstance(s, dict) and s.get("type")]


async def sync_plugins_to_db(db) -> int:
    """磁盘 → DB 同步：新增插入、存在更新、磁盘消失删除（幂等，返回变更数）"""
    db = _ensure_repo(db)
    from sqlalchemy import select
    from app.models.plugin import Plugin

    disk = scan_disk()
    changed = 0
    result = await db.execute(select(Plugin))
    db_plugins = {p.id: p for p in result.scalars().all()}

    # 磁盘消失 → 删除（卸载语义）
    for pid in list(db_plugins.keys()):
        if pid not in disk:
            await db.delete(db_plugins[pid])
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
            enabled = False if is_skin else bool(m.get('default_enabled', False))
            db.add(Plugin(id=pid, enabled=enabled, **defaults))
            logger.info(f"插件发现: {pid} ({defaults['name']})")
            changed += 1
        else:
            dirty = any(getattr(existing, k) != v for k, v in defaults.items())
            if dirty:
                for k, v in defaults.items():
                    setattr(existing, k, v)
                changed += 1
    if changed:
        await db.commit()
    return changed
