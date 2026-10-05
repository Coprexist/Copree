"""
AI 对话日志服务
保存、查询、清理 AI 完整对话记录
"""
import difflib
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete, func, text

from app.models.conversation_log import ConversationLogConfig, ConversationLog
from app.repositories.content_repo import ContentRepository
from app.utils.pure.cache_stats import cache_hit_rate_pct
from app.utils.pure.conversation_log import (
    LogRetention, cap_per_state, plan_log_trim,
)
from app.utils.pure.state_stack import frame_of_state_key, state_frame_of, state_key_of

logger = logging.getLogger(__name__)



# ── 用量账 ──

_USAGE_FIELDS = (
    "total_tokens", "prompt_tokens", "completion_tokens",
    "reasoning_tokens", "cached_tokens", "api_calls",
)


async def accumulate_usage_daily(
    content_repo: ContentRepository,
    agent_id: int | None,
    user_id: int | None,
    model: str | None,
    token_usage: dict | None,
) -> None:
    """把一次 LLM 调用的用量累加到当天那一行

    与 _trim_old_logs 相反：这里只累加、从不删除，对话日志被裁掉也带不走它。
    agent_id / user_id 非正数一律归 0（见 UsageDaily 的列注释），
    这样世界 AI（agent_id 为空、记账人记 user_id）和普通 AI 落进同一个唯一键。
    """
    usage = token_usage or {}
    values = {f: int(usage.get(f) or 0) for f in _USAGE_FIELDS}
    # 没真调模型、或调用没带回用量的轮次不占行，免得把「有记录的天数」灌水
    if values["api_calls"] <= 0 and values["total_tokens"] <= 0:
        return

    params = {
        "stat_date": datetime.now(timezone.utc).date(),
        "agent_id": agent_id if agent_id and agent_id > 0 else 0,
        "user_id": user_id if user_id and user_id > 0 else 0,
        "model": model or "",
        **values,
    }
    # ON CONFLICT 在 PG 与 SQLite 上语法一致，唯一键由 uq_usage_daily_key 保证
    await content_repo.execute(text("""
        INSERT INTO usage_daily (
            stat_date, agent_id, user_id, model,
            total_tokens, prompt_tokens, completion_tokens,
            reasoning_tokens, cached_tokens, api_calls, updated_at
        ) VALUES (
            :stat_date, :agent_id, :user_id, :model,
            :total_tokens, :prompt_tokens, :completion_tokens,
            :reasoning_tokens, :cached_tokens, :api_calls, now()
        )
        ON CONFLICT (stat_date, agent_id, user_id, model) DO UPDATE SET
            total_tokens = usage_daily.total_tokens + EXCLUDED.total_tokens,
            prompt_tokens = usage_daily.prompt_tokens + EXCLUDED.prompt_tokens,
            completion_tokens = usage_daily.completion_tokens + EXCLUDED.completion_tokens,
            reasoning_tokens = usage_daily.reasoning_tokens + EXCLUDED.reasoning_tokens,
            cached_tokens = usage_daily.cached_tokens + EXCLUDED.cached_tokens,
            api_calls = usage_daily.api_calls + EXCLUDED.api_calls,
            updated_at = now()
    """), params)


def _as_date(value: datetime | None):
    """聚合表是天粒度，调用方传来的精确时刻按 UTC 日期比对（边界那一天整天计入）"""
    return value.date() if isinstance(value, datetime) else value


# ── 保存 ──

async def save_conversation_log(
    content_repo: ContentRepository,
    agent_id: int,
    messages: list[dict],
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    token_usage: dict | None = None,
    has_output: bool = False,
    model: str | None = None,
    thinking_enabled: bool = False,
    user_id: int | None = None,
) -> int | None:
    """保存一次完整对话，自动清理超出限制的旧记录

    user_id：记账人（世界 AI 用量传世界主人；普通 AI 记录留空按 agent.owner_id 归属）。
    """
    try:
        log = ConversationLog(
            agent_id=agent_id,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
            conversation_type=conversation_type,
            messages=messages,
            message_count=len(messages),
            token_usage=token_usage,
            has_output=has_output,
            model=model,
            thinking_enabled=thinking_enabled,
            # 状态身份在这里算一次存下：裁剪按它分桶、列表按它归堆，都不必回读整份 messages
            state_key=state_key_of(state_frame_of(messages)),
        )
        content_repo.add(log)
        await content_repo.flush()
        await content_repo.refresh(log)

        # 用量账先落，且单独兜异常：它一旦失败不该连累日志本身，
        # 反过来日志被裁掉也带不走它
        try:
            await accumulate_usage_daily(content_repo, agent_id, user_id, model, token_usage)
        except Exception as e:
            logger.error(f"累加用量日聚合失败 (agent={agent_id}): {e}")

        # 清理超出限制的旧记录
        await _trim_old_logs(content_repo, agent_id)

        return log.id
    except Exception as e:
        logger.error(f"保存对话日志失败 (agent={agent_id}): {e}")
        return None


# 一次裁剪最多看多少行：每 AI 的账 = 各状态保留数之和（几十到几百），留足余量就够，
# 不为「理论上无限的状态数」把整表读进内存
TRIM_SCAN_LIMIT = 2000


async def _trim_old_logs(content_repo: ContentRepository, agent_id: int):
    """按状态分桶保留：判定是纯函数（utils/pure/conversation_log.plan_log_trim），这里只做 IO。

    只取 id / state_key / created_at 三列：messages 是整份请求体，按它分组等于把几十万字
    搬进内存只为算个分组。
    """
    retention = await _get_retention(content_repo, agent_id)
    result = await content_repo.execute(
        select(ConversationLog.id, ConversationLog.state_key, ConversationLog.created_at)
        .where(ConversationLog.agent_id == agent_id)
        .order_by(ConversationLog.created_at.desc(), ConversationLog.id.desc())
        .limit(TRIM_SCAN_LIMIT)
    )
    rows = [{"id": r[0], "state_key": r[1], "created_at": r[2]} for r in result.all()]
    old_ids = plan_log_trim(rows, retention, now=datetime.now(timezone.utc).replace(tzinfo=None))
    if old_ids:
        await content_repo.execute(
            delete(ConversationLog).where(ConversationLog.id.in_(old_ids))
        )
        logger.info(f"清理 agent={agent_id} 的 {len(old_ids)} 条旧对话日志（{_retention_text(retention)}）")


async def _get_agent_log_limit(content_repo: ContentRepository, agent_id: int) -> int:
    """获取某个 AI 的对话日志保留上限"""
    # 先查 per-AI 设置
    from app.models.agent import Agent
    agent_result = await content_repo.execute(
        select(Agent.conversation_logs_limit).where(Agent.id == agent_id)
    )
    agent_limit = agent_result.scalar_one_or_none()
    if agent_limit is not None:
        return agent_limit

    # 回退到全局上限
    config = await _get_config(content_repo)
    return config.max_conversation_logs if config else 20


# 保留策略的旋钮（读、写、校验共用这一份；默认值本身在 utils/pure/conversation_log）
# 路由凭 KEEP_KNOB_NAMES 判断「这次提交了哪些旋钮」（显式传 null = 清除回默认）
_KEEP_KNOBS = {
    "idle_keep": "沉寂状态保留数",
    "aged_keep": "老旧状态保留数",
    "idle_days": "沉寂阈值天数",
    "aged_days": "老旧阈值天数",
}
KEEP_KNOB_NAMES = tuple(_KEEP_KNOBS)


async def _get_retention(content_repo: ContentRepository, agent_id: int) -> LogRetention:
    """这个 AI 的保留策略（唯一入口）：活跃档可按 AI 覆盖，其余档来自全局配置。

    配置里为 NULL 的旋钮**不传**（让值对象的默认生效）——默认值只有
    utils/pure/conversation_log 一处，别在这里再抄一份。
    """
    config = await _get_config(content_repo)
    values = {"active_keep": await _get_agent_log_limit(content_repo, agent_id)}
    for knob in _KEEP_KNOBS:
        values[knob] = getattr(config, knob, None)
    return LogRetention(**{k: v for k, v in values.items() if v is not None})


def _retention_text(retention: LogRetention) -> str:
    """保留口径说人话（日志与界面同一句的来源）"""
    return (f"活跃（{retention.idle_days} 天内动过）{retention.active_keep} 条、"
            f"沉寂 {retention.idle_keep} 条、超 {retention.aged_days} 天 {retention.aged_keep} 条")


# ── 配置 ──

async def _get_config(content_repo: ContentRepository) -> ConversationLogConfig:
    """获取全局配置（保证返回有效对象）"""
    result = await content_repo.execute(select(ConversationLogConfig).where(ConversationLogConfig.id == 1))
    config = result.scalar_one_or_none()
    if config is None:
        config = ConversationLogConfig(id=1)
        content_repo.add(config)
        await content_repo.flush()
    return config


async def get_config_dict(content_repo: ContentRepository) -> dict:
    """获取全局配置（字典格式，供 API 返回）"""
    config = await _get_config(content_repo)
    return {
        "max_conversation_logs": config.max_conversation_logs,
        "default_user_conversation_logs": config.default_user_conversation_logs,
        "default_user_log_access": config.default_user_log_access,
        "default_delay_reply_enabled": config.default_delay_reply_enabled,
        "compression_threshold": getattr(config, 'compression_threshold', 60) or 60,
        # 两个系数旋钮：None = 未设置（用代码默认），前端据此显示占位
        "idle_threshold_percent": getattr(config, "idle_threshold_percent", None),
        "compress_target_percent": getattr(config, "compress_target_percent", None),
        # 保留策略的旋钮（同上：None = 用代码默认）
        **{knob: getattr(config, knob, None) for knob in _KEEP_KNOBS},
    }


async def update_config(
    content_repo: ContentRepository,
    updated_by: int,
    max_conversation_logs: int | None = None,
    default_user_conversation_logs: int | None = None,
    default_user_log_access: bool | None = None,
    default_delay_reply_enabled: bool | None = None,
    compression_threshold: int | None = None,
    idle_threshold_percent: int | None = None,
    compress_target_percent: int | None = None,
    knobs: dict[str, int | None] | None = None,
) -> dict:
    """更新全局配置

    knobs：保留策略旋钮。**出现在这个 dict 里**才处理——值为 None 表示「清除该旋钮、
    回代码默认」（前端留空就是这个意思）；没出现的旋钮一律不动。这样「留空 = 用默认」
    才真的成立，而不是留空被当成"不改"、设过之后再也回不去。
    """
    config = await _get_config(content_repo)

    if max_conversation_logs is not None:
        if max_conversation_logs < 1:
            raise ValueError("全局上限至少为 1")
        config.max_conversation_logs = max_conversation_logs
    if default_user_conversation_logs is not None:
        if default_user_conversation_logs < 1:
            raise ValueError("用户默认值至少为 1")
        if default_user_conversation_logs > config.max_conversation_logs:
            raise ValueError(f"用户默认值不能超过全局上限 {config.max_conversation_logs}")
        config.default_user_conversation_logs = default_user_conversation_logs
    if default_user_log_access is not None:
        config.default_user_log_access = default_user_log_access
    if default_delay_reply_enabled is not None:
        config.default_delay_reply_enabled = default_delay_reply_enabled
    if compression_threshold is not None:
        if compression_threshold < 1 or compression_threshold > 100:
            raise ValueError("压缩阈值必须在 1-100 之间")
        config.compression_threshold = compression_threshold
    for field, value, label in (
        ("idle_threshold_percent", idle_threshold_percent, "冷阈值系数"),
        ("compress_target_percent", compress_target_percent, "压后目标"),
    ):
        if value is None:
            continue
        if value < 1 or value > 99:   # 0/100 会贴到区间端点，等于没有这一档
            raise ValueError(f"{label}必须在 1-99 之间")
        setattr(config, field, value)

    # 保留策略的旋钮（条数上限 500、天数上限 365，与前端输入框一致）
    for field, label in _KEEP_KNOBS.items():
        if knobs is None or field not in knobs:
            continue
        value = knobs[field]
        if value is None:          # 清除：回代码默认（默认只存在 utils/pure/conversation_log）
            setattr(config, field, None)
            continue
        hi = 365 if field.endswith("_days") else 500
        if value < 1 or value > hi:
            raise ValueError(f"{label}必须在 1-{hi} 之间")
        setattr(config, field, value)

    config.updated_by = updated_by
    config.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await content_repo.flush()
    return await get_config_dict(content_repo)


# ── 用户设置 ──

async def get_user_log_limit(content_repo: ContentRepository, user_id: int) -> dict:
    """获取用户的对话日志保留设置"""
    from app.models.user import User
    config = await _get_config(content_repo)

    result = await content_repo.execute(
        select(User.conversation_logs_limit).where(User.id == user_id)
    )
    user_limit = result.scalar_one_or_none()

    effective = user_limit if user_limit is not None else config.default_user_conversation_logs
    max_allowed = config.max_conversation_logs

    return {
        "user_limit": user_limit,
        "effective": effective,
        "max_allowed": max_allowed,
        "system_default": config.default_user_conversation_logs,
    }


async def update_user_log_limit(content_repo: ContentRepository, user_id: int, limit: int) -> dict:
    """更新用户的对话日志保留数"""
    config = await _get_config(content_repo)

    if limit < 1:
        raise ValueError("保留数至少为 1")
    if limit > config.max_conversation_logs:
        raise ValueError(f"不能超过管理员设定的系统上限 {config.max_conversation_logs}")

    from app.models.user import User
    result = await content_repo.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("用户不存在")

    user.conversation_logs_limit = limit
    await content_repo.flush()
    return await get_user_log_limit(content_repo, user_id)


# ── 查询 ──

async def get_agent_logs(
    content_repo: ContentRepository,
    agent_id: int,
    user_id: int | None = None,
    is_admin: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """获取 AI 的对话日志列表（摘要，不含完整 messages）

    条数已由裁剪定好（每段状态各留几条，见 utils/pure/conversation_log），这里照实读出来；
    普通用户再多一道「每段状态最多看几条」（per-user 覆盖 / 全局默认 default_user_conversation_logs），
    管理员不受它限制——保留策略本身已经封过顶，再叠一道只会让分组看起来残缺。
    """
    if not is_admin:
        if not await _user_can_view_agent_logs(content_repo, agent_id, user_id):
            raise ValueError("无权查看此 AI 的对话日志")

    result = await content_repo.execute(
        select(ConversationLog)
        .where(ConversationLog.agent_id == agent_id)
        .order_by(ConversationLog.created_at.desc(), ConversationLog.id.desc())
        .limit(max(1, int(limit)))
        .offset(offset)
    )
    logs = list(result.scalars().all())
    if not is_admin:
        user_limit = await get_user_log_limit(content_repo, user_id)
        logs = cap_per_state(logs, lambda log: log.state_key or "", int(user_limit["effective"]))

    items = [_log_to_summary(log) for log in logs]
    await _fill_group_labels(content_repo, items)
    return items


async def get_log_detail(
    content_repo: ContentRepository,
    log_id: int,
    user_id: int | None = None,
    is_admin: bool = False,
) -> dict | None:
    """获取单条对话日志的完整内容（含 messages）"""
    result = await content_repo.execute(
        select(ConversationLog).where(ConversationLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if log is None:
        return None

    if not is_admin:
        if not await _user_can_view_agent_logs(content_repo, log.agent_id, user_id):
            raise ValueError("无权查看此对话日志")

    detail = _log_to_detail(log)
    await _fill_group_labels(content_repo, [detail])
    return detail


async def get_log_delta(
    content_repo: ContentRepository,
    log_id: int,
    prev_id: int | None = None,
    user_id: int | None = None,
    is_admin: bool = False,
) -> dict | None:
    """这段状态下「这一条比上一条多了什么」（增量视图用）；日志不存在返回 None。

    prev_id 由调用方从同一段状态的历史里挑：列表已经带回每条的状态帧身份，前端点开时
    就知道上一条是谁，后端不必逐条回读 messages 去找。两条状态帧不一致时不给增量——
    跨状态的请求体本来就不同源，差值没有意义。
    """
    result = await content_repo.execute(
        select(ConversationLog).where(ConversationLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if log is None:
        return None
    if not is_admin and not await _user_can_view_agent_logs(content_repo, log.agent_id, user_id):
        raise ValueError("无权查看此对话日志")

    no_delta = {"prev_log_id": None, "ops": [], "added_count": 0, "removed_count": 0, "shared_count": 0}
    if not prev_id or prev_id == log_id:
        return no_delta

    prev_result = await content_repo.execute(
        select(ConversationLog).where(ConversationLog.id == prev_id)
    )
    prev = prev_result.scalar_one_or_none()
    if prev is None or prev.agent_id != log.agent_id:
        return no_delta
    if state_frame_of(prev.messages or []) != state_frame_of(log.messages or []):
        return no_delta
    return {"prev_log_id": prev.id, **_diff_messages(prev.messages or [], log.messages or [])}


async def attach_mention_names(repo, detail: dict) -> dict:
    """给日志详情补一张「<@!id> → 名字」的表，前端把机器令牌显示成人名。

    只查真正出现过的 id（一般 0~2 个）：导出那条路一直这么干，详情这边漏了，
    于是同一个 <@!41> 在导出里是「@浮生」，在详情里还是原始令牌。
    """
    from app.utils.message_serializer import mention_names

    contents = [
        msg["content"] for msg in (detail.get("messages") or [])
        if isinstance(msg, dict) and isinstance(msg.get("content"), str)
    ]
    detail["mention_names"] = await mention_names(repo, contents)
    return detail


# ── Token 用量聚合查询 ──

def _with_cache_hit_rate(rows: list[dict]) -> list[dict]:
    """给用量行补命中率。口径只在 cache_stats 里定：面板、世界、对话三处各算一遍必然对不上"""
    for row in rows:
        row["cache_hit_rate_pct"] = cache_hit_rate_pct(row.get("prompt_tokens"), row.get("cached_tokens"))
    return rows


def _scope_clause(scope: str) -> str | None:
    """用量账的两个世界：agent_id>0 = 居民 AI；agent_id=0 = 群视界 AI（世界 AI 没有 agent 行，记账归 0）。

    all 不加条件，保持「全站」的旧口径；控制台分成两个版面后各自只取自己那一半。
    """
    return {"residents": "ud.agent_id > 0", "worlds": "ud.agent_id = 0"}.get(scope)


async def get_user_agents_token_summary(
    content_repo: ContentRepository,
    user_id: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """获取用户所有 AI 的 token 消耗汇总（按 AI+模型分组）"""
    where_clauses = ["ag.owner_id = :user_id"]
    params: dict = {"user_id": user_id}
    if start_date:
        where_clauses.append("ud.stat_date >= :start_date")
        params["start_date"] = _as_date(start_date)
    if end_date:
        where_clauses.append("ud.stat_date <= :end_date")
        params["end_date"] = _as_date(end_date)
    where_sql = " AND ".join(where_clauses)

    # 世界 AI 那半边的区间口径与普通 AI 对齐：原先它漏了日期条件，
    # 不管选几天都把历史全算进去
    world_clauses = ["ud.agent_id = 0", "ud.user_id = :user_id"]
    if start_date:
        world_clauses.append("ud.stat_date >= :start_date")
    if end_date:
        world_clauses.append("ud.stat_date <= :end_date")
    world_sql = " AND ".join(world_clauses)

    stmt = text(f"""
        SELECT
            ud.agent_id,
            ag.name AS agent_name,
            NULLIF(ud.model, '') AS model,
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS total_calls
        FROM usage_daily ud
        JOIN agents ag ON ag.id = ud.agent_id
        WHERE {where_sql}
        GROUP BY ud.agent_id, ag.name, ud.model

        UNION ALL

        -- 世界 AI 用量：记账人 = user_id（世界 AI 表单的世界主人），没有 agent 行 → 虚拟「群视界 agent」
        SELECT
            -1 AS agent_id,
            '群视界 agent' AS agent_name,
            NULLIF(ud.model, '') AS model,
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS total_calls
        FROM usage_daily ud
        WHERE {world_sql}
        GROUP BY ud.model
        ORDER BY total_tokens DESC
    """)
    result = await content_repo.execute(stmt, params)
    rows = result.mappings().all()
    return _with_cache_hit_rate([dict(r) for r in rows])


async def get_agent_token_daily(
    content_repo: ContentRepository,
    agent_id: int,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    user_id: int | None = None,
) -> list[dict]:
    """获取单个 AI 每日 token 消耗分布（agent_id=-1 = 群视界 agent 虚拟条目：按记账人 + agent_id 空）"""
    if agent_id == -1:
        where_clauses = ["ud.user_id = :user_id", "ud.agent_id = 0"]
        params: dict = {"user_id": user_id}
    else:
        where_clauses = ["ud.agent_id = :agent_id"]
        params: dict = {"agent_id": agent_id}
    if start_date:
        where_clauses.append("ud.stat_date >= :start_date")
        params["start_date"] = _as_date(start_date)
    if end_date:
        where_clauses.append("ud.stat_date <= :end_date")
        params["end_date"] = _as_date(end_date)
    where_sql = " AND ".join(where_clauses)

    stmt = text(f"""
        SELECT
            ud.stat_date AS date,
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS request_count
        FROM usage_daily ud
        WHERE {where_sql}
        GROUP BY ud.stat_date
        ORDER BY date ASC
    """)
    result = await content_repo.execute(stmt, params)
    rows = result.mappings().all()
    return _with_cache_hit_rate([dict(r) for r in rows])


async def get_admin_global_token_daily(
    content_repo: ContentRepository,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    scope: str = "all",
) -> list[dict]:
    """获取全站每日 token 消耗分布（含世界 AI）

    与按 AI 的每日曲线同一形状，只是不筛 agent：控制台那条「全站」曲线原先拿
    第一个 AI 的数据顶替，画出来的根本不是全站。
    """
    where_clauses = []
    params: dict = {}
    clause = _scope_clause(scope)
    if clause:
        where_clauses.append(clause)
    if start_date:
        where_clauses.append("ud.stat_date >= :start_date")
        params["start_date"] = _as_date(start_date)
    if end_date:
        where_clauses.append("ud.stat_date <= :end_date")
        params["end_date"] = _as_date(end_date)
    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = "WHERE " + where_sql

    stmt = text(f"""
        SELECT
            ud.stat_date AS date,
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS request_count
        FROM usage_daily ud
        {where_sql}
        GROUP BY ud.stat_date
        ORDER BY date ASC
    """)
    result = await content_repo.execute(stmt, params)
    rows = result.mappings().all()
    return _with_cache_hit_rate([dict(r) for r in rows])


async def get_admin_global_token_stats(
    content_repo: ContentRepository,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    scope: str = "all",
) -> dict:
    """获取全站 token 消耗总览（scope：all 全站 / residents 居民 AI / worlds 群视界 AI）"""
    where_clauses = []
    params: dict = {}
    clause = _scope_clause(scope)
    if clause:
        where_clauses.append(clause)
    if start_date:
        where_clauses.append("ud.stat_date >= :start_date")
        params["start_date"] = _as_date(start_date)
    if end_date:
        where_clauses.append("ud.stat_date <= :end_date")
        params["end_date"] = _as_date(end_date)
    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = "WHERE " + where_sql

    # LEFT JOIN：世界 AI 用 agent_id=0 记账（没有 agent 行），join 不上但用量要算进全站；
    # 人数与 AI 数仍然只数得到真实 agent 的那些，与改前口径一致
    stmt = text(f"""
        SELECT
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS total_calls,
            COUNT(DISTINCT ag.id) AS unique_agents,
            COUNT(DISTINCT ag.owner_id) AS unique_users
        FROM usage_daily ud
        LEFT JOIN agents ag ON ag.id = ud.agent_id
        {where_sql}
    """)
    result = await content_repo.execute(stmt, params)
    row = result.mappings().first()
    if row:
        d = dict(row)
        d["total_tokens"] = d["total_tokens"] or 0
        d["prompt_tokens"] = d["prompt_tokens"] or 0
        d["completion_tokens"] = d["completion_tokens"] or 0
        d["reasoning_tokens"] = d["reasoning_tokens"] or 0
        d["cached_tokens"] = d["cached_tokens"] or 0
        d["total_calls"] = d["total_calls"] or 0
        d["unique_agents"] = d["unique_agents"] or 0
        d["unique_users"] = d["unique_users"] or 0
        d["cache_hit_rate_pct"] = cache_hit_rate_pct(d["prompt_tokens"], d["cached_tokens"])
        return d
    return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "cached_tokens": 0, "total_calls": 0,
            "unique_agents": 0, "unique_users": 0, "cache_hit_rate_pct": 0.0}


async def get_admin_users_token_summary(
    content_repo: ContentRepository,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """获取按用户分组的 token 消耗明细"""
    where_clauses = []
    params: dict = {}
    if start_date:
        where_clauses.append("ud.stat_date >= :start_date")
        params["start_date"] = _as_date(start_date)
    if end_date:
        where_clauses.append("ud.stat_date <= :end_date")
        params["end_date"] = _as_date(end_date)
    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = "WHERE " + where_sql

    stmt = text(f"""
        SELECT
            u.id AS user_id,
            u.username,
            ud.agent_id,
            ag.name AS agent_name,
            COALESCE(SUM(ud.total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(ud.prompt_tokens), 0)::bigint AS prompt_tokens,
            COALESCE(SUM(ud.completion_tokens), 0)::bigint AS completion_tokens,
            COALESCE(SUM(ud.reasoning_tokens), 0)::bigint AS reasoning_tokens,
            COALESCE(SUM(ud.cached_tokens), 0)::bigint AS cached_tokens,
            COALESCE(SUM(ud.api_calls), 0)::bigint AS total_calls
        FROM usage_daily ud
        JOIN agents ag ON ag.id = ud.agent_id
        JOIN users u ON u.id = ag.owner_id
        {where_sql}
        GROUP BY u.id, u.username, ud.agent_id, ag.name
        ORDER BY u.id, total_tokens DESC
    """)
    result = await content_repo.execute(stmt, params)
    rows = result.mappings().all()
    return _with_cache_hit_rate([dict(r) for r in rows])


# ── 权限 ──

async def _user_can_view_agent_logs(content_repo: ContentRepository, agent_id: int, user_id: int | None) -> bool:
    """检查用户是否可以查看某 AI 的对话日志"""
    if user_id is None:
        return False

    from app.models.agent import Agent
    agent_result = await content_repo.execute(
        select(Agent.owner_id, Agent.user_can_view_logs).where(Agent.id == agent_id)
    )
    agent_row = agent_result.one_or_none()
    if agent_row is None:
        return False

    owner_id, per_ai_flag = agent_row

    # AI 的 owner 始终可查看
    if owner_id == user_id:
        return True

    # 检查 per-AI 开关
    if per_ai_flag is not None:
        return per_ai_flag

    # 回退到全局默认
    config = await _get_config(content_repo)
    return config.default_user_log_access


# ── Agent 管理 ──

async def get_agent_log_settings(content_repo: ContentRepository, agent_id: int) -> dict:
    """获取某 AI 的日志设置"""
    from app.models.agent import Agent
    result = await content_repo.execute(
        select(
            Agent.conversation_logs_limit,
            Agent.user_can_view_logs,
        ).where(Agent.id == agent_id)
    )
    row = result.one_or_none()
    if row is None:
        raise ValueError("AI 不存在")

    config = await _get_config(content_repo)
    return {
        "agent_id": agent_id,
        "conversation_logs_limit": row[0],
        "user_can_view_logs": row[1],
        "effective_limit": row[0] if row[0] is not None else config.max_conversation_logs,
        "effective_user_access": row[1] if row[1] is not None else config.default_user_log_access,
        "system_max": config.max_conversation_logs,
        "system_default_access": config.default_user_log_access,
    }


async def update_agent_log_settings(
    content_repo: ContentRepository,
    agent_id: int,
    conversation_logs_limit: int | None = None,
    user_can_view_logs: bool | None = None,
) -> dict:
    """更新某 AI 的日志设置"""
    from app.models.agent import Agent
    config = await _get_config(content_repo)

    result = await content_repo.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise ValueError("AI 不存在")

    if conversation_logs_limit is not None:
        if conversation_logs_limit < 1:
            raise ValueError("保留数至少为 1")
        if conversation_logs_limit > config.max_conversation_logs:
            raise ValueError(f"不能超过系统上限 {config.max_conversation_logs}")
        agent.conversation_logs_limit = conversation_logs_limit

    if user_can_view_logs is not None:
        agent.user_can_view_logs = user_can_view_logs

    await content_repo.flush()
    return await get_agent_log_settings(content_repo, agent_id)


# ── 内部工具 ──

# 一轮对话的结局：报错 / 被收尾（工具轮次用尽、超时收尾）/ 无输出 / 正常。
# 列表按它分开显示——截断、空转、没说话的轮次跟正常回复长得一样，混在一起就没法挑出来修。
_STATUS_MARKS = (
    ("error", ("异常中断", "Traceback", "执行失败", "模型调用失败")),
    ("wrapup", ("[本轮收尾]",)),
)


def _run_status(messages: list[dict], has_output: bool) -> str:
    """判这一轮的结局：只看 AI 侧留下的字（工具返回也算），命中标记就按标记算。

    边界 = **最后一条 user 消息之后**。账本里会长住历史轮次的工具失败
    （"[本轮工具] xxx(失败…)"、某次 run_script 的 Traceback），扫整份 messages
    等于把「前面失败过」算成「这轮失败」——线上一条 687 条的长会话就是这么被标红的。
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    start = 0
    for i in range(len(msgs) - 1, -1, -1):     # 找本轮起点：最后一条 user 之后
        if msgs[i].get("role") == "user":
            start = i + 1
            break
    text = "\n".join(
        str(m.get("content") or "") for m in msgs[start:]
        if m.get("role") in ("assistant", "tool")
    )
    for status, marks in _STATUS_MARKS:
        if any(mark in text for mark in marks):
            return status
    return "ok" if has_output else "no_output"


def _message_key(msg) -> str:
    """一条消息的内容指纹：键序不影响比对，同内容就算同一条"""
    return json.dumps(msg, sort_keys=True, ensure_ascii=False, default=str)


def _diff_messages(prev: list[dict], current: list[dict]) -> dict:
    """两条请求体的「改变量」——按内容对齐后，多出来的、没了的、没动的。

    不能按公共前缀算：请求体不是纯追加，中间那几块注入（相关记忆、状态摘要、当前时间）
    每次调用都会变，实测 199 条里只有第 0 条对得上，前缀法会把整份都算成新的。
    按内容对齐则内容相同就算没变（挪了位置也算）。ops 保持原顺序，前端才能把
    「这里少了这几条、那里多了这几条」照着位置摆出来——有增有减，只给新增那一截是看不全的。
    """
    ops: list[dict] = []
    added = removed = shared = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=[_message_key(m) for m in prev or []],
        b=[_message_key(m) for m in current or []],
        autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            shared += i2 - i1
            ops.append({"tag": "equal", "count": i2 - i1})
        elif tag == "insert":
            added += j2 - j1
            ops.append({"tag": "insert", "messages": (current or [])[j1:j2]})
        elif tag == "delete":
            removed += i2 - i1
            ops.append({"tag": "delete", "messages": (prev or [])[i1:i2]})
        else:  # replace：同一处既少了又多了，分开摆，别让前端猜
            added += j2 - j1
            removed += i2 - i1
            ops.append({
                "tag": "replace",
                "removed": (prev or [])[i1:i2],
                "added": (current or [])[j1:j2],
            })
    return {"ops": ops, "added_count": added, "removed_count": removed, "shared_count": shared}


# 帧身份的读法只有一处：以写入时算好的列为准，列为空（迁移前的老行）退回现算一次。
# 列表与详情共用它，否则同一份数据两个口径（详情曾经不读列，同一行两页显示不一样）。
def _state_frame_of_log(log: ConversationLog) -> dict:
    return frame_of_state_key(log.state_key) or state_frame_of(log.messages or [])


def _bare_group_id(label: str) -> int | None:
    """裸的会话键 group:{id}（帧没写 label 时摘要回退打印的就是它）"""
    head, _, tail = label.partition(":")
    return int(tail) if head == "group" and tail.isdigit() else None


async def _fill_group_labels(content_repo: ContentRepository, items: list[dict]) -> None:
    """帧 label 是裸的 group:{id} → 补成群名，界面上不再显示编号。

    label 是建帧时写的，历史帧有几条路没写（enter_group 早期、persist_last_task_as_state 自动压帧），
    只有 context_ref。这里按会话名批量补一次：写入路径与存量一起覆盖，也不必让每条建帧路径各记一次名字。
    无状态的行（没有帧）不动——那不是"缺少名字"，是那轮本来没有状态身份。
    """
    from app.models.group import Group

    want: set[int] = set()
    for item in items:
        frame = item.get("state_frame") or {}
        if not frame.get("type"):
            continue
        gid = _bare_group_id(str(frame.get("label") or ""))
        if gid is not None:
            want.add(gid)
    if not want:
        return

    rows = await content_repo.execute(select(Group.id, Group.name).where(Group.id.in_(want)))
    names = {int(i): n for i, n in rows.all()}
    for item in items:
        frame = item.get("state_frame") or {}
        gid = _bare_group_id(str(frame.get("label") or "")) if frame.get("type") else None
        if gid is not None and names.get(gid):
            frame["label"] = f"群「{names[gid]}」"


def _log_to_summary(log: ConversationLog) -> dict:
    """转为摘要（不含完整 messages，前端列表用）"""
    # 取前两条和后一条消息作为预览
    msgs = log.messages or []
    preview = []
    if len(msgs) > 0:
        preview.append(_summarize_message(msgs[0]))
    if len(msgs) > 1:
        preview.append(_summarize_message(msgs[1]))
    if len(msgs) > 3:
        preview.append({"_more": f"... 共 {len(msgs)} 条消息"})
        preview.append(_summarize_message(msgs[-1]))

    return {
        "id": log.id,
        "agent_id": log.agent_id,
        "conversation_type": log.conversation_type,
        "group_id": log.group_id,
        "session_id": log.session_id,
        "message_count": log.message_count,
        "token_usage": log.token_usage,
        "has_output": log.has_output,
        "status": _run_status(msgs, bool(log.has_output)),
        # 这轮是在哪段状态下发出的（帧身份 = type + label）：不同状态的请求体前缀本就不同，
        # 列表按它归堆才看得出「这段状态的上下文长什么样」
        "state_frame": _state_frame_of_log(log),
        "model": log.model,
        "thinking_enabled": log.thinking_enabled,
        "preview": preview,
        "created_at": str(log.created_at) if log.created_at else None,
    }


def _log_to_detail(log: ConversationLog) -> dict:
    """转为完整详情（含 messages）"""
    return {
        "id": log.id,
        "agent_id": log.agent_id,
        "conversation_type": log.conversation_type,
        "group_id": log.group_id,
        "session_id": log.session_id,
        "messages": log.messages,
        "message_count": log.message_count,
        "token_usage": log.token_usage,
        "has_output": log.has_output,
        "status": _run_status(log.messages or [], bool(log.has_output)),
        "state_frame": _state_frame_of_log(log),
        "model": log.model,
        "thinking_enabled": log.thinking_enabled,
        "created_at": str(log.created_at) if log.created_at else None,
    }


def _summarize_message(msg: dict) -> dict:
    """将单条消息压缩为预览摘要"""
    role = msg.get("role", "?")
    content = msg.get("content", "")
    if isinstance(content, str) and len(content) > 100:
        content = content[:100] + "..."
    elif isinstance(content, list):
        content = "[multi-part content]"
    summary = {"role": role}
    if content:
        summary["content"] = content
    if msg.get("tool_calls"):
        summary["tool_calls"] = [tc.get("function", {}).get("name", "?") for tc in msg["tool_calls"]]
    if msg.get("name"):
        summary["name"] = msg["name"]
    return summary


async def get_session_token_usage(
    content_repo: ContentRepository,
    session_id: str,
    user_id: int,
) -> dict:
    """获取指定 DM 会话的 token 消耗汇总（用于前端自费聊天显示）"""
    from app.models.conversation_log import ConversationLog
    result = await content_repo.execute(
        text("""
            SELECT
                COALESCE(SUM((token_usage->>'total_tokens')::int), 0) AS total_tokens,
                COALESCE(SUM((token_usage->>'api_calls')::int), 0) AS api_calls
            FROM ai_conversation_logs
            WHERE session_id = :sid
        """),
        {"sid": session_id},
    )
    row = result.one()
    return {
        "total_tokens": row.total_tokens or 0,
        "api_calls": row.api_calls or 0,
        "session_id": session_id,
    }
