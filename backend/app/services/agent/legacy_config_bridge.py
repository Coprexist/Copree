"""旧字段 → 配置档：给还在提交裸 Key 的调用点留的最后一道桥。

两个裸 Key 档已经从解析链删除（docs/dev/api_config_chain.md §3.3）：
`users.api_base_url/api_key_encrypted` 与 `agents.api_base_url/api_key_encrypted`。
但界面上还有几处老表单在提交这两个值，直接丢弃会让用户「填了却不生效」——
那比明说没配更难查。

所以这里把这类写入**翻译成一条配置档**（厂商 + 档），不写回裸列：
地址进厂商、Key 进配置档，两者永远在同一条链上（R1「Key 必须长在配置上」）。

**阶段性文件**：前端切到配置档选择后，这个文件和两个裸列一起删。
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update

logger = logging.getLogger(__name__)

# 旧表单没有"配置档"概念，给它一个固定名字；同名再保存就是更新同一条
LEGACY_PROFILE_NAME = "我的配置"


async def upsert_profile(
    db, *, user_id: int, kind: str = "llm",
    base_url: str | None,
    api_key: str | None = None,
    primary_model: str | None = None,
    secondary_model: str | None = None,
    name: str = LEGACY_PROFILE_NAME,
    make_default: bool = False,
) -> int | None:
    """按 (地址, 名字) 找一条配置档；没有就建。返回 profile_id。

    api_key=None 表示"这次没提交 Key"（保留原值）；api_key="" 表示"要清掉"。
    只建配置档、不绑定——绑定是调用方的事（AI 级还是用户默认级不同）。
    """
    from app.models.api_profile import UserApiProvider, UserApiProfile
    from app.utils.crypto import encrypt_api_key

    base = (base_url or "").strip()
    if not base:
        return None

    provider = (await db.execute(
        select(UserApiProvider).where(
            UserApiProvider.user_id == user_id,
            UserApiProvider.kind == kind,
            UserApiProvider.base_url == base,
        ).order_by(UserApiProvider.id)
    )).scalars().first()
    if provider is None:
        provider = UserApiProvider(
            user_id=user_id, kind=kind, name=base, base_url=base,
            default_primary=primary_model, default_secondary=secondary_model,
        )
        db.add(provider)
        await db.flush()
    else:
        # 新填的型号只是补空，不覆盖用户已经保存的选择
        if primary_model and not provider.default_primary:
            provider.default_primary = primary_model
        if secondary_model and not provider.default_secondary:
            provider.default_secondary = secondary_model

    profile = (await db.execute(
        select(UserApiProfile).where(
            UserApiProfile.provider_id == provider.id,
            UserApiProfile.name == name,
        )
    )).scalars().first()
    if profile is None:
        profile = UserApiProfile(
            user_id=user_id, provider_id=provider.id, name=name,
            primary_model=primary_model, secondary_model=secondary_model,
        )
        db.add(profile)
        await db.flush()

    if api_key is not None:
        profile.api_key_encrypted = encrypt_api_key(api_key) if api_key else None
    if primary_model and not profile.primary_model:
        profile.primary_model = primary_model
    if secondary_model and not profile.secondary_model:
        profile.secondary_model = secondary_model

    if make_default:
        others = (await db.execute(
            select(UserApiProfile.id)
            .join(UserApiProvider, UserApiProvider.id == UserApiProfile.provider_id)
            .where(
                UserApiProfile.user_id == user_id,
                UserApiProvider.kind == kind,
                UserApiProfile.id != profile.id,
            )
        )).scalars().all()
        if others:
            await db.execute(
                update(UserApiProfile)
                .where(UserApiProfile.id.in_(others))
                .values(is_default=False)
            )
        profile.is_default = True

    await db.flush()
    logger.info(
        "旧字段写入已翻译成配置档: user=%s kind=%s profile=%s base=%s",
        user_id, kind, profile.id, base,
    )
    return profile.id
