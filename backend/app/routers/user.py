"""
用户设置路由
"""
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from app.database import get_db
from app.repositories.user_repo import UserRepository
from app.repositories.api_key_pool_repo import ApiKeyPoolRepository
from app.services.infrastructure.auth_service import update_user_settings
from app.utils.auth import get_current_user
from app.routers.deps import get_user_repo, get_api_key_pool_repo
from app.schemas.auth import UserInfoResponse
from app.models.user import User
from app.utils.text import validate_status_text
from sqlalchemy import select

router = APIRouter(prefix="/user", tags=["用户设置"])


class UpdateSettingsRequest(BaseModel):
    """更新用户设置请求"""
    username: str | None = Field(None, min_length=1, max_length=50)
    password: str | None = Field(None, min_length=6, max_length=100)
    api_base_url: str | None = None
    api_key: str | None = None
    auto_approve_vector_timeout: int | None = None
    auto_approve_vector_default: bool | None = None
    timezone: str | None = None
    language: str | None = None
    ui_prefs: dict | None = None
    prefer_own_key: bool | None = None
    avatar_url: str | None = None
    bio: str | None = None
    status_text: str | None = None
    status_color: str | None = None
    global_chat_model: str | None = None
    global_work_model: str | None = None

    @field_validator("status_text")
    @classmethod
    def check_status_text(cls, v: str | None) -> str | None:
        return validate_status_text(v)


@router.get("/provider-presets")
async def list_provider_presets(
    kind: str = "llm",
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """普通用户可读的厂商选项：平台已配置的供应商 + 内置预设（只读镜像）。

    与 /agents/models 不是一回事：那个是"这个账号**运行期真能走**的通道"
    （有额度走池 Key、配了本人 Key 走那条，去重后常常只剩一条），
    这里是"平台配了哪些厂商"，用来给"选预设 → 自动填地址"当选项。

    不含任何密钥：provider 配置本身不存 key（池 Key 在 api_key_pool，
    本人的 key 由用户自己填）。
    """
    from app.services.agent.provider_presets import get_all_presets
    from app.services.infrastructure.system_settings_service import get_providers

    return {
        "presets": get_all_presets(kind),
        "providers": await get_providers(db),
    }


class RedeemRequest(BaseModel):
    """兑换码请求"""
    code: str = Field(..., min_length=1, max_length=32)


@router.put("/settings", response_model=UserInfoResponse)
async def update_settings(
    req: UpdateSettingsRequest,
    current_user: dict = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    api_key_pool_repo: ApiKeyPoolRepository = Depends(get_api_key_pool_repo),
):
    """更新用户设置（用户名、密码、API Key、策略模式等）"""
    try:
        return await update_user_settings(
            user_id=current_user["user_id"],
            user_repo=user_repo,
            api_key_pool_repo=api_key_pool_repo,
            username=req.username,
            password=req.password,
            api_base_url=req.api_base_url,
            api_key=req.api_key,
            auto_approve_vector_timeout=req.auto_approve_vector_timeout,
            auto_approve_vector_default=req.auto_approve_vector_default,
            timezone=req.timezone,
            language=req.language,
            ui_prefs=req.ui_prefs,
            avatar_url=req.avatar_url,
            bio=req.bio,
            status_text=req.status_text,
            status_color=req.status_color,
            prefer_own_key=req.prefer_own_key,
            global_chat_model=req.global_chat_model,
            global_work_model=req.global_work_model,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/redeem")
async def redeem_code(
    req: RedeemRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """使用兑换码增加额度（一次性/不限人次、每人限一次等规则见 redemption_service）"""
    from app.services.infrastructure.redemption_service import RedemptionError, redeem

    try:
        return await redeem(db, user_id=current_user["user_id"], code=req.code)
    except RedemptionError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


# v0.1.5: 用户额度状态
@router.get("/credit-status")
async def credit_status(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前用户的额度状态。

    返回:
        api_credit: 剩余额度
        estimated_tokens: 估算剩余 Token 数
        monthly_consumed: 本月已消费 credit
        assigned_key_name: 绑定的池 Key 名（或 null）
    """
    from app.services.infrastructure.quota_service import get_user_credit_status
    return await get_user_credit_status(db, current_user["user_id"])


class TestApiBody(BaseModel):
    api_base_url: str | None = None
    api_key: str | None = None


@router.post("/test-api-connection")
async def test_api_connection(
    req: TestApiBody,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """测试 API 连接（服务端代理，避免浏览器 CORS 限制）。

    探测策略 / 文案 / 脱敏全部收敛在 app/services/agent/api_probe.py，
    这里只负责取 key（用户输入 > 库里已存）与转成前端契约。
    """
    from app.services.agent.api_probe import probe_provider

    base_url = req.api_base_url or "https://api.deepseek.com"
    key = req.api_key

    # 没传 key（输入框空着就点测试）→ 用库里已保存的那把
    if not key:
        from app.services.infrastructure.user_credentials import user_api_key
        key = await user_api_key(db, current_user["user_id"])

    if not key:
        raise HTTPException(status_code=400, detail="请先配置 API Key")

    # 私网地址必须"已登记"（用户/平台保存过）才允许请求——否则这就是个内网端口扫描器
    from app.services.agent.base_url_registry import saved_private_hosts
    probe = await probe_provider(
        base_url, key,
        allow_private_hosts=await saved_private_hosts(db, current_user["user_id"]),
    )
    return {"ok": probe.ok, "message": probe.message, "kind": probe.kind}


@router.post("/avatar")
async def upload_user_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传用户头像（统一存到 uploads/avatars/，不计入存储配额）"""
    import os
    import uuid
    from app.config import settings

    # 类型 / 大小 / 真伪一起校验（用户 / AI / 群聊三处头像共用，见 utils/avatar_upload）；
    # 上限取「运行时生效值」，与 /user/config/upload-limits 告诉前端的那份保持一致
    from app.config import get_effective_avatar_max_size_mb
    from app.utils.avatar_upload import read_avatar
    content = await read_avatar(file, get_effective_avatar_max_size_mb())

    # 压缩头像（≤2MB，保持透明通道）
    from app.utils.image_compress import compress_avatar
    content = compress_avatar(content)
    # 自动检测输出格式：JPEG 以 \xFF\xD8 开头，PNG 以 \x89PNG 开头
    ext = "png" if content[:4] == b'\x89PNG' else "jpg"

    # 头像存储目录
    upload_dir = settings.avatars_dir
    os.makedirs(upload_dir, exist_ok=True)

    # 清理旧头像文件
    from sqlalchemy import select
    from app.models.user import User
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one()
    if user.avatar_url:
        old_name = user.avatar_url.rsplit('/', 1)[-1]
        for fname in (old_name, f"thumb_{old_name}"):
            old_path = os.path.join(upload_dir, fname)
            if os.path.isfile(old_path):
                os.remove(old_path)

    # 保存到统一头像目录
    filename = f"user_{current_user['user_id']}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # 生成缩略图
    from app.utils.image_compress import make_avatar_thumbnail
    thumb = make_avatar_thumbnail(content)
    thumb_name = f"thumb_{filename}"
    with open(os.path.join(upload_dir, thumb_name), "wb") as f:
        f.write(thumb)

    avatar_url = f"/api/fs/download-avatar/{filename}"
    user.avatar_url = avatar_url
    await db.flush()

    # 入队联邦 profile 同步
    try:
        from app.services.federation.federation_service import enqueue_profile_update
        await enqueue_profile_update(db, "user", current_user["user_id"], "avatar_url", avatar_url)
    except Exception:
        pass

    return {"avatar_url": avatar_url}


@router.get("/stats")
async def get_user_stats(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户个人统计卡片：AI数、好友数、群聊数、存储用量（高效 COUNT 查询）"""
    from sqlalchemy import func, or_
    from app.models.agent import Agent
    from app.models.friendship import Friendship
    from app.models.group import Group as GroupModel, GroupMember

    uid = current_user["user_id"]

    ai_n = (await db.execute(select(func.count(Agent.id)).where(Agent.owner_id == uid))).scalar() or 0

    friend_n = (await db.execute(
        select(func.count(Friendship.id)).where(Friendship.user_id == uid)
    )).scalar() or 0

    group_n = (await db.execute(
        select(func.count(func.distinct(GroupModel.id))).where(
            or_(
                (GroupModel.owner_type == "human") & (GroupModel.owner_id == uid),
                GroupModel.id.in_(select(GroupMember.group_id).where(GroupMember.member_type == "human", GroupMember.member_id == uid))
            )
        )
    )).scalar() or 0

    # 与 /user/storage 的 total_used 同口径（单一来源）：只算「我的文件」，
    # 即 users/{uid}/files 下按 physical_key 去重后的字节。AI 的产出与对话里的附件不计入。
    from app.services.content.file_service import measure_usage, user_space_dir
    storage_n = (await measure_usage(
        db, "human", [uid], path_prefix=user_space_dir(uid),
    )).get(uid, {}).get("bytes", 0)

    return {"ai_count": ai_n, "friend_count": friend_n, "group_count": group_n, "storage_used": storage_n}


@router.get("/storage")
async def get_user_storage(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """存储概览：按 §3.1 的三块口径分别给出数字（去重后的字节数）

    → 我的文件    = users/{uid}/files（能删，计入 100 MB 上限，平台不自动清）
    → 我在对话里产生的 = users/{uid}/chat（不占上限，按 24 小时 / 梯度回收）
    → AI 的工作文件 = 名下各 AI 自己的空间（属于 AI，单独给数，不混进「我的文件」）

    三块都用 file_service.measure_usage：按 physical_key 去重、只算字节真在盘上的行，
    范围由 path_prefix 圈定。以前这里是逐行累加，把硬链接和历史版本一起算进去，
    数字虚高（§5.7.1）。
    """
    from sqlalchemy import select
    from app.models.agent import Agent
    from app.models.user import User
    from app.services.content.file_service import measure_usage, user_space_dir, user_chat_dir

    uid = current_user["user_id"]
    agents = (await db.execute(select(Agent).where(Agent.owner_id == uid))).scalars().all()
    agent_ids = [a.id for a in agents]

    mine = (await measure_usage(db, "human", [uid], path_prefix=user_space_dir(uid))).get(uid, {})
    produced = (await measure_usage(db, "human", [uid], path_prefix=user_chat_dir(uid))).get(uid, {})
    per_agent_usage = await measure_usage(db, "ai", agent_ids)

    total_used = mine.get("bytes", 0)
    total_files = mine.get("files", 0)

    # AI 自己的账：步 5 的存储页按 §3.1 单列，在此之前它们不进 total_used
    per_agent = [
        {
            "agent_id": a.id,
            "agent_name": a.name,
            "used": per_agent_usage.get(a.id, {}).get("bytes", 0),
            "files": per_agent_usage.get(a.id, {}).get("files", 0),
        }
        for a in agents
    ]

    # 上限值：与 upload_file 的配额闸同源（file_quota_mb），写入时真的会拦
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
    quota_mb = user.file_quota_mb or 100
    quota_bytes = quota_mb * 1024 * 1024

    return {
        "total_used": total_used,
        "total_files": total_files,
        "quota_mb": quota_mb,
        "quota_bytes": quota_bytes,
        "usage_percent": round(total_used / quota_bytes * 100, 1) if quota_bytes > 0 else 0,
        # 「我在对话里产生的」：对话里的附件，只展示，不占上限（§7.1）。
        # 字段名不叫 archived_* ——现在这一栏的判据是目录，不是「有没有归档」。
        "chat_used": produced.get("bytes", 0),
        "chat_files": produced.get("files", 0),
        "per_agent": per_agent,
    }


@router.get("/search")
async def search_users(
    q: str = "",
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """搜索用户（按用户名模糊匹配，用于合作者添加等场景）"""
    if len(q) < 1:
        return {"users": []}
    result = await db.execute(
        select(User).where(
            User.username.ilike(f"%{q}%"),
            User.type == "human",
            User.id != current_user["user_id"],
        ).limit(10)
    )
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "avatar_url": getattr(u, "avatar_url", None),
            }
            for u in users
        ]
    }


@router.get("/config/upload-limits")
async def get_upload_limits():
    """获取当前文件/头像上传大小限制（供前端使用）"""
    from app.config import get_effective_upload_max_size_mb, get_effective_avatar_max_size_mb
    return {
        "upload_max_size_mb": get_effective_upload_max_size_mb(),
        "avatar_max_size_mb": get_effective_avatar_max_size_mb(),
    }


@router.get("/profile/{entity_type}/{entity_id}")
async def get_profile(
    entity_type: str,
    entity_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取用户/AI 的公开资料卡信息"""
    from app.models.user import User
    from app.models.agent import Agent
    from app.models.friendship import Friendship

    if entity_type not in ("human", "ai"):
        raise HTTPException(status_code=400, detail="类型无效，仅支持 human/ai")

    profile: dict = {
        "entity_type": entity_type,
        "entity_id": entity_id,
    }

    if entity_type == "human":
        result = await db.execute(
            select(User).where(User.id == entity_id, User.is_active == True, User.type == "human")
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        profile.update({
            "name": user.username,
            "avatar_url": getattr(user, "avatar_url", None),
            "bio": getattr(user, "bio", None),
            "status_text": getattr(user, "status_text", None),
            "status_color": getattr(user, "status_color", None),
            "created_at": str(user.created_at) if user.created_at else None,
            "last_active_at": getattr(user, "last_active_at", None) and str(user.last_active_at),
            "owner_name": None,
        })

        # 好友检查
        friend_check = await db.execute(
            select(Friendship).where(
                Friendship.user_id == current_user["user_id"],
                Friendship.friend_type == entity_type,
                Friendship.friend_id == entity_id,
            )
        )
        friendship = friend_check.scalar_one_or_none()
        profile["is_friend"] = friendship is not None
        profile["friendship_id"] = friendship.id if friendship else None
        profile["is_priority"] = bool(friendship.is_priority) if friendship else False
        return profile
    else:
        # entity_id 是 User.id（对外统一），映射到 Agent 表
        result = await db.execute(
            select(Agent).where(Agent.user_id == entity_id, Agent.discoverable == True)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="AI 不存在或不可发现")
        # 查制作者
        owner_result = await db.execute(
            select(User.username).where(User.id == agent.owner_id)
        )
        owner_name = owner_result.scalar_one_or_none()
        profile.update({
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "bio": getattr(agent, "bio", None),
            "status_text": getattr(agent, "status_text", None),
            "status_color": getattr(agent, "status_color", None),
            "state": agent.state,
            "created_at": str(agent.created_at) if agent.created_at else None,
            "owner_name": owner_name,
        })

        # 好友检查（entity_id 统一为 User.id）
        friend_check = await db.execute(
            select(Friendship).where(
                Friendship.user_id == current_user["user_id"],
                Friendship.friend_type == entity_type,
                Friendship.friend_id == entity_id,
            )
        )
        friendship = friend_check.scalar_one_or_none()
        profile["is_friend"] = friendship is not None
        profile["friendship_id"] = friendship.id if friendship else None
        profile["is_priority"] = bool(friendship.is_priority) if friendship else False
        return profile
