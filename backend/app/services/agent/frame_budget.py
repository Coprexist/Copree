"""帧预算：插件帧里的 LLM 调用次数与轮次单独算。

「一次」= 一次 LLM API 调用（与 agents.llm_call_count 同口径）。普通对话一轮最多
`agent.max_tool_rounds` 次（默认 6），插件帧用插件配置里的 `frame_round_limit`（默认 50）——
帧主打持续，轮次上限要明显更宽；小时 / 天的次数上限由创建者在通道卡片里设
（`frame_hour_limit` / `frame_day_limit`，0 = 不限）。

为什么限制写在插件配置里而不是平台表：这些是"这个 AI 在这条通道上"的事，和 QQ 通道的
token 额度同一个位置；平台按 `context_ref` 前缀 + manifest 里声明过的帧类型认出插件帧
（`plugin_of`），再去读它的实例配置。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.pure import frame_usage as pure

logger = logging.getLogger(__name__)

WARN_RATIO = pure.ratio(4, 5)      # 到 80% 就在账本里说一句，别等撞墙才发现


def plugin_of(frame: dict | None) -> str:
    """帧属于哪个插件；不是插件帧返回空串。

    只认两件事同时成立：`context_ref` 前缀是插件 id，**并且**帧类型在该插件的 manifest
    `frames` 段里声明过。只看前缀会把群帧 / 私信帧 / 世界帧（`group:` / `dm:` / `world:`）
    也当成插件帧——而那是绝大多数轮次，认错的代价是普通对话的上限与记账全被改写。
    """
    ref = str((frame or {}).get("context_ref") or "")
    head, sep, _ = ref.partition(":")
    if not sep or not head:
        return ""
    from app.services.plugin.frames import declared_frames

    try:
        declared = declared_frames(head)
    except Exception as e:  # noqa: BLE001 —— 读不到声明就当普通帧，别让预算挡住这一轮
        logger.debug("帧类型声明读取失败（%s）: %s", head, e)
        return ""
    return head if str((frame or {}).get("type") or "") in declared else ""


async def _config(plugin_id: str, agent_id: int) -> dict:
    """读这个插件在这个 AI 上的实例配置（通道实例名 = agent-<id>）。"""
    from app.services.plugin import config as plugin_config
    from app.services.plugin.channel import instance_of

    try:
        return await plugin_config.get_config(plugin_id, instance_of(int(agent_id)))
    except Exception as e:  # noqa: BLE001 —— 配置读不到只是没有预算，不该拦住这一轮
        logger.debug("帧预算读配置失败（%s）: %s", plugin_id, e)
        return {}


async def _current(db: AsyncSession, agent_id: int) -> dict | None:
    from app.services.agent.state_stack_service import get_current_frame

    return await get_current_frame(db, agent_id)


async def state(db: AsyncSession, agent_id: int) -> dict:
    """当前帧的预算状态；不是插件帧（或没有帧）返回 {}。"""
    return await state_of(db, agent_id, await _current(db, agent_id))


async def state_of(db: AsyncSession, agent_id: int, frame: dict | None) -> dict:
    """给定一帧的预算状态。

    换个说法就明白为什么要它：段收尾要复述的是**那一段**当时的预算，不是换帧之后新帧的。
    """
    plugin_id = plugin_of(frame)
    if not plugin_id:
        return {}
    cfg = await _config(plugin_id, agent_id)
    usage = frame.get("usage") or {}
    hour_limit = pure.limit(cfg.get("frame_hour_limit"))
    day_limit = pure.limit(cfg.get("frame_day_limit"))
    hour_used = pure.count(usage, "hour")
    day_used = pure.count(usage, "day")
    return {
        "frame_id": str(frame.get("id") or ""),
        "context_ref": str(frame.get("context_ref") or ""),
        "plugin": plugin_id,
        "kind": str(frame.get("type") or ""),
        "label": str(frame.get("label") or ""),
        "round_limit": pure.round_limit(cfg.get("frame_round_limit")),
        "hour_limit": hour_limit,
        "day_limit": day_limit,
        "hour_used": hour_used,
        "day_used": day_used,
        # 创建者给的建议单次时长（分钟，0 = 不提）：只是建议，平台不按它掐断
        "session_minutes": pure.limit(cfg.get("frame_session_minutes")),
        "usage": usage,
    }


async def describe(db: AsyncSession, agent_id: int, st: dict | None = None, *,
                    frame: dict | None = None) -> str:
    """帧预算的一句话，进状态摘要——AI 不必靠猜自己还剩多少。

    不是插件帧就走空串：普通对话的轮次上限在提示词别处说过，不在这里重复。

    frame：按这一帧算（段收尾的快照渲染用）；不给就按此刻的当前帧。
    """
    if st is None:
        st = await state_of(db, agent_id, frame) if frame is not None else await state(db, agent_id)
    if not st:
        return ""
    percent = await _token_ratio(db, agent_id, st)
    return pure.describe(
        st["round_limit"], st["hour_used"], st["hour_limit"],
        st["day_used"], st["day_limit"], percent, st["session_minutes"])


class Turn:
    """一轮（一次唤醒）的帧预算：开轮时定下上限与"要不要开"，之后每次调用只复查。

    四个开轮点（群聊、私信、闹钟、独立事件）都要这三件事：算上限、提醒、判断能不能开。
    各写一遍必然漏一处——漏掉的那条路会在帧里无限跑。
    """

    def __init__(self, db: AsyncSession, agent_id: int, default_loops: int,
                 round_limit: int, blocked: str) -> None:
        self.db = db
        self.agent_id = agent_id
        self.round_limit = int(round_limit or default_loops)
        self.blocked = blocked

    @property
    def max_loops(self) -> int:
        return self.round_limit

    async def check(self) -> str:
        """中途复查（executor 每次 LLM 调用前调它）：用满就把这一轮停下来。"""
        return await blocked_reason(self.db, self.agent_id)


async def open_turn(db: AsyncSession, agent_id: int, default_loops: int) -> Turn:
    """开一轮：读一次预算状态，接近上限就先提醒，已用满则带上原因返回（调用方据此不调模型）。"""
    st = await state(db, agent_id)
    if not st:
        return Turn(db, agent_id, default_loops, default_loops, "")
    await warn(db, agent_id, st)
    return Turn(db, agent_id, default_loops, st["round_limit"],
                await blocked_reason(db, agent_id, st))


async def blocked_reason(db: AsyncSession, agent_id: int, st: dict | None = None) -> str:
    """次数或 token 额度已经用满时返回一句原因；没满返回空串。"""
    st = st if st is not None else await state(db, agent_id)
    if not st:
        return ""
    if st["hour_limit"] and st["hour_used"] >= st["hour_limit"]:
        return f"这一小时已经用满 {st['hour_limit']} 次调用"
    if st["day_limit"] and st["day_used"] >= st["day_limit"]:
        return f"今天已经用满 {st['day_limit']} 次调用"
    return await token_blocked(db, agent_id, st)


async def token_blocked(db: AsyncSession, agent_id: int, st: dict | None = None) -> str:
    """帧的 token 额度用满时返回一句原因。走通道额度那一套（channel/token_quota）。"""
    st = st if st is not None else await state(db, agent_id)
    if not st:
        return ""
    from app.services.channel import token_quota

    try:
        decision = await token_quota.decide(
            db, agent_id=int(agent_id), channel_kind=st["plugin"],
            conversation=st["context_ref"],
        )
    except Exception as e:  # noqa: BLE001 —— 判定失败按放行处理，别把额度问题变成停摆
        logger.debug("帧 token 额度判定失败: %s", e)
        return ""
    if decision.get("allowed"):
        return ""
    return (
        f"这个状态帧的 token 额度用完了"
        f"（{decision.get('used_tokens')}/{decision.get('limit_tokens')}）"
    )


async def warn(db: AsyncSession, agent_id: int, st: dict | None = None) -> str:
    """接近上限时返回一句提醒（每个窗口只提醒一次），否则空串。

    提醒走账本（api.report_notice），不是群消息：帧里的 AI 不需要把"快没额度了"说给谁听，
    但它下一轮要看得见这件事。
    """
    st = st if st is not None else await state(db, agent_id)
    if not st:
        return ""
    usage = st.get("usage") or {}
    marks = usage.setdefault("warned", {})
    text = ""
    for slot, limit_key, used_key, unit in (
        ("hour", "hour_limit", "hour_used", "这一小时"),
        ("day", "day_limit", "day_used", "今天"),
    ):
        cap = int(st.get(limit_key) or 0)
        if not cap:
            continue
        key = pure.hour_bucket() if slot == "hour" else pure.day_bucket()
        if marks.get(slot) == key:
            continue
        used = int(st.get(used_key) or 0)
        if pure.ratio(used, cap) < WARN_RATIO:
            continue
        marks[slot] = key
        text += f"{unit}的调用额度用了 {used}/{cap}，快满了。"
    token = await _token_ratio(db, agent_id, st)
    if token >= WARN_RATIO:
        key = pure.day_bucket()
        if marks.get("token") != key:
            marks["token"] = key
            text += f"这个状态帧的 token 额度用了 {token:.0%}，快满了。"
    if not text:
        return ""
    await _persist_marks(db, agent_id, st["frame_id"], usage)
    # 前缀用帧自己的名字：这份提醒是平台替任何插件帧发的，不该写死某一个插件
    await _notify(db, agent_id, st["context_ref"], f"【{st['label'] or '状态帧'} · 额度提醒】{text}")
    return text


async def _token_ratio(db: AsyncSession, agent_id: int, st: dict) -> float:
    """帧的 token 用量比例；额度为 0（不限）或判定失败返回 0。"""
    from app.services.channel import token_quota

    try:
        decision = await token_quota.decide(
            db, agent_id=int(agent_id), channel_kind=st["plugin"], conversation=st["context_ref"])
    except Exception:  # noqa: BLE001
        return 0.0
    limit = int(decision.get("limit_tokens") or 0)
    if limit <= 0:
        return 0.0
    return pure.ratio(int(decision.get("used_tokens") or 0), limit)


async def _persist_marks(db: AsyncSession, agent_id: int, frame_id: str, usage: dict) -> None:
    from app.services.agent.state_stack_service import update_frame

    try:
        await update_frame(db, agent_id, frame_id, {"usage": usage}, allow=("usage",))
    except Exception as e:  # noqa: BLE001
        logger.debug("帧预算提醒标记写入失败: %s", e)


async def _notify(db: AsyncSession, agent_id: int, context_ref: str, text: str) -> None:
    """往帧自己的账本投一条通知（与插件同一条公共面，不进群聊）。

    这条走的是 notify_agent 的"带 db"形态：只写不提交，跟调用方那笔写同一个事务。
    """
    from app.services.plugin import api

    try:
        await api.notify_agent(agent_id, text, context_ref=context_ref,
                               ref=f"frame_budget:{context_ref}", db=db)
    except Exception as e:  # noqa: BLE001
        logger.debug("帧预算提醒投递失败: %s", e)
