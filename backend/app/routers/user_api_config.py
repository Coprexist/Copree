"""用户自己的 API 配置 —— 两层结构的管理接口

    GET    /user/api-config/providers?kind=llm     厂商（带名下配置档）
    POST   /user/api-config/providers              新建/接入一家厂商
    POST   /user/api-config/providers/connect      从内置预设批量接入
    PUT    /user/api-config/providers/{id}         改一家厂商
    POST   /user/api-config/providers/{id}/restore 按内置预设恢复默认
    DELETE /user/api-config/providers/{id}         删一家（名下有档时要 ?force=true）
    POST   /user/api-config/profiles               新建配置档
    PUT    /user/api-config/profiles/{id}          改配置档
    POST   /user/api-config/profiles/{id}/default  设为默认档
    DELETE /user/api-config/profiles/{id}          删配置档

Key 一律加密存、只回末四位；没有"读出明文"的接口——那接口等于把钥匙串挂在门上。

删除是有约束的（不是后路）：默认档不许直接删（要先指认另一条为默认），
厂商名下还有档时要显式 force。宁可多问一句，也不要让某个 AI 悄悄换了 Key。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_profile import UserApiProvider, UserApiProfile
from app.utils.auth import get_current_user
from app.utils.crypto import decrypt_api_key, encrypt_api_key

router = APIRouter(prefix="/user/api-config", tags=["用户 API 配置"])

KINDS = ("llm", "voice")


# ── 请求体 ────────────────────────────────────────────────────────────

class ProviderIn(BaseModel):
    """新建/修改一家厂商。只传需要改的字段（PUT 是部分更新）。"""

    kind: str = "llm"
    name: str | None = Field(None, max_length=80)
    preset_key: str | None = Field(None, max_length=40)
    base_url: str | None = None
    api_key: str | None = None          # 这家的默认 Key；空串 = 清空
    options: list[dict] | None = None   # [{value,label}]
    default_primary: str | None = Field(None, max_length=200)
    default_secondary: str | None = Field(None, max_length=200)
    thinking_supported: bool | None = None
    system_layout: str | None = Field(None, max_length=16)


class ConnectIn(BaseModel):
    """从内置预设接入（一个或多个 key）"""

    kind: str = "llm"
    preset_keys: list[str] = Field(..., min_length=1)


class ProfileIn(BaseModel):
    """新建/修改一条配置档"""

    provider_id: int | None = None      # 新建时必填
    name: str | None = Field(None, max_length=80)
    api_key: str | None = None          # 空串 = 清空 → 用厂商默认
    primary_model: str | None = Field(None, max_length=200)
    secondary_model: str | None = Field(None, max_length=200)


# ── 序列化 ────────────────────────────────────────────────────────────

def _last4(enc: str | None) -> str:
    if not enc:
        return ""
    try:
        return decrypt_api_key(enc)[-4:]
    except Exception:
        return ""


def _profile_dict(p: UserApiProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "has_key": bool(p.api_key_encrypted),
        "key_last4": _last4(p.api_key_encrypted),
        "primary_model": p.primary_model,
        "secondary_model": p.secondary_model,
        "is_default": bool(p.is_default),
        "sort_order": p.sort_order,
    }


def _provider_dict(p: UserApiProvider, profiles: list[UserApiProfile]) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "name": p.name,
        "preset_key": p.preset_key,
        "base_url": p.base_url,
        "has_key": bool(p.default_api_key_encrypted),
        "key_last4": _last4(p.default_api_key_encrypted),
        "options": p.options or [],
        "default_primary": p.default_primary,
        "default_secondary": p.default_secondary,
        "thinking_supported": bool(p.thinking_supported),
        "system_layout": p.system_layout,
        "profiles": [_profile_dict(x) for x in profiles],
    }


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind 只能是 llm 或 voice")
    return kind


def _apply_key(model, raw: str | None) -> None:
    """Key 的三态：不传=不改，空串=清空，其它=加密保存"""
    if raw is None:
        return
    model.api_key_encrypted = encrypt_api_key(raw) if raw.strip() else None


# ── 厂商 ──────────────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers(
    kind: str = "llm",
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """已接入的厂商，以及挂在他下面的配置档（界面按这个顺序铺卡片）"""
    _check_kind(kind)
    rows = (await db.execute(
        select(UserApiProvider)
        .where(UserApiProvider.user_id == current_user["user_id"],
               UserApiProvider.kind == kind)
        .order_by(UserApiProvider.sort_order, UserApiProvider.id)
    )).scalars().all()
    if not rows:
        return {"providers": []}

    ids = [p.id for p in rows]
    profiles = (await db.execute(
        select(UserApiProfile)
        .where(UserApiProfile.provider_id.in_(ids))
        .order_by(UserApiProfile.sort_order, UserApiProfile.id)
    )).scalars().all()
    by_provider: dict[int, list[UserApiProfile]] = {i: [] for i in ids}
    for prof in profiles:
        by_provider.setdefault(prof.provider_id, []).append(prof)

    return {"providers": [_provider_dict(p, by_provider[p.id]) for p in rows]}


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    req: ProviderIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """接入一家厂商。带 preset_key 时先按内置预设填好，再用请求里的字段覆盖。"""
    kind = _check_kind(req.kind)
    preset = _preset_of(req.preset_key, kind) if req.preset_key else None

    provider = UserApiProvider(
        user_id=current_user["user_id"],
        kind=kind,
        name=req.name or (preset["label"] if preset else "自定义厂商"),
        preset_key=req.preset_key if preset else None,
        base_url=req.base_url or (preset["base_url"] if preset else ""),
        options=req.options if req.options is not None else _options_of(preset),
        default_primary=req.default_primary or _primary_of(preset),
        default_secondary=req.default_secondary or _secondary_of(preset),
        thinking_supported=(req.thinking_supported
                            if req.thinking_supported is not None
                            else bool(preset.get("thinking_supported")) if preset else False),
        system_layout=req.system_layout or (preset.get("system_layout") if preset else None)
        or "anywhere",
    )
    _apply_key(provider, req.api_key)
    db.add(provider)
    await db.flush()
    return _provider_dict(provider, [])


@router.post("/providers/connect", status_code=status.HTTP_201_CREATED)
async def connect_presets(
    req: ConnectIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从内置预设批量接入：列表里只有接入过的厂商，这一步就是"接线"的动作。"""
    kind = _check_kind(req.kind)
    existing = set((await db.execute(
        select(UserApiProvider.preset_key).where(
            UserApiProvider.user_id == current_user["user_id"],
            UserApiProvider.kind == kind,
        )
    )).scalars().all())

    created = []
    for key in req.preset_keys:
        if key in existing:
            continue
        preset = _preset_of(key, kind)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"没有这条预设：{key}")
        provider = UserApiProvider(
            user_id=current_user["user_id"],
            kind=kind,
            name=preset["label"],
            preset_key=key,
            base_url=preset["base_url"],
            options=_options_of(preset),
            default_primary=_primary_of(preset),
            default_secondary=_secondary_of(preset),
            thinking_supported=bool(preset.get("thinking_supported")),
            system_layout=preset.get("system_layout") or "anywhere",
        )
        db.add(provider)
        created.append(provider)
    await db.flush()
    return {"created": [_provider_dict(p, []) for p in created]}


@router.put("/providers/{provider_id}")
async def update_provider(
    provider_id: int,
    req: ProviderIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider = await _own_provider(db, provider_id, current_user["user_id"])
    for field in ("name", "base_url", "default_primary", "default_secondary",
                  "thinking_supported", "system_layout", "options"):
        if field in req.model_fields_set and getattr(req, field) is not None:
            setattr(provider, field, getattr(req, field))
    _apply_key(provider, req.api_key)
    await db.flush()
    profiles = (await db.execute(
        select(UserApiProfile).where(UserApiProfile.provider_id == provider.id)
    )).scalars().all()
    return _provider_dict(provider, profiles)


@router.post("/providers/{provider_id}/restore")
async def restore_provider(
    provider_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按内置预设把地址/模型清单/能力恢复成原样（Key 不动，那是你的东西）"""
    provider = await _own_provider(db, provider_id, current_user["user_id"])
    preset = _preset_of(provider.preset_key, provider.kind) if provider.preset_key else None
    if preset is None:
        raise HTTPException(status_code=400, detail="这条厂商不是从内置预设接入的，无法恢复")
    provider.base_url = preset["base_url"]
    provider.options = _options_of(preset)
    provider.default_primary = _primary_of(preset)
    provider.default_secondary = _secondary_of(preset)
    provider.thinking_supported = bool(preset.get("thinking_supported"))
    provider.system_layout = preset.get("system_layout") or "anywhere"
    await db.flush()
    profiles = (await db.execute(
        select(UserApiProfile).where(UserApiProfile.provider_id == provider.id)
    )).scalars().all()
    return _provider_dict(provider, profiles)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: int,
    force: bool = Query(False, description="名下还有配置档时，是否连它们一起删"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider = await _own_provider(db, provider_id, current_user["user_id"])
    count = len((await db.execute(
        select(UserApiProfile.id).where(UserApiProfile.provider_id == provider.id)
    )).scalars().all())
    if count and not force:
        raise HTTPException(
            status_code=409,
            detail=f"这家厂商下还有 {count} 个配置档，确定一起删要带 force=true",
        )
    await db.delete(provider)
    return None


# ── 配置档 ────────────────────────────────────────────────────────────

@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    req: ProfileIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not req.provider_id:
        raise HTTPException(status_code=400, detail="新建配置档要指定厂商")
    provider = await _own_provider(db, req.provider_id, current_user["user_id"])

    profile = UserApiProfile(
        user_id=current_user["user_id"],
        provider_id=provider.id,
        name=req.name or "新配置",
        primary_model=req.primary_model,
        secondary_model=req.secondary_model,
    )
    _apply_key(profile, req.api_key)
    db.add(profile)
    await db.flush()
    # 第一条档自动成为默认档：否则新用户没有默认可用
    if not await _has_default(db, current_user["user_id"], provider.kind):
        profile.is_default = True
    await db.flush()
    return _profile_dict(profile)


@router.put("/profiles/{profile_id}")
async def update_profile(
    profile_id: int,
    req: ProfileIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _own_profile(db, profile_id, current_user["user_id"])
    if req.name is not None:
        profile.name = req.name
    if "primary_model" in req.model_fields_set:
        profile.primary_model = req.primary_model
    if "secondary_model" in req.model_fields_set:
        profile.secondary_model = req.secondary_model
    _apply_key(profile, req.api_key)
    await db.flush()
    return _profile_dict(profile)


@router.post("/profiles/{profile_id}/default")
async def set_default_profile(
    profile_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设为默认档：同一 kind 下只能有一条，所以先把别的同 kind 档清掉"""
    profile = await _own_profile(db, profile_id, current_user["user_id"])
    provider = await db.get(UserApiProvider, profile.provider_id)
    others = (await db.execute(
        select(UserApiProfile)
        .join(UserApiProvider, UserApiProfile.provider_id == UserApiProvider.id)
        .where(UserApiProfile.user_id == current_user["user_id"],
               UserApiProvider.kind == provider.kind)
    )).scalars().all()
    for row in others:
        row.is_default = row.id == profile.id
    await db.flush()
    return {"default_profile_id": profile.id}


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _own_profile(db, profile_id, current_user["user_id"])
    if profile.is_default:
        provider = await db.get(UserApiProvider, profile.provider_id)
        rest = (await db.execute(
            select(UserApiProfile.id)
            .join(UserApiProvider, UserApiProfile.provider_id == UserApiProvider.id)
            .where(UserApiProfile.user_id == current_user["user_id"],
                   UserApiProvider.kind == provider.kind,
                   UserApiProfile.id != profile.id)
        )).scalars().all()
        if rest:
            raise HTTPException(
                status_code=409,
                detail="这是默认档，先把另一条设为默认再删（否则没选配置档的 AI 会跟着变）",
            )
    await db.delete(profile)
    return None


# ── 内部 ──────────────────────────────────────────────────────────────

async def _own_provider(db: AsyncSession, provider_id: int, user_id: int) -> UserApiProvider:
    provider = await db.get(UserApiProvider, provider_id)
    if provider is None or provider.user_id != user_id:
        raise HTTPException(status_code=404, detail="厂商不存在")
    return provider


async def _own_profile(db: AsyncSession, profile_id: int, user_id: int) -> UserApiProfile:
    profile = await db.get(UserApiProfile, profile_id)
    if profile is None or profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="配置档不存在")
    return profile


async def _has_default(db: AsyncSession, user_id: int, kind: str) -> bool:
    row = (await db.execute(
        select(UserApiProfile.id)
        .join(UserApiProvider, UserApiProfile.provider_id == UserApiProvider.id)
        .where(UserApiProfile.user_id == user_id,
               UserApiProvider.kind == kind,
               UserApiProfile.is_default.is_(True))
        .limit(1)
    )).first()
    return row is not None


def _preset_of(key: str | None, kind: str) -> dict | None:
    if not key:
        return None
    from app.services.agent.provider_presets import get_all_presets

    for p in get_all_presets(kind):
        if p["key"] == key:
            return p
    return None


def _options_of(preset: dict | None) -> list[dict] | None:
    """内置预设的 models/voices → 通用 options 形状（LLM 与语音同一条路径）"""
    if not preset:
        return None
    rows = preset.get("models") or preset.get("voices") or []
    out = [{"value": r.get("value", ""), "label": r.get("label") or r.get("value", "")}
           for r in rows if r.get("value")]
    return out or None


def _primary_of(preset: dict | None) -> str | None:
    if not preset:
        return None
    return preset.get("chat_model") or preset.get("voice_id") or None


def _secondary_of(preset: dict | None) -> str | None:
    if not preset:
        return None
    return preset.get("work_model") or None
