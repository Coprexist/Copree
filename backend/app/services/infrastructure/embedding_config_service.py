"""
Embedding 配置持久化服务（system_settings.embedding_config）

- 加载：启动时读 DB → 解密 api_key → 填入缓存（settings 自动生效）
- 保存：管理 API 写入 → 加密 api_key → 更新 DB + 同步缓存（热生效）
- 恢复默认：清 DB 字段 + 清缓存（回到 env/默认值）

加密：api_key_encrypted 用 Fernet（与 smtp_config 一致），不落明文。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.infra_repo import InfraRepository, SQLAlchemyInfraRepository
from app.db_config_source import set_db_overrides, clear_db_overrides
from app.utils.crypto import encrypt_api_key, decrypt_api_key

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyInfraRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyInfraRepository(db_or_repo)
    return db_or_repo


def _refresh_settings() -> None:
    """重建全局 settings 实例（DB 覆盖热更新生效）。

    pydantic-settings 实例在创建时固定 source 快照，运行时改缓存后
    必须重建 Settings() 并替换模块级引用，现有 settings.xxx 读取才生效。
    """
    import app.config as config_module
    new_settings = config_module.Settings()
    config_module.settings = new_settings
    # 其他模块 `from app.config import settings` 拿到的是旧引用，无法全局替换；
    # 但后续读取走 settings.embedding_* 的大多是 `from app.config import settings`
    # 后模块级持有——为兼容，这里同时提供 get_db_override 直读通道（见 get_effective_config）。
    logger.info("🔄 settings 已重建（DB 配置覆盖生效）")

#: 允许前端图形化修改的配置键（第一类：热生效）
EDITABLE_KEYS = [
    "embedding_backend",     # disabled | ollama | api | local
    "embedding_base_url",
    "embedding_api_key",     # 加密存储
    "embedding_model",
    "embedding_dimension",
]


async def load_db_config(db: AsyncSession) -> None:
    """启动时加载 DB 覆盖进缓存（幂等；无覆盖则保持 env 生效）"""
    db = _ensure_repo(db)
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None or not row.embedding_config:
        return
    cfg = dict(row.embedding_config)
    # 解密 api_key（存的是加密值，缓存里放明文供 provider 用）
    enc = cfg.pop("api_key_encrypted", None)
    if enc:
        try:
            cfg["embedding_api_key"] = decrypt_api_key(enc)
        except Exception as e:
            logger.warning(f"解密 embedding api_key 失败: {e}")
    set_db_overrides(cfg)
    _refresh_settings()  # 重建 settings 实例，让 DB 覆盖对全局读取生效（启动加载路径）
    logger.info(f"🗄️ 已从 DB 加载 embedding 配置覆盖: {list(cfg.keys())}")


async def save_db_config(db: AsyncSession, values: dict) -> dict:
    """保存 embedding 配置（DB 持久化 + 缓存热更新）。返回保存后的配置。"""
    db = _ensure_repo(db)
    from app.models.system_settings import SystemSettings

    # 只接收允许的键
    clean = {k: v for k, v in values.items() if k in EDITABLE_KEYS}
    if not clean:
        raise ValueError("没有可保存的配置项")

    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)

    # 合并现有配置（保留未提交的键）
    merged = dict(row.embedding_config or {})
    for k, v in clean.items():
        merged[k] = v

    # api_key 单独加密存储
    if "embedding_api_key" in merged:
        raw_key = merged.pop("embedding_api_key")
        if raw_key:
            merged["api_key_encrypted"] = encrypt_api_key(str(raw_key))
        else:
            merged.pop("api_key_encrypted", None)

    row.embedding_config = merged
    await db.commit()

    # 同步缓存（api_key 用明文进缓存）
    cache_cfg = {k: v for k, v in merged.items() if k != "api_key_encrypted"}
    if "api_key_encrypted" in merged:
        try:
            cache_cfg["embedding_api_key"] = decrypt_api_key(merged["api_key_encrypted"])
        except Exception:
            pass
    set_db_overrides(cache_cfg)
    _refresh_settings()
    logger.info(f"💾 embedding 配置已保存: {list(clean.keys())}")
    return cache_cfg


async def clear_db_config(db: AsyncSession) -> dict:
    """恢复默认：清 DB 字段 + 清缓存（回到 env/默认值）"""
    db = _ensure_repo(db)
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is not None and row.embedding_config:
        row.embedding_config = None
        await db.commit()
    clear_db_overrides()
    _refresh_settings()
    logger.info("🗑️ embedding 配置已恢复默认（DB 覆盖清除）")
    return {}


async def check_dimension_consistency(db) -> list[str]:
    """启动自检：**ORM 列维度 / 生效配置 / 库里实际列维度**三者必须一致。

    为什么要有它：2026-09-25 线上事故——容器没有 EMBEDDING_DIMENSION（落到默认 1536），
    而四个向量列与 DB 配置都是 768，写入每次都以 `expected 1536 dimensions, not 768` 失败；
    向量只是"可选增强"，失败只进日志、记忆一直存不下来——静默故障必须变成响的。
    返回问题描述列表（空 = 一致）。
    """
    from sqlalchemy import text as sa_text

    from app.config import settings
    from app.db_config_source import get_db_override
    from app.models.memory import RoughMemory

    orm_dim = getattr(RoughMemory.__table__.c.embedding.type, "dim", None)
    configured = get_db_override("embedding_dimension") or settings.embedding_dimension
    actual = (await db.execute(sa_text(
        "select distinct atttypmod from pg_attribute "
        "where attrelid = 'rough_memories'::regclass and attname = 'embedding'"
    ))).scalar()

    problems: list[str] = []
    if orm_dim and configured and int(orm_dim) != int(configured):
        problems.append(f"ORM 列维度 {orm_dim} ≠ 生效配置 {configured}（列维度在 import 时定死，DB 覆盖改不了）")
    if orm_dim and actual and int(orm_dim) != int(actual):
        problems.append(f"ORM 列维度 {orm_dim} ≠ 库里实际列维度 {actual}")
    return problems


async def get_effective_config(db: AsyncSession) -> dict:
    """返回当前生效的 embedding 配置（DB 覆盖 + env 兜底，api_key 脱敏）"""
    db = _ensure_repo(db)
    from app.config import settings
    cfg = {
        "embedding_backend": settings.embedding_backend,
        "embedding_base_url": settings.embedding_base_url,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "embedding_api_key_set": bool(settings.embedding_api_key),
    }
    # 标注哪些来自 DB 覆盖
    from app.db_config_source import get_db_override
    cfg["source"] = {
        k: "db" if get_db_override(k) is not None else "env"
        for k in ["embedding_backend", "embedding_base_url", "embedding_model", "embedding_dimension"]
    }
    return cfg
