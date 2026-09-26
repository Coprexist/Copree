"""
AI 代理管理路由
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import Response
import json
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.agent import (
    AgentCreateRequest,
    AgentGenerateRequest,
    AgentGenerateResponse,
    AgentUpdateConfigRequest,
    AgentStateRequest,
    AgentResponse,
    AgentConfigHistoryResponse,
    ApplyPresetRequest,
    WorkspaceFileUpdate,
    WorkspaceResponse,
)
from app.schemas.opencli import OpenCLIExecuteRequest, OpenCLIExecuteResponse
from app.services.content.opencli_service import execute_opencli
from app.chat.delivery import (
    check_unread,
    get_pending_messages,
)
from app.ai.group_logic import (
    pause_notifications,
    resume_and_fetch,
)
from app.services.agent.agent_service import (
    create_agent,
    list_agents,
    get_agent,
    update_agent_config,
    rollback_config,
    switch_agent_state,
    get_config_history,
    generate_agent_personality,
    export_agent_soul,
    import_agent_soul,
    agent_to_dict,
    CONFIG_PROFILES,
    apply_config_profile,
    _get_collaborator,
    add_collaborator,
    remove_collaborator,
    update_collaborator,
    list_collaborators,
    collaborator_to_dict,
)
from app.services.agent import workspace_service
from app.utils.auth import get_current_user
from app.utils.crypto import decrypt_api_key
from app.models.user import User
from sqlalchemy import select

router = APIRouter(prefix="/agents", tags=["AI 管理"])


@router.get("/models")
async def get_available_models(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回可用模型选项列表（所有供应商的全部模型，供前端下拉框分组展示）"""
    from app.config import settings
    from app.services.infrastructure.system_settings_service import get_providers, get_default_provider
    from app.utils.pure.provider_config import (
        collect_all_models, build_provider_summaries,
        get_default_models, get_thinking_supported,
    )

    providers = await get_providers(db)

    if not providers:
        # provider_config 为空时，使用内置预设作为默认值
        from app.services.agent.provider_presets import get_all_presets
        builtin = get_all_presets()
        provider_list = []
        for i, p in enumerate(builtin):
            provider_list.append({
                "name": p["label"],
                "provider": p["key"],
                "base_url": p["base_url"],
                "api_key_url": p.get("api_key_url", ""),
                "thinking_supported": p.get("thinking_supported", False),
                "is_default": i == 0,
            })
        all_models = settings.get_model_options()
        return {
            "models": all_models,
            "defaults": {"chat_model": settings.default_chat_model, "work_model": settings.default_work_model},
            "providers": provider_list,
            "provider": {"key": "deepseek", "base_url": settings.deepseek_base_url, "thinking_supported": settings.is_deepseek_api, "is_deepseek": settings.is_deepseek_api},
        }

    all_models = collect_all_models(providers)
    provider_list = build_provider_summaries(providers)
    chat_model, work_model = get_default_models(providers)
    default_provider = await get_default_provider(db)
    api_base = (default_provider.get("base_url") or settings.deepseek_base_url) if default_provider else settings.deepseek_base_url

    return {
        "models": all_models,
        "defaults": {
            "chat_model": chat_model or settings.default_chat_model,
            "work_model": work_model or settings.default_work_model,
        },
        "providers": provider_list,
        "provider": {
            "key": default_provider.get("provider", "unknown") if default_provider else "unknown",
            "base_url": api_base,
            "thinking_supported": get_thinking_supported(providers, api_base),
            "is_deepseek": "deepseek.com" in api_base,
        },
    }


async def _require_agent_access(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """依赖注入：获取 Agent 并校验访问权限（owner / 合作者，管理员可绕过）"""
    agent_result = await get_agent(db, agent_id)
    if agent_result.is_err():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=agent_result.error)
    agent = agent_result.ok
    if agent.owner_id == current_user["user_id"] or current_user["role"] == "admin":
        return agent
    # 检查合作者
    collab = await _get_collaborator(db, agent_id, current_user["user_id"])
    if collab is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问")
    agent._collaborator = collab
    return agent


def _require_collab_permission(agent, permission: str):
    """检查合作者是否有特定权限（仅对非 owner 非 admin 的合作者生效）"""
    collab = getattr(agent, "_collaborator", None)
    if collab is not None and not getattr(collab, permission, False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"需要 '{permission}' 权限")


async def _get_user_api_key(db: AsyncSession, user_id: int) -> str | None:
    """获取用户的 API Key（解密后）"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and user.api_key_encrypted:
        return decrypt_api_key(user.api_key_encrypted)
    return None


async def _get_user_api_base(db: AsyncSession, user_id: int) -> str:
    """获取用户的 API Base URL"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and user.api_base_url:
        return user.api_base_url
    from app.config import settings
    return settings.deepseek_base_url


@router.get("", response_model=list[AgentResponse])
async def list_my_agents(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取我的 AI 列表"""
    agents = await list_agents(db, current_user["user_id"])
    return [agent_to_dict(a) for a in agents]


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_new_agent(
    req: AgentCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新 AI（消耗额度）"""
    try:
        agent = await create_agent(
            db,
            owner_id=current_user["user_id"],
            name=req.name,
            system_prompt=req.system_prompt,
            temperature=req.temperature,
            top_p=req.top_p,
            presence_penalty=req.presence_penalty,
            frequency_penalty=req.frequency_penalty,
            chat_model=req.chat_model,
            work_model=req.work_model,
            thinking_enabled=req.thinking_enabled,
            is_admin=current_user["role"] == "admin",
            api_credit_cost=req.api_credit_cost,
            hide_ai_identity=req.hide_ai_identity,
            delay_reply_enabled=req.delay_reply_enabled,
            config_profile=req.config_profile,
            max_tool_rounds=req.max_tool_rounds,
            alarm_max_tool_rounds=req.alarm_max_tool_rounds,
            force_alarm_on_end=req.force_alarm_on_end,
            max_alarms=req.max_alarms,
            is_ai_editable=req.is_ai_editable,
            ai_type=req.ai_type,
            reminder_grace=req.reminder_grace,
            allow_friend_requests=req.allow_friend_requests,
            auto_respond_friend_request=req.auto_respond_friend_request,
            discoverable=req.discoverable,
            memory_load_mode=req.memory_load_mode,
            memory_recent_count=req.memory_recent_count,
            memory_shared_scope=req.memory_shared_scope,
            bio=req.bio,
            status_text=req.status_text,
            allow_others_chat=req.allow_others_chat,
            others_chat_mode=req.others_chat_mode,
            others_chat_quota=req.others_chat_quota,
            others_chat_used=req.others_chat_used,
            disallow_mode=req.disallow_mode,
        )
        return agent_to_dict(agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/generate", response_model=AgentGenerateResponse)
async def generate_personality(
    req: AgentGenerateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI 辅助生成性格配置"""
    try:
        api_key = await _get_user_api_key(db, current_user["user_id"])
        api_base = await _get_user_api_base(db, current_user["user_id"])
        result = await generate_agent_personality(
            description=req.description,
            api_base_url=api_base,
            api_key=api_key,
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成失败: {str(e)}",
        )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent_detail(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 详情"""
    agent = await _require_agent_access(agent_id, current_user, db)
    return agent_to_dict(agent)


@router.put("/{agent_id}/config", response_model=AgentResponse)
async def update_config(
    agent_id: int,
    req: AgentUpdateConfigRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 AI 配置（AI 自修改或用户手动修改）"""
    try:
        updates = req.model_dump(exclude_unset=True)
        agent = await update_agent_config(
            db,
            agent_id=agent_id,
            operator_id=current_user["user_id"],
            updates=updates,
            is_admin=current_user["role"] == "admin",
        )
        return agent_to_dict(agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{agent_id}/rollback/{version_id}", response_model=AgentResponse)
async def rollback(
    agent_id: int,
    version_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """回滚 AI 配置到历史版本"""
    try:
        agent = await rollback_config(
            db,
            agent_id=agent_id,
            version_id=version_id if version_id > 0 else -1,
            operator_id=current_user["user_id"],
        )
        return agent_to_dict(agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{agent_id}/state", response_model=AgentResponse)
async def switch_state(
    agent_id: int,
    req: AgentStateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """切换 AI 状态"""
    try:
        agent = await switch_agent_state(
            db,
            agent_id=agent_id,
            target_state=req.target_state,
            duration_hours=req.duration_hours,
            reason=req.reason,
        )
        return agent_to_dict(agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{agent_id}/history", response_model=list[AgentConfigHistoryResponse])
async def config_history(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 配置历史"""
    agent = await _require_agent_access(agent_id, current_user, db)
    history = await get_config_history(db, agent_id)
    return [
        {
            "id": h.id,
            "agent_id": h.agent_id,
            "system_prompt": h.system_prompt,
            "temperature": h.temperature,
            "top_p": h.top_p,
            "presence_penalty": h.presence_penalty,
            "frequency_penalty": h.frequency_penalty,
            "created_at": str(h.created_at) if h.created_at else None,
        }
        for h in history
    ]


# ---------- 消息聚合 / 暂停通知 ----------

@router.get("/{agent_id}/unread")
async def get_unread_summary(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 的各群聊未读消息摘要"""
    agent = await _require_agent_access(agent_id, current_user, db)
    summaries = await check_unread(db, agent_id)
    return {"agent_id": agent_id, "groups": summaries}


@router.post("/{agent_id}/pause")
async def pause_agent_notifications(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """暂停 AI 的通知（任务期间暂存消息）"""
    agent = await _require_agent_access(agent_id, current_user, db)
    try:
        await pause_notifications(db, agent_id)
        return {"message": "通知已暂停", "agent_id": agent_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{agent_id}/resume")
async def resume_agent_notifications(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复 AI 的通知，返回暂停期间的暂存消息"""
    agent = await _require_agent_access(agent_id, current_user, db)
    try:
        _, pending = await resume_and_fetch(db, agent_id)
        return {
            "message": "通知已恢复",
            "agent_id": agent_id,
            "pending_count": len(pending),
            "pending_messages": pending,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------- 灵魂存档（导出/导入） ----------

@router.get("/{agent_id}/export")
async def export_soul(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出 AI 灵魂档案（配置 + 历史 + 记忆 + 好友）"""
    agent = await _require_agent_access(agent_id, current_user, db)
    try:
        data = await export_agent_soul(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    # 文件名只保留 ASCII（中文 isalnum()=True 会突破 latin-1 编码限制）
    safe_name = "".join(c if (c.isascii() and c.isalnum()) or c in "._- " else "_" for c in agent.name)[:30]
    safe_name = safe_name.strip().replace(" ", "_") or "agent"
    # RFC 5987: UTF-8 编码真实文件名，latin-1 safe 文件名作为 fallback
    from urllib.parse import quote
    full_name = f"soul_{agent.name}.json"
    encoded = quote(full_name, safe="")
    return Response(
        content=json_str.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{safe_name}.json\"; "
                f"filename*=UTF-8''{encoded}"
            ),
        },
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_soul(
    file: UploadFile = File(...),
    import_memories: bool = Query(True),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导入 AI 灵魂档案（JSON 文件）"""
    from app.schemas.agent_export import AgentSoulArchive

    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .json 文件",
        )

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JSON 格式无效",
        )

    # 校验结构
    errors = AgentSoulArchive.validate(data)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件格式不合法: {'; '.join(errors)}",
        )

    try:
        agent = await import_agent_soul(
            db,
            data=data,
            owner_id=current_user["user_id"],
            import_memories=import_memories,
            is_admin=current_user["role"] == "admin",
        )
        return agent_to_dict(agent)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------- 删除 AI ----------

@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 AI（返还 api_credit_cost 额度）"""
    from app.services.agent.agent_service import delete_agent as delete_agent_svc
    try:
        result = await delete_agent_svc(
            db,
            agent_id=agent_id,
            operator_id=current_user["user_id"],
            is_admin=current_user["role"] == "admin",
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------- 合作者管理 ----------

@router.get("/{agent_id}/collaborators")
async def get_collaborators(
    agent_id: int,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看 AI 的合作者列表"""
    collabs = await list_collaborators(db, agent_id)
    is_owner = agent.owner_id == current_user["user_id"] or current_user["role"] == "admin"
    # 批量查询用户信息
    user_ids = [c.user_id for c in collabs]
    user_map = {}
    if user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for u in users_result.scalars().all():
            user_map[u.id] = u
    result = []
    for c in collabs:
        d = collaborator_to_dict(c)
        u = user_map.get(c.user_id)
        d["username"] = u.username if u else None
        d["avatar_url"] = getattr(u, "avatar_url", None) if u else None
        result.append(d)
    return {
        "collaborators": result,
        "is_owner": is_owner,
    }


@router.post("/{agent_id}/collaborators")
async def add_agent_collaborator(
    agent_id: int,
    req: dict,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加合作者（仅 owner 或 can_manage_collaborators 者可操作）"""
    # 权限检查
    is_owner = agent.owner_id == current_user["user_id"] or current_user["role"] == "admin"
    if not is_owner:
        collab = await _get_collaborator(db, agent_id, current_user["user_id"])
        if collab is None or not collab.can_manage_collaborators:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理合作者")

    # 支持 username 或 user_id 两种方式定位用户
    target_user_id = req.get("user_id")
    username = req.get("username", "").strip()
    if target_user_id:
        user_result = await db.execute(select(User).where(User.id == target_user_id))
        user = user_result.scalar_one_or_none()
    elif username:
        user_result = await db.execute(select(User).where(User.username == username))
        user = user_result.scalar_one_or_none()
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请提供用户名或用户 ID")
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if user.id == agent.owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="创建者无需添加为合作者")

    try:
        collab = await add_collaborator(
            db,
            agent_id=agent_id,
            user_id=user.id,
            can_edit=req.get("can_edit", True),
            can_delete=req.get("can_delete", False),
            can_manage_collaborators=req.get("can_manage_collaborators", False),
        )
        return collaborator_to_dict(collab)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{agent_id}/collaborators/{user_id}")
async def update_agent_collaborator(
    agent_id: int,
    user_id: int,
    req: dict,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新合作者权限（仅 owner 或 can_manage_collaborators 者可操作）"""
    is_owner = agent.owner_id == current_user["user_id"] or current_user["role"] == "admin"
    if not is_owner:
        collab = await _get_collaborator(db, agent_id, current_user["user_id"])
        if collab is None or not collab.can_manage_collaborators:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理合作者")

    try:
        collab = await update_collaborator(
            db,
            agent_id=agent_id,
            user_id=user_id,
            can_edit=req.get("can_edit"),
            can_delete=req.get("can_delete"),
            can_manage_collaborators=req.get("can_manage_collaborators"),
        )
        return collaborator_to_dict(collab)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/{agent_id}/collaborators/{user_id}")
async def remove_agent_collaborator(
    agent_id: int,
    user_id: int,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """移除合作者（仅 owner 或 can_manage_collaborators 者可操作）"""
    is_owner = agent.owner_id == current_user["user_id"] or current_user["role"] == "admin"
    if not is_owner:
        collab = await _get_collaborator(db, agent_id, current_user["user_id"])
        if collab is None or not collab.can_manage_collaborators:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权管理合作者")

    try:
        await remove_collaborator(db, agent_id, user_id)
        return {"message": "已移除合作者"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------- API Token ----------

@router.post("/{agent_id}/token")
async def generate_agent_token(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """为 AI 生成/刷新外部 API Token"""
    import secrets
    agent = await _require_agent_access(agent_id, current_user, db)
    token = "at-" + secrets.token_hex(24)
    agent.api_token = token
    await db.flush()
    return {"agent_id": agent_id, "api_token": token}


@router.get("/{agent_id}/token")
async def get_agent_token(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 当前的 API Token（脱敏）"""
    agent = await _require_agent_access(agent_id, current_user, db)
    token = agent.api_token
    if token:
        masked = token[:8] + "..." + token[-4:] if len(token) > 12 else token[:4] + "***"
    else:
        masked = None
    return {"agent_id": agent_id, "api_token": masked, "has_token": token is not None}


# ---------- 头像 ----------

@router.post("/{agent_id}/avatar")
async def upload_agent_avatar(
    agent_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传 AI 头像（统一存 uploads/avatars/，大小受全局限制）"""
    import os
    import uuid
    from app.config import settings

    agent = await _require_agent_access(agent_id, current_user, db)

    # 验证文件类型
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 JPEG/PNG/GIF/WebP")

    # 大小限制
    max_bytes = settings.avatar_max_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"头像不能超过 {settings.avatar_max_size_mb}MB")

    # 压缩头像（≤2MB，保持透明通道）
    from app.utils.image_compress import compress_avatar
    content = compress_avatar(content)
    ext = "png" if content[:4] == b'\x89PNG' else "jpg"

    # 头像存储目录
    upload_dir = settings.avatars_dir
    os.makedirs(upload_dir, exist_ok=True)

    # 清理旧头像文件
    if agent.avatar_url:
        old_name = agent.avatar_url.rsplit('/', 1)[-1]
        for fname in (old_name, f"thumb_{old_name}"):
            old_path = os.path.join(upload_dir, fname)
            if os.path.isfile(old_path):
                os.remove(old_path)

    # 保存到统一头像目录
    filename = f"agent_{agent_id}_{uuid.uuid4().hex[:8]}.{ext}"
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
    agent.avatar_url = avatar_url
    await db.flush()

    # 入队联邦 profile 同步
    try:
        from app.services.federation.federation_service import enqueue_profile_update
        await enqueue_profile_update(db, "agent", agent_id, "avatar_url", avatar_url)
    except Exception:
        pass

    return {"agent_id": agent_id, "avatar_url": avatar_url}


# ---------- 存储管理 ----------

@router.get("/{agent_id}/storage")
async def get_agent_storage(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看 AI 的文件空间占用（工作区 + 附件 + 数据库文件元数据）"""
    import os
    from app.config import settings
    from app.models.file import FileMetadata as FM
    from sqlalchemy import select, func as sqlfunc

    agent = await _require_agent_access(agent_id, current_user, db)

    # 查询该 AI 的文件（FileMetadata 中 owner_type="ai" 且 owner_id=agent_id）
    db_result = await db.execute(
        select(sqlfunc.sum(FM.size), sqlfunc.count(FM.id))
        .where(FM.owner_type == "ai", FM.owner_id == agent_id)
    )
    db_sum, db_count = db_result.one()
    total_size = db_sum or 0
    file_count = db_count or 0

    # 获取文件列表（从 DB，按大小降序）
    files = []
    file_list_result = await db.execute(
        select(FM)
        .where(FM.owner_type == "ai", FM.owner_id == agent_id)
        .order_by(FM.size.desc())
        .limit(50)
    )
    for f in file_list_result.scalars().all():
        files.append({
            "id": f.id,
            "path": f.path,
            "size": f.size or 0,
            "name": f.path.rsplit("/", 1)[-1] if "/" in f.path else f.path,
        })

    # 3. 配额（默认每 AI 100MB）
    quota_mb = int(os.getenv("STORAGE_QUOTA_PER_AI_MB", "100"))
    quota_bytes = quota_mb * 1024 * 1024

    return {
        "agent_id": agent_id,
        "total_size": total_size,
        "file_count": file_count,
        "quota_bytes": quota_bytes,
        "quota_mb": quota_mb,
        "usage_percent": round(total_size / quota_bytes * 100, 1) if quota_bytes > 0 else 0,
        "files": sorted(files, key=lambda f: f["size"], reverse=True)[:50],
    }


# ---------- 记忆查看 ----------

@router.get("/{agent_id}/memories")
async def get_agent_memories(
    agent_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看 AI 的所有记忆条目"""
    from app.models.memory import RoughMemory, DetailMemory

    agent = await _require_agent_access(agent_id, current_user, db)

    # 总数
    total_result = await db.execute(
        select(func.count(RoughMemory.id)).where(
            RoughMemory.owner_type == "ai",
            RoughMemory.owner_id == agent_id,
        )
    )
    total = total_result.scalar() or 0

    # 分页查询
    offset = (page - 1) * page_size
    result = await db.execute(
        select(RoughMemory)
        .where(RoughMemory.owner_type == "ai", RoughMemory.owner_id == agent_id)
        .order_by(RoughMemory.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    memories = result.scalars().all()

    items = []
    for rm in memories:
        detail_result = await db.execute(
            select(DetailMemory).where(DetailMemory.rough_id == rm.id).limit(1)
        )
        detail = detail_result.scalar_one_or_none()
        items.append({
            "id": rm.id,
            "title": rm.title,
            "content": detail.content if detail else None,
            "scope": rm.scope,
            "group_id": rm.group_id,
            "created_at": str(rm.created_at) if rm.created_at else None,
        })

    return {
        "agent_id": agent_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


# ---------- 结构化记忆（目录级键值存储） ----------

@router.get("/{agent_id}/structured-records")
async def get_agent_structured_records(
    agent_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 的结构化记忆目录树（category → sub_key → fields）"""
    await _require_agent_access(agent_id, current_user, db)

    from app.services.memory.structured_memory_service import sr_categories, sr_list, sr_get

    data = await sr_categories(db, agent_id)
    tree = []
    for cat in data.get("categories", []):
        sub_data = await sr_list(db, agent_id, cat["name"])
        subs = []
        for item in sub_data.get("items", []):
            fields_data = await sr_get(db, agent_id, cat["name"], item["sub_key"])
            subs.append({
                "sub_key": item["sub_key"],
                "field_count": item["field_count"],
                "last_update": item.get("last_update"),
                "fields": fields_data.get("fields", {}),
            })
        tree.append({
            "category": cat["name"],
            "record_count": cat["record_count"],
            "sub_count": cat["sub_count"],
            "subs": subs,
        })
    return {"categories": tree}


# ---------- OpenCLI 工具调用 ----------

@router.post("/{agent_id}/opencli")
async def execute_opencli_tool(
    agent_id: int,
    req: OpenCLIExecuteRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AI 工具调用：执行 OpenCLI 命令。
    返回标准工具调用格式（成功返回结果，失败返回 {"error": true, ...}）。
    """
    from app.utils.error_handler import build_tool_error, log_error

    agent_result = await get_agent(db, agent_id)
    if agent_result.is_err():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=agent_result.error)
    agent = agent_result.ok

    try:
        result = await execute_opencli(
            db,
            agent_id=agent_id,
            command=req.command,
            args=req.args,
        )
        return result
    except PermissionError as e:
        await log_error(
            db, "opencli_permission_denied", "ai", agent_id,
            target_type="opencli", details={"command": req.command, "reason": str(e)},
            level="WARNING",
        )
        return build_tool_error("OPENCLI_PERMISSION_DENIED", str(e))
    except TimeoutError as e:
        await log_error(
            db, "opencli_timeout", "ai", agent_id,
            target_type="opencli", details={"command": req.command, "reason": str(e)},
            level="WARNING",
        )
        return build_tool_error("OPENCLI_TIMEOUT", str(e))
    except Exception as e:
        await log_error(
            db, "opencli_exec_failed", "ai", agent_id,
            target_type="opencli", details={"command": req.command, "error": str(e)},
            level="ERROR",
        )
        return build_tool_error("OPENCLI_EXEC_FAILED", f"命令执行失败: {str(e)}")


# ──────────────────────────── 三档 AI 配置 ────────────────────────────

@router.get("/presets")
async def list_config_presets():
    """返回所有可用的配置档位预设"""
    return {
        "presets": [
            {"key": k, "name": v["name"], "description": v["description"],
             "temperature": v["temperature"], "thinking_enabled": v["thinking_enabled"],
             "max_tool_rounds": v.get("max_tool_rounds", 6),
             "alarm_max_tool_rounds": v.get("alarm_max_tool_rounds", 10),
             "force_alarm_on_end": v.get("force_alarm_on_end", False),
             "max_alarms": v.get("max_alarms", 10),
             "delay_reply_enabled": v.get("delay_reply_enabled", False),
             "is_ai_editable": v.get("is_ai_editable", True),
             "hide_ai_identity": v.get("hide_ai_identity", False),
             }
            for k, v in CONFIG_PROFILES.items()
        ],
        "current_default": "chat",
    }


@router.get("/{agent_id}/preset-preview")
async def preview_preset_change(
    agent_id: int,
    profile: str,
    agent: any = Depends(_require_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """预览切换预设后的变更（不实际应用）"""
    if profile not in CONFIG_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的配置档: {profile}，可选: {list(CONFIG_PROFILES.keys())}",
        )
    try:
        preview = await apply_config_profile(db, agent_id, profile, dry_run=True)
        return preview
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{agent_id}/apply-preset")
async def apply_preset(
    agent_id: int,
    req: ApplyPresetRequest,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """应用配置档位到 AI（按升降级规则智能合并，保护用户手动调整）"""
    if req.profile not in CONFIG_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的配置档: {req.profile}，可选: {list(CONFIG_PROFILES.keys())}",
        )
    try:
        updated = await apply_config_profile(db, agent_id, req.profile)
        return agent_to_dict(updated)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ──────────────────────────── 对话限额重置 ────────────────────────────

@router.post("/{agent_id}/reset-others-chat-used")
async def reset_others_chat_used(
    agent_id: int,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重置 AI 的他人对话使用计数"""
    if agent.owner_id != current_user["user_id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅 AI 主人或管理员可重置")
    agent.others_chat_used = 0
    await db.flush()
    return {"ok": True, "others_chat_used": 0}


# ──────────────────────────── AI 个人工作区 ────────────────────────────

@router.get("/{agent_id}/workspace", response_model=WorkspaceResponse)
async def get_workspace(
    agent_id: int,
    agent: any = Depends(_require_agent_access),
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 的工作区文件（TODO / PLAN / JOURNAL）"""
    files = await workspace_service.get_all_workspace_files(db, agent_id)
    return WorkspaceResponse(**files)


@router.put("/{agent_id}/workspace", response_model=WorkspaceResponse)
async def update_workspace(
    agent_id: int,
    req: WorkspaceFileUpdate,
    agent: any = Depends(_require_agent_access),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 AI 的工作区文件（也可由 AI 工具调用触发）"""
    if req.file not in ("todo", "plan", "journal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的文件类型: {req.file}，可选: todo|plan|journal",
        )
    await workspace_service.set_workspace_file(db, agent_id, req.file, req.content)
    files = await workspace_service.get_all_workspace_files(db, agent_id)
    return WorkspaceResponse(**files)
