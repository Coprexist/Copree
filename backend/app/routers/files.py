"""
文件系统路由
上传、下载、删除、列表、协作模式管理、消息附件
"""
import os
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.file import FileMetadata, FileReference, FileCollaborator
from app.utils.auth import get_current_user
from app.config import settings
from app.services.infrastructure.maintenance import maintenance
from app.services.content.file_service import (
    upload_file, list_files, get_file, get_file_physical_path,
    delete_file, check_file_access, track_file_reference,
    set_collaboration_mode, add_file_collaborator, remove_file_collaborator,
    get_file_collaborators, get_file_referrers,
    _check_path_safe, disk_path,
    _remove_forward_reference,
    list_viewed_files,
    user_space_dir, user_chat_dir, StorageQuotaExceeded, keep_to_my_files,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fs", tags=["文件"])


# ============================================================
# 目录列表
# ============================================================

@router.get("/list")
async def list_directory(
    scope: str = Query(
        "mine",
        pattern="^(mine|produced|viewed)$",
        description="mine=我的文件 / produced=我在对话里产生的 / viewed=浏览过的",
    ),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按存储页的三块口径列文件（§3.1）

    三块是**并列**关系，不是一个集合的筛选：我的文件＝users/{uid}/files、我在对话里
    产生的＝users/{uid}/chat、浏览过的＝别人发的、我看过或转发过的。所以入参是 scope
    而不是"目录 + 若干开关"——目录由后端按口径算，前端不需要知道搬迁细节。

    以前这里还会把名下 AI 的文件追加进列表（并有一个 include_forwarded 开关），
    与三块并列的口径冲突，两者都已移除（§10）。
    """
    uid = current_user["user_id"]
    if scope == "viewed":
        return await list_viewed_files(db, uid)

    directory = user_space_dir(uid) if scope == "mine" else user_chat_dir(uid)
    return await list_files(db, directory, "human", uid)


# ============================================================
# 文件上传
# ============================================================

@router.post("/upload")
async def upload_file_endpoint(
    collaboration_mode: str = Query("solo", pattern="^(solo|shared|open)$"),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传到「我的文件」（users/{uid}/files）

    这里**不接受目录参数**：存储页上传的东西进的是用户自己的空间，配额天天盯着它，
    路径必须由后端算死。输入框附件走 /upload-attachment，进的是 users/{uid}/chat。
    """
    content = await file.read()
    from app.config import get_effective_upload_max_size_mb
    max_mb = get_effective_upload_max_size_mb()
    max_bytes = max_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(400, f"文件大小不能超过 {max_mb}MB")
    try:
        metadata = await upload_file(
            db, user_space_dir(current_user["user_id"]),
            file.filename or f"unnamed_{uuid.uuid4().hex[:8]}",
            content, file.content_type, "human", current_user["user_id"],
            collaboration_mode=collaboration_mode,
        )
    except StorageQuotaExceeded as e:
        # 413：这次写入装不下。带上已用/上限，界面直接显示，不用再查一次
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    return {
        "id": metadata.id,
        "path": metadata.path,
        "size": metadata.size,
        "mime_type": metadata.mime_type,
        "collaboration_mode": metadata.collaboration_mode,
        "created_at": str(metadata.created_at) if metadata.created_at else None,
    }


# ============================================================
# 语音文稿（按 file_id 批量取）
# ============================================================

@router.get("/transcripts")
async def get_transcripts(
    file_ids: str = Query(..., description="逗号分隔的 file_id"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一批文件的语音文稿 → `{"199": "晚自习别熬太晚。"}`（只回请求者读得到的）。

    文稿的家在 `file_metadata.transcript`，消息负载里**不带**它：语音条按 file_id 懒取，
    这样转发、ASR 回填都只改一处，消息快照也不会变成 N 份。单次最多 50 个 id。
    """
    ids: list[int] = []
    for part in (file_ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    ids = ids[:50]
    if not ids:
        return {}
    from app.services.content.file_service import transcripts_for

    texts = await transcripts_for(db, ids)
    out: dict[str, str] = {}
    for fid, body in texts.items():
        allowed = await check_file_access(db, fid, "human", current_user["user_id"], "read")
        if allowed is not None:
            out[str(fid)] = body
    return out


# ============================================================
# 消息附件上传（独立端点，上传后返回 file_id 供消息引用）
# ============================================================

@router.post("/upload-attachment")
async def upload_attachment(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传消息附件（进 users/{uid}/chat，发出去之后字节也还在这里）"""
    content = await file.read()

    # 限制附件大小
    from app.config import get_effective_upload_max_size_mb
    max_mb = get_effective_upload_max_size_mb()
    max_size = max_mb * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                          detail=f"附件大小不能超过 {max_mb}MB")

    try:
        metadata = await upload_file(
            db, user_chat_dir(current_user["user_id"]),
            file.filename or f"att_{uuid.uuid4().hex[:8]}",
            content, file.content_type, "human", current_user["user_id"],
            collaboration_mode="solo",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"附件上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"上传失败: {e}")

    return {
        "file_id": metadata.id,
        "name": os.path.basename(metadata.path),
        "path": metadata.path,
        "size": metadata.size,
        "mime_type": metadata.mime_type,
    }


# ============================================================
# 收进我的文件（§7.3）
# ============================================================

@router.post("/keep")
async def keep_file_endpoint(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把一份文件收进我的文件（硬链接，不占第二份磁盘）

    这是第一栏**唯一**的入口：预览弹窗和消息三点菜单都调它，规则只写一处。
    已在我的空间里返回 already=true（幂等）；配额不够 413，消息里带已用/上限/本次大小。
    """
    file_id = int(body.get("file_id") or 0)
    if not file_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 file_id")
    # 可选显示名：语音收藏传「谁发的 + 前几个字」，落成目录项就是它的标题。
    # 只影响名字，不改变硬链接与权限（规范在 collect_alias 一处做）。
    alias = body.get("name")
    try:
        return await keep_to_my_files(
            db, current_user["user_id"], file_id,
            name=str(alias) if alias else None,
        )
    except StorageQuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/unkeep")
async def unkeep_file_endpoint(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把收藏的那一份移出我的文件（只删我这边的目录项，原消息里的语音不受影响）"""
    file_id = int(body.get("file_id") or 0)
    if not file_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 file_id")
    from app.services.content.file_service import unkeep_from_my_files
    # 可选 name：与收藏时同一个标题，按目标路径精确删那一份（没有就退回内容哈希）
    alias = body.get("name")
    try:
        return await unkeep_from_my_files(
            db, current_user["user_id"], file_id,
            name=str(alias) if alias else None,
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================
# 文件下载
# ============================================================

@router.get("/download-avatar/{filename}")
async def serve_avatar(filename: str, thumb: bool = Query(False)):
    """直接返回头像文件（无需鉴权，仅限 avatars 目录）。
    加 ?thumb=1 返回缩略图（动图除外）。"""
    import os
    from fastapi.responses import FileResponse
    if thumb:
        thumb_name = f"thumb_{filename}"
        thumb_path = os.path.join(settings.avatars_dir, thumb_name)
        if os.path.isfile(thumb_path):
            return FileResponse(thumb_path, headers={"Cache-Control": "public, max-age=604800"})
        # 无缩略图时降级到原图
        filepath = os.path.join(settings.avatars_dir, filename)
        if os.path.isfile(filepath):
            return FileResponse(filepath, headers={"Cache-Control": "public, max-age=604800"})
        raise HTTPException(status_code=404, detail="头像不存在")
    filepath = os.path.join(settings.avatars_dir, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="头像不存在")
    return FileResponse(filepath, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/download/{file_id}")
async def download_file(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """下载文件（含权限检查）"""
    try:
        metadata = await get_file(db, file_id)
    except Exception as e:
        logger.error(f"下载文件 {file_id} 查询元数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="查询文件失败")

    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    # 权限检查
    try:
        can_access = await check_file_access(db, file_id, "human", current_user["user_id"], "read")
    except Exception as e:
        logger.error(f"下载文件 {file_id} 权限检查异常: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"权限检查失败: {e}")

    if not can_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此文件")

    physical_path = disk_path(metadata.path)
    if not os.path.exists(physical_path):
        logger.error(f"下载文件 {file_id}: 物理文件不存在 path={physical_path} metadata.path={metadata.path}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"物理文件不存在")

    # 追踪引用
    await track_file_reference(db, file_id, "human", current_user["user_id"], "read")

    return FileResponse(
        physical_path,
        media_type=metadata.mime_type or "application/octet-stream",
        filename=os.path.basename(metadata.path),
    )


@router.get("/public/{file_id}")
async def download_public_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """公开下载（无需登录）— 仅限维护弹窗引用的图片，其余一律 403。

    免鉴权是场景决定的：硬维护时用户未登录，弹窗里的 <img> 也带不了 Authorization。
    因此把「能匿名下载」收敛成维护图片白名单（MaintenanceManager.is_public_file），
    而不是放开整张 file_metadata 表。
    """
    if not maintenance.is_public_file(file_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该文件不对外公开")

    metadata = await get_file(db, file_id)
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    physical_path = disk_path(metadata.path)
    if not os.path.exists(physical_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="物理文件不存在")
    return FileResponse(physical_path, media_type=metadata.mime_type or "application/octet-stream")


# ============================================================
# 文件删除
# ============================================================

@router.delete("/delete/{file_id}")
async def delete_file_endpoint(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除文件（owner 删除时会自动过户给转发者或标记孤儿）"""
    try:
        result = await delete_file(db, file_id, "human", current_user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    action = result.get("action", "deleted")
    messages = {
        "deleted": "文件已删除",
        "released": "已释放文件引用",
        "transferred": f"文件已由他人接管",
        "orphaned": "文件无人持有，将在宽限期后清理",
    }
    return {"message": messages.get(action, "操作成功"), **result}


@router.delete("/release/{file_id}")
async def release_file_reference(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """释放转发文件的引用（非 owner 从存储中移除）"""
    try:
        result = await delete_file(db, file_id, "human", current_user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "已释放文件引用", **result}


# ============================================================
# 协作模式管理
# ============================================================

@router.put("/{file_id}/collaboration-mode")
async def update_collaboration_mode(
    file_id: int,
    mode: str = Query(..., pattern="^(solo|shared|open)$"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改文件协作模式（仅 owner）"""
    try:
        metadata = await set_collaboration_mode(db, file_id, mode, "human", current_user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {
        "file_id": metadata.id,
        "path": metadata.path,
        "collaboration_mode": metadata.collaboration_mode,
    }


@router.post("/{file_id}/collaborators")
async def add_collaborator(
    file_id: int,
    collaborator_type: str = Query(..., pattern="^(ai|user)$"),
    collaborator_id: int = Query(...),
    role: str = Query("collaborator", pattern="^(collaborator|viewer)$"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加文件协作者（仅 owner）"""
    try:
        collab = await add_file_collaborator(
            db, file_id, collaborator_type, collaborator_id, role,
            requester_type="human", requester_id=current_user["user_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {
        "id": collab.id,
        "file_id": collab.file_id,
        "collaborator_type": collab.collaborator_type,
        "collaborator_id": collab.collaborator_id,
        "role": collab.role,
    }


@router.delete("/{file_id}/collaborators/{collaborator_type}/{collaborator_id}")
async def remove_collaborator(
    file_id: int,
    collaborator_type: str,
    collaborator_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """移除文件协作者"""
    try:
        await remove_file_collaborator(db, file_id, collaborator_type, collaborator_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return {"message": "协作者已移除"}


@router.get("/{file_id}/collaborators")
async def list_collaborators(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出文件的协作者"""
    metadata = await get_file(db, file_id)
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    collaborators = await get_file_collaborators(db, file_id)
    referrers = await get_file_referrers(db, file_id)

    return {
        "file_id": file_id,
        "path": metadata.path,
        "collaboration_mode": metadata.collaboration_mode,
        "collaborators": collaborators,
        "referrers": referrers,
    }


@router.get("/{file_id}/references-detail")
async def get_file_references_detail(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取文件的详细引用信息（含群名、AI 名等可读名称），用于删除确认"""
    from app.models.file import FileReference as FR
    from app.models.agent import Agent
    from app.models.user import User
    from app.models.group import Group as GroupModel
    from app.models.message import Message as MessageModel
    from sqlalchemy import select

    metadata = await get_file(db, file_id)
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    # 查询所有引用
    ref_result = await db.execute(
        select(FR).where(FR.file_id == file_id)
    )
    refs = ref_result.scalars().all()

    references = []
    for r in refs:
        ref_info = {
            "referrer_type": r.referrer_type,
            "referrer_id": r.referrer_id,
            "ref_type": r.ref_type,
            "display": f"{r.referrer_type}:{r.referrer_id}",
        }

        # 解析为可读名称
        if r.referrer_type == "ai":
            agent_result = await db.execute(select(Agent.name).where(Agent.id == r.referrer_id))
            name = agent_result.scalar_one_or_none()
            ref_info["display"] = f"AI「{name or r.referrer_id}」"

        elif r.referrer_type == "human":
            user_result = await db.execute(select(User.username).where(User.id == r.referrer_id))
            name = user_result.scalar_one_or_none()
            ref_info["display"] = f"用户「{name or r.referrer_id}」"

        elif r.referrer_type == "group":
            group_result = await db.execute(select(GroupModel.name).where(GroupModel.id == r.referrer_id))
            name = group_result.scalar_one_or_none()
            ref_info["display"] = f"群聊「{name or r.referrer_id}」"

        elif r.referrer_type == "message":
            # 消息引用：查出消息所在的群聊或 DM
            msg_result = await db.execute(
                select(MessageModel.group_id).where(MessageModel.id == r.referrer_id)
            )
            gid = msg_result.scalar_one_or_none()
            if gid is not None:
                # 群聊消息
                gname_result = await db.execute(select(GroupModel.name).where(GroupModel.id == gid))
                gname = gname_result.scalar_one_or_none()
                ref_info["display"] = f"群聊「{gname or gid}」的消息 #{r.referrer_id}"
            else:
                ref_info["display"] = f"私信消息 #{r.referrer_id}"

        references.append(ref_info)

    return {
        "file_id": file_id,
        "path": metadata.path,
        "name": metadata.path.rsplit("/", 1)[-1] if "/" in metadata.path else metadata.path,
        "size": metadata.size,
        "reference_count": len(references),
        "references": references,
    }


# ============================================================
# 创建目录
# ============================================================

@router.post("/mkdir")
async def create_directory(
    path: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """创建目录"""
    if not _check_path_safe(path):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="路径不合法")

    physical_path = disk_path(path)
    os.makedirs(physical_path, exist_ok=True)

    return {"message": "目录已创建", "path": path}
