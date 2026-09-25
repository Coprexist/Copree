"""
统一插件 API — 目录即插件，两级开关

- GET    /plugins                 插件列表（含管理员全局开关 + 当前用户偏好 + 生效状态 + 皮肤变量
                                   + service 类的运行态与配置需求）
- POST   /plugins/{id}/toggle     管理员全局开放/关闭
- POST   /plugins/{id}/pref       用户个人启用/停用
- POST   /plugins/rescan          管理员手动重扫磁盘（新增/卸载/改代码立即生效）
- GET    /plugins/{id}/config     管理员：每个实例的配置与运行态（永不回显机密）
- PUT    /plugins/{id}/config     管理员：全量保存实例列表（列表即真相；未传的键不动，空串=清除）
- GET    /plugins/store           插件商城清单（安装包 + 安装状态；读所有人可看）
- POST   /plugins/store/packages  管理员：上传安装包（只审阅入库，不安装）
- POST   /plugins/store/packages/{file}/install  管理员：安装 / 更新（先亮 manifest 再确认）
- DELETE /plugins/store/packages/{file}          管理员：删除安装包
- DELETE /plugins/store/installed/{id}           管理员：卸载已安装插件（内置插件不可卸）

生效规则：effective = plugins.enabled AND user_plugin_prefs.enabled（偏好默认开）
皮肤规则：用户同一时刻只启用一个 skin 类插件（启用 A 自动停用其余 skin）
服务规则：service 类插件的启停走 /admin/plugins/{id}/start|stop（运行态属于服务，不属于用户偏好）
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.auth import get_current_user, require_admin
from app.services.plugin import catalog
from app.services.plugin import config as plugin_config
from app.services.plugin.skill_bridge import apply_skill_plugins

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["统一插件"])


async def _broadcast_plugins_changed() -> None:
    """广播插件变更 → 在线用户前端即时刷新皮肤/插件状态（回退保障）"""
    try:
        from app.routers.ws import manager as ws_manager
        await ws_manager.broadcast_to_all({"type": "plugins_changed"})
    except Exception as e:
        logger.warning(f"广播 plugins_changed 失败（不影响主流程）: {e}")


class PrefRequest(BaseModel):
    enabled: bool = True


class ConfigRequest(BaseModel):
    """列表即真相：提交哪些实例就留哪些实例，没提交的实例会被删除。"""
    instances: list[dict] = []


async def _service_view(plugin_id: str, db: AsyncSession) -> dict:
    """service 插件视图：每个实例的运行态 + 配置 + 缺口。

    一个插件可以有多份配置（每份 = 一个实例，比如每个 QQ 机器人一份）。
    列表视图与配置接口共用这一份数据——前端一次拿全，不用自己拼。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key
    from app.services.plugin import api

    defn = api.get_service_def(plugin_id)
    schema = dict(defn.config_schema) if defn else {}
    states = await plugin_config.desired_states(db=db)
    instances = []
    for instance in await plugin_config.list_instances(plugin_id, db=db):
        masked = await plugin_config.mask_config(plugin_id, instance, db=db)
        plugin = PluginRegistry.get(registry_key(plugin_id, instance))
        running = False
        detail: dict = {}
        if plugin is not None:
            try:
                detail = dict(await plugin.get_status() or {})
                running = bool(detail.pop("running", False))
                detail.pop("installed", None)
            except Exception:
                running = False
        missing = [
            key for key, spec in schema.items()
            if spec.get("required") and not (
                masked["secrets"].get(key, False) if spec.get("secret") else bool(masked["values"].get(key))
            )
        ]
        instances.append({
            "instance": instance,
            "values": masked["values"],
            "secrets": masked["secrets"],
            "running": running,
            "missing_required": missing,
            "detail": detail,
            "desired_running": states.get((plugin_id, instance), True),
        })
    return {
        "multi_instance": bool(defn and defn.multi_instance),
        "config_schema": schema,
        "instances": instances,
    }


def _to_view(row, manifest: dict, user_pref: bool, is_admin: bool, users_count: int | None = None) -> dict:
    """DB 行 + 磁盘 manifest → API 视图"""
    skin_vars = catalog.get_skin_vars(manifest) if manifest.get("category") == "skin" else {}
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "version": row.version,
        "author": row.author,
        "icon": row.icon,
        "builtin": row.builtin,
        "global_enabled": row.enabled,
        "user_enabled": user_pref,
        "effective": bool(row.enabled and user_pref),
        "is_admin": is_admin,
        "users_count": users_count,  # 管理员视角：显式启用了该插件的用户数（皮肤 = 正在用这套的人数）
        "skin_vars": skin_vars,
    }


@router.get("")
async def list_plugins(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """插件列表（登录即可；管理员看到全局开关，普通用户看到自己的开关）"""
    from app.models.plugin import Plugin, UserPluginPref
    from app.models.user import User as UserModel

    await catalog.sync_plugins_to_db(db)  # 懒同步：目录变化即时可见（装好即可用）
    await apply_skill_plugins(db)

    user_id = current_user["user_id"]
    # 角色从 DB 重读（JWT role 可能是提权前的旧值），与 require_admin 同源
    urow = await db.get(UserModel, user_id)
    is_admin = bool(urow and urow.role == "admin")

    result = await db.execute(select(Plugin))
    rows = {p.id: p for p in result.scalars().all()}

    # 所有用户（含 admin）都查个人偏好：admin 也是普通用户，皮肤开关同样走 pref
    pref_result = await db.execute(
        select(UserPluginPref).where(UserPluginPref.user_id == user_id)
    )
    prefs = {p.plugin_id: p.enabled for p in pref_result.scalars().all()}

    # 管理员视角：统计每个插件被多少用户显式启用（皮肤 = 正在用这套皮肤的人数）
    users_count: dict[str, int] = {}
    if is_admin:
        from sqlalchemy import func
        count_result = await db.execute(
            select(UserPluginPref.plugin_id, func.count(UserPluginPref.id))
            .where(UserPluginPref.enabled.is_(True))
            .group_by(UserPluginPref.plugin_id)
        )
        users_count = {pid: int(c) for pid, c in count_result.all()}

    disk = catalog.scan_disk()
    plugins = []
    for pid in sorted(rows.keys()):
        row = rows[pid]
        manifest = disk.get(pid, {})
        user_pref = prefs.get(pid, True)
        view = _to_view(row, manifest, user_pref, is_admin, users_count.get(pid))
        if row.category == "service":
            # service 类的"运行态"在注册表里，不在 DB 里——两个来源合并成一份视图
            view["service"] = await _service_view(pid, db)
        plugins.append(view)
    return {"plugins": plugins}


@router.post("/rescan")
async def rescan_plugins(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员手动重扫磁盘插件目录（新增/卸载/改代码立即生效）"""
    changed = await catalog.sync_plugins_to_db(db)
    await apply_skill_plugins(db, force=True)
    await _broadcast_plugins_changed()
    return {"message": f"重扫完成，{changed} 项变更"}


@router.post("/{plugin_id}/toggle")
async def toggle_plugin(
    plugin_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员全局开放/关闭插件"""
    from app.models.plugin import Plugin

    row = await db.get(Plugin, plugin_id)
    if row is None:
        raise HTTPException(404, f"未知插件: {plugin_id}")
    row.enabled = not row.enabled
    await db.commit()
    await apply_skill_plugins(db)
    state = "开放" if row.enabled else "关闭"
    logger.info(f"管理员{state}插件 {plugin_id}")
    await _broadcast_plugins_changed()
    return {"message": f"「{row.name}」已{state}", "global_enabled": row.enabled}


@router.post("/{plugin_id}/pref")
async def set_plugin_pref(
    plugin_id: str,
    req: PrefRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户个人启用/停用插件；skin 类互斥（启用一个自动停用其余）"""
    from app.models.plugin import Plugin, UserPluginPref

    row = await db.get(Plugin, plugin_id)
    if row is None:
        raise HTTPException(404, f"未知插件: {plugin_id}")
    if not row.enabled:
        raise HTTPException(403, f"「{row.name}」已被管理员关闭，无法启用")

    user_id = current_user["user_id"]

    if req.enabled and row.category == "skin":
        # 互斥：显式把其他所有皮肤的用户偏好置为停用（无记录也要落 False 记录，
        # 否则列表默认 user_enabled=True 会让多个皮肤同时 effective）
        other_skins = await db.execute(
            select(Plugin).where(Plugin.category == "skin", Plugin.id != plugin_id)
        )
        for other_row in other_skins.scalars().all():
            other_pref = (
                await db.execute(
                    select(UserPluginPref).where(
                        UserPluginPref.user_id == user_id,
                        UserPluginPref.plugin_id == other_row.id,
                    )
                )
            ).scalar_one_or_none()
            if other_pref is None:
                db.add(UserPluginPref(user_id=user_id, plugin_id=other_row.id, enabled=False))
            else:
                other_pref.enabled = False

    pref = (
        await db.execute(
            select(UserPluginPref).where(
                UserPluginPref.user_id == user_id,
                UserPluginPref.plugin_id == plugin_id,
            )
        )
    ).scalar_one_or_none()

    if pref is None:
        pref = UserPluginPref(user_id=user_id, plugin_id=plugin_id, enabled=req.enabled)
        db.add(pref)
    else:
        pref.enabled = req.enabled
    await db.commit()

    return {
        "message": f"「{row.name}」已{'启用' if req.enabled else '停用'}",
        "user_enabled": req.enabled,
        "effective": bool(row.enabled and req.enabled),
    }


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """插件配置视图：每个实例的 schema + 非机密值 + 机密键"有没有填" + 运行态。永不回显机密。"""
    from app.models.plugin import Plugin

    if await db.get(Plugin, plugin_id) is None:
        raise HTTPException(404, f"未知插件: {plugin_id}")
    return await _service_view(plugin_id, db)


@router.put("/{plugin_id}/config")
async def put_plugin_config(
    plugin_id: str,
    req: ConfigRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """全量保存实例列表：没提交的实例会被删除（停掉并回收），单个实例里未传的键不动。"""
    from app.models.plugin import Plugin

    row = await db.get(Plugin, plugin_id)
    if row is None:
        raise HTTPException(404, f"未知插件: {plugin_id}")
    try:
        summary = await plugin_config.replace_instances(
            plugin_id, req.instances, actor=str(admin.get("user_id") or ""), db=db
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 运行时与配置对齐：新增的实例建起来、删掉的实例停掉——启停逻辑只住在 skill_bridge 一处
    await apply_skill_plugins(db)
    await _broadcast_plugins_changed()
    return {"message": f"「{row.name}」配置已保存", "changed": summary}


# ═══════════════════════════════════════════════════════════════
# 插件商城（一期：本地安装包）— 上传 → 审阅 → 安装/更新/卸载
# ═══════════════════════════════════════════════════════════════

async def _after_store_change(db: AsyncSession, plugin_id: str | None = None) -> int:
    """安装/卸载之后把三层状态对齐：磁盘 → DB → 运行时。

    顺序不能反：先让 catalog 把 DB 行同步成磁盘的样子（新增/删除），
    再让 skill_bridge 按 DB 开关加载/回收代码。卸载还要清掉服务期望状态，
    否则同名插件下次装上会带着上一辈子的启停记录。
    """
    from app.models.plugin import PluginServiceState
    from sqlalchemy import delete

    changed = await catalog.sync_plugins_to_db(db)
    if plugin_id:
        await db.execute(delete(PluginServiceState).where(PluginServiceState.plugin_id == plugin_id))
        await db.commit()
    await apply_skill_plugins(db, force=True)
    await _broadcast_plugins_changed()
    return changed


@router.get("/store")
async def list_plugin_store(
    _user: dict = Depends(get_current_user),
):
    """商城清单：仓库里有哪些安装包、装没装、能不能更新。

    读操作对所有登录用户开放（普通用户也能看到"有什么插件可用"），
    上传/安装/卸载一律要管理员——这是安装包的信任边界。
    """
    from app.services.plugin import store

    return {"packages": store.list_packages()}


@router.post("/store/packages")
async def upload_store_package(
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin),
):
    """上传安装包：只做校验与入库，**不安装**——安装是管理员看过 manifest 之后的单独动作"""
    from app.services.plugin import store

    data = await file.read()
    try:
        return store.save_package(file.filename or "", data)
    except store.PackageError as e:
        raise HTTPException(400, str(e))


@router.post("/store/packages/{file_name}/install")
async def install_store_package(
    file_name: str,
    upgrade: bool = Query(False),
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """安装 / 更新：包 → DATA_DIR/plugins/<id> → DB → 运行时"""
    from app.services.plugin import store

    path = (store.PACKAGE_DIR / file_name).resolve()
    if store.PACKAGE_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, f"安装包不存在: {file_name}")
    try:
        result = store.install_package(path.read_bytes(), upgrade=upgrade)
    except store.PackageError as e:
        raise HTTPException(400, str(e))
    changed = await _after_store_change(db, result["manifest"]["id"])
    logger.info("管理员安装插件 %s v%s（DB 变更 %s）", result["manifest"]["id"], result["manifest"]["version"], changed)
    return {"message": f"「{result['manifest']['name']}」已{'更新' if upgrade else '安装'}", **result}


@router.delete("/store/packages/{file_name}")
async def delete_store_package(
    file_name: str,
    _admin: dict = Depends(require_admin),
):
    """删除安装包文件（已安装的插件不受影响）"""
    from app.services.plugin import store

    try:
        store.delete_package(file_name)
    except store.PackageError as e:
        raise HTTPException(404, str(e))
    return {"message": "安装包已删除"}


@router.delete("/store/installed/{plugin_id}")
async def uninstall_store_plugin(
    plugin_id: str,
    _admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """卸载已安装插件：删用户插件目录（内置插件一律拒绝）→ 停服务 → 清 DB"""
    from app.services.plugin import store

    try:
        store.uninstall_plugin(plugin_id)
    except store.PackageError as e:
        raise HTTPException(400, str(e))
    await _after_store_change(db, plugin_id)
    logger.info("管理员卸载插件 %s", plugin_id)
    return {"message": f"「{plugin_id}」已卸载"}

