"""
世界商城 × GitHub 同步服务
==========================
职责边界（单一职责，路由层只做参数校验与鉴权，业务都在这里）：
- 配置读写         get_market_config / save_market_config
- GitHub HTTP     _gh_get / _gh_put / _gh_download（Contents API 封装）
- 索引快照         load_snapshot / save_snapshot（本地文件缓存，GitHub 板块的数据源）
- 同步             sync_item_to_github（本地 → GitHub：meta + zip + index）
- 刷新             refresh_from_github（GitHub → 快照）
- 导入             import_from_github（快照条目 → 下载 zip → 建世界）
- 状态计算         compute_sync_state（已同步 / 同步过 / 未同步）

设计要点：
- GitHub 板块不实时请求远端：refresh 把 index.json 落成快照文件，板块读快照；
  管理员可手动刷新（auto_sync 开启时启动自动拉取）。
- 目录名 = 作者命名（清洗后的标题），GitHub 支持中文路径；空标题兜底 world-{id}。
- 云端 downloads = 同步时本地下载数的快照（GitHub 无文件下载统计 API，字段预留扩展）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.paths import DATA_DIR, MARKET_DIR
from app.repositories.infra_repo import SQLAlchemyInfraRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyInfraRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyInfraRepository(db_or_repo)
    return db_or_repo


GITHUB_API = "https://api.github.com"
INDEX_PATH = "worlds/index.json"
SNAPSHOT_PATH = str(MARKET_DIR / "github_index_cache.json")

# ─────────────────────────── 配置 ───────────────────────────


def _encrypt_token(tok: str) -> str:
    """GitHub token 加密存储（Fernet）"""
    from app.utils.crypto import encrypt_api_key
    return encrypt_api_key(tok) if tok else ""


def _decrypt_token(stored: str) -> str:
    """解密 GitHub token；旧明文数据兼容（解密失败按明文处理）"""
    if not stored:
        return ""
    from app.utils.crypto import decrypt_api_key
    try:
        return decrypt_api_key(stored)
    except Exception:
        return stored


def mask_token(tok: str) -> str:
    """脱敏显示：只留前 4 后 4（如 ghp_Wr…ku5N）；过短全隐"""
    if not tok:
        return ""
    if len(tok) <= 8:
        return "***"
    return f"{tok[:4]}…{tok[-4:]}"


async def get_market_config(db) -> dict:
    """读商城配置（含 GitHub 设置）——直接查 DB；token 解密供服务内使用。
    首次读取时若无机器人签名密钥 → 自动生成并保存（机器人负责社区仓库写入）。"""
    db = _ensure_repo(db)
    from sqlalchemy import text
    row = (await db.execute(text("SELECT market_config FROM system_settings WHERE id=1"))).first()
    raw = row[0] if row else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    cfg = dict(raw or {})
    # 机器人签名密钥对（懒生成）：写入社区仓库时用机器人身份加签，其他实例以此验签
    if not cfg.get("bot_public_key"):
        priv_pem, pub_pem = _generate_signing_keypair()
        cfg["bot_sign_key_encrypted"] = _encrypt_token(priv_pem)
        cfg["bot_public_key"] = pub_pem
        await db.execute(
            text("UPDATE system_settings SET market_config = :cfg WHERE id = 1"),
            {"cfg": json.dumps(cfg, ensure_ascii=False)},
        )
        await db.commit()
    from app.config import settings as _settings
    return {
        "github_repo": str(cfg.get("github_repo") or "").strip(),
        # DB 配置优先，空则回退 .env GITHUB_TOKEN（部署时 .env 是默认权威；商城页设置可覆盖）
        "github_token": _decrypt_token(str(cfg.get("github_token") or "")) or _settings.github_token,
        "auto_sync_enabled": bool(cfg.get("auto_sync_enabled", False)),
        "bot_public_key": str(cfg.get("bot_public_key") or ""),
        "bot_sign_key": _decrypt_token(str(cfg.get("bot_sign_key_encrypted") or "")),
    }


async def save_market_config(db, *, github_repo: str | None = None,
                             github_token: str | None = None,
                             auto_sync_enabled: bool | None = None) -> dict:
    """更新商城配置（仅更新传入的字段，其余保留）；token 加密存储"""
    db = _ensure_repo(db)
    from sqlalchemy import text
    row = (await db.execute(text("SELECT market_config FROM system_settings WHERE id=1"))).first()
    raw = row[0] if row else None
    cfg = dict(raw or {}) if isinstance(raw, dict) else {}
    if github_repo is not None:
        cfg["github_repo"] = github_repo.strip()
    if github_token is not None:
        cfg["github_token"] = _encrypt_token(github_token)
    if auto_sync_enabled is not None:
        cfg["auto_sync_enabled"] = bool(auto_sync_enabled)
    await db.execute(
        text("UPDATE system_settings SET market_config = :cfg WHERE id = 1"),
        {"cfg": json.dumps(cfg, ensure_ascii=False)},
    )
    await db.commit()
    return cfg


def _repo_parts(repo: str) -> tuple[str, str] | None:
    """'owner/name' → (owner, name)"""
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", repo.strip())
    return (m.group(1), m.group(2)) if m else None


# ─────────────────────────── 作者签名（Ed25519） ───────────────────────────
# 签名 payload 覆盖 meta 关键字段 + zip 哈希，任何篡改（含换包）都会验签失败。
# 双签名：作者签名（meta.signature）+ 机器人背书签名（bot_signature）——
# 信任根 = 系统机器人公钥（固定已知），其他实例以此验签。

SIGNED_FIELDS = ("id", "title", "description", "author_github_id", "updated_at", "zip_sha256", "downloads")


def _sign_payload(meta: dict) -> str:
    """meta 关键字段 → 规范化签名串（固定顺序，防拼接歧义）"""
    return "\x1f".join(str(meta.get(k) or "") for k in SIGNED_FIELDS)


def _load_privkey(pem: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _load_pubkey(pem: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_public_key(pem.encode())


def _generate_signing_keypair() -> tuple[str, str]:
    """生成 Ed25519 密钥对，返回 (私钥PEM, 公钥PEM)"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
    pub_pem = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem


def sign_meta(meta: dict, privkey_pem: str) -> str:
    """对 meta 签名，返回 base64 签名"""
    import base64
    priv = _load_privkey(privkey_pem)
    return base64.b64encode(priv.sign(_sign_payload(meta).encode())).decode()


# ─────────────────────────── GitHub HTTP ───────────────────────────


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh_get(owner: str, repo: str, path: str, token: str) -> dict | None:
    """GET Contents API；404 返回 None，其他非 200 抛错"""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}", headers=_headers(token))
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"GitHub 读取失败 {path}: {r.status_code} {r.text[:200]}")
    return r.json()


async def _gh_put(owner: str, repo: str, path: str, content_b64: str, token: str,
                  sha: str | None = None, message: str = "sync") -> dict:
    """PUT Contents API（创建/更新，更新需带 sha）"""
    body = {"message": message, "content": content_b64}
    if sha:
        body["sha"] = sha
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
                             json=body, headers=_headers(token))
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub 写入失败 {path}: {r.status_code} {r.text[:300]}")
    return r.json()


async def _gh_download(owner: str, repo: str, path: str, token: str) -> bytes:
    """下载仓库文件内容（Contents API base64 解码）"""
    data = await _gh_get(owner, repo, path, token)
    if data is None:
        raise ValueError(f"GitHub 文件不存在: {path}")
    return base64.b64decode(data["content"])


async def verify_github_token(token: str) -> tuple[str, int]:
    """验证 GitHub token 有效性，返回 (用户名, 数字 user id)——id 是身份锚（改名不变）"""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{GITHUB_API}/user", headers=_headers(token))
    if r.status_code != 200:
        raise RuntimeError(f"GitHub token 无效（{r.status_code}），请检查后重试")
    data = r.json()
    username = data.get("login") or ""
    uid = data.get("id")
    if not username or not uid:
        raise RuntimeError("GitHub 响应异常，未获取到用户名")
    return username, int(uid)


# ─────────────────────────── 索引快照 ───────────────────────────


def _snapshot_file() -> Path:
    # 支持环境变量覆盖（可测试性；生产默认数据根的 market/github_index_cache.json）
    return Path(os.environ.get("GITHUB_SNAPSHOT_PATH", SNAPSHOT_PATH))


def load_snapshot() -> dict:
    """读 GitHub 索引快照；缺失/损坏返回空结构（不抛错）"""
    try:
        data = json.loads(_snapshot_file().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("worlds"), list):
            return data
    except (OSError, json.JSONDecodeError) as e:
        # 快照损坏静默当空 → 商城看起来"东西全没了"，必须留痕
        logger.warning(f"🌐 商城快照读取失败，按空处理: {e}")
    return {"synced_at": None, "worlds": []}


def save_snapshot(worlds: list[dict], synced_at: str | None = None) -> None:
    """写 GitHub 索引快照（原子写：先临时文件再 rename）。
    快照只是缓存，写失败仅告警、不阻塞主流程。"""
    data = {"synced_at": synced_at or datetime.now(timezone.utc).isoformat(), "worlds": worlds}
    path = _snapshot_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"快照写入失败（不影响主流程）: {e}")


def snapshot_map() -> dict[int, dict]:
    """快照条目按 id 建索引，供本地商品状态计算/标注使用"""
    return {int(w.get("id") or 0): w for w in load_snapshot().get("worlds", [])}


# ─────────────────────────── 目录名（作者命名） ───────────────────────────


def _clean_slug(title: str | None) -> str:
    """标题 → 安全目录名：去路径分隔符/保留字/控制字符；保留中文；截断 60 字符"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title or "").strip().strip(" .")
    return s[:60]


def _slug(item) -> str:
    """同步目录名：优先作者命名（清洗后的标题），空则兜底 world-{id}"""
    return _clean_slug(item.title) or f"world-{item.id}"


def _item_dir(item) -> str:
    return f"worlds/{_slug(item)}"


# ─────────────────────────── 同步状态 ───────────────────────────


def compute_sync_state(item, gh_entry: dict | None) -> str:
    """本地商品的同步状态：
    - unsynced  从未同步（无 github_path）
    - synced    同步后无改动（本地 updated_at ≤ 快照里的 updated_at）
    - stale     同步后又改过（本地 updated_at > 快照里的 updated_at，需重新同步）
    """
    if not getattr(item, "github_path", None):
        return "unsynced"
    gh_updated = (gh_entry or {}).get("updated_at")
    if not gh_updated or item.updated_at is None:
        return "stale"  # 快照缺失/异常 → 保守提示重新同步
    try:
        gh_ts = datetime.fromisoformat(gh_updated)
        local_ts = item.updated_at
        if local_ts.tzinfo is None:
            local_ts = local_ts.replace(tzinfo=timezone.utc)
        if gh_ts.tzinfo is None:
            gh_ts = gh_ts.replace(tzinfo=timezone.utc)
        return "synced" if local_ts <= gh_ts else "stale"
    except (ValueError, TypeError):
        return "stale"


# ─────────────────────────── 同步（本地 → GitHub） ───────────────────────────


async def sync_item_to_github(db, item) -> dict:
    """机器人模式同步：校验通过后由机器人（系统 token，唯一写权限）写入社区仓库。
    - 目录所有权：worlds/{世界名}/ 不存在 → 创建；已存在且作者==发布者 → 更新；
      作者是别人 → 拒绝（只能写自己的）
    - 双签名：作者签名（meta.signature）+ 机器人背书签名（bot_signature）
    - meta 记录：作者 GitHub id（身份锚）、来源仓库链接、zip 哈希"""
    db = _ensure_repo(db)
    cfg = await get_market_config(db)
    parts = _repo_parts(cfg["github_repo"])
    if not parts:
        raise ValueError("后台未配置 GitHub 仓库（market_config.github_repo）")
    owner, repo = parts
    token = cfg["github_token"]
    if not token:
        raise ValueError("系统未配置 GitHub Token（请管理员在后台配置机器人 token）")
    bot_priv = cfg.get("bot_sign_key") or ""
    bot_pub = cfg.get("bot_public_key") or ""
    if not bot_priv or not bot_pub:
        raise ValueError("系统机器人签名密钥缺失")

    pkg = DATA_DIR / item.package_path
    if not pkg.is_file():
        raise ValueError("商品包文件缺失，无法同步")

    # 发布者的 GitHub 身份与作者签名密钥（users 表）
    from sqlalchemy import text as _text
    row = (await db.execute(_text(
        "SELECT github_id, github_username, github_public_key, github_sign_key_encrypted FROM users WHERE id = :uid"
    ), {"uid": item.author_id})).first()
    gh_id = int(row.github_id) if row and row.github_id else 0
    gh_name = (row.github_username or "") if row else ""
    pub_pem = (row.github_public_key or "") if row else ""
    priv_enc = (row.github_sign_key_encrypted or "") if row else ""
    if not gh_id or not pub_pem or not priv_enc:
        raise ValueError("发布者未绑定 GitHub（缺少签名密钥），无法同步")
    from app.utils.crypto import decrypt_api_key
    try:
        priv_pem = decrypt_api_key(priv_enc)
    except Exception:
        raise ValueError("发布者签名密钥解密失败，请重新绑定 GitHub")

    slug = _slug(item)
    d = _item_dir(item)
    import hashlib
    zip_sha256 = hashlib.sha256(pkg.read_bytes()).hexdigest()
    meta = {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "description": item.description or "",
        "tags": item.tags or [],
        "author_name": item.author_name or f"user-{item.author_id}",
        "author_github": gh_name,
        "author_github_id": gh_id,
        "author_public_key": pub_pem,
        "source_world_id": item.source_world_id,
        "package_size": item.package_size,
        "downloads": item.downloads or 0,
        "zip_sha256": zip_sha256,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "download_url": f"{d}/world.zip",
    }
    meta["signature"] = sign_meta(meta, priv_pem)          # 作者签名
    meta["bot_public_key"] = bot_pub
    meta["bot_signature"] = sign_meta(meta, bot_priv)      # 机器人背书签名

    # ① 目录所有权检查（账本 index.json）：同名目录若属别人 → 拒绝
    idx = await _gh_get(owner, repo, INDEX_PATH, token)
    worlds = _index_worlds(idx)
    dup = next((w for w in worlds if w.get("slug") == slug and int(w.get("id") or 0) != item.id), None)
    if dup:
        raise ValueError(f"GitHub 上已存在同名世界「{dup.get('title')}」（{slug}），属于 @{dup.get('author_github') or dup.get('author_name') or '?'}，你只能同步自己的世界")
    # ② 目录所有权检查（meta 兑底）
    old_meta_raw = await _gh_get(owner, repo, f"{d}/meta.json", token)
    if old_meta_raw:
        try:
            old_meta = json.loads(base64.b64decode(old_meta_raw["content"]).decode())
            old_owner = int(old_meta.get("author_github_id") or 0)
            if old_owner and old_owner != gh_id:
                raise ValueError("该目录属于其他 GitHub 用户，你只能同步自己的世界")
        except ValueError:
            raise
        except Exception:
            pass

    # 机器人写入（唯一写权限）
    await _gh_put(owner, repo, f"{d}/meta.json",
                  base64.b64encode(json.dumps(meta, ensure_ascii=False).encode()).decode(),
                  token, sha=old_meta_raw.get("sha") if old_meta_raw else None,
                  message=f"sync {slug} meta (author @{gh_name}, by bot)")
    old_zip = await _gh_get(owner, repo, f"{d}/world.zip", token)
    await _gh_put(owner, repo, f"{d}/world.zip",
                  base64.b64encode(pkg.read_bytes()).decode(),
                  token, sha=old_zip.get("sha") if old_zip else None,
                  message=f"sync {slug} package (author @{gh_name}, by bot)")

    # 更新 index.json（条目带双签名与哈希，供其他实例验签）
    entry = {
        "id": item.id,
        "slug": slug,
        "title": meta["title"],
        "description": meta["description"],
        "tags": meta["tags"],
        "author_name": meta["author_name"],
        "author_github": gh_name,
        "author_github_id": gh_id,
        "author_public_key": pub_pem,
        "downloads": meta["downloads"],
        "updated_at": meta["updated_at"],
        "zip_sha256": zip_sha256,
        "signature": meta["signature"],
        "bot_signature": meta["bot_signature"],
        "zip": f"{d}/world.zip",
        "meta": f"{d}/meta.json",
    }
    worlds = [w for w in worlds if int(w.get("id") or 0) != item.id]
    worlds.append(entry)
    await _gh_put(owner, repo, INDEX_PATH,
                  base64.b64encode(json.dumps({"worlds": worlds}, ensure_ascii=False, indent=2).encode()).decode(),
                  token, sha=idx.get("sha") if idx else None,
                  message=f"update index ({slug})")
    save_snapshot(worlds)

    # 记录 github_path
    await db.execute(_text("UPDATE world_market_items SET github_path = :p WHERE id = :id"),
                     {"p": d, "id": item.id})
    await db.commit()
    logger.info(f"🏪 商品 #{item.id}「{item.title}」已由机器人同步到 {cfg['github_repo']}/{d}（作者 @{gh_name}）")
    return {"success": True, "path": d, "author_github": gh_name}


def _index_worlds(idx: dict | None) -> list[dict]:
    """index.json 响应 → 世界条目列表（容错）"""
    if not idx:
        return []
    try:
        data = json.loads(base64.b64decode(idx["content"]).decode())
        return data.get("worlds", []) if isinstance(data, dict) else []
    except (KeyError, json.JSONDecodeError):
        return []


# ─────────────────────────── 刷新（GitHub → 快照） ───────────────────────────


async def refresh_from_github(db) -> dict:
    """管理员手动/自动刷新：拉 index.json → 机器人背书验签 → 更新快照。
    每条目附带可信度：signature_valid（机器人签名有效）。"""
    cfg = await get_market_config(db)
    parts = _repo_parts(cfg["github_repo"])
    if not parts:
        raise ValueError("后台未配置 GitHub 仓库（market_config.github_repo）")
    owner, repo = parts
    token = cfg["github_token"]
    if not token:
        raise ValueError("后台未配置 GitHub Token（market_config.github_token）")

    idx = await _gh_get(owner, repo, INDEX_PATH, token)
    if idx is None:
        # 仓库还没有 index.json → 初始化空索引，快照为空
        await _gh_put(owner, repo, INDEX_PATH,
                      base64.b64encode(json.dumps({"worlds": []}, ensure_ascii=False).encode()).decode(),
                      token, message="init market index")
        save_snapshot([])
        return {"added": 0, "updated": 0, "removed": 0}

    worlds = _index_worlds(idx)
    old = {int(w.get("id") or 0): w for w in load_snapshot().get("worlds", [])}
    bot_pub = cfg.get("bot_public_key") or ""
    added = updated = 0
    for w in worlds:
        wid = int(w.get("id") or 0)
        if wid not in old:
            added += 1
        elif old[wid].get("updated_at") != w.get("updated_at"):
            updated += 1
        # 机器人背书验签：信任根 = 系统机器人公钥（固定已知，无需 TOFU）
        w["signature_valid"] = None
        w["key_changed"] = False
        if bot_pub and w.get("bot_signature"):
            try:
                bot_key = _load_pubkey(bot_pub)
                bot_key.verify(base64.b64decode(w["bot_signature"]), _sign_payload(w).encode())
                w["signature_valid"] = True
            except Exception:
                w["signature_valid"] = False
    removed = sum(1 for wid in old if wid not in {int(w.get("id") or 0) for w in worlds})
    save_snapshot(worlds)
    logger.info(f"🏪 GitHub 商城刷新完成: +{added} 新增, {updated} 更新, -{removed} 移除")
    return {"added": added, "updated": updated, "removed": removed}


# ─────────────────────────── 导入（GitHub → 本地世界） ───────────────────────────


async def import_from_github(db, user_id: int, item_id: int) -> dict:
    """按快照条目导入：下载 world.zip → 创建新世界 → 导入文件（安全过滤）"""
    cfg = await get_market_config(db)
    parts = _repo_parts(cfg["github_repo"])
    if not parts:
        raise ValueError("后台未配置 GitHub 仓库")
    owner, repo = parts
    token = cfg["github_token"]

    entry = snapshot_map().get(item_id)
    if entry is None:
        raise ValueError("快照中不存在该资源（先刷新 GitHub 商城）")
    zip_path = entry.get("zip") or ""
    if not zip_path:
        raise ValueError("该资源缺少包文件路径")

    from app.services.world.world_service import create_world
    from app.services.world.world_file_service import import_zip
    zip_bytes = await _gh_download(owner, repo, zip_path, token)
    if not zip_bytes:
        raise ValueError("资源包为空")

    created = await create_world(db, user_id, str(entry.get("title") or f"World {item_id}")[:100],
                                 str(entry.get("description") or ""), 1.0, None)
    world_id = int(created["id"])
    try:
        result = import_zip(world_id, zip_bytes)
        if result.get("imported", 0) == 0:
            raise ValueError("资源包没有可导入的文件")
    except ValueError as e:
        raise ValueError(str(e))
    logger.info(f"🏪 用户 {user_id} 从 GitHub 导入资源 {item_id}「{entry.get('title')}」→ 新世界 #{world_id}")
    return {"world_id": world_id, "name": created.get("name") or entry.get("title"),
            "imported": result.get("imported", 0)}
