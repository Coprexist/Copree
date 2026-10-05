"""
文件服务层
文件 CRUD、权限检查、协作模式判定、O(n) 引用通知
"""
import os
import uuid
import json
import hashlib
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete, and_, func, text
from app.models.file import FileMetadata, FileReference, FileCollaborator
from app.config import settings
from app.db_providers import get_provider
from app.repositories.content_repo import ContentRepository, SQLAlchemyContentRepository

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyContentRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyContentRepository(db_or_repo)
    return db_or_repo

# ============================================================
# 权限检查
# ============================================================

def _check_path_safe(path: str) -> bool:
    """检查路径是否安全（防目录穿越）"""
    if path in (".", "/"):
        return True
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or normalized.startswith("/"):
        return False
    return True


def _get_physical_path(relative_path: str) -> str:
    """构建物理存储路径"""
    return os.path.join(settings.data_dir, relative_path)


# ── AI 文件空间：路径换算的唯一入口 ──
# AI 的文件空间就是它的沙箱目录 data/agents/{id}/（run_script、记忆、OpenCLI 都在这里，
# 由 app/paths.agent_dir 解析）。file_* 以前直接落在 data/ 根，同一个「我的文件」分成两处：
# file_write 写出来的脚本，run_script 里 os.listdir 找不到；两侧还各留一份同名不同内容的
# 文件，AI 改完再读像是"读到旧缓存"。AI 侧一律经下面两个函数换算，存储路径全库唯一。
AI_FILE_ROOT = "agents"


def ai_stored_path(agent_id: int, file_path: str) -> str:
    """AI 视角的路径 → 存储路径（agents/{id}/...）；爬出自己文件空间当场拒绝。

    开头的斜杠按「文件空间根」理解（AI 习惯写 /notes/a.md），不是系统绝对路径；
    前缀化之后 .. 只可能爬到别人的文件空间，所以规范化后仍以 .. 开头的一律拒绝。
    """
    rel = str(file_path or "").strip().replace("\\", "/").lstrip("/")
    norm = os.path.normpath(rel) if rel else ""
    if norm in (".", "/"):
        norm = ""
    elif norm.startswith(".."):
        raise ValueError("路径不合法：只能访问你自己文件空间内的文件")
    root = f"{AI_FILE_ROOT}/{agent_id}"
    return f"{root}/{norm}" if norm else root


def ai_view_path(agent_id: int, stored_path: str) -> str:
    """存储路径 → AI 视角（剥掉 agents/{id}/ 前缀），列表与界面回显用"""
    prefix = f"{AI_FILE_ROOT}/{agent_id}/"
    path = str(stored_path or "")
    return path[len(prefix):] if path.startswith(prefix) else path


def _check_permission(metadata: FileMetadata, requester_type: str, requester_id: int,
                      required_perm: str = "read") -> bool:
    """
    检查请求者对文件是否有指定权限。

    collaboration_mode 判定逻辑：
    - solo: 仅 owner 有全部权限
    - shared: owner + file_collaborators 中的协作者有权限
    - open: 所有同群/同 owner 的 AI 有 read 权限，write 需 owner 或 collaborator
    """
    # Owner 始终有全部权限
    if metadata.owner_type == requester_type and metadata.owner_id == requester_id:
        return True

    # 简单权限检查：根据 permissions JSONB 中的 rules
    if metadata.permissions and isinstance(metadata.permissions, dict):
        rules = metadata.permissions.get("rules", [])
        # 按角色匹配权限
        for rule in rules:
            role = rule.get("role", "")
            perm_str = rule.get("perm", "")
            if required_perm in perm_str:
                # 如果有更细粒度的检查逻辑，在此扩展
                pass

    return False


def _normalize_collab_type(requester_type: str) -> str:
    """将 requester_type 映射为 FileCollaborator 中存储的类型。

    FileCollaborator.collaborator_type 只允许 'ai' | 'user'，
    但调用方可能传 'human'（历史原因），需要统一映射。
    """
    if requester_type == "human":
        return "user"
    return requester_type


async def check_file_access(db: AsyncSession, file_id: int, requester_type: str,
                            requester_id: int, required_perm: str = "read") -> FileMetadata | None:
    db = _ensure_repo(db)
    """检查文件访问权限，返回 FileMetadata 或 None

    权限判定顺序：
    1. Owner 始终有权限
    2. collaboration_mode: solo / shared / open
    3. 消息附件可见性：文件被附在请求者可见的群聊/DM 消息中 → 允许 read
    """
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        return None

    # Owner 始终有权限
    if metadata.owner_type == requester_type and metadata.owner_id == requester_id:
        return metadata

    # AI 的文件 → 其制作者和合作者也有权限
    if metadata.owner_type == "ai" and requester_type == "human":
        from app.models.agent import Agent as AgentModel, AgentCollaborator
        agent_result = await db.execute(
            select(AgentModel).where(
                (AgentModel.id == metadata.owner_id) | (AgentModel.user_id == metadata.owner_id)
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is not None:
            # 制作者
            if agent.owner_id == requester_id:
                return metadata
            # 合作者
            collab_result = await db.execute(
                select(AgentCollaborator).where(
                    AgentCollaborator.agent_id == metadata.owner_id,
                    AgentCollaborator.user_id == requester_id,
                )
            )
            if collab_result.scalar_one_or_none() is not None:
                return metadata

    # collaboration_mode 判定
    mode = metadata.collaboration_mode or "solo"
    collab_type = _normalize_collab_type(requester_type)

    if mode == "solo":
        # solo 也检查协作者（send_file 等场景会自动添加），然后 fall through 到消息附件检查
        pass

    elif mode == "shared":
        # 检查是否为显式协作者（用规范化后的类型匹配 DB 中的 'user'）
        collab_result = await db.execute(
            select(FileCollaborator).where(
                FileCollaborator.file_id == file_id,
                FileCollaborator.collaborator_type == collab_type,
                FileCollaborator.collaborator_id == requester_id,
            )
        )
        collab = collab_result.scalar_one_or_none()
        if collab is not None:
            # viewer 角色只能读
            if collab.role == "viewer" and required_perm not in ("read",):
                return None
            return metadata

    elif mode == "open":
        # open 模式：read 权限对所有人开放，write 需 owner
        if required_perm == "read":
            return metadata
        # write 以上需 owner（已在上面返回）
        return None

    # ── 消息附件可见性检查（兜底） ──
    # 如果文件被附在请求者可见的消息中，授予 read 权限
    # 这覆盖了 AI send_file 到群聊/DM 后接收方下载的场景
    if required_perm == "read":
        try:
            if await _file_attached_to_visible_message(db, file_id, requester_type, requester_id):
                return metadata
        except Exception:
            logger.warning(f"消息附件可见性检查失败 (file_id={file_id})", exc_info=True)

    return None


async def _file_attached_to_visible_message(
    db: AsyncSession, file_id: int, requester_type: str, requester_id: int,
) -> bool:
    db = _ensure_repo(db)
    """检查文件是否被附在请求者可见的消息中（群聊成员 或 DM 参与者）"""
    pattern = json.dumps([{"file_id": file_id}])

    # 1) 群聊消息：请求者是否为消息所在群的成员
    #    附件 JSON 数组包含匹配（Postgres: @> JSONB；SQLite: json_each 遍历）
    provider = get_provider()
    if provider.name == "postgres":
        attachment_cond = "m.attachments @> CAST(:pattern AS JSONB)"
        attachment_params = {"pattern": pattern}
    else:
        attachment_cond = (
            "EXISTS (SELECT 1 FROM json_each(m.attachments) je "
            "WHERE json_extract(je.value, '$.file_id') = :file_id)"
        )
        attachment_params = {"file_id": file_id}

    check_group = text(f"""
        SELECT 1 FROM messages m
        JOIN group_members gm ON m.group_id = gm.group_id
        WHERE {attachment_cond}
          AND gm.member_type = :req_type
          AND gm.member_id = :req_id
        LIMIT 1
    """)
    r = await db.execute(check_group, {
        **attachment_params,
        "req_type": requester_type,
        "req_id": requester_id,
    })
    if r.scalar():
        return True

    # 2) DM 消息：请求者是否为 DM 会话的参与者
    # dm_messages 的 attachments 是 Text 列存 JSON 字符串，用 LIKE 匹配
    check_dm = text("""
        SELECT 1 FROM dm_messages dm
        JOIN dm_sessions ds ON dm.session_id = ds.session_id
        WHERE dm.attachments LIKE :like_pattern
          AND (ds.user1_id = :req_id OR ds.user2_id = :req_id)
        LIMIT 1
    """)
    r = await db.execute(check_dm, {
        "like_pattern": f"%\"file_id\": {file_id}%",
        "req_id": requester_id,
    })
    if r.scalar():
        return True

    return False


# ============================================================
# 文件 CRUD
# ============================================================

async def upload_file(
    db: AsyncSession,
    path: str,
    filename: str,
    content: bytes,
    mime_type: str | None,
    owner_type: str,
    owner_id: int,
    collaboration_mode: str = "solo",
) -> FileMetadata:
    db = _ensure_repo(db)
    """上传文件到指定路径（含三级去重：文件名 → 大小 → 内容哈希）"""
    if not _check_path_safe(path):
        raise ValueError("路径不合法：仅支持相对路径")

    relative_path = os.path.join(path, filename or f"unnamed_{uuid.uuid4().hex[:8]}")

    # ── 三级去重：文件名 → 大小 → 内容哈希 ──
    result = await db.execute(
        select(FileMetadata).where(
            FileMetadata.path == relative_path,
            FileMetadata.owner_type == owner_type,
            FileMetadata.owner_id == owner_id,
        ).limit(1)
    )
    existing = result.scalars().first()

    if existing and existing.size == len(content):
        # 大小相同 → 计算内容哈希进一步比对
        content_hash = hashlib.sha256(content).hexdigest()
        # 若已有文件未存哈希则补存
        if not existing.content_hash:
            existing.content_hash = content_hash
            await db.flush()
        if existing.content_hash == content_hash:
            logger.info(f"文件去重复用: {relative_path} (SHA256={content_hash[:8]}…)")
            return existing

    # ── 非重复 → 写入物理文件 ──
    content_hash = hashlib.sha256(content).hexdigest()
    physical_path = _get_physical_path(relative_path)
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)

    with open(physical_path, "wb") as f:
        f.write(content)

    # 记录元数据
    metadata = FileMetadata(
        path=relative_path,
        owner_type=owner_type,
        owner_id=owner_id,
        size=len(content),
        mime_type=mime_type,
        content_hash=content_hash,
        collaboration_mode=collaboration_mode,
        permissions={
            "owner": f"{owner_type}:{owner_id}",
            "rules": [
                {"role": "owner", "perm": "rwcdm"},
                {"role": "admin", "perm": "rwcd"},
                {"role": "collaborator", "perm": "rwc"},
                {"role": "viewer", "perm": "r"},
            ],
        },
    )
    db.add(metadata)
    await db.flush()
    await db.refresh(metadata)

    logger.info(f"文件上传完成: {relative_path} ({len(content)} bytes) by {owner_type}:{owner_id}")
    return metadata


async def list_files(
    db: AsyncSession,
    path: str,
    requester_type: str,
    requester_id: int,
) -> list[dict]:
    db = _ensure_repo(db)
    """列出目录内容（过滤无权条目）

    只查询请求者自身或有权限的文件，不加载全表。
    """
    if not _check_path_safe(path):
        raise ValueError("路径不合法：仅支持相对路径")

    # 只查询属于请求者的文件（owner 匹配），避免加载全表
    query = select(FileMetadata).where(
        FileMetadata.owner_type == requester_type,
        FileMetadata.owner_id == requester_id,
    )
    if path not in (".", "/"):
        query = query.where(FileMetadata.path.like(f"{path}%"))

    result = await db.execute(query)
    files = result.scalars().all()

    filtered = []
    for f in files:
        can_access = await check_file_access(db, f.id, requester_type, requester_id, "read")
        if can_access:
            filtered.append({
                "id": f.id,
                "path": f.path,
                "owner_type": f.owner_type,
                "owner_id": f.owner_id,
                "size": f.size,
                "mime_type": f.mime_type,
                "collaboration_mode": f.collaboration_mode,
                "created_at": str(f.created_at) if f.created_at else None,
                "updated_at": str(f.updated_at) if f.updated_at else None,
            })

    return filtered


async def get_file(db: AsyncSession, file_id: int) -> FileMetadata | None:
    db = _ensure_repo(db)
    """获取文件元数据"""
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    return result.scalar_one_or_none()


async def get_file_physical_path(db: AsyncSession, file_id: int,
                                 requester_type: str, requester_id: int) -> str | None:
    """获取文件的物理路径（含权限检查）"""
    metadata = await check_file_access(db, file_id, requester_type, requester_id, "read")
    if metadata is None:
        return None

    physical_path = _get_physical_path(metadata.path)
    if not os.path.exists(physical_path):
        return None

    return physical_path


async def delete_file(
    db: AsyncSession,
    file_id: int,
    requester_type: str,
    requester_id: int,
) -> dict:
    db = _ensure_repo(db)
    """删除文件（owner 删除时若有转发引用则过户给最早转发者，否则真删）"""
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        raise ValueError("文件不存在")

    # ── 转发者删除引用（非 owner） ──
    if not (metadata.owner_type == requester_type and metadata.owner_id == requester_id):
        removed = await _remove_forward_reference(db, file_id, requester_type, requester_id)
        if not removed:
            raise ValueError("无权删除此文件")
        return {"action": "released", "file_id": file_id}

    # ── Owner 删除：查找接盘者 ──
    successor = await _find_first_forwarder(db, file_id)
    if successor:
        # 过户给最早转发者
        old_owner = f"{metadata.owner_type}:{metadata.owner_id}"
        metadata.owner_type = successor[0]
        metadata.owner_id = successor[1]
        metadata.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # 删除接盘者的 forward 引用（他已升级为 owner）
        await db.execute(
            sa_delete(FileReference).where(
                FileReference.file_id == file_id,
                FileReference.referrer_type == successor[0],
                FileReference.referrer_id == successor[1],
                FileReference.ref_type == "forward",
            )
        )
        await db.flush()
        logger.info(f"文件 {file_id} 已过户: {old_owner} → {successor[0]}:{successor[1]}")
        return {"action": "transferred", "file_id": file_id, "new_owner": f"{successor[0]}:{successor[1]}"}

    # ── 无接盘者 → 孤儿或真删 ──
    forward_count = await db.execute(
        select(func.count(FileReference.id)).where(
            FileReference.file_id == file_id,
            FileReference.ref_type == "forward",
        )
    )
    if forward_count.scalar() > 0:
        # 仍有 forward 引用但无匹配 referrer（异常情况）→ 孤儿
        await _orphan_file(db, file_id)
        return {"action": "orphaned", "file_id": file_id}

    # 真删
    physical_path = _get_physical_path(metadata.path)
    if os.path.exists(physical_path):
        os.remove(physical_path)
    await db.delete(metadata)
    await db.flush()
    logger.info(f"文件已物理删除: {metadata.path} by {requester_type}:{requester_id}")
    return {"action": "deleted", "file_id": file_id}


# ============================================================
# 文件转发引用（零拷贝转发 + 过户 + 孤儿清理）
# ============================================================

async def _find_first_forwarder(db: AsyncSession, file_id: int) -> tuple[str, int] | None:
    db = _ensure_repo(db)
    """查找文件的第一个转发者（FIFO 排序），用于过户接盘"""
    r = await db.execute(
        select(FileReference.referrer_type, FileReference.referrer_id).where(
            FileReference.file_id == file_id,
            FileReference.ref_type == "forward",
        ).order_by(FileReference.created_at.asc()).limit(1)
    )
    row = r.one_or_none()
    return (row[0], row[1]) if row else None


async def track_forward_reference(
    db: AsyncSession,
    file_id: int,
    referrer_type: str,
    referrer_id: int,
) -> bool:
    db = _ensure_repo(db)
    """创建转发引用（幂等：同一人对同一文件只保留一条 forward 记录）。
    非 owner 发送含附件的消息时自动调用。
    返回 True 表示新创建（可用于扣配额），False 表示已存在或为 owner。
    """
    # Owner 自己不发转发引用
    meta = await db.get(FileMetadata, file_id)
    if not meta:
        return False
    if meta.owner_type == referrer_type and meta.owner_id == referrer_id:
        return False

    # 幂等：已有 forward 引用则跳过
    existing = await db.execute(
        select(FileReference).where(
            FileReference.file_id == file_id,
            FileReference.referrer_type == referrer_type,
            FileReference.referrer_id == referrer_id,
            FileReference.ref_type == "forward",
        )
    )
    if existing.scalar_one_or_none():
        return False

    ref = FileReference(
        file_id=file_id,
        referrer_type=referrer_type,
        referrer_id=referrer_id,
        ref_type="forward",
    )
    db.add(ref)
    await db.flush()
    logger.info(f"转发引用: file={file_id} → {referrer_type}:{referrer_id}")
    return True


async def _remove_forward_reference(
    db: AsyncSession,
    file_id: int,
    referrer_type: str,
    referrer_id: int,
) -> bool:
    db = _ensure_repo(db)
    """删除转发者的引用记录（返还配额时调用），返回是否找到并删除"""
    r = await db.execute(
        sa_delete(FileReference).where(
            FileReference.file_id == file_id,
            FileReference.referrer_type == referrer_type,
            FileReference.referrer_id == referrer_id,
            FileReference.ref_type == "forward",
        )
    )
    await db.flush()
    deleted = r.rowcount > 0
    if deleted:
        logger.info(f"转发引用已释放: file={file_id} ← {referrer_type}:{referrer_id}")
    return deleted


async def _orphan_file(db: AsyncSession, file_id: int):
    db = _ensure_repo(db)
    """将文件标记为孤儿（无主状态，宽限期后清理）"""
    meta = await db.get(FileMetadata, file_id)
    if meta:
        meta.owner_type = "system"
        meta.owner_id = 0
        meta.permissions = {"orphaned_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}
        await db.flush()
        logger.info(f"文件已标记为孤儿: {file_id}")


async def cleanup_orphaned_files(db: AsyncSession, retention_days: int = 7):
    db = _ensure_repo(db)
    """清理超过宽限期的孤儿文件（由定时任务调用）"""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)

    r = await db.execute(
        select(FileMetadata).where(
            FileMetadata.owner_type == "system",
            FileMetadata.owner_id == 0,
        )
    )
    orphans = r.scalars().all()

    deleted = 0
    for meta in orphans:
        # 从 permissions JSONB 读取 orphaned_at 时间
        perms = meta.permissions or {}
        orphaned_str = perms.get("orphaned_at", "")
        try:
            orphaned_at = datetime.fromisoformat(orphaned_str)
        except (ValueError, TypeError):
            orphaned_at = meta.updated_at or meta.created_at

        if orphaned_at and orphaned_at < cutoff:
            physical_path = _get_physical_path(meta.path)
            if os.path.exists(physical_path):
                os.remove(physical_path)
            await db.delete(meta)
            deleted += 1

    if deleted:
        await db.flush()
        logger.info(f"已清理 {deleted} 个过期孤儿文件（宽限期 {retention_days} 天）")
    return deleted


async def get_user_forwarded_file_ids(db: AsyncSession, user_id: int) -> set[int]:
    db = _ensure_repo(db)
    """获取用户所有转发引用的 file_id 集合（用于存储计算）"""
    r = await db.execute(
        select(FileReference.file_id).where(
            FileReference.referrer_type == "human",
            FileReference.referrer_id == user_id,
            FileReference.ref_type == "forward",
        )
    )
    return {row[0] for row in r.all()}


async def orphan_cleanup_worker():
    """后台 worker：每小时检查并清理过期孤儿文件"""
    import asyncio
    from app.database import async_session
    from app.models.system_settings import SystemSettings

    while True:
        try:
            await asyncio.sleep(3600)
            async with async_session() as db:
                r = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
                ss = r.scalar_one_or_none()
                retention_days = ss.orphan_retention_days if ss else 7
                await cleanup_orphaned_files(db, retention_days=retention_days)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("孤儿文件清理出错", exc_info=True)


# ============================================================
# 文件引用追踪（用于 O(n) 通知）
# ============================================================

async def track_file_reference(
    db: AsyncSession,
    file_id: int,
    referrer_type: str,
    referrer_id: int,
    ref_type: str = "read",
) -> FileReference:
    db = _ensure_repo(db)
    """记录文件引用（幂等：DB UNIQUE 约束保证唯一，同引用方只保留一条）"""
    result = await db.execute(
        select(FileReference).where(
            FileReference.file_id == file_id,
            FileReference.referrer_type == referrer_type,
            FileReference.referrer_id == referrer_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        # 更新引用类型（如从 read 升级到 write）
        if existing.ref_type != ref_type:
            existing.ref_type = ref_type
            await db.flush()
        return existing

    ref = FileReference(
        file_id=file_id,
        referrer_type=referrer_type,
        referrer_id=referrer_id,
        ref_type=ref_type,
    )
    db.add(ref)
    await db.flush()
    await db.refresh(ref)
    return ref


async def get_file_referrers(db: AsyncSession, file_id: int) -> list[dict]:
    db = _ensure_repo(db)
    """获取文件的所有引用方（用于 O(n) 通知）"""
    result = await db.execute(
        select(FileReference).where(FileReference.file_id == file_id)
    )
    refs = result.scalars().all()
    return [
        {
            "referrer_type": r.referrer_type,
            "referrer_id": r.referrer_id,
            "ref_type": r.ref_type,
        }
        for r in refs
    ]


async def get_ai_referenced_files(db: AsyncSession, ai_id: int) -> list[dict]:
    db = _ensure_repo(db)
    """获取 AI 引用的所有文件"""
    result = await db.execute(
        select(FileReference, FileMetadata).join(
            FileMetadata, FileReference.file_id == FileMetadata.id
        ).where(
            FileReference.referrer_type == "ai",
            FileReference.referrer_id == ai_id,
        )
    )
    rows = result.all()
    return [
        {
            "file_id": ref.file_id,
            "path": meta.path,
            "ref_type": ref.ref_type,
            "size": meta.size,
            "mime_type": meta.mime_type,
        }
        for ref, meta in rows
    ]


# ============================================================
# 协作模式管理
# ============================================================

async def set_collaboration_mode(
    db: AsyncSession,
    file_id: int,
    mode: str,
    requester_type: str,
    requester_id: int,
) -> FileMetadata:
    db = _ensure_repo(db)
    """修改文件的协作模式（仅 owner 可操作）"""
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        raise ValueError("文件不存在")

    if not (metadata.owner_type == requester_type and metadata.owner_id == requester_id):
        raise ValueError("仅文件所有者可修改协作模式")

    if mode not in ("solo", "shared", "open"):
        raise ValueError("无效的协作模式，可选值: solo, shared, open")

    metadata.collaboration_mode = mode
    metadata.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()

    logger.info(f"文件 {file_id} 协作模式已更改为 {mode}")
    return metadata


async def add_file_collaborator(
    db: AsyncSession,
    file_id: int,
    collaborator_type: str,
    collaborator_id: int,
    role: str = "collaborator",
    requester_type: str | None = None,
    requester_id: int | None = None,
) -> FileCollaborator:
    db = _ensure_repo(db)
    """添加文件协作者（仅 owner 可操作）"""
    # 权限检查
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.id == file_id)
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        raise ValueError("文件不存在")

    if requester_type and requester_id:
        if not (metadata.owner_type == requester_type and metadata.owner_id == requester_id):
            raise ValueError("仅文件所有者可管理协作者")

    # 检查是否已存在
    exist_result = await db.execute(
        select(FileCollaborator).where(
            FileCollaborator.file_id == file_id,
            FileCollaborator.collaborator_type == collaborator_type,
            FileCollaborator.collaborator_id == collaborator_id,
        )
    )
    if exist_result.scalar_one_or_none():
        raise ValueError("该协作者已存在")

    collab = FileCollaborator(
        file_id=file_id,
        collaborator_type=collaborator_type,
        collaborator_id=collaborator_id,
        role=role,
    )
    db.add(collab)
    await db.flush()
    await db.refresh(collab)

    # 自动将协作模式设为 shared
    if metadata.collaboration_mode == "solo":
        metadata.collaboration_mode = "shared"
        metadata.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.flush()

    logger.info(f"已添加协作者 {collaborator_type}:{collaborator_id} 到文件 {file_id}")
    return collab


async def remove_file_collaborator(
    db: AsyncSession,
    file_id: int,
    collaborator_type: str,
    collaborator_id: int,
) -> bool:
    db = _ensure_repo(db)
    """移除文件协作者"""
    result = await db.execute(
        select(FileCollaborator).where(
            FileCollaborator.file_id == file_id,
            FileCollaborator.collaborator_type == collaborator_type,
            FileCollaborator.collaborator_id == collaborator_id,
        )
    )
    collab = result.scalar_one_or_none()
    if collab is None:
        raise ValueError("协作者不存在")

    await db.delete(collab)
    await db.flush()
    logger.info(f"已移除协作者 {collaborator_type}:{collaborator_id} 从文件 {file_id}")
    return True


async def get_file_collaborators(db: AsyncSession, file_id: int) -> list[dict]:
    db = _ensure_repo(db)
    """获取文件的所有协作者"""
    result = await db.execute(
        select(FileCollaborator).where(FileCollaborator.file_id == file_id)
    )
    collabs = result.scalars().all()
    return [
        {
            "collaborator_type": c.collaborator_type,
            "collaborator_id": c.collaborator_id,
            "role": c.role,
        }
        for c in collabs
    ]


# ============================================================
# O(n) 通知：文件变更时通知所有引用方
# ============================================================

async def notify_file_changed(db: AsyncSession, file_id: int, change_type: str,
                              changed_by_type: str, changed_by_id: int):
    db = _ensure_repo(db)
    """
    文件变更时通知所有引用方（O(n) 遍历）。
    通过 WebSocket 推送 file_changed 事件到每个在线 AI。
    """
    from app.routers.ws import manager

    referrers = await get_file_referrers(db, file_id)
    metadata = await get_file(db, file_id)

    notified = set()
    for ref in referrers:
        if ref["referrer_type"] == "ai":
            ai_id = ref["referrer_id"]
            if ai_id in notified:
                continue
            notified.add(ai_id)

            # 通过 WebSocket 查找 AI 对应的 user_id 并推送
            from app.models.agent import Agent
            agent_result = await db.execute(
                select(Agent.user_id).where(Agent.id == ai_id)
            )
            agent_user_id = agent_result.scalar_one_or_none()
            if agent_user_id:
                try:
                    await manager.send_to_user(agent_user_id, {
                        "type": "file_changed",
                        "data": {
                            "file_id": file_id,
                            "path": metadata.path if metadata else None,
                            "change_type": change_type,
                            "changed_by_type": changed_by_type,
                            "changed_by_id": changed_by_id,
                        },
                    })
                except Exception as e:
                    logger.warning(f"文件变更通知AI {ai_id} 失败: {e}")

    logger.info(f"文件 {file_id} 变更通知已发送给 {len(notified)} 个引用方")


# ============================================================
# AI 文件操作工具辅助函数
# ============================================================

async def ai_read_file(db: AsyncSession, agent_id: int, file_path: str) -> str:
    db = _ensure_repo(db)
    """AI 读取文件（自动追踪引用）"""
    file_path = ai_stored_path(agent_id, file_path)

    physical_path = _get_physical_path(file_path)
    if not os.path.exists(physical_path):
        raise ValueError(f"文件不存在: {file_path}")

    # 检查权限
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.path == file_path)
    )
    metadata = result.scalar_one_or_none()
    if metadata:
        can_access = await check_file_access(db, metadata.id, "ai", agent_id, "read")
        if not can_access:
            raise ValueError("无权读取：文件不存在或不属于你")
    # 无元数据的文件（旧数据）允许读取

    # 读取文件
    try:
        with open(physical_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # 二进制文件尝试用 latin-1
        with open(physical_path, "r", encoding="latin-1") as f:
            content = f.read()

    # 追踪引用
    if metadata:
        await track_file_reference(db, metadata.id, "ai", agent_id, "read")

    return content


async def ai_write_file(db: AsyncSession, agent_id: int, file_path: str,
                        content: str, collaboration_mode: str = "solo") -> FileMetadata:
    db = _ensure_repo(db)
    """AI 写入文件（创建或覆盖）"""
    file_path = ai_stored_path(agent_id, file_path)

    physical_path = _get_physical_path(file_path)
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)

    content_bytes = content.encode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    with open(physical_path, "wb") as f:
        f.write(content_bytes)

    # 查找或创建元数据
    result = await db.execute(
        select(FileMetadata).where(FileMetadata.path == file_path)
    )
    metadata = result.scalar_one_or_none()

    if metadata:
        # 更新已有文件（覆盖时更新所有权，确保权限一致）
        metadata.owner_type = "ai"
        metadata.owner_id = agent_id
        metadata.size = len(content_bytes)
        metadata.content_hash = content_hash
        metadata.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await track_file_reference(db, metadata.id, "ai", agent_id, "write")
        await db.flush()
        # 通知引用方
        await notify_file_changed(db, metadata.id, "modified", "ai", agent_id)
    else:
        # 创建新文件
        metadata = FileMetadata(
            path=file_path,
            owner_type="ai",
            owner_id=agent_id,
            size=len(content_bytes),
            mime_type="text/plain",
            content_hash=content_hash,
            collaboration_mode=collaboration_mode,
            permissions={
                "owner": f"ai:{agent_id}",
                "rules": [
                    {"role": "owner", "perm": "rwcdm"},
                    {"role": "collaborator", "perm": "rwc"},
                    {"role": "viewer", "perm": "r"},
                ],
            },
        )
        db.add(metadata)
        await db.flush()
        await db.refresh(metadata)
        await track_file_reference(db, metadata.id, "ai", agent_id, "write")

    return metadata


async def ai_list_files(db: AsyncSession, agent_id: int, path: str = "/") -> list[dict]:
    """AI 列出目录（回显 AI 视角的路径，不带 agents/{id}/ 前缀）"""
    files = await list_files(db, ai_stored_path(agent_id, path) + "/", "ai", agent_id)
    for item in files:
        item["path"] = ai_view_path(agent_id, item["path"])
    return files


async def ai_delete_file(db: AsyncSession, agent_id: int, file_path: str) -> bool:
    db = _ensure_repo(db)
    """AI 删除文件"""
    result = await db.execute(
        select(FileMetadata).where(
            FileMetadata.path == ai_stored_path(agent_id, file_path),
            FileMetadata.owner_type == "ai", FileMetadata.owner_id == agent_id,
        )
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        raise ValueError("文件不存在")

    return await delete_file(db, metadata.id, "ai", agent_id)


async def ai_share_file(db: AsyncSession, agent_id: int, file_path: str,
                        target_type: str, target_id: int, role: str = "collaborator") -> dict:
    db = _ensure_repo(db)
    """AI 分享文件给其他 AI 或用户"""
    result = await db.execute(
        select(FileMetadata).where(
            FileMetadata.path == ai_stored_path(agent_id, file_path),
            FileMetadata.owner_type == "ai", FileMetadata.owner_id == agent_id,
        )
    )
    metadata = result.scalar_one_or_none()
    if metadata is None:
        raise ValueError("文件不存在")

    if not (metadata.owner_type == "ai" and metadata.owner_id == agent_id):
        raise ValueError("仅文件所有者可分享")

    # 添加协作者（自动切换协作模式为 shared）
    await add_file_collaborator(db, metadata.id, target_type, target_id, role)

    # 追踪分享引用
    await track_file_reference(db, metadata.id, target_type, target_id, "share")

    return {
        "file_id": metadata.id,
        "path": metadata.path,
        "shared_with": f"{target_type}:{target_id}",
        "role": role,
    }
