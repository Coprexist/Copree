"""API 配置的启动自检：只 WARN，不阻塞（docs/dev/api_config_chain.md §7）。

为什么单列一个模块：这些检查要能被测试直接调（造脏数据 → 断言问题列表），
塞进 bootstrap 的 lifespan 就只能靠读日志验证。bootstrap 只负责「调一次 + 打日志」。

**静态、不联网**：启动时探测每家地址的连通性会把启动拖成分钟级；
连通性用管理台的「测试连接」（有超时与并发控制）。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 每次启动记一条 info：避免有人以为系统预设就是"当前生效的配置"
INFO_SYSTEM_PRESETS = (
    "系统预设只是模板，不参与运行时解析（真正生效的是每个人的 配置档 / 池 / 默认档）"
)


async def _leftover_bare_columns(db) -> list[str]:
    """迁移 0131 应已清空的两组列：再非空说明有人绕过写入（或迁移没跑）。"""
    rows = (await db.execute(text(
        "SELECT 'users' AS tbl, id FROM users WHERE "
        "coalesce(api_base_url, '') <> '' OR coalesce(api_key_encrypted, '') <> '' "
        "OR coalesce(global_chat_model, '') <> '' OR coalesce(global_work_model, '') <> '' "
        "UNION ALL "
        "SELECT 'agents', id FROM agents WHERE "
        "coalesce(api_base_url, '') <> '' OR coalesce(api_key_encrypted, '') <> '' "
        "OR coalesce(chat_model, '') <> '' OR coalesce(work_model, '') <> ''"
    ))).all()
    return [
        f"{table} #{row_id} 还有裸 Key/模型列（迁移 0131 应已清空，说明有写入绕过配置档）"
        for table, row_id in rows
    ]


async def _default_profile_anomalies(db) -> list[str]:
    """默认档：有配置档却没有默认（解析会掉到 ② 池）、或默认多于一条（取哪条看运气）。

    **没配过的人不报**——143 个用户里 114 个从没配过 API，那不是错。
    """
    problems: list[str] = []
    duplicates = (await db.execute(text(
        "SELECT p.user_id, count(*) FROM user_api_profiles p "
        "JOIN user_api_providers pr ON pr.id = p.provider_id "
        "WHERE pr.kind = 'llm' AND p.is_default "
        "GROUP BY p.user_id HAVING count(*) > 1"
    ))).all()
    problems.extend(
        f"用户 #{user_id} 有 {count} 条默认配置档（应当只有一条，取哪条取决于排序）"
        for user_id, count in duplicates
    )

    missing = (await db.execute(text(
        "SELECT DISTINCT p.user_id FROM user_api_profiles p "
        "JOIN user_api_providers pr ON pr.id = p.provider_id "
        "WHERE pr.kind = 'llm' AND NOT EXISTS ("
        "  SELECT 1 FROM user_api_profiles d "
        "  JOIN user_api_providers dp ON dp.id = d.provider_id "
        "  WHERE d.user_id = p.user_id AND dp.kind = 'llm' AND d.is_default"
        ")"
    ))).all()
    problems.extend(
        f"用户 #{user_id} 有配置档但没有默认档（③ 这一层会永远跳过）"
        for (user_id,) in missing
    )
    return problems


async def _profile_provider_mismatch(db) -> list[str]:
    """被 AI 绑定为 LLM 的配置档，其厂商不存在或不是 LLM：这条绑定永远不生效。

    不能笼统地查「provider.kind <> llm」：语音档与 LLM 档同表，指向 voice 厂商是**正常**的
    （2026-11-25 在生产库跑出 3 条假阳性才改的这条判据）。
    """
    rows = (await db.execute(text(
        "SELECT p.id, p.name FROM user_api_profiles p "
        "LEFT JOIN user_api_providers pr ON pr.id = p.provider_id "
        "WHERE p.id IN (SELECT llm_profile_id FROM agents "
        "               WHERE llm_profile_id IS NOT NULL) "
        "AND (pr.id IS NULL OR pr.kind <> 'llm')"
    ))).all()
    return [
        f"配置档 #{profile_id}「{name}」指向的厂商不存在或不是 LLM，永远不会被解析到"
        for profile_id, name in rows
    ]


async def collect_api_config_problems(db) -> list[str]:
    """扫一遍配置卫生，返回人话问题列表（空 = 没问题）。不改数据、不联网。"""
    problems: list[str] = []
    for check in (
        _leftover_bare_columns,
        _default_profile_anomalies,
        _profile_provider_mismatch,
    ):
        try:
            problems.extend(await check(db))
        except Exception as e:  # noqa: BLE001 —— 自检自己不能把启动搞挂
            logger.warning("配置自检项 %s 执行失败（跳过）: %s", check.__name__, e)
    return problems
