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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_profile import UserApiProvider, UserApiProfile
from app.utils.auth import get_current_user
from app.utils.crypto import decrypt_api_key, encrypt_api_key
from app.utils.pure.provider_presets import ProviderPreset, get_preset

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


def _profile_dict(p: UserApiProfile, used_by: list[dict] | None = None) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "has_key": bool(p.api_key_encrypted),
        "key_last4": _last4(p.api_key_encrypted),
        "primary_model": p.primary_model,
        "secondary_model": p.secondary_model,
        "is_default": bool(p.is_default),
        "sort_order": p.sort_order,
        # 反向索引：哪些 AI 绑着这条配置（见 docs/dev/api_config_chain.md §6.4）
        "used_by": list(used_by or []),
        "used_by_count": len(used_by or []),
    }


async def _profile_usage(db: AsyncSession, profile_ids: list[int]) -> dict[int, list[dict]]:
    """这条配置被哪些 AI 绑着（反向索引，静态）。

    语义严格是「**绑定了**」，不是「正在用」：后者要算额度、可达性与优先级，
    静态读不出来，硬算就是第二个真相。绑定数只回答「改这条会影响到谁」。
    LLM 与语音两处都查：同一条配置只会属于一种 kind，合并归堆不会串。
    """
    from app.models.agent import Agent
    from app.models.agent_voice import AgentVoiceConfig

    usage: dict[int, list[dict]] = {}
    ids = [i for i in profile_ids if i]
    if not ids:
        return usage
    llm_rows = (await db.execute(
        select(Agent.llm_profile_id, Agent.id, Agent.name)
        .where(Agent.llm_profile_id.in_(ids))
        .order_by(Agent.name)
    )).all()
    for pid, aid, name in llm_rows:
        usage.setdefault(pid, []).append({"id": aid, "name": name})
    voice_rows = (await db.execute(
        select(AgentVoiceConfig.voice_profile_id, Agent.id, Agent.name)
        .join(Agent, Agent.id == AgentVoiceConfig.agent_id)
        .where(AgentVoiceConfig.voice_profile_id.in_(ids))
        .order_by(Agent.name)
    )).all()
    for pid, aid, name in voice_rows:
        usage.setdefault(pid, []).append({"id": aid, "name": name})
    return usage


def _provider_dict(p: UserApiProvider, profiles: list[UserApiProfile],
                   usage: dict[int, list[dict]] | None = None) -> dict:
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
        # 布局的判定证据：source 为空表示"从未判定"，界面据此显示「自动识别」
        "system_layout_source": p.system_layout_source or "",
        "system_template_hash": p.system_template_hash or "",
        "system_layout_checked_at": (p.system_layout_checked_at.isoformat()
                                     if p.system_layout_checked_at else ""),
        "profiles": [_profile_dict(x, (usage or {}).get(x.id)) for x in profiles],
    }


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="kind 只能是 llm 或 voice")
    return kind


# 两张表的 Key 列名不同（厂商 default_api_key_encrypted / 配置档 api_key_encrypted），
# 所以字段名必须显式传：写错的名字不会报错——SQLAlchemy 允许在实例上挂任意属性，只是
# 那一次保存永远落不了库。表现就是「填了 Key、点保存、重开还是空」（2026-11-22 oxo 报）。
PROVIDER_KEY_FIELD = "default_api_key_encrypted"
PROFILE_KEY_FIELD = "api_key_encrypted"


def _apply_key(model, raw: str | None, field: str) -> None:
    """Key 的三态：不传=不改，空串=清空，其它=加密保存"""
    if raw is None:
        return
    setattr(model, field, encrypt_api_key(raw) if raw.strip() else None)


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

    usage = await _profile_usage(db, [x.id for x in profiles])
    return {"providers": [_provider_dict(p, by_provider[p.id], usage) for p in rows]}


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    req: ProviderIn,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """接入一家厂商。带 preset_key 时先按内置预设填好，再用请求里的字段覆盖。

    system 布局在这里**判一次**（手选 > 探测 > 预设 > 按地址猜）：用户自己填的地址
    大多没有预设，光靠一个默认 anywhere 会让每轮都 500（见 channel_layout.py）。
    """
    kind = _check_kind(req.kind)
    preset = _preset_of(req.preset_key, kind)
    defaults = _preset_values(preset)
    base_url = req.base_url or defaults["base_url"]
    # 预设原地址就不用探测：平台已经给过结论，为每一家各等 5 秒换不来新信息；
    # 一旦地址被改成别处（中转站、本机服务），就必须重新认。
    layout = await _decide_layout(
        db, current_user["user_id"],
        base_url=base_url,
        model=req.default_primary or defaults["default_primary"],
        manual=req.system_layout,
        preset_layout=preset["system_layout"] if preset else None,
        probe=preset is None or base_url != preset["base_url"],
    )

    provider = UserApiProvider(
        user_id=current_user["user_id"],
        kind=kind,
        name=req.name or (preset["label"] if preset else "自定义厂商"),
        preset_key=defaults["preset_key"],
        base_url=base_url,
        options=req.options if req.options is not None else defaults["options"],
        default_primary=req.default_primary or defaults["default_primary"],
        default_secondary=req.default_secondary or defaults["default_secondary"],
        thinking_supported=(req.thinking_supported
                            if req.thinking_supported is not None
                            else defaults["thinking_supported"]),
        **_layout_columns(layout),
    )
    _apply_key(provider, req.api_key, PROVIDER_KEY_FIELD)
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
        # 批量接入不探测：预设已经给了结论，为每家各等 5 秒换不来新信息
        layout = await _decide_layout(
            db, current_user["user_id"],
            base_url=preset["base_url"],
            model=preset["chat_model"] or preset["voice_id"] or None,
            manual=None, preset_layout=preset["system_layout"], probe=False,
        )
        provider = _new_provider(current_user["user_id"], kind, preset, layout)
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
    # system_layout 不在这份列表里：它不是"随手改的字段"，而是要重算的判定结论
    for field in ("name", "base_url", "default_primary", "default_secondary",
                  "thinking_supported", "options"):
        if field in req.model_fields_set and getattr(req, field) is not None:
            setattr(provider, field, getattr(req, field))

    # 何时重算布局：用户手选了、或者地址/主型号变了（换了服务就该重新认一次）。
    # 只改名字、Key、模型清单时不重算：那不会改变对端模板。
    base_url_changed = "base_url" in req.model_fields_set and req.base_url is not None
    model_changed = ("default_primary" in req.model_fields_set
                     and req.default_primary is not None)
    if req.system_layout or base_url_changed or model_changed:
        preset = _preset_of(provider.preset_key, provider.kind)
        # 地址还在预设原处就信预设的结论，改到别处才重新探测
        probe = preset is None or provider.base_url != preset["base_url"]
        layout = await _decide_layout(
            db, current_user["user_id"],
            base_url=provider.base_url,
            model=provider.default_primary,
            manual=req.system_layout,
            preset_layout=preset["system_layout"] if preset else None,
            probe=probe,
        )
        for column, value in _layout_columns(layout).items():
            setattr(provider, column, value)

    _apply_key(provider, req.api_key, PROVIDER_KEY_FIELD)
    await db.flush()
    profiles = (await db.execute(
        select(UserApiProfile).where(UserApiProfile.provider_id == provider.id)
    )).scalars().all()
    return _provider_dict(provider, profiles, await _profile_usage(db, [x.id for x in profiles]))


@router.post("/providers/{provider_id}/restore")
async def restore_provider(
    provider_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按内置预设把地址/模型清单/能力恢复成原样（Key 不动，那是你的东西）"""
    provider = await _own_provider(db, provider_id, current_user["user_id"])
    preset = _preset_of(provider.preset_key, provider.kind)
    if preset is None:
        raise HTTPException(status_code=400, detail="这条厂商不是从内置预设接入的，无法恢复")
    # 恢复默认不联网探：这一步是"回到出厂值"，结论本来就写在预设里
    layout = await _decide_layout(
        db, current_user["user_id"],
        base_url=preset["base_url"],
        model=preset["chat_model"] or preset["voice_id"] or None,
        manual=None, preset_layout=preset["system_layout"], probe=False,
    )
    _reset_provider(provider, preset, layout)
    await db.flush()
    profiles = (await db.execute(
        select(UserApiProfile).where(UserApiProfile.provider_id == provider.id)
    )).scalars().all()
    return _provider_dict(provider, profiles, await _profile_usage(db, [x.id for x in profiles]))


@router.post("/providers/{provider_id}/probe-layout")
async def probe_provider_layout(
    provider_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重新识别这家厂商的 system 摆放，并把结论落库。

    用户主动点的「重新识别」：此时地址已经保存过，所以私网也允许探一次
    （登记规则见 services/agent/base_url_registry.py）。改完本地服务、换了模型之后
    点一下就能拿到新结论，不必等到下次保存。
    """
    provider = await _own_provider(db, provider_id, current_user["user_id"])
    preset = _preset_of(provider.preset_key, provider.kind)
    layout = await _decide_layout(
        db, current_user["user_id"],
        base_url=provider.base_url,
        model=provider.default_primary,
        manual=None,
        preset_layout=preset["system_layout"] if preset else None,
    )
    for column, value in _layout_columns(layout).items():
        setattr(provider, column, value)
    await db.flush()
    return {"layout": layout.layout, "source": layout.source, "message": layout.message}


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


async def provider_api_key(db: AsyncSession, provider_id: int, user_id: int) -> str | None:
    """这条厂商自己的 Key（解密后），没存就 None。

    唯一用途：给「测试连通性 / 刷新模型清单」取 Key。**不回落用户账号级 Key**——
    docs/dev/api_profiles.md 的铁律是「Key 绝不跨厂商借」，探测也不例外：
    拿 DeepSeek 的 Key 去打 ollama 看似能通，实际把两条配置混成一条；
    而只用本地模型、从没配过云端 Key 的用户会被一句「请先配置 API Key」挡在门外。
    """
    provider = await _own_provider(db, provider_id, user_id)
    if not provider.default_api_key_encrypted:
        return None
    return decrypt_api_key(provider.default_api_key_encrypted)


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
    _apply_key(profile, req.api_key, PROFILE_KEY_FIELD)
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
    _apply_key(profile, req.api_key, PROFILE_KEY_FIELD)
    await db.flush()
    return _profile_dict(profile, (await _profile_usage(db, [profile.id])).get(profile.id))


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


def _preset_of(key: str | None, kind: str) -> ProviderPreset | None:
    """按 key 取内置预设，并校验它属于这个 kind（LLM 与语音不能互相接错）

    取预设一律走 pure 层的 get_preset（它给归一化后的完整形状），
    不要自己遍历 get_all_presets 找 key——那就是第二套查找逻辑。
    """
    preset = get_preset(key) if key else None
    return preset if preset and preset["kind"] == kind else None


def _preset_values(preset: ProviderPreset | None) -> dict:
    """内置预设 → 厂商行的默认字段（没有预设时给「自定义厂商」的空底）

    新建（POST/connect）与"恢复默认"共用这一份：字段怎么从预设里取，
    只在这里定义一次，否则三个端点迟早各写各的（副模型有、语音主模型没有之类）。

    **system 布局不在这里**：它不是"从预设抄一个值"，而是一次判定
    （手选 > 探测 > 预设 > 按地址猜，见 services/agent/channel_layout.py）。
    把预设值当作布局直接抄下来，正是自建地址拿到默认 anywhere、每轮 500 的来源。
    """
    if not preset:
        return {"preset_key": None, "base_url": "", "options": None,
                "default_primary": None, "default_secondary": None,
                "thinking_supported": False}
    # 下拉项：LLM 取 models、语音取 voices，统一成 [{value,label}]
    rows = preset["models"] or preset["voices"]
    options = [{"value": r.get("value", ""), "label": r.get("label") or r.get("value", "")}
               for r in rows if r.get("value")]
    return {
        "preset_key": preset["key"],
        "base_url": preset["base_url"],
        "options": options or None,
        # 主模型：LLM 是 chat_model、语音是 voice_id（同一个位置，两种叫法）
        "default_primary": preset["chat_model"] or preset["voice_id"] or None,
        "default_secondary": preset["work_model"] or None,
        "thinking_supported": preset["thinking_supported"],
    }


async def _decide_layout(
    db: AsyncSession, user_id: int, *,
    base_url: str,
    model: str | None,
    manual: str | None,
    preset_layout: str | None,
    probe: bool = True,
):
    """定这条厂商的 system 摆放：共享判定 + 「已保存的私网地址」这条授权线。

    私网地址只有在**已经保存过**时才允许去探（否则这个接口就是一台内网扫描器）。
    新建一条本机 llama-server 时它还没登记，于是退化成「按地址保守猜」——
    字面私网会得到 leading，正好是本地推理服务需要的。
    """
    from app.services.agent.base_url_registry import saved_private_hosts
    from app.services.agent.channel_layout import decide_system_layout

    return await decide_system_layout(
        base_url=base_url, model=model, manual=manual, preset_layout=preset_layout,
        probe=probe,
        allow_private_hosts=await saved_private_hosts(db, user_id) if probe else (),
    )


def _layout_columns(decision) -> dict:
    """判定结论 → 厂商行上的四列（时间列是 naive，与 created_at 同口径）"""
    checked_at = None
    if decision.checked_at:
        try:
            checked_at = datetime.fromisoformat(
                decision.checked_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            checked_at = None
    return {
        "system_layout": decision.layout,
        "system_layout_source": decision.source,
        "system_template_hash": decision.template_hash or None,
        "system_layout_checked_at": checked_at,
    }


def _new_provider(user_id: int, kind: str, preset: ProviderPreset, layout) -> UserApiProvider:
    """从内置预设接一家厂商（名称沿用预设的厂商名）"""
    return UserApiProvider(user_id=user_id, kind=kind, name=preset["label"],
                           **_preset_values(preset), **_layout_columns(layout))


def _reset_provider(provider: UserApiProvider, preset: ProviderPreset, layout) -> None:
    """按内置预设恢复地址/模型清单/能力（Key 不动，那是用户的东西；身份也不变）"""
    values = _preset_values(preset)
    values.pop("preset_key")
    values.update(_layout_columns(layout))
    for field, value in values.items():
        setattr(provider, field, value)
