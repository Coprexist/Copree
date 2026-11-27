"""会话上下文压缩（**唯一实现**）。

谁在用：
- 用户显式敲 `/compact`（斜杠命令）
- 世界 AI 调 `compact_context` 工具

两条路走同一份实现与同一套文案（卡片文案由工具插件的 `summary()` 出，命令经 `tool_result_summary` 复用）。

**为什么原来"不用压"是错的**（用户 2026-09-16 反馈：明明 20+ 轮工具调用，却说"当前会话只有 6 条对话"）：
旧实现取 `get_chat_history(limit=200)` = **最后 200 行**，再过滤掉 tool/note。可那 200 行里 95% 是
工具卡片与思考——实测某会话真实对话 30~68 条，按这个口径只数得到 3~10 条，于是永远"没超出保留窗口"、
永远跳过。判定口径现在与聊天一致：模型真正带的只有 **user/ai 两种角色**的消息。

**可压内容 = 保留窗口之外的那部分**：窗口内最近 N 条本来就会原样带上（压缩它们反而丢细节），
窗口外的东西才是摘要该收的。旧摘要会一并喂进去合并（否则这次压缩等于把更早的历史抹掉）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# 一次压缩最多喂多少条给摘要模型：超长会话别把"生成摘要"这次调用本身顶爆（取窗口外最近的这些）
COMPACT_INPUT_MAX = 300


def window_plan(total: int, keep_last: int) -> dict:
    """窗口账（纯函数）：真实对话多少条、保留几条、窗口外几条可压"""
    return {"real_messages": total, "keep_last": keep_last, "compressible": max(0, total - keep_last)}


def forget_session_state(world, skey: str) -> None:
    """清掉该会话的「摘要 + compact 边界 + 未完成工作流记忆」（clear / 新会话用）。

    三件东西必须同进同退：少清一样，下一轮就会带着上一段会话的残留——摘要在而边界悬空、
    或历史清了而工作流记忆还在（AI 会"接着做"已经不存在的工作）。
    """
    cfg = dict(world.config or {})
    summaries = dict(cfg.get("chat_summaries") or {})
    summaries.pop(skey, None)
    cfg["chat_summaries"] = summaries
    bounds = dict(cfg.get("chat_summary_bounds") or {})
    bounds.pop(skey, None)
    cfg["chat_summary_bounds"] = bounds
    cfg["workflow_memory"] = None
    world.config = cfg


def build_compact_input(rows: list[dict], summary: str = "", *, keep_last: int,
                        cap: int = COMPACT_INPUT_MAX) -> list[dict]:
    """拼给 compress_messages 的输入：占位 system + 旧摘要 + 窗口外的对话（最近 cap 条）。

    - **旧摘要必须带上**：它是更早内容的唯一载体，不带就等于这次压缩把历史抹掉
    - 只喂窗口外的部分：窗口内最近 N 条聊天本来就会原样带上
    - 角色映射 ai → assistant，与聊天上下文一致
    """
    payload = [{"role": "system", "content": "世界 AI 对话"}]
    if summary:
        payload.append({"role": "system", "content": f"【此前的上下文摘要】\n{summary}"})
    outside = rows[:max(0, len(rows) - keep_last)][-cap:]
    payload += [
        {"role": "assistant" if r.get("role") == "ai" else "user",
         "content": str(r.get("content") or "")}
        for r in outside
    ]
    return payload


async def collect_real_messages(world_repo, world) -> list[dict]:
    """该会话里**模型会看到**的消息（user/ai 两种角色，按时间正序，带 DB id）。

    tool/note 不进模型上下文（它们只是给你看的卡片与思考），所以既不计入、也不参与压缩。
    查询走 get_chat_history（唯一实现）：口径必须与聊天一致——两处各写一份 SQL 必然漂移
    （2026-09-16 那个坑就是判定口径和聊天不一致，数出来的根本不是同一个东西）。
    """
    from app.services.world.world_chat_service import (
        REAL_ROLES, get_chat_history, session_id_for_db,
    )
    rows = await get_chat_history(
        world_repo, world.id, limit=None, session_id=session_id_for_db(world), roles=REAL_ROLES,
    )
    return [{"id": r["id"], "role": r["role"], "content": r["content"] or ""} for r in rows]


async def compact_session(world_repo, world) -> dict:
    """把窗口外的对话收进摘要（唯一实现）。返回结果字典（工具与命令共用同一套字段）。

    字段：success / skipped / reason / real_messages / keep_last / compressible / merged_previous /
    before_tokens / after_tokens / compression_ratio_pct
    """
    from app.services.world.world_chat_service import WORLD_CHAT_KEEP_LAST

    rows = await collect_real_messages(world_repo, world)
    plan = window_plan(len(rows), WORLD_CHAT_KEEP_LAST)
    if plan["compressible"] == 0:
        # 没到窗口就别花一次 LLM 调用（也压不出东西：窗口内本来就会原样带上）
        logger.info(f"🌐 世界 #{world.id} 无需压缩：{plan}")
        return {"success": True, "skipped": True, "reason": "window", **plan}

    try:
        from sqlalchemy import select
        from app.models.world import WorldAI
        from app.services.memory.context_compression_service import compress_messages
        from app.services.world.world_chat_service import (
            _resolve_world_credentials, resolve_world_chat_model, session_key,
        )

        api_key, api_base = await _resolve_world_credentials(world_repo, world)
        wai = (await world_repo.execute(
            select(WorldAI).where(WorldAI.world_id == world.id))).scalar_one_or_none()
        model = await resolve_world_chat_model(world_repo, world, api_base, wai)

        summaries = dict((world.config or {}).get("chat_summaries") or {})
        previous = summaries.get(session_key(world)) or ""
        payload = build_compact_input(rows, previous, keep_last=WORLD_CHAT_KEEP_LAST)
        new_messages, stats = await compress_messages(
            messages=payload, api_base_url=api_base, api_key=api_key, model=model,
            keep_system=True,
            keep_last_n=0,        # 窗口内的消息不在这份输入里，这里的全部内容都该被摘要
        )
        if not stats.get("compressed"):
            return {"success": False, "error": stats.get("reason", "压缩未执行"), **plan}
        summary = next(
            (m["content"] for m in new_messages
             if m.get("role") == "system" and "上下文摘要" in str(m.get("content", ""))),
            "",
        )
        if not summary:
            return {"success": False, "error": "摘要提取失败", **plan}

        cfg = dict(world.config or {})
        skey = session_key(world)
        summaries = dict(cfg.get("chat_summaries") or {})
        summaries[skey] = summary
        cfg["chat_summaries"] = summaries
        # compact 边界 = 摘要覆盖到的最后一条（不含）的真实消息 id：下次对话从它往后**全部**带上，
        # 之后每轮只往后追加（前缀只增不减 → 跨轮命中缓存），直到下一次 compact 前移。
        # 取窗口前一条（+1 那个下标）才对得上"窗口内保留 KEEP_LAST 条"
        if len(rows) > WORLD_CHAT_KEEP_LAST:
            bounds = dict(cfg.get("chat_summary_bounds") or {})
            bounds[skey] = rows[-WORLD_CHAT_KEEP_LAST - 1]["id"]
            cfg["chat_summary_bounds"] = bounds
        world.config = cfg
        await _refresh_capabilities(world_repo, world)
        await world_repo.flush()
        return {
            "success": True, "merged_previous": bool(previous), **plan,
            "before_tokens": stats.get("before_tokens", 0),
            "after_tokens": stats.get("after_tokens", 0),
            "compression_ratio_pct": stats.get("compression_ratio_pct", 0),
        }
    except Exception as e:
        logger.warning(f"🌐 世界 #{world.id} 压缩失败: {e}")
        return {"success": False, "error": str(e), **plan}


async def _refresh_capabilities(world_repo, world) -> None:
    """压缩后解锁能力懒加载（提示词/强注入/昵称/技能变更在此生效）"""
    try:
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import apply_pending_changes
        from app.services.world.world_chat_service import prefix_capability_sources
        await apply_pending_changes(SQLAlchemyCapabilityRepository(world_repo.session), world.config,
                                    prefix_capability_sources(world.id))
    except Exception as e:
        # 压缩成功但能力变更没生效 → 用户以为已生效；必须留痕
        logger.warning(f"🌐 世界 #{world.id} compact 后应用能力变更失败: {e}")
