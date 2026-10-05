"""LLM 凭证解析 —— 用户 Key → 池 Key → 全局默认 base（唯一入口）。

世界 AI 与创建助手都要为「某个人」解析计费凭证：世界侧原本自己写了一份
（world_chat_service._resolve_world_credentials），再抄一份迟早分叉，这里收成一个口子。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def resolve_user_credentials(repo, user_id: int | None) -> tuple[str | None, str]:
    """返回 (api_key, api_base)；没有 Key 就是 (None, 全局默认 base)，让 LLM 层报清晰错误"""
    from app.config import settings
    from app.models.user import User

    api_key, api_base = None, settings.deepseek_base_url
    user = await repo.get(User, user_id) if user_id else None
    if user is None:
        return api_key, api_base
    try:
        from app.utils.crypto import decrypt_api_key
        if user.api_key_encrypted:
            return decrypt_api_key(user.api_key_encrypted), user.api_base_url or settings.deepseek_base_url
        from app.services.infrastructure.quota_service import find_best_pool_key
        pool_key = await find_best_pool_key(repo, user.id)
        if pool_key:
            return (decrypt_api_key(pool_key.api_key_encrypted),
                    pool_key.api_base_url or settings.deepseek_base_url)
    except Exception as e:
        # 密钥解密失败等：降级到无 Key（走全局默认 base）
        logger.warning(f"凭证解析降级 (user={user_id}): {e}")
    return api_key, api_base
