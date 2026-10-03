"""
世界商城 API — 世界包发布 / 浏览 / 一键导入（2026-08-07 MVP）

- 发布：世界代码区（不含 content/）导出 zip → 存 data/market/ → 商品元数据入库
- 浏览：列表 + 搜索（标题/描述）+ 标签过滤
- 导入：下载 zip → 创建新世界 → import_zip（安全过滤）→ 一键复制
- 分级：MVP 只做 world（完整世界包）；block（积木组件）后置
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.paths import DATA_DIR, MARKET_DIR
from app.utils.auth import get_current_user
from app.services.world.market_github import (
    snapshot_map, load_snapshot, compute_sync_state, sync_item_to_github,
    refresh_from_github, import_from_github, verify_github_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["世界商城"])

MAX_PACKAGE_BYTES = 20 * 1024 * 1024  # 世界包上限 20MB


# ═══════════════════════════════════════════════════════════════
# 模型
# ═══════════════════════════════════════════════════════════════

class PublishRequest(BaseModel):
    world_id: int
    title: str = ""
    description: str = ""
    tags: list[str] = []
    sync_github: bool = False  # 发布后同步到 GitHub（需后台已配置）


class UpdateItemRequest(BaseModel):
    """编辑商品（字段可选，只更新提供的）"""
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None


# ═══════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════

def _item_dict(item, gh_entry: dict | None = None) -> dict:
    """商品序列化。gh_entry = 快照里同 id 的 GitHub 条目（无则 None）——
    用于携带云端信息（云端更新时间/下载数）与同步状态。"""
    gh = gh_entry or {}
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "description": item.description,
        "tags": item.tags or [],
        "author_id": item.author_id,
        "author_name": item.author_name,
        "source_world_id": item.source_world_id,
        "source": getattr(item, "source", "local") or "local",
        "github_path": getattr(item, "github_path", None),
        "package_size": item.package_size,
        "downloads": item.downloads,                    # 本地下载数（本实例导入次数）
        "github_downloads": gh.get("downloads"),       # 云端下载数（同步时快照）
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "github_updated_at": gh.get("updated_at"),     # 云端更新时间（对比用）
        "sync_state": compute_sync_state(item, gh),     # unsynced / synced / stale
        "slug": gh.get("slug"),
    }


async def _require_owner(db: AsyncSession, world_id: int, user_id: int):
    from app.models.world import World
    world = (await db.execute(select(World).where(World.id == world_id))).scalar_one_or_none()
    if world is None:
        raise HTTPException(status_code=404, detail="世界不存在")
    if world.owner_id != user_id:
        raise HTTPException(status_code=403, detail="只有世界创建者可以发布")
    return world


# ═══════════════════════════════════════════════════════════════
# 发布 / 列表 / 详情 / 下架 / 导入 / 下载
# ═══════════════════════════════════════════════════════════════

@router.post("/items")
async def publish_item(
    req: PublishRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发布当前世界到商城（世界代码区打包，不含 content/）"""
    world = await _require_owner(db, req.world_id, current_user["user_id"])
    from app.services.world.world_file_service import export_zip
    data = export_zip(world.id, include_content=False)
    if not data:
        raise HTTPException(status_code=400, detail="世界没有可发布的文件")
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"世界包过大（{len(data)//1024}KB > {MAX_PACKAGE_BYTES//1024//1024}MB）")

    from app.models.world import WorldMarketItem
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:16]}.zip"
    (MARKET_DIR / fname).write_bytes(data)

    title = (req.title or world.name).strip()[:100]
    # 本地查重：同名且在架商品 → 拒绝（保证作者命名在 GitHub 上唯一）
    dup = (await db.execute(
        select(WorldMarketItem).where(
            WorldMarketItem.kind == "world", WorldMarketItem.status == "on",
            WorldMarketItem.title == title,
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"同名世界「{title}」已在商城中（商品 #{dup.id}）")

    item = WorldMarketItem(
        kind="world",
        title=title,
        description=(req.description or world.description or "").strip(),
        tags=[str(t).strip()[:30] for t in (req.tags or []) if str(t).strip()][:10],
        author_id=current_user["user_id"],
        author_name=current_user.get("username") or "",
        source_world_id=world.id,
        package_path=f"market/{fname}",
        package_size=len(data),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    logger.info(f"🏪 世界 #{world.id}「{title}」发布到商城（item {item.id}，{len(data)}B）")
    # 发布后同步 GitHub（可选；失败不影响站内发布）
    if req.sync_github:
        try:
            from app.services.world.market_github import sync_item_to_github
            await sync_item_to_github(db, item)
            await db.refresh(item)
        except Exception as e:
            logger.warning(f"🏪 商品 #{item.id} 同步 GitHub 失败（站内发布成功）: {e}")
    return _item_dict(item)


@router.get("/items")
async def list_items(
    q: str = Query("", description="搜索标题/描述"),
    tag: str = Query("", description="按标签过滤"),
    kind: str = Query("world", description="world | block"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本地板块列表（在架商品；q 模糊搜索，tag 精确匹配）。
    附带快照信息：同步状态、云端更新时间/下载数。"""
    from app.models.world import WorldMarketItem
    stmt = select(WorldMarketItem).where(WorldMarketItem.status == "on", WorldMarketItem.kind == kind)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(WorldMarketItem.title.ilike(like), WorldMarketItem.description.ilike(like)))
    if tag.strip():
        stmt = stmt.where(WorldMarketItem.tags.contains([tag.strip()]))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(
        stmt.order_by(WorldMarketItem.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    gh_map = snapshot_map()
    return {"total": total, "items": [_item_dict(r, gh_map.get(r.id)) for r in rows]}


@router.get("/items/{item_id}")
async def get_item(
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在")
    return _item_dict(item)


@router.put("/items/{item_id}")
async def update_item(
    item_id: int,
    req: UpdateItemRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑商品（标题/描述/标签；仅发布者或管理员）"""
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.author_id != current_user["user_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有发布者或管理员可以编辑")
    if req.title is not None and str(req.title).strip():
        item.title = str(req.title).strip()[:100]
    if req.description is not None:
        item.description = str(req.description).strip()
    if req.tags is not None:
        item.tags = [str(t).strip()[:30] for t in req.tags if str(t).strip()][:10]
    await db.commit()
    await db.refresh(item)
    return _item_dict(item)


@router.delete("/items/{item_id}")
async def unpublish_item(
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下架（发布者本人或管理员）"""
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.author_id != current_user["user_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有发布者或管理员可以下架")
    item.status = "off"
    await db.commit()
    return {"success": True}


@router.post("/items/{item_id}/import")
async def import_item(
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一键导入：下载商城包 → 创建新世界 → 导入文件（安全过滤）"""
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在")
    pkg = DATA_DIR / item.package_path
    if not pkg.is_file():
        raise HTTPException(status_code=404, detail="商品包缺失")

    from app.services.world.world_service import create_world
    from app.services.world.world_file_service import import_zip
    created = await create_world(db, current_user["user_id"], item.title, item.description or "", 1.0, None)
    world_id = int(created["id"])
    try:
        result = import_zip(world_id, pkg.read_bytes())
        if result.get("imported", 0) == 0:
            raise HTTPException(status_code=400, detail="商品包没有可导入的文件")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    item.downloads = (item.downloads or 0) + 1
    await db.commit()
    logger.info(f"🏪 用户 {current_user['user_id']} 导入商城商品 {item_id} → 新世界 #{world_id}（{result.get('imported')} 文件）")
    return {"world_id": world_id, "name": created.get("name") or item.title, "imported": result.get("imported", 0)}


@router.post("/items/{item_id}/sync-github")
async def sync_item_github(
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把商品同步到 GitHub 仓库（发布者或管理员）"""
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.author_id != current_user["user_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只有发布者或管理员可以同步")
    if item.source == "github":
        raise HTTPException(status_code=400, detail="GitHub 缓存商品不能同步（它已在仓库中）")
    try:
        from app.services.world.market_github import sync_item_to_github
        # 机器人模式：校验身份后由系统 token（机器人，唯一写权限）写入。
        # 同步前自动验证绑定 token 仍有效且 github_id 匹配（失效 → 拒绝并提示重新绑定）
        user_token = await _user_github_token(db, current_user["user_id"])
        if not user_token:
            raise HTTPException(status_code=400, detail="请先在「我的」页绑定自己的 GitHub 账户，再进行同步")
        username, gh_id = await verify_github_token(user_token)
        from app.models.user import User
        me = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one()
        if not me.github_id or gh_id != int(me.github_id):
            raise HTTPException(status_code=400, detail="绑定的 GitHub token 与当前账户不匹配，请重新绑定")
        r = await sync_item_to_github(db, item)
        return r
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning(f"🏪 同步 GitHub 失败 item {item_id}: {e}")
        raise HTTPException(status_code=502, detail=f"同步失败: {str(e)[:200]}")


async def _user_github_token(db: AsyncSession, user_id: int) -> str | None:
    """取用户绑定的 GitHub token（解密）；未绑定返回 None（同步将被拒绝）"""
    from app.models.user import User
    from app.utils.crypto import decrypt_api_key
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user and user.github_token_encrypted:
        try:
            return decrypt_api_key(user.github_token_encrypted)
        except Exception:
            logger.warning(f"👤 用户 {user_id} 的 GitHub token 解密失败")
    return None


@router.post("/github/bind")
async def bind_github(
    req: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """绑定当前用户的 GitHub 账户：验证 token → 存用户名 + 加密存储 token。
    同步到 GitHub 时优先以用户身份推送（回退管理员 token）。"""
    token = str(req.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="缺少 GitHub token")
    try:
        username, gh_id = await verify_github_token(token)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from app.models.user import User
    from app.utils.crypto import encrypt_api_key
    from app.services.world.market_github import _generate_signing_keypair
    user = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one()
    user.github_token_encrypted = encrypt_api_key(token)
    user.github_username = username
    user.github_id = gh_id
    # 签名密钥对：首次绑定生成（Ed25519），私钥加密存储、公钥随 meta 发布
    if not user.github_sign_key_encrypted:
        priv_pem, pub_pem = _generate_signing_keypair()
        user.github_sign_key_encrypted = encrypt_api_key(priv_pem)
        user.github_public_key = pub_pem
    await db.commit()
    logger.info(f"👤 用户 {current_user['user_id']} 绑定 GitHub 账户 @{username}（id={gh_id}）")
    return {"bound": True, "username": username}


@router.get("/github/bind")
async def github_bind_status(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户的 GitHub 绑定状态（不回显 token）"""
    from app.models.user import User
    user = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one()
    return {"bound": bool(user.github_token_encrypted), "username": user.github_username}


@router.delete("/github/bind")
async def unbind_github(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解绑当前用户的 GitHub 账户"""
    from app.models.user import User
    user = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one()
    user.github_token_encrypted = None
    user.github_username = None
    await db.commit()
    logger.info(f"👤 用户 {current_user['user_id']} 解绑 GitHub 账户")
    return {"bound": False}


@router.get("/github/items")
async def list_github_items(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """GitHub 板块列表：读索引快照（不实时请求远端）。
    每条标注：is_local（本地同 id 在架商品）/ is_mine（当前用户 github_id == 作者）/ 签名状态。"""
    from app.models.world import WorldMarketItem
    from app.models.user import User
    local_ids = set((await db.execute(
        select(WorldMarketItem.id).where(
            WorldMarketItem.status == "on", WorldMarketItem.source == "local")
    )).scalars().all())
    me = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one_or_none()
    my_gh_id = int(me.github_id or 0) if me else 0
    snap = load_snapshot()
    items = []
    for w in snap.get("worlds", []):
        author_gh_id = int(w.get("author_github_id") or 0)
        items.append({
            "id": w.get("id"),
            "slug": w.get("slug"),
            "kind": w.get("kind") or "world",
            "title": w.get("title"),
            "description": w.get("description"),
            "tags": w.get("tags") or [],
            "author_name": w.get("author_name"),
            "author_github": w.get("author_github"),
            "downloads": w.get("downloads"),
            "updated_at": w.get("updated_at"),
            "is_local": int(w.get("id") or 0) in local_ids,
            "is_mine": bool(my_gh_id and author_gh_id and my_gh_id == author_gh_id),
            "signature_valid": w.get("signature_valid"),
            "key_changed": bool(w.get("key_changed")),
        })
    return {"synced_at": snap.get("synced_at"), "items": items, "total": len(items)}


@router.post("/github/import")
async def import_github_item(
    req: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从 GitHub 导入资源（快照条目 → 下载 zip → 创建新世界）"""
    item_id = int(req.get("id") or 0)
    if not item_id:
        raise HTTPException(status_code=400, detail="缺少资源 id")
    try:
        return await import_from_github(db, current_user["user_id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning(f"🏪 GitHub 导入失败 item {item_id}: {e}")
        raise HTTPException(status_code=502, detail=f"导入失败: {str(e)[:200]}")


@router.post("/github/refresh")
async def refresh_github(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员手动刷新：拉取 GitHub 仓库最新索引，更新缓存商品"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可刷新 GitHub 商城")
    try:
        from app.services.world.market_github import refresh_from_github
        r = await refresh_from_github(db)
        return r
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.warning(f"🏪 GitHub 刷新失败: {e}")
        raise HTTPException(status_code=502, detail=f"刷新失败: {str(e)[:200]}")


@router.get("/settings")
async def get_market_settings(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """商城配置：管理员可见脱敏 token（前4…后4）；普通用户只见开关"""
    from app.services.world.market_github import get_market_config, mask_token
    cfg = await get_market_config(db)
    if current_user.get("role") == "admin":
        cfg["github_token"] = mask_token(cfg["github_token"])
    else:
        cfg["github_token"] = ""
    return cfg


@router.put("/settings")
async def update_market_settings(
    req: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新商城配置（仅管理员）：github_repo / github_token / auto_sync_enabled。
    token 加密存储；传入值与当前脱敏值一致视为未修改（防误存脱敏串）。"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改商城配置")
    from app.services.world.market_github import save_market_config, get_market_config, mask_token
    new_token = req.get("github_token")
    if new_token:
        # 与当前脱敏值相同 → 管理员只是重新提交了显示值，未真正修改
        cur = await get_market_config(db)
        if new_token.strip() == mask_token(cur.get("github_token") or ""):
            new_token = None
    cfg = await save_market_config(
        db,
        github_repo=req.get("github_repo"),
        github_token=new_token,
        auto_sync_enabled=req.get("auto_sync_enabled"),
    )
    cfg["github_token"] = mask_token(cfg.get("github_token") or "")
    return cfg


@router.get("/items/{item_id}/download")
async def download_item(
    item_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载商品 zip（手动导入用）"""
    from app.models.world import WorldMarketItem
    item = (await db.execute(select(WorldMarketItem).where(WorldMarketItem.id == item_id))).scalar_one_or_none()
    if item is None or item.status != "on":
        raise HTTPException(status_code=404, detail="商品不存在")
    pkg = DATA_DIR / item.package_path
    if not pkg.is_file():
        raise HTTPException(status_code=404, detail="商品包缺失")
    from fastapi.responses import Response
    return Response(
        content=pkg.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=market_{item_id}.zip"},
    )
