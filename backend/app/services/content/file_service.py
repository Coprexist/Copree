"""
文件服务层
文件 CRUD、权限检查、协作模式判定、O(n) 引用通知
"""
import os
import re
import shutil
import uuid
import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete as sa_delete, and_, or_, func, text
from app.models.file import FileMetadata, FileReference, FileCollaborator
from app.config import settings
from app.db_providers import get_provider
from app.repositories.content_repo import ContentRepository, SQLAlchemyContentRepository
from app.utils.message_serializer import normalize_attachments
from app.utils.mime import guess_mime, is_generic
from app.utils.pure.paths import unique_sibling
from app.services.content.file_budget import (
    announce_cleanup_failed, announce_quota_exceeded, charge_file_op, charge_file_write,
)

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


def disk_path(relative_path: str) -> str:
    """站内相对路径 → 磁盘物理路径（全仓唯一拼法）

    附件、AI 文件、下载/预览都走它：以前各处在自个儿"data_dir + 相对路径"，
    路径口径一变就得挨个找（2026-11-14 收口）。
    """
    return os.path.join(settings.data_dir, relative_path)


def physical_key_of(relative_path: str) -> str | None:
    """站内路径 → 物理键（硬链接组标识，全仓唯一拼法）

    优先 (st_dev, st_ino)：这是唯一能说明"这两行是同一份字节"的事实——路径和哈希都不能，
    同一份内容被两个人各上传一次是两行两条字节，而 os.link 出来的两份共用一个 inode。
    文件不在磁盘上就返回 None，调用方按"自己一组"处理：宁可少去重，也不编一个假键把
    两份不同的文件认成一份。
    """
    try:
        st = os.stat(disk_path(relative_path))
    except OSError:
        return None
    return f"i:{st.st_dev}:{st.st_ino}"


async def _claim_bytes(db: AsyncSession, metadata: FileMetadata) -> None:
    """把「这条路径现在的字节」认到这一行名下（**唯一入口**，谁刚写完字节谁调）

    为什么必须动别的行：同一个路径会被反复覆盖（upload_file 每次内容变了就新建一行），
    而 `open(path, "wb")` 是原地截断，inode 不变。不清旧行的话，历史上十几个版本会算出
    同一个物理键——2026-11-19 实测：`attachments/image.png` 有 27 行历史版本，全部指向
    同一个 inode。后果是双向的：用量把一份文件算成 27 份，清理时又因为"同组还有活行"而
    一个字节都不释放。

    不变式：`physical_key` 非空 ⟺ 这一行的字节现在确实躺在它的 path 上。
    """
    await db.execute(
        update(FileMetadata)
        .where(FileMetadata.path == metadata.path, FileMetadata.id != metadata.id)
        .values(physical_key=None)
    )
    metadata.physical_key = physical_key_of(metadata.path)
    await db.flush()   # 立刻落库：调用方常紧跟着 refresh()，未 flush 的改动会被它冲掉


# ── 存储空间：路径即归属（唯一入口）──
# 一个文件归谁、算在谁头上，只看它在哪个目录里，不看 archived 之类的状态位：
#   users/{uid}/files  我的文件：用户主动上传、以及从对话里「收进我的文件」的（硬链接）
#   users/{uid}/chat   我在对话里产生的：输入框附件，发给别人之后字节也还在这里
#   agents/{id}/…      AI 自己的文件空间（AI 是独立的占用者，不占主人配额）
# 判据落在路径上有两个好处：删一个人的空间就是删一个目录；验收字节数时 du 一遍就对上。
USER_ROOT = "users"


def user_space_dir(user_id: int) -> str:
    """我的文件（存储页第一栏）：占 100 MB 配额，平台永不自动清理"""
    return f"{USER_ROOT}/{int(user_id)}/files"


def user_chat_dir(user_id: int) -> str:
    """我在对话里产生的（存储页第二栏）：不占配额，按 24 小时 / 梯度回收"""
    return f"{USER_ROOT}/{int(user_id)}/chat"


UNSENT_TTL_HOURS = 24      # 「上传了但没发」的附件留多久（§3.5）


def unsent_deadline(now: datetime | None = None) -> datetime:
    """没发出去的附件什么时候回收（§3.5）。无时区 UTC，与库里其它时间列同一口径。"""
    base = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return base + timedelta(hours=UNSENT_TTL_HOURS)


def is_user_chat_path(path: str) -> bool:
    """是不是「我在对话里产生的」那一栏（`users/{uid}/chat/…`）

    与 ensure_quota 同一个思路：规则挂在路径上，写入口多一个也自动被覆盖。
    """
    parts = str(path or "").split("/")
    return len(parts) >= 4 and parts[0] == USER_ROOT and parts[2] == "chat"


async def measure_usage(
    db: AsyncSession,
    owner_type: str,
    owner_ids: list[int],
    *,
    path_prefix: str | None = None,
) -> dict[int, dict[str, int]]:
    """按物理键去重后的用量（**唯一计量入口**：展示、配额、清理排序都走它）

    返回 {owner_id: {"bytes": int, "files": int}}，没有文件的 owner 不出现。

    去重规则：**在调用方给定的范围里**，同一个 physical_key 只算一次，记给 id 最小的
    那一行。范围由调用方决定——path_prefix 圈出一次统计的目录（配额只算
    users/{uid}/files），不传就是该 owner 的全部文件。

    跨账户的硬链接（AI 把别人的附件 fetch 进自己 inbox）会在两边各算一份，这是刻意的：
    两份都会出现在各自的文件列表里，合成一份会让其中一栏的数字凭空少一块。§5.7 的
    「一份字节只算一次」在本实现里的准确说法是「同一范围内只算一次」。

    只算 physical_key 非空的行，这是「这条路径的字节现在确实躺在盘上」的唯一凭据。
    将来清理删掉字节时会把 physical_key 置 NULL，用量随之归零——不需要额外的
    deleted_at 判断（§14.1 里那条 SQL 的 deleted_at 是本实现之前写的，已按此修正）。

    前缀与去重必须同时作用于"挑代表行"和"汇总"——只筛一边会让被筛掉的那份字节凭空消失。
    """
    if not owner_ids:
        return {}

    def _scope():
        conds = [
            FileMetadata.owner_type == owner_type,
            FileMetadata.owner_id.in_(owner_ids),
            FileMetadata.physical_key.is_not(None),
        ]
        if path_prefix:
            conds.append(FileMetadata.path.like(f"{path_prefix}%"))
        return and_(*conds)

    # 每个 physical_key 挑一行代表（id 最小＝最早登记），再按代表行汇总
    representatives = (
        select(func.min(FileMetadata.id))
        .where(_scope())
        .group_by(FileMetadata.physical_key)
        .scalar_subquery()
    )
    stmt = (
        select(
            FileMetadata.owner_id,
            func.coalesce(func.sum(FileMetadata.size), 0),
            func.count(FileMetadata.id),
        )
        .where(_scope(), FileMetadata.id.in_(representatives))
        .group_by(FileMetadata.owner_id)
    )
    rows = (await db.execute(stmt)).all()
    return {
        row[0]: {"bytes": int(row[1] or 0), "files": int(row[2] or 0)}
        for row in rows
    }


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


def _normalize_collab_type(requester_type: str) -> str:
    """将 requester_type 映射为 FileCollaborator 中存储的类型。

    FileCollaborator.collaborator_type 只允许 'ai' | 'user'，
    但调用方可能传 'human'（历史原因），需要统一映射。
    """
    if requester_type == "human":
        return "user"
    return requester_type


async def _owner_agent(db: AsyncSession, metadata: FileMetadata):
    """AI 文件归谁：owner_id 约定是 agent.id，查不到再按 AI 的虚拟账号兜一次。

    存量里有 15 条记录（2026-07 那批）把 agents.user_id 当 agent.id 写了进去，
    例如 owner_id=16 其实属于 agent 10、41 属于 agent 25，path 也跟着落在 agents/16/。
    scripts/repair_ai_file_owner_ids.py 归正、这类记录清零之后，这里可以只剩 db.get。

    不能写成一个 where(id == X OR user_id == X)：agents.user_id 与别的 agent.id 数值
    撞上会同时命中两行，scalar_one_or_none 直接抛 MultipleResultsFound。
    """
    from app.models.agent import Agent as AgentModel

    agent = await db.get(AgentModel, metadata.owner_id)
    if agent is not None:
        return agent
    result = await db.execute(
        select(AgentModel).where(AgentModel.user_id == metadata.owner_id).limit(1)
    )
    return result.scalars().first()


async def is_file_owner(db: AsyncSession, metadata: FileMetadata,
                        requester_type: str | None, requester_id: int | None) -> bool:
    """这一行是不是**他的**文件（唯一判据）。

    AI 有两层身份：文件的 owner_id 记的是 agents.id，而消息的 sender_id、工具的入参可能是
    虚拟账号 users.id（见 docs/dev/id_semantics.md）。直接比数字会把「AI 发自己的文件」
    认成别人的文件——2026-11-14 真机：file 214 是 ai:120 自己的语音，发送时却记了一条
    forward ai:120，等于给自己刷一次不该有的保护引用。这里两种写法都认。
    """
    if not requester_type or requester_id is None:
        return False
    if metadata.owner_type != requester_type:
        return False
    if int(metadata.owner_id) == int(requester_id):
        return True
    if requester_type != "ai":
        return False
    from app.models.agent import Agent

    virtual = (await db.execute(
        select(Agent.user_id).where(Agent.id == int(metadata.owner_id))
    )).scalar()
    return virtual is not None and int(virtual) == int(requester_id)


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

    # Owner 始终有权限（AI 的两种身份写法都认，见 is_file_owner）
    if await is_file_owner(db, metadata, requester_type, requester_id):
        return metadata

    # AI 的文件 → 其制作者和合作者也有权限
    if metadata.owner_type == "ai" and requester_type == "human":
        from app.models.agent import AgentCollaborator
        agent = await _owner_agent(db, metadata)
        if agent is not None:
            # 制作者
            if agent.owner_id == requester_id:
                return metadata
            # 合作者
            collab_result = await db.execute(
                select(AgentCollaborator).where(
                    AgentCollaborator.agent_id == agent.id,
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
    """检查文件是否被附在请求者可见的消息中（群聊成员 或 DM 参与者）

    AI 的"身份"有两层：入参是 agents.id，而群成员表 / 私信参与方记的是它的虚拟账号
    users.id。这里必须两层都认，否则 AI 读不到"附在它自己群里"的附件——2026-11-14 真机：
    QQ 群里的图 AI 能看到，用 file_fetch 收就报"不是你可见的附件"，就是漏了这一步换算。
    """
    member_ids: list[tuple[str, int]] = [(requester_type, int(requester_id))]
    if requester_type == "ai":
        from app.models.agent import Agent

        virtual_user_id = (await db.execute(
            select(Agent.user_id).where(Agent.id == int(requester_id))
        )).scalar()
        if virtual_user_id:
            # 群里 AI 成员记的是 ('ai', users.id)；私信只看 id，不看类型，所以这一对两边都够用
            member_ids.append(("ai", int(virtual_user_id)))
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
    for req_type, req_id in member_ids:
        r = await db.execute(check_group, {
            **attachment_params,
            "req_type": req_type,
            "req_id": req_id,
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
    for _req_type, req_id in member_ids:
        r = await db.execute(check_dm, {
            "like_pattern": f"%\"file_id\": {file_id}%",
            "req_id": req_id,
        })
        if r.scalar():
            return True

    return False


async def track_attachments(db: AsyncSession, attachments, owner_type: str, owner_id: int) -> None:
    """一条消息的附件引用记账（唯一入口）：群消息与私信共用一份

    附件快照只是"这条消息引了哪把文件"；真正的"谁读过/转发过它"记在 file_references。
    以前 gm.py 与 dm.py 各抄一遍这段循环——两处一旦漂移，配额与去重就算到不同的人头上。

    这里同时给文件盖两个时间戳（同一件事的两面，所以一条 UPDATE 一起写）：
    - archived_to_message_at：非空＝已被消息带走，不再算发送者的文件（§3.2）；
    - last_referenced_at：清理梯度按它排新旧，转发一次就重新变"新"（§5.2）。
    用 func.now() 而不是 Python 侧取当前时间：它就是本事务的起点，与消息行的
    created_at（server_default now()）是同一个值，不会出现"消息比附件还早"的错位。
    """
    # 私信的 attachments 是 Text 列（JSON 字符串），群里是 JSONB（list）。两个入口都走这里，
    # 所以"字符串还是列表"在这一行收口——漏了就等于那条消息的附件没人记账。
    attachments = normalize_attachments(attachments)
    if not attachments:
        return
    ids = []
    for att in attachments:
        fid = att.get("file_id") if isinstance(att, dict) else getattr(att, "file_id", None)
        if fid:
            ids.append(int(fid))
    if not ids:
        return
    # 每一条带附件的消息算一次文件操作（发送与转发都走这里）。放在记账 UPDATE 之前：
    # 超限就抛，消息行与附件引用一起回滚，不会留下「消息发了、附件没记账」的半条
    await charge_file_op(owner_type, owner_id)
    await db.execute(
        update(FileMetadata)
        .where(FileMetadata.id.in_(ids))
        .values(
            last_referenced_at=func.now(),
            # 一开始归档时间就定了：它记的是"第一次被带走"的那刻，重发不刷新
            archived_to_message_at=func.coalesce(FileMetadata.archived_to_message_at, func.now()),
            # 进了消息就不归「24 小时没发就回收」管了，清理梯度接手（§3.5）
            reclaim_after=None,
        )
    )
    for fid in ids:
        await track_forward_reference(db, fid, owner_type, owner_id)


async def set_transcript(db: AsyncSession, file_id: int, text: str | None) -> None:
    """写一份音频的文稿（**唯一写入口**）。

    两个调用方：AI 语音合成完（text 现成）、人的语音转写完（ASR）。文稿是**文件**的属性，
    不写进消息的 attachments 快照——消息侧只留 file_id，见迁移 `0113` 的说明。
    空串视为"没有文稿"（存 NULL），免得 "有键但空" 和 "没转写" 两种状态混在一起。
    """
    row = await db.get(FileMetadata, int(file_id))
    if row is None:
        return
    body = (text or "").strip()
    row.transcript = body or None
    await db.flush()


async def transcripts_for(db: AsyncSession, file_ids) -> dict[int, str]:
    """一批文件 id → 文稿（只回有文稿的那些）。**不判权限**，与 ai_file 同一分工：
    调用方给的是它已经看得到的消息里的 file_id。"""
    ids = {int(i) for i in (file_ids or []) if i}
    if not ids:
        return {}
    rows = (await db.execute(
        select(FileMetadata.id, FileMetadata.transcript).where(FileMetadata.id.in_(ids))
    )).all()
    return {int(fid): body for fid, body in rows if body}


async def ai_file(db: AsyncSession, agent_id: int, file_path: str) -> FileMetadata | None:
    """AI 视角路径 → 它自己文件空间里的那行元数据（唯一查法：前缀只在这里拼一次）

    只认 `agents/{id}/…` 这个前缀（见 ai_stored_path）。**不判权限**：分工是
    "路径到行"归这里，"这行能不能读"归 check_file_access——读共享文件的人也要能找到行。
    """
    stored = ai_stored_path(agent_id, file_path)
    return (await db.execute(
        select(FileMetadata).where(FileMetadata.path == stored)
    )).scalar_one_or_none()


# ============================================================
# 文件 CRUD
# ============================================================

class StorageQuotaExceeded(ValueError):
    """「我的文件」装不下这次写入（§6.1）"""

    def __init__(self, used: int, quota: int, incoming: int):
        self.used, self.quota, self.incoming = used, quota, incoming
        super().__init__(
            f"存储空间不足：已用 {used / 1048576:.1f} MB / 上限 {quota / 1048576:.0f} MB，"
            f"这次要写 {incoming / 1048576:.1f} MB。删掉一些文件再传。"
        )


async def ensure_quota(
    db: AsyncSession,
    owner_type: str,
    owner_id: int,
    relative_path: str,
    incoming: int,
    replacing: int = 0,
) -> None:
    """写入「我的文件」前的硬闸（§6.1）：装不下就当场拒绝

    为什么是拒绝而不是"删点旧的腾地方"：我的文件承诺「只有你能删」（§3.1）。承诺一旦
    破了，用户就没法规划自己的空间；拒绝一次上传的代价，远小于删掉一个已存的文件。

    为什么拦在 upload_file 里而不是每个路由各拦一次：判据是**目标路径**——写进某个人的
    空间就查那个人的配额。以后多一个入口（从对话里「收进我的文件」）也自动被这层覆盖。
    写别处（对话附件、AI 自己的空间）不查：那些不占个人配额。
    """
    if owner_type != "human":
        return
    space = user_space_dir(owner_id) + "/"
    if not relative_path.startswith(space):
        return

    from app.models.user import User

    user = await db.get(User, int(owner_id))
    quota = int((user.file_quota_mb if user else None) or 100) * 1024 * 1024
    used = (await measure_usage(
        db, "human", [int(owner_id)], path_prefix=user_space_dir(owner_id),
    )).get(int(owner_id), {}).get("bytes", 0)
    # 同名覆盖是替换不是新增：老字节要减掉，否则 90 MB 的文件换个 95 MB 的内容会被误拦
    after = used - max(0, int(replacing)) + int(incoming)
    if after > quota:
        await announce_quota_exceeded(owner_type, owner_id, used=used, quota=quota, incoming=incoming)
        raise StorageQuotaExceeded(used, quota, int(incoming))


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

    # 每分钟操作次数（§6.2）：上传算一次。放在最前面——被限流的那次连去重都不该做
    await charge_file_op(owner_type, owner_id)

    # 先剥掉首尾斜杠再拼：os.path.join("/", "a.png") 得到的是**绝对路径** "/a.png"，
    # disk_path 会原样返回它（join 遇到绝对路径就丢掉前缀），于是字节被写进容器根目录——
    # 不进 data 卷、重启即丢，还绕过了用量统计。调用方传 "/" 表示"data 根"，不是真的根。
    relative_path = os.path.join(path.strip("/"), filename or f"unnamed_{uuid.uuid4().hex[:8]}")

    # 音频顺带探一次时长（不可变属性：文件内容定了就不会变，存一次即可）。
    # 探测失败留 None——预览退回「[语音]」，不因为量不出时长就挡住上传
    duration_ms = None
    if (mime_type or "").startswith("audio/"):
        from app.utils.audio_probe import audio_duration_ms

        duration_ms = audio_duration_ms(content, mime_type)

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
            # 老行可能是加这一列之前落的：同一个文件被复用时顺手补上，不另跑迁移
            if existing.duration_ms is None and duration_ms:
                existing.duration_ms = duration_ms
                await db.flush()
            # 再传一次是「还想要它」的信号：起新的一轮 24 小时，别让它下一分钟就被回收
            if is_user_chat_path(relative_path):
                existing.reclaim_after = unsent_deadline()
                await db.flush()
            logger.info(f"文件去重复用: {relative_path} (SHA256={content_hash[:8]}…)")
            return existing

    # ── 配额闸：放在去重之后 ──
    # 同一个文件再传一次不写字节（上面已经 return），不该因为"空间不够"被拦；
    # 真写新字节的才查。replacing 只在老行确实占着字节时才算（墓碑没占地方）
    replacing = existing.size if (existing is not None and existing.physical_key) else 0
    await ensure_quota(db, owner_type, owner_id, relative_path, len(content), replacing)
    # 日额度闸（§6.2）：去重命中在上面已经 return（没写字节、不占额度），到这里就是要真写。
    # 同路径覆盖写不算「新增文件」——inode 不变，physical_key 也不变（§5.7）
    await charge_file_write(owner_type, owner_id, len(content), new_file=not replacing)

    # ── 非重复 → 写入物理文件 ──
    content_hash = hashlib.sha256(content).hexdigest()
    physical_path = disk_path(relative_path)
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
        duration_ms=duration_ms,
        content_hash=content_hash,
        collaboration_mode=collaboration_mode,
        # 只有「我在对话里产生的」这一栏才有回收时刻；发出时 track_attachments 会清掉它
        reclaim_after=unsent_deadline() if is_user_chat_path(relative_path) else None,
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
    await _claim_bytes(db, metadata)   # 字节刚写完，这条路径的字节归这一行

    logger.info(f"文件上传完成: {relative_path} ({len(content)} bytes) by {owner_type}:{owner_id}")
    return metadata


async def list_files(
    db: AsyncSession,
    path: str,
    requester_type: str,
    requester_id: int,
) -> list[dict]:
    """列出目录内容（过滤无权条目）

    只查询请求者自身或有权限的文件，不加载全表。

    `path` 是站内目录前缀，不是"任意目录"：三栏的口径就是目录（user_space_dir /
    user_chat_dir），AI 的 file_list 传它自己的 agents/{id}/。所以这里没有"只列未归档"
    之类的开关——一个文件在哪一栏，由它躺在哪个目录里决定。

    只列 `physical_key` 非空的行：这一列非空 ⟺ 字节确实躺在它的 path 上（§5.7.1）。
    为空的是覆盖写留下的历史版本或已缺失的路径，它们点开会 404，不该出现在文件列表里。
    与用量口径一致——否则列表条数和 total_files 会对不上。
    """
    db = _ensure_repo(db)
    if not _check_path_safe(path):
        raise ValueError("路径不合法：仅支持相对路径")

    # 只查询属于请求者的文件（owner 匹配），避免加载全表
    query = select(FileMetadata).where(
        FileMetadata.owner_type == requester_type,
        FileMetadata.owner_id == requester_id,
        FileMetadata.physical_key.is_not(None),
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
                # 语音在列表里要显示成「语音行」而不是一个 webm 文件名：
                # 文稿与时长本来就跟着这条元数据走，列表顺手带上比前端再取一次省事
                "transcript": f.transcript,
                "duration_ms": f.duration_ms,
                "collaboration_mode": f.collaboration_mode,
                "created_at": str(f.created_at) if f.created_at else None,
                "updated_at": str(f.updated_at) if f.updated_at else None,
                # 非空＝这一条到点会被回收（§3.5）；界面按它显示「X 小时后清除」
                "reclaim_after": str(f.reclaim_after) if f.reclaim_after else None,
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

    physical_path = disk_path(metadata.path)
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
    if not await is_file_owner(db, metadata, requester_type, requester_id):
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
    physical_path = disk_path(metadata.path)
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
    """把"这个人是转发方"记进引用表（非 owner 发送含附件的消息时调用）

    表的形状是设计文档 §5.3 定的：**同一引用方对同一文件只留一条记录，重复引用只更新
    ref_type**。所以这里不能自己 INSERT：一个人只要先看过/下载过这个文件（会落一行
    read），再转发就撞 (file_id, referrer_type, referrer_id) 的唯一键，整个发送事务
    跟着回滚——界面报"已发送"，对方聊天里却没有这条消息（2026-11-09 线上 file 183）。
    返回 True 表示他现在算转发方（占他的配额），False 表示文件是他自己的。
    """
    # Owner 自己不发转发引用
    meta = await db.get(FileMetadata, file_id)
    if not meta:
        return False
    if await is_file_owner(db, meta, referrer_type, referrer_id):
        return False

    ref = await track_file_reference(db, file_id, referrer_type, referrer_id, "forward")
    if ref.ref_type == "forward":
        logger.info(f"转发引用: file={file_id} → {referrer_type}:{referrer_id}")
    return ref.ref_type == "forward"


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
            physical_path = disk_path(meta.path)
            if os.path.exists(physical_path):
                os.remove(physical_path)
            await db.delete(meta)
            deleted += 1

    if deleted:
        await db.flush()
        logger.info(f"已清理 {deleted} 个过期孤儿文件（宽限期 {retention_days} 天）")
    return deleted


async def list_viewed_files(db: AsyncSession, user_id: int) -> list[dict]:
    """我看过或转发过的**别人的**文件（存储页的「浏览过的」）

    与另两栏不同，这里**不按 physical_key 过滤**：文件被清理后条目仍在、只是打不开（§7.1
    「文件本身属于发送者，被清理后条目还在但打不开」）。所以带上 is_available，让界面把
    已清理的条目灰掉——静默消失会让用户以为自己没看过。

    自己的文件不算「浏览」：AI 抓取的引用记在 ai 账上，天然不在这里。
    """
    rows = (await db.execute(
        select(FileMetadata, FileReference.ref_type)
        .join(FileReference, FileReference.file_id == FileMetadata.id)
        .where(
            FileReference.referrer_type == "human",
            FileReference.referrer_id == user_id,
            FileReference.ref_type.in_(("read", "forward")),
            or_(FileMetadata.owner_type != "human", FileMetadata.owner_id != user_id),
        )
        .order_by(FileReference.created_at.desc())
    )).all()
    return [
        {
            "id": meta.id,
            "path": meta.path,
            "size": meta.size,
            "mime_type": meta.mime_type,
            "owner_type": meta.owner_type,
            "owner_id": meta.owner_id,
            "ref_type": ref_type,
            # 字节还在盘上就能打开，不在就显示「已清理」
            "is_available": meta.physical_key is not None,
        }
        for meta, ref_type in rows
    ]


async def reclaim_unsent_attachments(db: AsyncSession, *, now: datetime | None = None) -> int:
    """「上传了但一直没发」的附件：连字节带行一起删（§3.5）

    判据是 reclaim_after，不是 created_at：上传时按 24 小时算、发出时清空，存量迁移时
    还额外给过一次宽限——三者对不齐，所以用一个字段当唯一时钟。

    没有墓碑：它从没进过任何消息，墓碑（§4）是留给已归档附件的。

    删字节不查「同组还有没有活行」：同一个 physical_key 就是同一个 inode，os.remove 只
    摘掉这一个目录项，别的行（AI 抓进 inbox 的那份）指向的字节还在。反过来若这里去改
    「删整组」，就会把别人还在用的文件一起清掉。
    """
    cutoff = now or datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (await db.execute(
        select(FileMetadata).where(
            FileMetadata.reclaim_after.is_not(None),
            FileMetadata.reclaim_after <= cutoff,
        )
    )).scalars().all()

    removed = 0
    for meta in rows:
        if meta.physical_key is not None:
            try:
                os.remove(disk_path(meta.path))
            except FileNotFoundError:
                pass
            except OSError as e:
                # 删不掉就留到下一轮：行和字节必须一起走，别留下指着空路径的活行
                logger.warning(f"回收附件失败，留待下一轮: {meta.path}: {e}")
                continue
        await db.delete(meta)
        removed += 1
    if removed:
        await db.flush()
        logger.info(f"回收未发送附件 {removed} 个（截止 {cutoff}）")
    return removed


async def file_cleanup_worker():
    """后台 worker：每分钟扫一次「到点该删的」，孤儿清理每小时一次

    两种频率差在哪：撤回的附件定的是「一分钟后排删」（§5.6），一小时才扫一次就等于延迟
    一小时；而孤儿清理要遍历目录、算宽限期，没必要每分钟跑。用同一个循环按分钟计数，
    还是只有一个定时器——多一个定时器就是多一处可能忘记重启的地方。
    """
    import asyncio
    from app.database import async_session
    from app.models.system_settings import SystemSettings

    ticks_until_orphans = 60        # 启动先做回收；孤儿清理一小时后再来
    while True:
        try:
            await asyncio.sleep(60)
            async with async_session() as db:
                await reclaim_unsent_attachments(db)
                await db.commit()
            ticks_until_orphans -= 1
            if ticks_until_orphans > 0:
                continue
            ticks_until_orphans = 60
            async with async_session() as db:
                r = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
                ss = r.scalar_one_or_none()
                retention_days = ss.orphan_retention_days if ss else 7
                await cleanup_orphaned_files(db, retention_days=retention_days)
                await db.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("文件清理出错", exc_info=True)
            # 清理挂了盘就会一直长：这条必须让管理员看到（同一天只报一次，别刷屏）
            await announce_cleanup_failed(e)


# ============================================================
# 文件引用追踪（用于 O(n) 通知）
# ============================================================

# 引用强弱：只升不降。一行记录只能装一个 ref_type，而 forward 决定"这份文件归谁保护"——
# 被转发过就意味着有人还要它，所以它不进清理候选（§5.4 L0）；read/write 只是使用痕迹，随时会失效。
# 若让一次预览把 forward 降成 read，等于把一份有人要的文件悄悄放回清理队列。
_REF_RANK = {"read": 0, "import": 1, "write": 2, "share": 2, "forward": 3}


async def track_file_reference(
    db: AsyncSession,
    file_id: int,
    referrer_type: str,
    referrer_id: int,
    ref_type: str = "read",
) -> FileReference:
    db = _ensure_repo(db)
    """记录文件引用（幂等：DB UNIQUE 约束保证唯一，同引用方只保留一条，ref_type 只升不降）"""
    result = await db.execute(
        select(FileReference).where(
            FileReference.file_id == file_id,
            FileReference.referrer_type == referrer_type,
            FileReference.referrer_id == referrer_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        # 更新引用类型（如从 read 升级到 write）；反向降级不生效，见 _REF_RANK
        if _REF_RANK.get(ref_type, 0) > _REF_RANK.get(existing.ref_type, 0):
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

    if not await is_file_owner(db, metadata, requester_type, requester_id):
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
        if not await is_file_owner(db, metadata, requester_type, requester_id):
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

    physical_path = disk_path(file_path)
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


async def _adopt_into(
    db: AsyncSession,
    src: FileMetadata,
    *,
    target_rel: str,
    owner_type: str,
    owner_id: int,
) -> dict:
    """把 src 的字节「收」到 target_rel 名下：硬链接一份 + 新元数据行（唯一入口）

    ai_fetch_attachment（收进 AI 自己的空间）与 keep_to_my_files（收进我的文件）除了目标
    目录之外是同一个动作，所以实现只留这一份：撞名让后缀、配额硬闸、文稿/时长跟着内容走
    一份快照、硬链接失败时如实退回复制，都在这里。调用方各自只管两件事——「这个文件你有
    权收吗」和收完记哪笔引用。

    硬链接不占第二份磁盘；跨卷或文件系统不支持时复制（`linked=False`，不假装）。
    目标路径上已经有一行可用时直接复用它（幂等）：同一个按钮点两次不该多出一份配额。
    """
    db = _ensure_repo(db)
    src_physical = disk_path(src.path)
    existing = (await db.execute(
        select(FileMetadata).where(FileMetadata.path == target_rel)
    )).scalar_one_or_none()
    # physical_key 非空 ⟺ 这一行的字节确实躺在它的 path 上（见 _claim_bytes），所以复用它
    # 不用再碰盘
    if existing is not None and existing.physical_key is not None:
        return _adopt_receipt(existing, target_rel, already=True)

    if not os.path.isfile(src_physical):
        raise ValueError("原文件已经不在磁盘上了，收不了")

    if existing is not None and int(existing.id) == int(src.id):
        # 目标就是它自己（「我的文件」里的文件又点了一次）：行在、字节也在，认下来即可
        await _claim_bytes(db, existing)
        return _adopt_receipt(existing, target_rel, already=True)

    # 同名但字节不同，或那行是字节已丢的墓碑：让一个后缀，绝不覆盖盘上已有的东西
    if existing is not None or os.path.exists(disk_path(target_rel)):
        target_rel = unique_sibling(target_rel, int(src.id))

    # 判据是目标路径：写进 users/{uid}/files/ 才查配额，AI 空间与对话目录不查（§6.1）
    await ensure_quota(db, owner_type, int(owner_id), target_rel, int(src.size or 0))

    dst_physical = disk_path(target_rel)
    os.makedirs(os.path.dirname(dst_physical), exist_ok=True)
    linked = True
    try:
        os.link(src_physical, dst_physical)          # 同一份 inode：不占第二份磁盘
    except OSError:
        shutil.copyfile(src_physical, dst_physical)  # 跨卷/不支持硬链接：如实退回复制
        linked = False

    owner = f"{owner_type}:{int(owner_id)}"
    metadata = FileMetadata(
        path=target_rel,
        owner_type=owner_type,
        owner_id=int(owner_id),
        size=src.size,
        mime_type=src.mime_type,
        content_hash=src.content_hash,
        # 文稿与时长是这份内容自己的属性，跟着字节走一份快照：不带的话，收进来的语音／视频
        # 在目标空间里点开就没有文字。原件仍是一份，这边只是当时的拷贝。
        transcript=src.transcript,
        duration_ms=src.duration_ms,
        collaboration_mode="solo",
        reclaim_after=None,      # 进了目标空间就没有回收时刻（§3.1：我的文件平台不自动删）
        permissions={
            "owner": owner,
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
    # 硬链接与复制在物理键上不用分情况：硬链接对着同一个 inode，stat 出来自然相等；退回复制
    # 时那是新的一片字节，stat 出来自然不同。抄源行的键反而会在复制时出错
    await _claim_bytes(db, metadata)
    return _adopt_receipt(metadata, target_rel, already=False, linked=linked)


def _adopt_receipt(metadata: FileMetadata, path: str, *, already: bool,
                   linked: bool | None = None) -> dict:
    """收文件的统一回执：两个入口与工具层都按这一种形状读"""
    return {
        "file_id": int(metadata.id),
        "path": path,
        "name": os.path.basename(path),
        "size": int(metadata.size or 0),
        "mime_type": metadata.mime_type,
        "already": already,
        # already=True 时这次没建链接：报 None，别让调用方以为刚链了一份
        "linked": linked,
    }


async def ai_fetch_attachment(db: AsyncSession, agent_id: int, file_id: int) -> dict:
    """把"附在我可见消息上的附件"收进我的文件空间（硬链接，不复制字节）

    为什么要有这个动作：AI 在上下文里"看得见"图（多模态注入是按路径直接读字节，不看归属），
    但那份文件属于**发图的人**，而 AI 的文件工具只在自己的空间里工作——看到 ≠ 拿得到。
    想要留下/转发/二次处理，就显式收一次：把同一份物理文件硬链接到 agents/{id}/inbox/，
    元数据登记给 AI 自己。动作本身见 _adopt_into。
    """
    db = _ensure_repo(db)
    src = await check_file_access(db, file_id, "ai", agent_id, "read")
    if src is None:
        raise ValueError("收不了这个文件：它不是你可见消息里的附件")

    name = os.path.basename(str(src.path or "")) or f"file-{int(file_id)}"
    result = await _adopt_into(
        db, src,
        target_rel=ai_stored_path(int(agent_id), f"inbox/{name}"),
        owner_type="ai", owner_id=int(agent_id),
    )
    # AI 工具与回显里的路径都是相对自己根目录的（inbox/x.png），存储路径只在这一层用
    result["path"] = ai_view_path(int(agent_id), str(result["path"]))
    if result["already"]:
        result["note"] = "已经在你的文件空间里了"
        return result
    # 记一笔引用：谁读过、谁转发过，配额与去重都靠它（原件仍归发送者）
    await track_forward_reference(db, int(file_id), "ai", agent_id)
    how = "硬链接" if result["linked"] else "复制"
    logger.info(f"AI {agent_id} 收了附件 #{int(file_id)} → {result['path']}（{how}）")
    return result


# 收藏时可以带一个「显示名」（语音就是「谁发的 + 说了什么的前几个字」）。
# 名字只影响目录项，不影响字节与权限；但仍得是能用的文件名：路径分隔符、控制字符
# 一律剔掉，长度截断，扩展名永远跟随源文件（名字里没写就不写，绝不丢类型）。
_ALIAS_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ALIAS_MAX_CHARS = 48


def collect_alias(raw: str | None, src_name: str) -> str:
    """把「收藏时给的名字」规范成一个安全文件名：扩展名跟随源文件。"""
    stem, ext = os.path.splitext(src_name or "")
    alias = _ALIAS_BAD_CHARS.sub("", str(raw or ""))
    alias = " ".join(alias.split()).strip(" .")
    if ext and alias.lower().endswith(ext.lower()):
        alias = alias[: -len(ext)]
    alias = alias[:_ALIAS_MAX_CHARS].strip(" .")
    return f"{alias or stem or 'file'}{ext}"


async def keep_to_my_files(
    db: AsyncSession, user_id: int, file_id: int, name: str | None = None,
) -> dict:
    """把一份文件「收进我的文件」（第一栏的唯一入口，§7.3）

    做的是硬链接：`users/{uid}/files/` 多一个目录项、`file_metadata` 多一行归我；原件的
    行与字节都不动——对话里那份照样能被清理梯度收走，字节因为还有活链接而留下（§5.7）。
    收进来之后就占我的 100 MB、平台不再自动删（§3.1）。动作本身见 _adopt_into。

    name 是可选的显示名（语音收藏用「谁发的 + 前几个字」当标题）。同一份内容
    （content_hash 相同）已经在我的空间里时直接认那一条——标题不该让同一份字节
    在配额里变成两份。
    """
    db = _ensure_repo(db)
    uid = int(user_id)
    src = await check_file_access(db, int(file_id), "human", uid, "read")
    if src is None:
        raise LookupError("这个文件你现在看不到")

    if src.content_hash:
        existing = (await db.execute(
            select(FileMetadata).where(
                FileMetadata.owner_type == "human",
                FileMetadata.owner_id == uid,
                FileMetadata.content_hash == src.content_hash,
                FileMetadata.path.like(f"{user_space_dir(uid)}/%"),
                FileMetadata.physical_key.is_not(None),
            ).limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            return _adopt_receipt(existing, str(existing.path), already=True)

    src_name = os.path.basename(str(src.path or "")) or f"file-{int(file_id)}"
    name = collect_alias(name, src_name) if name else src_name
    result = await _adopt_into(
        db, src,
        target_rel=f"{user_space_dir(uid)}/{name}",
        owner_type="human", owner_id=uid,
    )
    if not result["already"]:
        how = "硬链接" if result["linked"] else "复制"
        logger.info(f"用户 {uid} 把 #{int(file_id)} 收进我的文件 → {result['path']}（{how}）")
    return result


async def unkeep_from_my_files(
    db: AsyncSession, user_id: int, file_id: int, name: str | None = None,
) -> dict:
    """把「收进我的文件」的那一份移出（只删我的目录项，原消息里的那份完好）

    带 name（收藏时用的标题）时按**目标路径**精确定位：同名同内容才算我收藏的那一条，
    不碰我空间里别的同字节文件。没带 name 的老入口退回 content_hash 判据。
    硬链接删除只减一个目录项，原件的行与字节都还在（§5.7）。
    源文件本来就在我的空间里时不删——那是原文件，不是收藏品。
    """
    db = _ensure_repo(db)
    uid = int(user_id)
    src = await check_file_access(db, int(file_id), "human", uid, "read")
    if src is None:
        raise LookupError("这个文件你现在看不到")
    src_name = os.path.basename(str(src.path or "")) or f"file-{int(file_id)}"
    removed: list[int] = []
    if name:
        exact = f"{user_space_dir(uid)}/{collect_alias(name, src_name)}"
        row = (await db.execute(
            select(FileMetadata).where(
                FileMetadata.owner_type == "human",
                FileMetadata.owner_id == uid,
                FileMetadata.path == exact,
            )
        )).scalar_one_or_none()
        # 同名但内容已被换掉时不删：那已经不是这条消息收藏的那一份
        if row is not None and int(row.id) != int(src.id) and (
            not src.content_hash or row.content_hash == src.content_hash
        ):
            await delete_file(db, int(row.id), "human", uid)
            removed.append(int(row.id))
        return {"removed": removed, "count": len(removed)}
    if src.content_hash:
        rows = (await db.execute(
            select(FileMetadata).where(
                FileMetadata.owner_type == "human",
                FileMetadata.owner_id == uid,
                FileMetadata.content_hash == src.content_hash,
                FileMetadata.path.like(f"{user_space_dir(uid)}/%"),
                FileMetadata.physical_key.is_not(None),
            )
        )).scalars().all()
        for row in rows:
            if int(row.id) == int(src.id):
                continue
            await delete_file(db, int(row.id), "human", uid)
            removed.append(int(row.id))
    return {"removed": removed, "count": len(removed)}


async def ai_write_file(db: AsyncSession, agent_id: int, file_path: str,
                        content: str, collaboration_mode: str = "solo") -> FileMetadata:
    db = _ensure_repo(db)
    """AI 写入文件（创建或覆盖）"""
    # agent_id 必须是 agents.id（文件空间 id 的单一口径）。传虚拟账号 users.id 时，同名 id
    # 在 agents 上也存在，文件会落进另一个 AI 的目录、原 AI 自己读不到——存量 data/agents/16/
    # 就是 2026-07 那批这么写出来的（见 docs/dev/id_semantics.md）
    from app.models.agent import Agent as AgentModel
    if await db.get(AgentModel, agent_id) is None:
        virtual = await db.execute(
            select(AgentModel.id).where(AgentModel.user_id == agent_id).limit(1)
        )
        if virtual.first() is not None:
            raise ValueError(f"未知 AI：{agent_id}（agent_id 须是 agents.id，不是虚拟账号 users.id）")
    file_path = ai_stored_path(agent_id, file_path)

    physical_path = disk_path(file_path)
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
        # 覆盖写入顺便纠正类型：存量行都是 text/plain，不改的话 svg/png 一直是文件卡片
        if is_generic(metadata.mime_type):
            metadata.mime_type = guess_mime(file_path)
        metadata.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await _claim_bytes(db, metadata)   # 原地截断覆盖：这条路径的字节现在归这一行
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
            # 按扩展名登记：写死的 text/plain 会让 svg/png 在聊天里渲染成文件卡片
            mime_type=guess_mime(file_path),
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
        await _claim_bytes(db, metadata)   # 新建的行，字节就是刚写的这份
        await db.refresh(metadata)
        await track_file_reference(db, metadata.id, "ai", agent_id, "write")

    # 知识库：正文就是文件，写完顺手让索引行跟上——这是「AI 编辑自己的知识库」的唯一入口
    try:
        from app.services.agent import knowledge_service as _kb
        await _kb.sync_on_write(db, agent_id, file_path, content)
    except Exception as e:  # noqa: BLE001 —— 索引没跟上不该让文件写入失败
        logger.warning("知识库索引同步失败 %s: %s", file_path, e)

    return metadata


async def ai_move_file(db: AsyncSession, agent_id: int, old_path: str, new_path: str) -> None:
    db = _ensure_repo(db)
    """AI 文件换位置 / 改名（同一文件空间内）。

    files 表的登记行是文件列表与存储占用的唯一来源：只在磁盘上 rename，
    文件会从 file_list 与存储页里"消失"，所以文件与登记行必须一起挪。
    """
    old_stored = ai_stored_path(agent_id, old_path)
    new_stored = ai_stored_path(agent_id, new_path)
    old_physical = disk_path(old_stored)
    new_physical = disk_path(new_stored)
    os.makedirs(os.path.dirname(new_physical), exist_ok=True)
    if os.path.exists(old_physical):
        os.rename(old_physical, new_physical)

    result = await db.execute(select(FileMetadata).where(FileMetadata.path == old_stored))
    metadata = result.scalar_one_or_none()
    if metadata is not None:
        metadata.path = new_stored
        await db.flush()
        await notify_file_changed(db, metadata.id, "modified", "ai", agent_id)

    try:
        from app.services.agent import knowledge_service as _kb
        await _kb.sync_on_move(db, agent_id, old_stored, new_stored)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库路径同步失败 %s: %s", new_stored, e)


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

    stored = metadata.path
    try:
        from app.services.agent import knowledge_service as _kb
        await _kb.sync_on_delete(db, agent_id, stored)
    except Exception as e:  # noqa: BLE001
        logger.warning("知识库索引删除失败 %s: %s", stored, e)

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
