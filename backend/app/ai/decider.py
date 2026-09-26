# 从 services/action_decider.py 搬迁而来
"""
统一行动决策模块

v0.1.4: 将原有的两套独立决策系统（被动回复 Gate 链 + 闹钟主动唤醒）
合并为统一的 decide_action() 函数，减少逻辑重复。

设计原则：
  - 保持向后兼容：所有现有行为不变
  - 不改变外部 API 和 WebSocket 协议
  - 闹钟优先级 85（低于 @提及强制回复 90+，高于普通回复）
"""
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 数据类型
# ══════════════════════════════════════════════════════════════

class ActionType(str, Enum):
    REPLY = "reply"           # 被动回复
    PROACTIVE = "proactive"   # 主动发言（空闲时）
    ALARM = "alarm"           # 闹钟唤醒
    NONE = "none"             # 不行动


@dataclass
class ActionDecision:
    """统一的行动决策结果"""
    should_act: bool
    action_type: ActionType
    priority: int              # 0-100，数值越大越优先
    reason: str
    details: dict = field(default_factory=dict)
    # 可选意愿信息（仅 should_act=True 时有意义）
    willingness_score: int = 0
    willingness_level: str = "low"


@dataclass
class ActionContext:
    """统一的行动上下文"""
    event_type: str            # "message" | "alarm" | "idle"
    agent_id: int
    group_id: int | None = None
    # 消息事件字段
    content: str = ""
    sender_type: str = "human"
    sender_id: int | None = None
    is_mentioned: bool = False
    is_at_all: bool = False        # v0.2.1: @all/@everyone/@全体 穿透 DND
    is_announcement: bool = False  # v0.2.1: 群公告 穿透 DND
    is_priority_friend: bool = False  # v2.0.6: 特别关心好友消息穿透 DND
    chain_depth: int = 0
    # 闹钟事件字段
    alarm_id: int | None = None
    alarm_task: str = ""
    # 空闲事件字段
    idle_seconds: int = 0


# ══════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════

async def decide_action(db, agent, context: ActionContext) -> ActionDecision:
    """
    统一的行动决策入口。

    接收统一的事件上下文（消息/闹钟/空闲），返回 ActionDecision。
    内部调用扩展后的 calculate_willingness。
    """
    agent_id = context.agent_id

    # ═══ 硬性门控（所有事件类型通用） ═══

    # blocked → 一律不行动
    if agent.state == "blocked":
        return ActionDecision(
            should_act=False, action_type=ActionType.NONE,
            priority=0, reason=f"AI {agent.name} 状态为 blocked",
        )

    # ═══ 按事件类型分发 ═══

    if context.event_type == "alarm":
        return _decide_alarm_action(agent, context)

    if context.event_type == "message":
        return await _decide_reply_action(db, agent, context)

    if context.event_type == "idle":
        return await _decide_proactive_action(db, agent, context)

    return ActionDecision(
        should_act=False, action_type=ActionType.NONE,
        priority=0, reason="未知事件类型",
    )


# ══════════════════════════════════════════════════════════════
# 各场景决策
# ══════════════════════════════════════════════════════════════

def _decide_alarm_action(agent, context: ActionContext) -> ActionDecision:
    """闹钟事件：blocked 以外一律唤醒"""
    return ActionDecision(
        should_act=True,
        action_type=ActionType.ALARM,
        priority=85,  # 闹钟优先级高，但低于 @提及 强制回复
        reason=f"闹钟 #{context.alarm_id} 触发: {context.alarm_task[:60]}",
        details={"alarm_id": context.alarm_id, "task": context.alarm_task},
    )


async def _decide_reply_action(db, agent, context: ActionContext) -> ActionDecision:
    """决定是否回复消息（原 _maybe_trigger_ai_reply 的 Gate 1-6 逻辑）"""
    from app.services.agent.agent_service import calculate_willingness, switch_agent_state
    from app.chat import chat_api

    agent_id = context.agent_id
    is_mentioned = context.is_mentioned
    profile = getattr(agent, 'config_profile', 'chat') or 'chat'

    # Gate 0: 按人静音（只对某个人：连 @ 也不唤醒）
    #   为什么排在最前面：这条规则存在的意义就是"这个人说话别叫我，哪怕 @ 我"——
    #   排在离线/@ 唤醒之后就等于形同虚设。条数维度在这里扣（这条也算他说的一条）。
    if context.group_id and context.sender_id and context.sender_id != getattr(agent, "user_id", None):
        silence = await chat_api.get_active_silence(db, agent_id, context.group_id, context.sender_id)
        if silence is not None:
            await chat_api.consume_silence(db, silence)
            return ActionDecision(
                False, ActionType.NONE, 0,
                f"AI {agent.name} 静音了这个人的消息（连 @ 也不唤醒）",
                details={"store_pending": True, "silenced_user_id": context.sender_id},
            )

    # Gate 1: 离线 + 未被 @ → 跳过；离线 + 被 @ → 唤醒
    if agent.state == "inactive":
        if not is_mentioned:
            return ActionDecision(False, ActionType.NONE, 0, f"AI {agent.name} 离线且未被 @提及")
        # 唤醒
        await switch_agent_state(db, agent_id=agent_id, target_state="active", reason="被 @提及唤醒")
        await db.flush()

    # Gate 2a: 屏蔽（无穿透，@/公告也不收）
    reachability = await chat_api.check_reachability(db, agent_id, context.group_id)
    is_muted = reachability["is_muted"]
    if is_muted:
        return ActionDecision(False, ActionType.NONE, 0,
            f"AI {agent.name} 屏蔽中，不响应任何消息", details={"store_pending": True})

    # Gate 2b: DND（@/公告可穿透）(v0.2.1): @提及 / @all / 群公告 / 特别关心 都可穿透 DND
    #   不穿透的那种是**屏蔽**（Gate 2a，muted_until）——两件事，别混（chat_service_design.md §4.2）
    in_dnd = reachability["is_dnd"]
    dnd_penetrate = is_mentioned or context.is_at_all or context.is_announcement or context.is_priority_friend
    dnd_was_penetrated = in_dnd and dnd_penetrate  # DND 被穿透时后续提醒 AI
    if in_dnd and not dnd_penetrate:
        return ActionDecision(
            False, ActionType.NONE, 0,
            f"AI {agent.name} DND 中且无穿透条件",
            details={"store_pending": True},
        )

    # Gate 3: 快速过滤器（config_profile）

    if not is_mentioned and profile == 'chat' and context.sender_type != 'human':
        return ActionDecision(False, ActionType.NONE, 0, f"AI {agent.name} 聊天档未@且非人类消息")

    # Gate 4: 意愿计算
    w = await calculate_willingness(
        db, agent_id, context.group_id, context.content,
        scenario="reply",
        is_mentioned=is_mentioned,
    )

    # 保存
    agent.last_willingness_score = w.score
    agent.last_willingness_reason = w.reason

    # v0.1.4: 记录意愿评分分布
    try:
        from app.services.infrastructure.metrics_collector import metrics
        await metrics.record_willingness(w.score)
    except Exception:
        pass

    # Gate 5: 意愿判断（使用 AI 的 auto_dnd_threshold，尊重用户设置）
    threshold = getattr(agent, 'auto_dnd_threshold', None) or 20
    if not is_mentioned and w.score < threshold:
        return ActionDecision(False, ActionType.NONE, w.score,
                            f"AI {agent.name} 意愿不足({w.score} < 阈值{threshold})")

    return ActionDecision(
        should_act=True,
        action_type=ActionType.REPLY,
        priority=w.score + (40 if is_mentioned else 0),
        reason=w.reason,
        willingness_score=w.score,
        willingness_level=w.level,
        details={"is_mentioned": is_mentioned, "willingness": w.details, "dnd_penetrated": dnd_was_penetrated},
    )


async def _decide_proactive_action(db, agent, context: ActionContext) -> ActionDecision:
    """决定是否主动发言（空闲触发，仅 digital_life 档）"""
    from app.services.agent.agent_service import calculate_willingness

    profile = getattr(agent, 'config_profile', 'chat') or 'chat'

    if profile not in ("digital_life",):
        return ActionDecision(False, ActionType.NONE, 0, "非数字生命档，不主动发言")

    if agent.state != "active":
        return ActionDecision(False, ActionType.NONE, 0, f"状态为 {agent.state}")

    w = await calculate_willingness(
        db, agent.id, context.group_id,
        message_content="",
        scenario="proactive",
        idle_seconds=context.idle_seconds,
    )

    if w.score < 30:
        return ActionDecision(False, ActionType.NONE, w.score,
                            f"主动发言意愿过低({w.score})")

    return ActionDecision(
        should_act=True,
        action_type=ActionType.PROACTIVE,
        priority=w.score,
        reason=w.reason,
        willingness_score=w.score,
        willingness_level=w.level,
    )
