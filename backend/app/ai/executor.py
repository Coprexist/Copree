# 从 app/ai/response_worker.py 抽离 — 工具执行引擎
"""
工具执行引擎：LLM 调用 + 工具循环 + API 配置 + 额度管理。

职责：
- 执行 _tool_call_loop（AI 的核心执行循环）
- 管理 API Key 选择、速率限制、Key 错误日志
- 发送系统错误通知
- 保存对话日志
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.chat import chat_api
from app.utils.pure.history import tool_ledger_note
from app.utils.pure.llm_endpoint import thinking_supported_for
from app.utils.pure.provider_config import channel_provider_info
from app.utils.pure.timeutil import get_zoneinfo
from app.utils.pure.tool_chain import heal_tool_chain

# ── 中断消息注入：AI 忙碌时，新消息不另起 executor，注入当前循环（链路见 docs/dev/conversation_history.md §4.2）──
# {agent_id: [{type: "user_message", content, sender_id, sender_name, session_id}]}
# 只发消息的工具：一轮里除了它们什么都没干时，思考痕迹没有留的价值
_MESSAGE_ONLY_TOOLS = ("send_gm", "send_dm")

# 额度外轮次（收尾轮）允许的工具：能说话、能收尾，不能再开检索
_CLOSING_TOOLS = ("send_gm", "send_dm", "end_turn")

# 强制留痕的思考上限：各轮拼起来可能很长，超了留头尾
_FORCED_THINKING_MAX = 6000

# 工具 → 活动状态：发消息 = 正在输入，发语音 = 正在录音（合成那几秒人得看得见）。
# 文案由前端 i18n 出，这里只给语义；沿用同一个 ai_typing 事件、多带一个 state，不新开一路 WS
_TOOL_ACTIVITY_STATE = {
    "send_gm": "typing",
    "send_dm": "typing",
    "send_voice": "speaking",
}

# 收尾轮指令：轮次已用尽，这一轮白送（不占额度）
# 只说话、不调工具时的提醒文案。工具轮里的 system_reminder 与轮末账本条目共用这一份——
# 两处各写一遍，迟早出现"账本说 A、提醒说 B"，而这条文字就是 AI 唯一能学到的纠错说明。
_PROSE_REMINDER = (
    "你刚才返回了文字但没有调用任何工具。文字不会自动发出去：在群里发言请调用 send_gm，"
    "私信请调用 send_dm；括号表情要写进 send_* 的 content 里，不能只返回括号文字；"
    "不想回复就调用 end_turn 结束本轮。"
)

_CLOSING_INSTRUCTION = (
    "[收尾] 工具轮次已经用完，这一轮是白送的：现在只能 send_gm/send_dm/end_turn。\n"
    "- 把已经查到、有把握的先发出去；没查到就说没查到，别硬编；\n"
    "- 发完再 end_turn。\n"
    "再调别的工具不会有结果，只会把这最后一次机会也浪费掉。"
)

# 对外动作：失败的后果由第三方承担（对方没收到），所以除回模型外还要告诉 owner。
# 见 docs/dev/error_handling.md §3.4——「以为发了」比「没发」更难查。
_OUTWARD_TOOLS = ("send_gm", "send_dm", "send_voice", "send_file")

# 工具失败提醒：与 _PROSE_REMINDER 对称——那条管「没调工具」，这条管「调了但失败」。
# 一轮只提醒一次：反复提醒既烧 token（每次都是整段上下文重跑），也会把模型逼成瞎试。
_TOOL_FAILURE_REMINDER = (
    "你刚才调用的工具失败了：{tools}。\n"
    "如果这条内容必须让对方看到，换一种方式重试，或者直接告诉对方「没发出去」；"
    "不要把失败的调用当作已完成。"
)

_pending_interrupts: dict[int, list[dict]] = {}
# 缓冲溢出时被丢掉的条数（只为告诉 AI「少了几条」，不是重放队列）
_dropped_interrupts: dict[int, int] = {}
# 正在 _tool_call_loop 里的 agent → 它在**哪些会话**里跑着（agent_id → {会话键}）。
# 为什么要到会话级：同一个 AI 可以群 A 跑一轮、同时收到群 B 的消息；"忙"得能分清哪个会话忙，
# 否则投递会串会话（见 conversation_of），也没法回答"能不能趁它干活喊一声"。
_active_runs: dict[int, set[str]] = {}
# 全局状态锁（保护以上两个变量的并发访问）
_state_lock = asyncio.Lock()

logger = logging.getLogger(__name__)

# 速率限制：{agent_id: last_call_timestamp}
_rate_limit_tracker: dict[int, float] = {}


# 每个 agent 的中断缓冲上限：这是"本轮再读一次"的通道，不是消息仓库——超了丢最旧的
# （消息本身已经在群里/账本里，下一轮历史照样看得到，丢的只是这一次投递），并留痕。
MAX_PENDING_INTERRUPTS = 20


async def add_pending_interrupt(agent_id: int, message: dict) -> None:
    """线程安全地添加待处理的中断消息（超上限丢最旧的，见 MAX_PENDING_INTERRUPTS）。"""
    async with _state_lock:
        items = _pending_interrupts.setdefault(agent_id, [])
        items.append(message)
        overflow = len(items) - MAX_PENDING_INTERRUPTS
        if overflow > 0:
            del items[:overflow]
            # 丢了多少要**告诉 AI**，不能只写服务端日志：它看不到就会以为那段时间没人说话
            # （降级必须可解释——见 docs/dev/tool_execution.md §3.4.7）
            _dropped_interrupts[agent_id] = min(
                _dropped_interrupts.get(agent_id, 0) + overflow, 999)
            logger.warning(
                f"AI(agent={agent_id}) 忙时中断缓冲超过 {MAX_PENDING_INTERRUPTS} 条，丢弃最旧的 {overflow} 条"
                "（消息仍在群/账本里，下一轮历史照常能看到）"
            )


def _overflow_notice(count: int) -> dict:
    """缓冲溢出时补的一条说明：只报数量与去哪里看，不重放内容。"""
    return {
        "type": "notice",
        "content": (
            f"（积压太多，有 {count} 条中途消息没能在这一轮之前送进来。"
            "它们仍在群/私信历史里，用 read_conversation 往回翻；别当成那段时间没人说话。）"
        ),
    }


def conversation_of(message: dict) -> tuple[str, object]:
    """一条中断消息属于哪个会话：群看 group_id，私信看 session_id。

    为什么要有这个：同一个 agent 的缓冲是**跨会话共用**的（它同时在群和私信里说话），
    不区分就会把 A 群的消息拼进正在跑的 B 群的轮次里——AI 会把两边的上下文混着读。
    """
    if message.get("group_id") is not None:
        return ("group", message["group_id"])
    if message.get("session_id") is not None:
        return ("dm", message["session_id"])
    return ("", None)


async def drain_pending_interrupts(agent_id: int, *, group_id: int | None = None,
                                   session_id: str | None = None) -> list[dict]:
    """取出待处理的中断消息；给了会话就**只取那个会话的**，其余留在缓冲里。

    不传会话 = 全取（历史调用口径）。工具轮每轮调用前按自己的会话取，取错会话等于投错人。
    """
    async with _state_lock:
        items = _pending_interrupts.get(agent_id) or []
        dropped = _dropped_interrupts.pop(agent_id, 0)
        if dropped:
            # 溢出说明不分会话（它说的是「你这边积压了」，哪个会话来取都给）
            items.append(_overflow_notice(dropped))
        if group_id is None and session_id is None:
            _pending_interrupts.pop(agent_id, None)
            return items
        want = ("group", group_id) if group_id is not None else ("dm", session_id)
        mine, keep = [], []
        for item in items:
            # 通知不分会话（它说的是"你这个 AI 变了"）：哪一轮来取都给，别让它等会话
            is_notice = item.get("type") == "notice"
            (mine if is_notice or conversation_of(item) == want else keep).append(item)
        if keep:
            _pending_interrupts[agent_id] = keep
        else:
            _pending_interrupts.pop(agent_id, None)
        return mine


def interrupt_message_block(pm: dict, agent_name: str) -> dict:
    """一条中断消息 → 上下文里那条 user 块。

    多条**各成一块**，不合并：AI 要分得清是谁在第几条说的，糊成一段就等于把两个人两句话
    说成一个人说的。带上 message_id，它才知道要引用回复哪条。
    """
    if pm.get("type") == "notice":
        # 平台说的一句话（能力/记忆变更的提前量）：系统口吻、随轮消失，不进历史
        return {"role": "system", "content": str(pm.get("content") or "")}

    from app.utils.pure.prompting import format_message

    tz_name = settings.display_timezone
    now_str = datetime.now(get_zoneinfo(tz_name)).strftime(f"%Y-%m-%d %H:%M {tz_name}")
    msg_struct = {
        "time": now_str,
        "speaker_name": pm.get("sender_name") or "用户",
        "speaker_id": pm.get("sender_id"),
        "is_self": False,
        "content": pm.get("content", ""),
        "message_id": pm.get("message_id"),
    }
    return {"role": "user", "content": format_message(msg_struct, agent_name, max_content_len=-1)}


async def restore_pending_interrupts(agent_id: int, messages: list[dict]) -> None:
    """把取出来但没投出去的中断消息放回缓冲（轮次收尾的兜底用）。

    顺序：放回队首——它们比新来的更早发生，下一轮该先看到它们。
    """
    if not messages:
        return
    async with _state_lock:
        old = _pending_interrupts.get(agent_id) or []
        _pending_interrupts[agent_id] = list(messages) + old


async def is_agent_running(agent_id: int, conversation: str | None = None) -> bool:
    """这个 agent 是否正在跑一轮；给了会话就只看那个会话。"""
    async with _state_lock:
        running = _active_runs.get(agent_id) or set()
        return bool(running) if conversation is None else conversation in running


async def mark_agent_running(agent_id: int, conversation: str | None = None) -> None:
    """标记 agent 正在某个会话里执行。"""
    async with _state_lock:
        _active_runs.setdefault(agent_id, set()).add(conversation or "")


async def unmark_agent_running(agent_id: int, conversation: str | None = None) -> None:
    """取消标记（会话跑完）。"""
    async with _state_lock:
        running = _active_runs.get(agent_id)
        if not running:
            return
        running.discard(conversation or "")
        if not running:
            _active_runs.pop(agent_id, None)


async def shout_change(agent_id: int, text: str) -> bool:
    """变更发生的那一刻，趁它正在干活"喊一声"（这一轮就能看到）。

    - 只在它**确实在跑一轮**时才喊；没在跑就不喊——账本那条变更通知照样会在下一轮给它，
      喊只是提前量（便签一定贴，喊只是顺口一句）。
    - 通知**不分会话**：「你的工具 / 记忆 / 人格变了」在哪个会话里都成立，所以它的会话字段为空，
      取用的那一轮会照收（见 drain_pending_interrupts）。
    - **绝不产生新一轮、也不落库**：投递只负责投递。type="notice" 的块随轮消失，
      轮末兜底只重排"人说的话"，通知直接丢掉（账本那条才是它的底子）。
    """
    if not str(text or "").strip() or not await is_agent_running(agent_id):
        return False
    await add_pending_interrupt(agent_id, {"type": "notice", "content": str(text).strip()})
    return True


def _get_tool_task_summary(tool_name: str, arguments: dict) -> str | None:
    """从 ToolRegistry 获取工具的中文任务摘要，找不到时返回 None。"""
    try:
        from app.tools.base import ToolRegistry
        plugin = ToolRegistry.get_plugin(tool_name)
        if plugin:
            return plugin.get_task_summary(arguments)
    except Exception:
        pass
    return None


# ============================================================
# 系统错误通知
# ============================================================

def _key_fatal_reason(status_code: int) -> str:
    """Key 不可用的说法：401 是钥匙不对，402 才是钱不够。

    以前不管哪种状态码都写"余额不足"，用户按提示去充值，充完照旧不能说话——
    真正的毛病是 Key 自己填错了。
    """
    if status_code == 401:
        return "API Key 无效（401）"
    if status_code == 402:
        return "余额不足（402）"
    return f"Key 不可用（{status_code}）"


def _key_tail_suffix(api_key: str | None) -> str:
    """Key 的点名说辞：末尾四位够认出是哪一把，又不至于把整把 Key 写进私信里。

    短到不像 Key 的（占位符、手滑粘贴的半截）不点名：那种长度下"末尾四位"已经等于露了大半，
    而且它本身就是填错，说"哪一把"没有意义。
    """
    key = (api_key or "").strip()
    return f"（末尾 {key[-4:]}）" if len(key) >= 16 else ""


def key_fatal_notice(
    *, agent_name: str, source: str, status_code: int, api_key: str | None,
    has_fallback: bool, tier_name: str,
) -> str:
    """「这把 Key 用不了」那条私信：谁不可用、为什么、下一步怎么办。

    四种收件场景说四种话（还有下一级可降级 / 系统额度 / 用户自己的 Key / 其它），
    所以组装只放在这一处：调用方只需算清楚 has_fallback，再由本函数决定说什么。
    """
    reason = _key_fatal_reason(status_code)
    tail = _key_tail_suffix(api_key)
    if has_fallback:
        return (
            f"⚠️ **{tier_name} Key{tail} 不可用**（{reason}）。\n"
            f"正在自动切换至下一优先级额度…"
        )
    if source == "pool_key":
        return (
            f"⚠️ **系统额度暂不可用**（{reason}）{tail}。\n"
            f"此次不记入你的额度使用情况。请稍后重试或联系管理员。"
        )
    if source == "user_key":
        return (
            f"⚠️ **{reason}**{tail}，且无可用系统额度。\n"
            f"请前往 [个人设置](/settings) 检查 Key 是否填错、余额是否用尽，或联系管理员补充额度。"
        )
    return f"⚠️ AI「{agent_name}」的 API 调用失败（{reason}）{tail}。\n请检查 API 配置。"


async def _send_system_error(
    db, agent, error_type: str, detail: str = "",
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    tool_name: str = "",
) -> None:
    """发送分类系统错误通知给 AI 的 owner（走 DM）"""
    from app.models.dm import DMMessage, DMSession

    SYSTEM_USER_ID = 0  # 硬编码：迁移保证 id=0 为系统用户

    from app.ai.error_catalog import render

    # 文案的唯一来源是 error_catalog.CATALOG（docs/dev/error_handling.md §3.1）；
    # 这里只做投递，不再就地拼文案——加一种错误只改表，不改这个函数。
    content = render(
        error_type,
        agent_name=getattr(agent, "name", "") or "",
        agent_id=getattr(agent, "id", "") or "",
        detail=detail,
        tool=tool_name,
    )

    try:
        # 统一走 DM 通知：发给 AI 的 owner，不广播到群
        owner_id = agent.owner_id
        if not owner_id:
            logger.warning(f"AI {agent.name}({agent.id}) 无 owner，无法发送系统通知")
            return

        dm_session = await chat_api.get_or_create_dm_session(db, SYSTEM_USER_ID, owner_id, skip_friendship_check=True)
        dm_sid = dm_session["session_id"]  # 返回 dict；skip_friendship_check=True：系统通知不受好友关系限制（2026-08-13 修复）

        # 复用 send_dm_message：统一处理 last_message 更新/未读标记（2026-08-13 重构，不再手动复制）
        dm_msg = await chat_api.send_dm_message(
            db, dm_sid, SYSTEM_USER_ID, content,
            skip_friendship_check=True,
            message_type="system",
        )

        # WebSocket 推送
        try:
            await chat_api.broadcast_to_dm(dm_sid, {
                "type": "new_dm_message",
                "message": {
                    **dm_msg,
                    "sender_name": "系统通知",
                    "is_system": True,
                },
            })
        except Exception:
            pass
        logger.info(f"📬 系统通知已发送给 AI {agent.name}({agent.id}) 的 Owner({owner_id})")
    except Exception as e:
        logger.error(f"  发送系统通知失败: {e}")


async def _send_system_error_notification(db, agent, content: str) -> None:
    """发送自定义系统通知（降级/余额不足等），走 DM 发给 AI 的 owner"""
    from app.models.dm import DMMessage, DMSession
    SYSTEM_USER_ID = 0
    try:
        owner_id = agent.owner_id
        if not owner_id:
            return
        dm_session = await chat_api.get_or_create_dm_session(db, SYSTEM_USER_ID, owner_id, skip_friendship_check=True)
        dm_sid = dm_session["session_id"]  # 返回 dict；skip_friendship_check=True：系统通知不受好友关系限制（2026-08-13 修复）
        # 复用 send_dm_message：统一处理 last_message 更新/未读标记（2026-08-13 重构）
        dm_msg = await chat_api.send_dm_message(
            db, dm_sid, SYSTEM_USER_ID, content,
            skip_friendship_check=True,
            message_type="system",
        )
        try:
            await chat_api.broadcast_to_dm(dm_sid, {
                "type": "new_dm_message",
                "message": {
                    **dm_msg,
                    "sender_name": "系统通知",
                    "is_system": True,
                },
            })
        except Exception:
            pass
        logger.info(f"📬 自定义通知已发送给 AI {agent.name}({agent.id}) 的 Owner({owner_id})")
    except Exception as e:
        logger.error(f"  发送自定义通知失败: {e}")


# ============================================================
# API 配置获取
# ============================================================

def _api_meta_of(res, base_url: str) -> dict:
    """把这次解析的结论压成日志要的几项（与 API 页签 stepper 同源，只是字段更少）。

    label 取命中那一档的配置名：source 说的是"走的哪一档"，label 回答"哪条"。
    排障时要同时知道这两件事——只看 model 分不清是 AI 绑定还是池。
    """
    hit = next((step for step in res.chain if step.level == res.source), None)
    return {
        "source": res.source,
        "label": hit.label if hit else "",
        "model": res.chat_model or "",
        "base_url": base_url,
    }


async def _get_api_config(
    db, agent,
    exclude_pool_key_id: int | None = None,
    chatter_id: int | None = None,
    force_own_key: bool = False,
    conversation_type: str | None = None,
    excluded_sources: set[str] | None = None,
    bill_to_owner: bool = False,
) -> tuple[str | None, str, str, int | None, dict]:
    """兼容薄壳：算好账单人，然后交给唯一的解析入口。

    **这里不再判断「用哪一档」**——那件事只有
    `app.services.agent.api_config_resolver` 做。返回旧五元组只是为了既有调用点先不动；
    新代码请直接用 `resolve_llm()` 拿 `Resolution`（它同时带决策轨迹，界面与请求共用）。

    账单人是业务规则，不是「用哪条配置」的一部分，所以留在这里：
    - bill_to_owner / 群聊 owner 付费 → AI 主人
    - 通用 AI 的私聊/聊天者付费 → 聊天者
    """
    from app.services.agent.api_config_resolver import (
        LEVEL_BOUND, LEVEL_DEFAULT, LEVEL_POOL, resolve_llm,
    )

    if bill_to_owner:
        bill_user_id = agent.owner_id
    elif conversation_type and conversation_type != "dm" and getattr(agent, "group_owner_pays", True):
        bill_user_id = agent.owner_id
    elif chatter_id and agent.ai_type in ("general", "semi_general"):
        bill_user_id = chatter_id
    else:
        bill_user_id = agent.owner_id

    # 旧来源名 → 新档位名（"agent_key" 是已删除的裸 Key 档，映射到 ① 保持重试语义）
    exclude = {
        {"agent_key": LEVEL_BOUND, "user_key": LEVEL_DEFAULT, "pool_key": LEVEL_POOL}.get(s, s)
        for s in (excluded_sources or ())
    }
    res = await resolve_llm(
        db, agent, bill_to=bill_user_id, exclude=exclude,
        exclude_pool_key_id=exclude_pool_key_id, force_own_key=force_own_key,
        scenario="agent_llm",
    )

    if not res.usable and res.decrypt_failures:
        from app.models.user import User as UserModel
        user = await db.get(UserModel, bill_user_id)
        if user is not None:
            notify = (_notify_force_own_key_failure if force_own_key
                      else _notify_key_degradation)
            asyncio.create_task(notify(db, agent, user, list(res.decrypt_failures)))

    api_base = res.base_url or settings.deepseek_base_url
    provider_info = dict(res.provider_info) or {
        "thinking_supported": thinking_supported_for(api_base), "base_url": api_base,
        "models": [], "default_model": None,
    }
    # 能不能真的请求：本地自建服务可以没有 Key（F4）。调用方一律看这个，
    # 不要再各自猜「api_key is None 就是没配」——池里那条本地配置会被误判。
    provider_info["usable"] = res.usable
    provider_info["api_meta"] = _api_meta_of(res, api_base)
    return res.api_key, api_base, res.credit_source, res.pool_key_id, provider_info


async def _notify_key_degradation(db, agent, user, failures: list[str]) -> None:
    """Key 解密失败导致降级时，通知 AI 的 owner"""
    try:
        failed_list = "\n".join(f"- {f}" for f in failures)
        msg = (
            f"⚠️ API Key 解密失败，已回退到全局默认配置\n\n"
            f"**失败来源**：\n{failed_list}\n\n"
            f"📌 可能原因：系统加密密钥已变更。\n"
            f"请前往 [AI 设置页](/agents/{agent.id}) 和 [个人设置](/settings) 重新填写 API Key。"
        )
        await _send_system_error_notification(db, agent, msg)
        logger.warning(f"降级通知已发送给 AI「{agent.name}」的 Owner({agent.owner_id})，失败来源: {failures}")
    except Exception as e:
        logger.error(f"发送降级通知失败: {e}")


async def _notify_force_own_key_failure(db, agent, user, failures: list[str]) -> None:
    """force_own_key 场景：解密失败 = 无法调用模型，通知用户重新配置"""
    try:
        failed_list = "\n".join(f"- {f}" for f in failures)
        msg = (
            f"⚠️ AI「{agent.name}」无法使用自有 API Key\n\n"
            f"你开启了「强制使用自有 Key」，但以下 Key 解密失败：\n{failed_list}\n\n"
            f"📌 **AI 暂时无法回复消息。**\n"
            f"请前往 [AI 设置页](/agents/{agent.id}) 重新填写 API Key。"
        )
        await _send_system_error_notification(db, agent, msg)
        logger.warning(f"force_own_key 解密失败通知已发送给 AI「{agent.name}」的 Owner({agent.owner_id})")
    except Exception as e:
        logger.error(f"发送 force_own_key 失败通知失败: {e}")


# ============================================================
# 工具调用循环
# ============================================================

# ── 工具结果带图（目前只有 B 站取帧用）─────────────────────────────
# 图片不走工具消息：多数供应商的 tool 角色只收文本，几十 KB 的 base64 塞进 JSON
# 还会把对话日志撑大。图片另起一条 user 消息注入，标签留在工具结果的 images[i].label。
MAX_TOOL_IMAGES = 4
TOOL_IMAGES_PREFIX = "【工具附图】"


def _take_tool_images(result: dict, tool_name: str) -> list[dict]:
    """把工具结果里的图片取出来（并从结果里摘掉），返回 [{url, label}]。

    images 每项可以是字符串，也可以是 {"url", "label"}；label 是这张图对应的
    画面时间，注入时按顺序念给模型，免得它以为第 2 张就是它要的那个时间点。
    """
    images = result.pop("images", None) if isinstance(result, dict) else None
    if not isinstance(images, list):
        return []
    items: list[dict] = []
    for raw in images:
        if isinstance(raw, str):
            url, label = raw, ""
        elif isinstance(raw, dict):
            url = str(raw.get("url") or "")
            label = str(raw.get("label") or "")
        else:
            continue
        if not url.startswith(("data:image/", "http://", "https://")):
            continue
        items.append({"url": url, "label": label, "tool": tool_name})
    return items


def _inject_tool_images(messages: list[dict], items: list[dict]) -> int:
    """把工具取回的图片注入成一条附图消息（返回注入张数）。

    顺序即对应关系：标签按同一顺序写在正文里，模型据文字认图，不靠位置猜。
    """
    kept = items[:MAX_TOOL_IMAGES]
    if not kept:
        return 0
    from app.utils.multimodal import image_note

    names = "、".join(
        f"第{i}张 {it['label']}" if it.get("label") else f"第{i}张"
        for i, it in enumerate(kept, start=1)
    )
    parts: list[dict] = [{
        "type": "text",
        "text": f"{TOOL_IMAGES_PREFIX}工具返回 {len(kept)} 张画面，按顺序：{names}。"
                "这些是工具给的截图，不是用户发的。",
    }]
    parts.extend({"type": "image_url", "image_url": {"url": it["url"]}} for it in kept)
    messages.append({"role": "user", "content": parts})
    messages.append({"role": "system", "content": image_note(len(kept), source="工具刚返回的画面")})
    return len(kept)




def _plain_text_send_enabled(agent) -> bool:
    """这个 AI 有没有把「只回文字」显式设成直接发？——直发开关的唯一判定入口。

    默认 silent：平台不替它发（正文可能是给自己记的过程，也可能是给别的会话的话）。
    用户在 AI 设置里选成 send，才认为「这句就是发给当前这些人的」，此时才允许投递。
    """
    return (getattr(agent, "plain_text_action", "silent") or "silent") == "send"


async def _deliver_plain_text(
    db, agent, text: str, *,
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    agent_name: str = "",
) -> bool:
    """把模型「直接说出口」的正文发到**当前会话**——plain_text_action=send 的唯一出口。

    为什么需要它：Copree 里 AI 靠调 send_gm / send_dm 说话，多会话才分得清「这句发给谁」。
    但小模型（本地 4B 那种）在几十个工具里挑不准，常常只返回文字；用户把这个 AI 设为
    「直接发」之后，平台就替它把这句发给**它正在说话的那个会话**——不外推、不跨会话，
    所以原来担心的「纯文字可能是给别的对话的」在这个选项下不成立（那是用户的选择）。

    返回 True=发出去了；False=没发（没有可用会话或发送失败，调用方回落静默留账）。
    """
    text = (text or "").strip()
    if not text:
        return False
    name = agent_name or getattr(agent, "name", "AI")
    try:
        if conversation_type == "dm" and session_id:
            from app.chat.dm import send_dm_message
            message = await send_dm_message(
                db, session_id, sender_id=agent.user_id, content=text,
            )
            await db.commit()
            try:
                await chat_api.broadcast_to_dm(session_id, {
                    "type": "new_dm_message",
                    "message": {**message, "sender_name": name},
                })
            except Exception:
                pass
            return True
        if group_id:
            from app.chat.gm import gm_message_to_dict, send_gm_message
            message = await send_gm_message(
                db, group_id=group_id, sender_type="ai",
                sender_id=agent.user_id, content=text,
            )
            await db.commit()
            try:
                await chat_api.broadcast_to_group(group_id, {
                    "type": "message",
                    "data": gm_message_to_dict(
                        message, sender_name=name,
                        sender_avatar_url=getattr(agent, "avatar_url", None),
                    ),
                })
            except Exception:
                pass
            return True
    except Exception as e:  # noqa: BLE001 —— 发不出去不该把这一轮打挂，回落静默留账
        logger.warning(f"直接发文字失败，回落静默留账: {e}")
    return False


async def _tool_call_loop(
    db,
    agent,
    group_id: int | None,
    messages: list[dict],
    tools: list[dict],
    model: str,
    api_base_url: str,
    api_key: str | None,
    max_loops: int = 5,
    chain_depth: int = 0,
    conversation_type: str = "group",
    session_id: str | None = None,
    trigger_user_id: int | None = None,
    effective_cfg: dict | None = None,
    credit_source: str = "user_key",
    pool_key_id: int | None = None,
    provider_supports_thinking: bool | None = None,
    system_layout: str = "anywhere",
    trigger: str = "user",
    is_federated: bool = False,
    pending_delivery: list[dict] | None = None,
    frame_budget_check=None,
    api_meta: dict | None = None,
) -> None:
    """
    工具调用循环：LLM 必须通过工具调用来执行所有操作（包括发消息）。

    pending_delivery：build_messages 收集的"这次渲染进请求、还没投递过的账本条目"。
    请求真的发出去（响应回来）之后才标记已读——解锁丢弃只看这个标记。

    铁律：文字不能自动发出去。想说话必须调 send_gm（群聊）或 send_dm（私信）。
    
    上下文压缩规则：在调 send_gm/send_dm 发消息之前的所有操作（工具调用、记忆查询等）
    会完整保留。当你调用了 send_gm/send_dm 之后，之前的中间操作链才会被压缩清理。

    v0.1.3: trigger_user_id 传入工具上下文供 store_memory 做 per-user 隔离。
    effective_cfg 为 get_effective_config 的返回值，提供 per-user 定制的 LLM 参数。
    v0.1.5: credit_source + pool_key_id 用于 LLM 调用后额度扣除。
    v0.1.5: stream=True 流式调用 + 工具格式校验 + trigger 字段（user/auto）。
    """
    if effective_cfg is None:
        effective_cfg = {}

    async def _save_log(*args, **kwargs):
        """本轮日志统一带上「用了哪条配置」——Resolution 只在调 LLM 的那层有，别丢在半路。"""
        kwargs.setdefault("api_meta", api_meta)
        await _save_conversation_log_safe(*args, **kwargs)

    from app.ai.llm import chat_completion, set_round_budget
    from app.services.tool_registry import dispatch_tool_call

    context = {
        "api_key": api_key,
        "api_base_url": api_base_url,
        "manager": chat_api,  # v1.x: ChatApi 代替原 ConnectionManager
        "agent_name": agent.name,
        "chain_depth": chain_depth,
        "conversation_type": conversation_type,
        "session_id": session_id,
        "trigger_user_id": trigger_user_id,
        "is_federated": is_federated,
        "_messages": messages,       # compress_context 工具需要读写
        "_agent": agent,             # compress_context 需要 agent.id 做 user_id
        "_model": model,             # 压缩用的模型
    }

    # 追踪 AI 在做什么（用于中断恢复）
    last_task = None
    # 累积 token 消耗（跨多轮工具调用）
    total_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0, "api_calls": 0}
    # system_reminder 额外轮次：AI 返回文字但忘了调 send_message 时，提醒不消耗配额
    _reminder_extra = 0
    # 上下文压缩：每轮工具调用循环最多自动压缩一次
    _auto_compressed = False
    # AI 尚未在本次循环中发过消息 → 压缩只应在空闲强制时触发，保中间结果
    _has_sent_message = False
    # 轮末封存用的工具总账（工具轮历史 → 账本）
    _tool_log: list[dict] = []
    # 本轮的唯一标识：开场写到帧上，收尾按它找回同一帧（§4.3）。
    # 用会话键反推是猜——同一会话先后两轮、一轮中途被切帧都会猜错。
    _turn_id = uuid.uuid4().hex[:12]
    # 已经就"Key 不可用"给用户发过一条带降级上下文的私信：末尾别再补一条同义的
    _notified_fatal = False
    # 收尾轮：轮次用尽却一句话没发时白送一轮（只送一次），否则整轮检索等于白做
    _closing_extra = 0
    _closing_active = False
    # 本轮是否已用 end_turn 收尾 / 轮末结算内容。
    # 这两个必须**在循环之前**初始化：循环可能在第一步之前就 break（帧预算用满、帧额度
    # 借不到），而收尾与嵌套函数（_finish / _dispatch_one_tool）会读它们——只在循环内
    # 赋值的话，那条路径上就是 "free variable '_end_turn' ... in enclosing scope"，
    # 整轮异常、一条回复都发不出去（2026-11-25 22:39 群 81 的线上现象）。
    _end_turn = False
    _settlement: dict = {}
    # 下一个 LLM 调用是额度外白送的轮次（提醒轮 / 收尾轮）
    _free_next = False
    # 本轮是 AI 自己收的尾（end_turn 工具 / intent=end_turn），不是被平台结束的
    _self_ended = False
    # 各轮思考：没走 end_turn 就被收尾时，它是「为什么在干这件事」的唯一记录
    _reasoning_log: list[str] = []
    _unsent_text = ""
    # 工具失败提醒：一个 turn 只提醒一次（与「没调工具」的提醒各管一半，互不叠加）
    _tool_fail_reminded = False
    # 对外动作失败的私信：一个 turn 只发一次，避免批量失败把 owner 刷屏
    _outward_notified = False
    # 本轮失败的工具名（去重、保序）与对外动作失败明细
    _failed_tools: list[str] = []
    _outward_failures: list[dict] = []

    # 开场认领：这一轮演的是哪一帧，先记下来。免费轮（提醒 / 收尾）一步都不扣额度，
    # 但收尾同样要出链——不在这里打标识，收尾时就找不到该关哪一帧。
    try:
        from app.services.agent.state_stack_service import begin_turn
        await begin_turn(
            db, agent.id,
            _context_ref(group_id, session_id, conversation_type),
            _turn_id,
        )
    except Exception as e:
        logger.warning(f"AI {agent.name}({agent.id}) 轮次开场认领帧失败（非致命）: {e}")

    def _did_real_work() -> bool:
        """本轮除了发消息/结束，还干过别的事没有。"""
        return any(
            (it.get("name") or "") not in _MESSAGE_ONLY_TOOLS
            for it in _tool_log
        )

    def _forced_reasoning() -> str:
        """没走 end_turn 就收尾时留下的思考：各轮拼起来，超长留头尾。"""
        text = "\n\n".join(t for t in _reasoning_log if (t or "").strip())
        if len(text) <= _FORCED_THINKING_MAX:
            return text
        half = _FORCED_THINKING_MAX // 2
        return f"{text[:half]}\n…（中略）…\n{text[-half:]}"

    def _cutoff_text(reason: str) -> str:
        """收尾说明的两种口径：话已经说出去了 / 一句都没发。"""
        tail = ("消息已经发出去了，但事情可能没做完。" if _has_sent_message
                else "一条消息都没发出去——上面的工具过程别当成已经说过的话。")
        return f"{reason}{tail}"

    async def _seal(reasoning: str = "", cutoff: str = "", *,
                    ended_code: str = "done_by_ai",
                    ended_reason: str = "本轮正常结束") -> None:
        """所有出口（end_turn 工具 / intent=end_turn / 循环走完 / 中断）共用这一次封存。

        不是 AI 自己收的尾（轮次用尽、模型只回了文字）时思考必须留：它是「为什么在干这件事」
        的唯一记录；只发过消息的轮次没有这个负担，照旧不留。

        未发送的正文由这里统一认领，调用方不必记：这一轮只要写过纯文字又一条都没发出去，
        不管是哪一轮收的尾，都把那句话和平台提醒一起写进账本，下一个自己看得到。

        不直接代发而是静默留账，是怕给其他对话发消息却没有调用工具导致泄漏——纯文字可能是
        思考输出、不是回答输出，也可能答的不是当前这段对话，平台分不清该发给谁。
        这可能会导致因为模型的专注问题等消耗多余的 token，但是能保证特殊情况下的隐私泄漏
        问题不出现。未来或将加上默认文字输出处理方式选择功能，可选是静默保留还是直接发出。
        """
        unsent = ((_unsent_text, _PROSE_REMINDER)
                  if _unsent_text and not _has_sent_message else None)
        if not (_end_turn or _self_ended) and _did_real_work():
            _settlement["keep_thinking"] = True
            reasoning = _forced_reasoning() or reasoning
        try:
            await _seal_turn(
                db, agent, group_id=group_id, session_id=session_id,
                conversation_type=conversation_type, tool_log=_tool_log,
                settlement=_settlement, reasoning=reasoning, cutoff=cutoff, unsent=unsent,
            )
        except Exception as e:
            logger.warning(f"轮末封存失败（非致命，下一轮只少了这一笔）: {e}")

        # 帧归位：这一轮演完了，把**这一轮演的帧**从 running 收回（按 turn_id，
        # 不猜栈顶——收尾不该去关别的会话的帧）。
        # 少了这一步帧会一直停在 running：下一个别的会话有人找它时，attach_stimulus 判
        # 「窗口被占」，只挂帧记未读、不唤醒——表现就是「群里说完一轮，私信永远不回」
        # （2026-11-23 外部反馈，实测必现）。by="ai"：告知已由上面 _seal_turn(cutoff=…)
        # 写进账本，这里 by="platform" 再写一条就是重复。
        try:
            from app.services.agent.state_stack_service import end_turn_frame
            await end_turn_frame(
                db, agent.id,
                turn_id=_turn_id,
                context_ref=_context_ref(group_id, session_id, conversation_type),
                code=ended_code, reason=ended_reason, by="ai",
            )
        except Exception as e:
            logger.warning(f"轮末帧归位失败（非致命，别的会话可能误判为忙）: {e}")

    async def _seal_aborted(reason: str) -> None:
        """取消/异常也是收尾出口：不在这里封存，这一轮就只剩用户那条消息。

        平台重启会直接 cancel 在跑的轮次（bootstrap.cancel_all_tasks），异常也会把帧直接弹走；
        两种都没走 end_turn，所以思考照收（判据在 _seal 里），并如实说明是怎么断的。
        """
        try:
            await _seal("", cutoff=_cutoff_text(reason),
                        ended_code="crash", ended_reason=reason)
            await _save_log(
                db, agent, messages, conversation_type,
                group_id, session_id,
                has_output=_has_sent_message, model=model,
            )
        except Exception as e:
            logger.warning(f"中断收尾失败（非致命，下一轮只少了这一笔）: {e}")

    def _grant_closing_round() -> None:
        """轮次用尽却一句话没发 → 白送一轮收尾（只送一次）。

        不送的话这一轮检索全白做，用户侧只看到「AI 不回我了」——静默收尾是必须堵死的出口。
        只在额度真的用完时送；提醒轮（system_reminder）自己也会送免费轮，别叠加。
        """
        nonlocal _closing_extra, _free_next, _closing_active
        if _closing_extra or _has_sent_message or _end_turn or _self_ended:
            return
        if loop_idx < max_loops + _reminder_extra + _closing_extra:
            return
        _closing_extra += 1
        _free_next = True
        _closing_active = True
        messages.append({"role": "user", "content": _CLOSING_INSTRUCTION})
        logger.info(
            f"AI {agent.name}({agent.id}) 工具轮次用尽且一条消息都没发，白送收尾轮"
            f"（上限 {max_loops} 轮）"
        )

    # ── 空闲强制压缩（2026-08-13 前移）：在第一次 LLM 调用之前检查 12h 空闲。
    # 之前放在工具循环内（调用成功后），key 失效等早期失败时根本执行不到——
    # 12 天没聊的对话带着全量历史硬跑，且 key 修复前永远不压缩。前移后即使本次失败，
    # 下次重试时上下文已瘦身。
    # 优先级：API 通 → LLM 总结压缩（保留要点）；API 不通 → 内联截断兜底
    try:
        from app.services.memory.context_compression_service import (
            inline_compress, compress_messages, should_compress, get_compression_thresholds,
        )
        from app.utils.pure.model_window import context_window_for
        # 体积是「该不该压」的必要条件，空闲只决定「什么时候压最划算」（§6）：
        # 冷路径用 T_idle——没超就不压，小上下文白跑一次摘要不值
        thresholds = await get_compression_thresholds(db)
        window = context_window_for(model, api_base_url)  # 窗口按模型与部署取，别拿全局常量套所有模型
        stale = (
            _is_conversation_idle(messages, hours=12)
            and should_compress(messages, context_window=window, threshold=thresholds.idle)
        )
        if stale and not getattr(context, "_precompressed", False):
            compressed_ok = False
            # 段号先给出来：压缩函数要把它写进提示，原文在压缩成功后才存（原文即折叠前的列表）
            from app.services.history.context_segment_service import new_segment_id
            seg_ref = new_segment_id()
            before_compress = messages
            # 优先 LLM 总结压缩（保留关键信息，适合继续对话的场景）
            if api_key:
                try:
                    messages, compress_stats = await compress_messages(
                        messages,
                        api_base_url=api_base_url,
                        api_key=api_key,
                        model=model,
                        user_id=str(agent.id),
                        segment_ref=seg_ref,
                    )
                    if compress_stats.get("compressed"):
                        compressed_ok = True
                        await _save_compressed_segment(
                            db, agent, conversation_type=conversation_type,
                            group_id=group_id, session_id=session_id,
                            before=before_compress, segment_ref=seg_ref,
                        )
                        logger.info(
                            f"AI {agent.name}({agent.id}) 空闲>12h LLM 总结压缩: "
                            f"{compress_stats.get('before_tokens')}→{compress_stats.get('after_tokens')} tok"
                        )
                except Exception as e:
                    logger.warning(f"空闲 LLM 压缩失败，回退内联截断: {e}")
            # API 不可用或总结失败 → 内联截断兜底
            if not compressed_ok:
                messages, compress_stats = inline_compress(messages, segment_ref=seg_ref)
                if compress_stats.get("compressed"):
                    compressed_ok = True
                    await _save_compressed_segment(
                        db, agent, conversation_type=conversation_type,
                        group_id=group_id, session_id=session_id,
                        before=before_compress, segment_ref=seg_ref,
                    )
                    logger.info(
                        f"AI {agent.name}({agent.id}) 空闲>12h 内联截断(API不可用): "
                        f"{compress_stats.get('before_tokens')}→{compress_stats.get('after_tokens')} tok"
                    )
            if compressed_ok:
                _auto_compressed = True
                context["_precompressed"] = True
                # 解锁点整套（以前这条路径只压内存、不重写账本 → 下一轮原文又回来了）
                await _unlock_context(
                    db, agent, group_id=group_id, session_id=session_id,
                    conversation_type=conversation_type,
                    summary=(compress_stats.get("summary") or ""),
                    trigger_user_id=trigger_user_id,
                    api_base_url=api_base_url, api_key=api_key,
                )
    except Exception as e:
        logger.warning(f"调用前空闲压缩跳过（非致命）: {e}")

    loop_idx = 0
    # 一轮可能一步都没跑就退出（帧预算用满、步数账借不到），而收尾要用 response 里的
    # reasoning_content。不预置就会在循环耗尽那条路上 UnboundLocalError——整轮异常、
    # 对方一条回复都收不到（2026-11-25 10:29 的线上现象就是它）。
    response: dict = {}
    try:
        while loop_idx < max_loops + _reminder_extra + _closing_extra:
            # 帧预算：小时 / 天的调用次数用满了就停，别把剩下的次数烧在已经没额度的帧里
            if frame_budget_check is not None:
                try:
                    stop_reason = await frame_budget_check()
                except Exception as e:  # noqa: BLE001 —— 预算查询故障不该打断这一轮
                    logger.debug(f"帧预算检查失败（按不限制处理）: {e}")
                    stop_reason = ""
                if stop_reason:
                    logger.info(f"AI {agent.name}({agent.id}) 帧预算用满，本轮停止：{stop_reason}")
                    break
            # 轮次读数：AI 不知道额度还剩几次，就会把每一轮都花在检索上、用尽后静默收尾
            _free_round = _free_next
            _free_next = False
            set_round_budget(messages, round_no=loop_idx + 1, total=max_loops, free=_free_round)
            # 帧额度（docs/dev/frame_turn_lifecycle.md §4）：一步 = 一次模型调用。
            # 不够先向父帧借（每个父帧留 1 步），链底也借不到才停——额度只转移不创造。
            # 白送的那一轮（收尾 / 提醒）不扣：它是平台给的，不是刺激给的。
            if not _free_round:
                from app.services.agent.state_stack_service import consume_step
                _steps_left, _no_steps = await consume_step(
                    db, agent.id,
                    # 扣**本轮处理的会话**那一帧，不是「当前帧」：刺激到达时只摆帧不切帧，
                    # 两者可能不是同一个（2026-11-25 线上就是这么扣错的）
                    context_ref=_context_ref(group_id, session_id, conversation_type),
                    turn_id=_turn_id,
                )
                if not _steps_left:
                    logger.info(f"AI {agent.name}({agent.id}) 帧额度用尽，本轮停止：{_no_steps}")
                    messages.append({
                        "role": "user",
                        "content": ("〔本轮的步数用完了：" + _no_steps + "。〕" + chr(10) + "这一轮到此为止：先把已经做的事收个尾，剩下的下次再说。"),
                    })
                    break
            # ── v0.1.5: 带分类重试的 LLM 调用 ──
            from app.ai.llm import (
                KeyFatalError, RateLimitError, ServerError, SystemLayoutError,
            )
            from app.services.infrastructure.api_key_concurrency import concurrency_mgr

            MAX_SERVER_RETRIES = 2
            excluded_sources: set[str] = set()  # 已尝试失败的来源（tier 级别）
            _excluded_pool_key_id: int | None = None  # 429 限流时排除特定池 Key
            last_error_type = None  # 追踪最后一个错误类型，用于系统通知
            last_error_detail = ""
            current_api_key = api_key
            current_api_base = api_base_url
            current_credit_source = credit_source
            current_pool_key_id = pool_key_id

            response = None
            # 降级重试循环：逐 tier 尝试，KeyFatal → 排除来源 → 下一级
            for key_attempt in range(3):
                # 切换 Key 或 tier 时重新获取配置
                if key_attempt > 0:
                    _prev_source = current_credit_source
                    current_api_key, current_api_base, current_credit_source, current_pool_key_id, _ = \
                        await _get_api_config(db, agent, excluded_sources=excluded_sources,
                                              exclude_pool_key_id=_excluded_pool_key_id,
                                              chatter_id=trigger_user_id)
                    # 没有新 tier 可用 → 跳出
                    if not current_api_key:
                        break
                    # 同 tier 但没换到新 Key（非 pool_key 场景）→ 跳出
                    if current_credit_source == _prev_source and current_credit_source != "pool_key":
                        break

                # 获取并发槽位
                acquired = False
                if current_pool_key_id:
                    # 获取 Key 的 concurrent_limit 用于并发判断
                    from app.models.api_key_pool import ApiKeyPool as ApiKeyPoolModel
                    key_result = await db.execute(
                        select(ApiKeyPoolModel).where(ApiKeyPoolModel.id == current_pool_key_id)
                    )
                    key_row = key_result.scalar_one_or_none()
                    db_limit = getattr(key_row, 'concurrent_limit', None) if key_row else None
                    if not await concurrency_mgr.acquire(current_pool_key_id, model, db_limit):
                        continue  # Key 已满，换下一个
                    acquired = True

                # 流式逐工具分发：回调在 SSE 解析到完整 tool_call 时即刻执行
                _pending_results: list[dict] = []  # {tc_id, result}
                _pending_images: list[dict] = []  # 工具带回的图片 {url, label}，下一轮注入
                _end_turn = False
                # 轮末结算（docs/dev/conversation_history.md §5）：思考留不留 + 留给后面自己的关键信息
                _settlement: dict = {}

                def _repair_json(raw: str) -> dict | None:
                    """尝试修复 LLM 生成的内容字段引号嵌套问题"""
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        pass
                    # JSON 尾部可能因内容过长被截断或引号不闭合
                    # 尝试提取 path + content 两个字段
                    m_path = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
                    if not m_path:
                        return None
                    path = m_path.group(1)
                    m_content = re.search(r'"content"\s*:\s*"(.+)$', raw, re.DOTALL)
                    if m_content:
                        raw_content = m_content.group(1).rstrip()
                        # 去掉末尾可能残留的 , 或 }
                        raw_content = re.sub(r'"?\s*[,}]?\s*$', '', raw_content)
                        # 反转义
                        content = raw_content.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                        return {"path": path, "content": content}
                    return None

                async def _dispatch_one_tool(tc: dict):
                    nonlocal last_task, _end_turn, _settlement, _closing_active, _has_sent_message
                    if _end_turn:
                        # 同一批里前一个工具已 end_turn → 后面的不执行，但**必须**留一条 tool 响应：
                        # assistant(tool_calls) 里每个 id 都要有回应，少一条整次请求 400
                        # （2026-09-14 修的同类线上问题，兜底见 app/utils/pure/tool_chain.py）
                        _pending_results.append({"tc_id": tc.get("id", ""), "result": {
                            "success": False,
                            "error": "本轮已结束（前一个工具调用了 end_turn），该工具未执行",
                        }})
                        return
                    tc_id = tc.get("id", "")
                    func_info = tc.get("function", {})
                    tool_name = func_info.get("name", "")
                    if _closing_active and tool_name not in _CLOSING_TOOLS:
                        # 收尾轮里开新检索 = 把最后一轮也烧掉，直接挡在这里
                        _pending_results.append({"tc_id": tc_id, "result": {
                            "success": False,
                            "error": "收尾轮：工具轮次已用完，只能 send_gm/send_dm/end_turn。"
                                     "请把已有结论发出去。",
                        }})
                        return
                    arguments_str = func_info.get("arguments", "{}")
                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError:
                        arguments = _repair_json(arguments_str)
                        if arguments is None:
                            _pending_results.append({"tc_id": tc_id, "result": {
                                "error": True, "message": f"工具 {tool_name} 参数 JSON 解析失败，且自动修复未能恢复。请尝试减少内容中引号的使用，或分多次 file_edit 写入。原始值前200字符: {arguments_str[:200]}"
                            }})
                            return
                        logger.info(f"AI {agent.name}({agent.id}): 自动修复 {tool_name} 参数 JSON 成功")
                    from app.services.tool_registry import validate_tool_call
                    is_valid, validate_error = validate_tool_call(tool_name, arguments)
                    if not is_valid:
                        logger.warning(f"工具格式校验失败: {validate_error}")
                        _pending_results.append({"tc_id": tc_id, "result": {"error": True, "message": validate_error}})
                        return
                    logger.info(f"AI {agent.name} 调用工具: {tool_name}({arguments})")
                    # 消息类工具推送活动状态（正在输入 / 正在录音），见 _TOOL_ACTIVITY_STATE
                    _activity_state = _TOOL_ACTIVITY_STATE.get(tool_name)
                    if _activity_state and trigger == "user":
                        # 恢复接口回放的是这份内存状态：不同步改它，刷新/切回来就只剩"思考中"
                        from app.ai.response_worker import activity_key, mark_activity_state
                        mark_activity_state(
                            activity_key(conversation_type, group_id=group_id, session_id=session_id),
                            agent.user_id, _activity_state,
                        )
                        _typing_data: dict = {
                            "user_id": agent.user_id, "agent_name": agent.name,
                            "agent_avatar_url": agent.avatar_url, "trigger": trigger,
                            "state": _activity_state,
                        }
                        if conversation_type == "dm" and session_id:
                            _typing_data["session_id"] = session_id
                        elif group_id is not None:
                            _typing_data["group_id"] = group_id
                        _typing_event = {"type": "ai_typing", "conversation_type": conversation_type, "data": _typing_data}
                        try:
                            if conversation_type == "dm" and session_id:
                                await chat_api.broadcast_to_dm(session_id, _typing_event)
                            elif group_id is not None:
                                await chat_api.broadcast_to_group(group_id, _typing_event)
                        except Exception:
                            pass
                    # 任务摘要：只有工具自己声明了"这轮在干什么"才算任务（get_task_summary）。
                    # 曾经用工具名兜底，于是线上留下了「当前任务：调用工具 pop_state」这种幻影任务：
                    # 它会一直挂着，每来一条消息就把"被打断"的时间戳刷新一遍，而 AI 什么也没接。
                    task_summary = _get_tool_task_summary(tool_name, arguments)
                    # 换帧类工具：执行前后各读一次状态链。换掉了就把「被离开的那一段」落进它的桶
                    # （口径见 services/agent/segment_log.py）。执行前那份快照必须留，它 active
                    # 的正是被离开的帧——执行完再读，读到的是新帧。
                    from app.services.agent import segment_log
                    from app.services.agent.state_stack_service import get_frames

                    stack_before = (await get_frames(db, agent.id)
                                    if tool_name in segment_log.SWITCH_TOOLS else None)
                    # 事务不跨外部 I/O：工具可能自己开会话写库（插件就是这么写的），
                    # 带着没提交的写去等它返回，等于把行锁抵押给一个正在等我们的调用——
                    # 2026-11-07 群 81 的自锁死就是这么来的（见 docs/dev/conversation_history.md）。
                    await db.commit()
                    result = await dispatch_tool_call(db, agent.id, group_id, tool_name, arguments, context)
                    if stack_before is not None:
                        await segment_log.record_leave(
                            db, agent, stack_before=stack_before, messages=messages,
                            conversation_type=conversation_type, group_id=group_id,
                            session_id=session_id)
                    if task_summary:
                        last_task = task_summary
                    _pending_images.extend(_take_tool_images(result, tool_name))
                    _pending_results.append({"tc_id": tc_id, "result": result})
                    _note_tool_failure(result, tool_name, _failed_tools, _outward_failures)
                    # 工具总账：工具名 + 失败原因或工具自报的摘要——轮末封存为一条 tool 条目
                    _tool_log.append({"name": tool_name, "note": tool_ledger_note(result)})
                    if isinstance(result, dict) and result.get("end_turn"):
                        _end_turn = True
                        # 收下结算决定（还没接账本：账本封存落地后在这里写条目）
                        _settlement["keep_thinking"] = bool(result.get("keep_thinking"))
                        _settlement["key_note"] = (result.get("key_note") or "").strip()
                    # 追踪 AI 是否已发消息：以「真发出去了」为准（失败只记「没做完」）。
                    # 这一个标记同时管三件事：收尾轮要不要白送、has_output 怎么记、发完消息后能不能压缩
                    if (tool_name in ("send_gm", "send_dm")
                            and isinstance(result, dict)
                            and not result.get("error")
                            and result.get("success") is not False):
                        _has_sent_message = True
                        # AI 刚发了消息→重置压缩标记，允许下一轮清理之前的操作链
                        _auto_compressed = False

                # ── 自动上下文压缩 ──
                # 策略：AI 没发消息时不压缩（保全中间操作链），发过消息后用 LLM 总结重要事件再压缩。
                # 另外 12 小时空闲时内联压缩（缓存已过期）——但要先过 T_idle 的体积门槛（§6）。
                if not _auto_compressed:
                    from app.services.memory.context_compression_service import should_compress, inline_compress, compress_messages, get_compression_thresholds
                    from app.utils.pure.model_window import context_window_for
                    thresholds = await get_compression_thresholds(db)
                    window = context_window_for(model, api_base_url)
                    # 两条路各自的体积门槛（§6）：冷（久未活跃）用更低的 T_idle，
                    # 热（本轮已发过消息）用 T_hot；都不满足就不压——体积是必要条件，空闲只决定时机
                    stale = _is_conversation_idle(messages, hours=12) and should_compress(
                        messages, context_window=window, threshold=thresholds.idle)
                    if stale or (_has_sent_message and should_compress(
                            messages, context_window=window, threshold=thresholds.hot)):
                        # 段号与折叠前原文：三条分支（内联 / LLM 总结 / 总结失败兜底）共用
                        from app.services.history.context_segment_service import new_segment_id
                        seg_ref = new_segment_id()
                        before_compress = messages
                        if stale:
                            # 空闲压缩：直接内联截断（缓存已过期，不浪费 API）
                            messages, compress_stats = inline_compress(messages, segment_ref=seg_ref)
                            if compress_stats.get("compressed"):
                                _auto_compressed = True
                                await _save_compressed_segment(
                                    db, agent, conversation_type=conversation_type,
                                    group_id=group_id, session_id=session_id,
                                    before=before_compress, segment_ref=seg_ref,
                                )
                        else:
                            # AI 刚发了消息：用 LLM 总结重要事件后再压缩，保留关键信息
                            # 压缩必须成功才能清空操作链，否则卡住重试
                            new_messages, compress_stats = await compress_messages(
                                messages,
                                api_base_url=current_api_base,
                                api_key=current_api_key,
                                model=model,
                                user_id=str(agent.id),
                                segment_ref=seg_ref,
                            )
                            if compress_stats.get("compressed"):
                                messages = new_messages
                                _auto_compressed = True
                                await _save_compressed_segment(
                                    db, agent, conversation_type=conversation_type,
                                    group_id=group_id, session_id=session_id,
                                    before=before_compress, segment_ref=seg_ref,
                                )
                            else:
                                logger.warning(
                                    f"AI {agent.name}({agent.id}) LLM 压缩失败"
                                    f"（{compress_stats.get('reason', '未知')}），降级为内联压缩兜底"
                                )
                                # 兜底：不依赖 LLM 的内联截断——避免 LLM 超时/Key 不可用时反复重试耗 API
                                messages, inline_stats = inline_compress(messages, segment_ref=seg_ref)
                                if inline_stats.get("compressed"):
                                    _auto_compressed = True
                                    compress_stats = inline_stats
                                    await _save_compressed_segment(
                                        db, agent, conversation_type=conversation_type,
                                        group_id=group_id, session_id=session_id,
                                        before=before_compress, segment_ref=seg_ref,
                                    )
                        if _auto_compressed:
                            logger.info(
                                f"AI {agent.name}({agent.id}) 上下文压缩完成："
                                f"{compress_stats['before_tokens']} → {compress_stats['after_tokens']} tokens"
                            )
                            try:
                                await _unlock_context(
                                    db, agent, group_id=group_id, session_id=session_id,
                                    conversation_type=conversation_type,
                                    summary=(compress_stats.get("summary") or ""),
                                    trigger_user_id=trigger_user_id,
                                    api_base_url=current_api_base, api_key=current_api_key,
                                )
                                await db.commit()
                            except Exception:
                                pass

                # ── 注入用户忙时消息（中断缓冲）：每步 LLM 调用前都来取一次（见 docs/dev/conversation_history.md §4.2）──
                # 只取本会话的中断：同一个 AI 的缓冲跨会话共用（见 conversation_of）
                pending_msgs = await drain_pending_interrupts(
                    agent.id,
                    group_id=group_id if conversation_type == "group" else None,
                    session_id=None if conversation_type == "group" else session_id,
                )
                if pending_msgs:
                    try:
                        # 刺激传导（docs/dev/frame_turn_lifecycle.md §7）：先取一次当前帧，
                        # 它说的「在」必须是消息进来时他正在做的那一段。只给结构性事实，
                        # 不含别的会话正文（I5）。
                        from app.services.agent.state_stack_service import get_current_frame
                        from app.utils.pure.frame_turn import status_note

                        try:
                            _frame = await get_current_frame(db, agent.id)
                        except Exception:  # noqa: BLE001 —— 取帧失败不该吞掉消息
                            _frame = None
                        state_note = (status_note(_frame, has_sent=_has_sent_message)
                                      if _frame else "")
                        for pm in pending_msgs:
                            if pm.get("type") == "user_message":
                                messages.append(interrupt_message_block(pm, agent.name))
                                if state_note:
                                    # 一段状态只附一次：多条消息共用同一个处境
                                    messages.append({"role": "system", "content": state_note})
                                    state_note = ""
                        logger.info(f"AI {agent.name}({agent.id}): 注入 {len(pending_msgs)} 条中断消息")
                    except Exception:
                        # 注入失败：回写缓冲，避免消息永久丢失
                        logger.warning(
                            f"AI {agent.name}({agent.id}) 中断消息注入失败，回写 {len(pending_msgs)} 条",
                            exc_info=True,
                        )
                        async with _state_lock:
                            old = _pending_interrupts.get(agent.id) or []
                            _pending_interrupts[agent.id] = pending_msgs + old

                # 发请求前先补齐工具链（并行调用被跳过 / 任何中断路径都可能留悬空 tool_calls，
                # 少一条响应整次请求就 400；纯函数校验，O(n)，代价可忽略）
                if heal_tool_chain(messages):
                    logger.warning(f"🔧 AI {agent.name}({agent.id}) 补齐悬空 tool_calls（避免 400）")
                # 请求前同样先提交：模型这一趟要等几十秒，别带着未提交的写干等（同上）。
                await db.commit()
                try:
                    # 内层：同 Key 重试（500/503）
                    for server_retry in range(MAX_SERVER_RETRIES + 1):
                        try:
                            response = await chat_completion(
                                messages=messages,
                                model=model,
                                api_base_url=current_api_base,
                                api_key=current_api_key,
                                tools=tools if tools else None,
                                temperature=effective_cfg["temperature"] or 0.8,
                                top_p=effective_cfg["top_p"] or 0.9,
                                presence_penalty=effective_cfg["presence_penalty"] or 0.5,
                                frequency_penalty=effective_cfg["frequency_penalty"] or 0.5,
                                thinking_enabled=effective_cfg["thinking_enabled"],
                                stream=True,
                                pool_key_id=current_pool_key_id,
                                provider_supports_thinking=provider_supports_thinking,
                                system_layout=system_layout,
                                on_tool_call=_dispatch_one_tool,
                                agent_id=agent.id,
                                db=db,
                            )
                            # 更新池 Key ID（可能已切换）
                            pool_key_id = current_pool_key_id
                            credit_source = current_credit_source
                            api_key = current_api_key
                            api_base_url = current_api_base
                            # 响应回来了 = 这条请求体真的发出去过；本轮带上、还没投递的账本条目
                            # 到此才算「已读」（解锁丢弃只看它，没投出去的原样搬进新账本）
                            if pending_delivery:
                                from app.services.history import history_service
                                await history_service.mark_seen(db, pending_delivery)
                                pending_delivery.clear()
                            break  # 成功
                        except ServerError as e:
                            if server_retry < MAX_SERVER_RETRIES:
                                delay = 2 if e.status_code == 500 else 3
                                logger.warning(
                                    f"AI {agent.name}({agent.id}) 服务器 {e.status_code}，"
                                    f"{delay}s 后同 Key 重试 ({server_retry + 1}/{MAX_SERVER_RETRIES})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                raise  # 同 Key 重试耗尽，抛出给外层

                    break  # 成功，退出 Key 切换循环

                except SystemLayoutError as e:
                    # 布局结论错了：学一次（下一轮生效）。本轮不重放——换新布局要重建
                    # 整个请求，风险与收益不划算；换 Key 也没用，所以不 continue。
                    corrected = False
                    try:
                        from app.services.agent.channel_layout import learn_from_error
                        corrected = await learn_from_error(db, e.base_url, e.message)
                    except Exception:  # noqa: BLE001 —— 学习失败不能盖住本轮错误
                        logger.warning("system 布局自纠正失败（不影响本轮）", exc_info=True)
                    last_error_type = "system_layout_mismatch"
                    last_error_detail = (
                        "已自动记录：这条通道要求 system 放在开头，下一轮生效。" if corrected
                        else "自动记录失败，请手动把「system 摆放」改成「只在开头」。"
                    )
                    logger.error(
                        f"AI {agent.name}({agent.id}) 通道 {e.base_url} 要求 system 在开头"
                        f"（{'已纠正' if corrected else '未纠正'}）"
                    )

                except RateLimitError as e:
                    last_error_type = "rate_limited"
                    last_error_detail = e.message
                    if current_pool_key_id:
                        await concurrency_mgr.mark_rate_limited(current_pool_key_id)
                        _excluded_pool_key_id = current_pool_key_id
                    logger.warning(
                        f"AI {agent.name}({agent.id}) Key #{current_pool_key_id} 429，"
                        f"冷却 60s，换池 Key ({key_attempt + 1}/3)"
                    )
                    continue

                except KeyFatalError as e:
                    fatal_type = "auth_error" if e.status_code == 401 else "insufficient_balance" if e.status_code == 402 else "key_fatal"
                    last_error_type = fatal_type
                    last_error_detail = e.message
                    await _log_key_fatal(db, current_pool_key_id, e.status_code, e.message)
                    excluded_sources.add(current_credit_source)

                    # ── 降级通知：当前 tier 不可用，尝试下一级 ──
                    tier_name = {
                        "agent_key": "AI 自有",
                        "user_key": "你的 API",
                        "pool_key": "系统额度",
                    }.get(current_credit_source, current_credit_source)

                    # 检查是否还有下一级可尝试
                    _next_check = await _get_api_config(
                        db, agent, excluded_sources=excluded_sources,
                        chatter_id=trigger_user_id
                    )
                    has_fallback = _next_check[0] is not None

                    await _send_system_error_notification(db, agent, key_fatal_notice(
                        agent_name=agent.name, source=current_credit_source,
                        status_code=e.status_code, api_key=current_api_key,
                        has_fallback=has_fallback, tier_name=tier_name,
                    ))
                    _notified_fatal = True
                    logger.error(
                        f"AI {agent.name}({agent.id}) {current_credit_source} "
                        f"{e.status_code} 不可用，尝试降级 ({key_attempt + 1}/3)"
                    )
                    continue

                except ServerError as e:
                    last_error_type = "server_error"
                    last_error_detail = f"{e.status_code}: {e.message}"
                    logger.error(
                        f"AI {agent.name}({agent.id}) Key #{current_pool_key_id} "
                        f"{e.status_code} 重试耗尽，最终失败"
                    )

                finally:
                    if acquired and current_pool_key_id:
                        await concurrency_mgr.release(current_pool_key_id)

            # ── 全部重试失败 ──
            if response is None:
                logger.error(f"AI {agent.name}({agent.id}) LLM 调用全部重试失败，last_error={last_error_type}")
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id, has_output=False, model=model,
                )
                # 发送分类系统通知：上面按 tier 已经发过一条更具体的（说明是哪把 Key、
                # 有没有降级空间），这里再补一条就是同一件事说两遍，用户一早上收两条一样的
                error_type = last_error_type or "all_failed"
                if not _notified_fatal:
                    await _send_system_error(db, agent, error_type, last_error_detail,
                                             conversation_type, group_id, session_id)
                return

            if response.get("reasoning_content"):
                _reasoning_log.append(response["reasoning_content"])

            # ── end_turn 已在流式回调中触发 → 补 assistant_msg + tool results 后退出 ──
            if _end_turn:
                assistant_msg = {"role": "assistant", "content": response.get("content")}
                if response.get("tool_calls"):
                    assistant_msg["tool_calls"] = response["tool_calls"]
                if response.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = response["reasoning_content"]
                messages.append(assistant_msg)
                for pr in _pending_results:
                    messages.append({"role": "tool", "tool_call_id": pr["tc_id"],
                                     "content": json.dumps(pr["result"], ensure_ascii=False)})
                _pending_images.clear()   # 本轮已结束，图注入给谁也看不见
                await _seal(response.get("reasoning_content") or "")
                logger.info(
                    f"AI {agent.name}({agent.id}) end_turn 流式触发，本轮结束"
                    f"（结算：keep_thinking={bool(_settlement.get('keep_thinking'))}，"
                    f"key_note={'有' if _settlement.get('key_note') else '无'}）"
                )
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id, has_output=_has_sent_message, model=model,
                    token_usage=total_usage,
                )
                return

            content = response.get("content")
            tool_calls = response.get("tool_calls")
            finish_reason = response.get("finish_reason", "stop")

            # 累积 token 消耗 + API 调用计数
            total_usage["api_calls"] += 1
            usage = response.get("usage", {})
            # 被输出额度/上下文截断、正文一个字都没有的一轮：思考把名额写满了。
            # 本地通道开着思考时已经不发 max_tokens（见 llm._build_chat_payload），这里只留证据——
            # 真发生了要能查出来，而不是只表现为「AI 没回」。不往群里塞提示：那是产品决定，
            # 而且群聊没有 notice 通道（创建助手那套是流式表单才有）。
            if finish_reason == "length" and not (content or "").strip() and not tool_calls:
                logger.warning(
                    f"⚠️ AI {agent.name}({agent.id}) 本轮被截断：finish_reason=length、正文为空，"
                    f"model={model}、usage={usage}"
                )
            if usage:
                for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
                    total_usage[k] += usage.get(k, 0)

            # ── 解析 JSON intent（轻量方案：提示词引导 + 后端解析，不用 response_format）──
            parsed_intent = None
            if content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "intent" in parsed:
                        parsed_intent = parsed["intent"]
                except (json.JSONDecodeError, TypeError):
                    pass

            # ── end_turn / no_action：AI 明确表示本轮结束 ──
            if parsed_intent in ("end_turn", "no_action") and not tool_calls and not _pending_results:
                logger.info(
                    f"AI {agent.name}({agent.id}) intent={parsed_intent}，本轮结束"
                )
                _self_ended = True
                await _seal(response.get("reasoning_content") or "")
                # 与另外三个出口同一条口径：没说出口的轮次不该留下"任务"（见文件末尾那条注释）
                if last_task and _has_sent_message:
                    try:
                        from app.services.agent.workspace_service import save_current_task
                        from app.services.agent.state_stack_service import persist_last_task_as_state
                        await save_current_task(db, agent.id, last_task)
                        await persist_last_task_as_state(
                            db, agent.id, last_task, group_id,
                            context_ref=f"group:{group_id}" if group_id else "",
                        )
                    except Exception:
                        pass
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id,
                    has_output=_has_sent_message, model=model,
                    token_usage=total_usage,
                )
                return

            # ── 提醒：有文字但没有工具调用（兜底机制）──
            reminder_grace = getattr(agent, 'reminder_grace', 'every_time')
            if reminder_grace == 'off':
                reminder_max = 0
            elif reminder_grace == 'once':
                reminder_max = 1
            else:  # 'every_time'
                reminder_max = 10
            # ── 本轮请求没有带工具（模型不接受 function calling）──
            # 工具根本不在它的可选范围里，"改用工具说"是自相矛盾的指令；而且每提醒一次
            # 都要把整段上下文重跑一遍——本地 CPU 推理一轮三分钟，白烧两轮还不一定出声。
            # 文字如实收进账本（_seal 统一认领），与"提醒后仍只说话"同一个出口。
            # 文本协议通道里工具是**可用**的，只是承载换成了正文标记（tool_text_protocol）——
            # 这种轮次照常走下面的提醒，别按"没工具"直接收账本。
            if (response.get("tools_unsupported") and not response.get("tool_text_protocol")
                    and content and not tool_calls and not _pending_results):
                _unsent_text = content.strip()
                logger.info(
                    f"AI {agent.name}({agent.id}) 通道不带工具（模型不支持 function calling），"
                    f"文字收进账本不发: {content[:60]}"
                )
                await _seal(response.get("reasoning_content") or "")
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id,
                    has_output=_has_sent_message, model=model, token_usage=total_usage,
                )
                return

            if content and not tool_calls and not _pending_results:
                # 记下来：这一轮可能被提醒后改走工具收尾，那句话不能跟着 messages 一起没掉
                _unsent_text = content.strip()
                # AI 设置项 plain_text_action（默认 silent）：
                #   send = 直接发到当前状态帧对应的会话——用户明确选了「像别的平台那样说话」；
                #   发不出去（没有会话/异常）就落回下面那套静默留账，不丢内容也不打挂这一轮。
                if _plain_text_send_enabled(agent):
                    if await _deliver_plain_text(
                        db, agent, _unsent_text,
                        conversation_type=conversation_type,
                        group_id=group_id, session_id=session_id,
                        agent_name=agent.name,
                    ):
                        logger.info(
                            f"AI {agent.name}({agent.id}) 只回了文字、按设置直发到当前会话: "
                            f"{_unsent_text[:60]}"
                        )
                        _unsent_text = ""
                        _has_sent_message = True
                        await _seal(response.get("reasoning_content") or "")
                        await _save_log(
                            db, agent, messages, conversation_type,
                            group_id, session_id,
                            has_output=True, model=model, token_usage=total_usage,
                        )
                        return
                # 提醒只发一次：协议讲一遍就够。反复要它「改用工具重说」既烧 token（每一遍都是
                # 整段上下文重跑），也会把模型逼成胡话——曾经一个晚上重试到第 10 遍才憋出一段乱码。
                # 提醒过还只说话，**不替它发**：正文可能是它给自己记的过程，也可能是准备发给另一个
                # 会话的话，平台分不清"这句是给谁的"，代发就是替它说错人。改写进账本（下一轮自己补）。
                if _reminder_extra >= min(reminder_max, 1):
                    logger.info(
                        f"AI {agent.name}({agent.id}) 提醒后仍只返回文字，不代发、写入账本: {content[:60]}"
                    )
                    await _seal(response.get("reasoning_content") or "")
                    await _save_log(
                        db, agent, messages, conversation_type,
                        group_id, session_id,
                        has_output=_has_sent_message, model=model, token_usage=total_usage,
                    )
                    return
                else:
                    logger.info(
                        f"AI {agent.name}({agent.id}) 返回了文字但无工具调用"
                        f"（intent={parsed_intent or '解析失败'}），"
                        f"注入提醒: {content[:80]}"
                    )
                    reminder_assistant_msg = {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [{
                            "id": "system_reminder",
                            "type": "function",
                            "function": {
                                "name": "system_reminder",
                                "arguments": "{}",
                            },
                        }],
                    }
                    if response.get("reasoning_content"):
                        reminder_assistant_msg["reasoning_content"] = response["reasoning_content"]
                    messages.append(reminder_assistant_msg)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": "system_reminder",
                        "content": json.dumps({
                            "reminder": True,
                            "message": _PROSE_REMINDER,
                        }, ensure_ascii=False),
                    })
                    if reminder_grace != 'off':
                        _reminder_extra += 1
                        _free_next = True
                    logger.info(f"AI {agent.name}({agent.id}) system_reminder 注入"
                                f"（grace={reminder_grace}, 额外={_reminder_extra}）")
                    await asyncio.sleep(0.3)
                    continue

            # ── 无工具调用也没有文字 → 退出 ──
            if not tool_calls:
                if last_task and _has_sent_message:
                    try:
                        from app.services.agent.workspace_service import save_current_task
                        from app.services.agent.state_stack_service import persist_last_task_as_state
                        await save_current_task(db, agent.id, last_task)
                        await persist_last_task_as_state(
                            db, agent.id, last_task, group_id,
                            context_ref=f"group:{group_id}" if group_id else "",
                        )
                    except Exception:
                        pass
                # 没走 end_turn 就被收尾：如实入账，别让下一轮以为这轮说过了
                await _seal(response.get("reasoning_content") or "", cutoff=_cutoff_text(
                    "模型只回了文字、没调用工具，本轮由平台结束，没走 end_turn。"
                ))
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id,
                    has_output=_has_sent_message, model=model,
                    token_usage=total_usage,
                )
                return

            # ── 有工具调用 → 执行 ──
            assistant_msg: dict = {"role": "assistant", "content": content}
            assistant_msg["tool_calls"] = tool_calls
            if response.get("reasoning_content"):
                assistant_msg["reasoning_content"] = response["reasoning_content"]
            messages.append(assistant_msg)

            for pr in _pending_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": pr["tc_id"],
                    "content": json.dumps(pr["result"], ensure_ascii=False),
                })
            # 工具带回的图片：紧跟在工具消息之后注入；不注入就丢掉（end_turn 路径）
            if _pending_images:
                _inject_tool_images(messages, _pending_images)
                _pending_images.clear()

            # ── 工具失败提醒（docs/dev/error_handling.md §3.2）──
            # 与「没调工具」的提醒对称：那条管「没做」，这条管「做了但没成」。
            # 一个 turn 只提醒一次——反复提醒既烧 token（每次都是整段上下文重跑），
            # 也会把模型逼成瞎试。
            if _failed_tools and not _tool_fail_reminded:
                _tool_fail_reminded = True
                messages.append({
                    "role": "user",
                    "content": _TOOL_FAILURE_REMINDER.format(
                        tools="、".join(_failed_tools[:5])),
                })
                logger.info(
                    f"AI {agent.name}({agent.id}) 工具失败提醒注入: "
                    f"{'、'.join(_failed_tools[:5])}"
                )
                if _outward_failures and not _outward_notified:
                    _outward_notified = True
                    await _notify_outward_failure(db, agent, _outward_failures)

            # ── 闹钟模式：第一轮工具执行完后注入收尾提醒 ──
            if conversation_type == "alarm":
                messages.append({
                    "role": "user",
                    "content": (
                        "⏰ 闹钟任务已执行。\n"
                        "- 如果任务已完成 → 停止，不要额外发言\n"
                        "- 如果情况有变 → 根据实际情况调整行动\n"
                        "- 如果有新的重要事项 → 可以接着规划执行"
                    ),
                })

            # 有工具结果 → 继续循环让 LLM 看到
            if _pending_results:
                await asyncio.sleep(0.5)
                loop_idx += 1
                _grant_closing_round()
                continue

            # LLM 未请求 tool_calls → 已完成，保存并退出
            if finish_reason != "tool_calls":
                if last_task and _has_sent_message:
                    try:
                        from app.services.agent.workspace_service import save_current_task
                        from app.services.agent.state_stack_service import persist_last_task_as_state
                        await save_current_task(db, agent.id, last_task)
                        await persist_last_task_as_state(
                            db, agent.id, last_task, group_id,
                            context_ref=f"group:{group_id}" if group_id else "",
                        )
                    except Exception:
                        pass
                await _seal(response.get("reasoning_content") or "", cutoff=_cutoff_text(
                    "模型没请求工具就结束了本轮，没走 end_turn。"
                ))
                await _save_log(
                    db, agent, messages, conversation_type,
                    group_id, session_id,
                    has_output=_has_sent_message, model=model,
                    token_usage=total_usage,
                )
                return

            # 短暂延迟，避免过于频繁的 API 调用
            await asyncio.sleep(0.5)
            loop_idx += 1
            _grant_closing_round()

    except asyncio.CancelledError:
        # 重启/关闭会取消在跑的轮次：不在这里收尾，这一轮的检索与思考会连同账本条目一起消失
        await _seal_aborted("本轮被平台取消（重启/关闭），没走 end_turn。")
        raise
    except Exception:
        await _seal_aborted("本轮因异常中断，没走 end_turn。")
        raise

    # 循环耗尽。没走 end_turn 就是被平台收的尾，得如实记进账本——
    # 少了这条，后面的自己只看到一串工具名，会以为那轮已经说过了
    cutoff = "" if _self_ended else _cutoff_text(
        "工具轮次用尽，本轮由平台结束，没走 end_turn。"
    )
    # 没发过消息的轮次里 last_task 只是「最后碰的那个工具」，不是任务，别留成幻影任务
    if last_task and _has_sent_message:
        try:
            from app.services.agent.workspace_service import save_current_task
            await save_current_task(db, agent.id, last_task)
        except Exception as e:
            logger.warning(f"保存当前任务失败: {e}")
    # v0.1.8: LLM 调用后扣除额度
    if total_usage["api_calls"] > 0 and total_usage["total_tokens"] > 0:
        try:
            if conversation_type == "dm" and agent.ai_type in ("general", "semi_general") and trigger_user_id:
                bill_user_id = trigger_user_id
            else:
                bill_user_id = agent.owner_id
            from app.services.infrastructure.quota_service import deduct_credit
            await deduct_credit(
                db,
                user_id=bill_user_id,
                tokens_used=total_usage["total_tokens"],
                source=credit_source,
                pool_key_id=pool_key_id,
                agent_id=agent.id,
                model=model,
            )
        except Exception as e:
            logger.warning(f"  扣除额度失败（不阻塞主流程）: {e}")

    await _seal(response.get("reasoning_content") or "", cutoff=cutoff)
    await _save_log(
        db, agent, messages, conversation_type,
        group_id, session_id,
        has_output=_has_sent_message, model=model,
        token_usage=total_usage,
    )


# ============================================================
# 速率限制
# ============================================================

def _check_rate_limit(agent_id: int) -> bool:
    """
    检查速率限制（简单内存实现）。
    返回 True 表示允许调用。
    """
    now = time.monotonic()
    last_call = _rate_limit_tracker.get(agent_id, 0)
    min_interval = 1.0 / settings.rate_limit_per_second

    if now - last_call < min_interval:
        return False

    _rate_limit_tracker[agent_id] = now
    return True


# ============================================================
# Key 致命错误日志
# ============================================================

async def _log_key_fatal(db, pool_key_id: int | None, status_code: int, message: str) -> None:
    """记录 402/401 致命错误到系统日志，通知管理员"""
    if pool_key_id is None:
        return
    try:
        from app.models.api_key_pool import ApiKeyPool as ApiKeyPoolModel
        key_result = await db.execute(
            select(ApiKeyPoolModel).where(ApiKeyPoolModel.id == pool_key_id)
        )
        key_row = key_result.scalar_one_or_none()
        key_name = key_row.name if key_row else f"#{pool_key_id}"
        logger.warning(
            f"  ⚠️ 系统通知：API Key 池「{key_name}」({pool_key_id}) 发生致命错误："
            f"{_key_fatal_reason(status_code)}，请管理员检查。详情: {message[:200]}"
        )
    except Exception as e:
        logger.error(f"记录 Key 致命错误失败: {e}")


# ============================================================
# 对话日志保存
# ============================================================

async def _save_conversation_log_safe(
    db, agent, messages: list[dict],
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    has_output: bool = False,
    model: str | None = None,
    token_usage: dict | None = None,
    api_meta: dict | None = None,
) -> None:
    """安全保存对话日志（失败不影响主流程）

    落库前把图片字节换成 [图片] 占位：日志是整份请求体的快照，一张 480×270 的
    base64 就几十 KB，留着既撑大账本，回看时也没有人读那些字节。
    """
    try:
        from app.services.content.conversation_log_service import save_conversation_log
        from app.utils.multimodal import strip_image_bytes
        await save_conversation_log(
            db,
            agent_id=agent.id,
            messages=strip_image_bytes(messages),
            conversation_type=conversation_type,
            group_id=group_id,
            session_id=session_id,
            token_usage=token_usage,
            has_output=has_output,
            model=model,
            thinking_enabled=bool(agent.thinking_enabled),
            api_meta=api_meta,
        )
    except Exception as e:
        logger.warning(f"保存对话日志失败 (agent={agent.id}): {e}")


# ============================================================
# 检查对话是否闲置
# ============================================================

# 解锁（压缩成功后的整套收尾）的实现在 services/history/context_unlock.py：AI 主动
# compress_context 也要走同一份收尾，留在本模块里工具就够不着。清单仍是契约（少做一样就是
# 半解锁），按老路径 import 的调用方与测试照旧——这里只做转发。
from app.services.history.context_unlock import UNLOCK_STEPS, unlock_context  # noqa: E402,F401


def _context_ref(group_id, session_id, conversation_type) -> str:
    """本会话在账本里的键（群 vs 私信）——实现在 context_unlock（那边也要用同一份）"""
    from app.services.history.context_unlock import context_ref_for
    return context_ref_for(conversation_type, group_id=group_id, session_id=session_id)


def _note_tool_failure(result, tool_name: str, failed: list[str],
                       outward: list[dict]) -> None:
    """把一次工具失败记进本轮的两个桶（普通失败 / 对外动作失败）。

    只认 error=True 的结果。INVALID_ARGS / UNKNOWN_TOOL 不进桶：模型自己知道改参数，
    再提醒一遍只会让它把同一个错误重试一次。
    """
    if not isinstance(result, dict) or not result.get("error"):
        return
    code = str(result.get("error_code") or "")
    if code in ("INVALID_ARGS", "UNKNOWN_TOOL"):
        return
    if tool_name not in failed:
        failed.append(tool_name)
    if result.get("outward") or tool_name in _OUTWARD_TOOLS:
        outward.append({
            "tool": tool_name,
            "detail": str(result.get("message") or "")[:200],
        })


# 同一 AI 同一对外工具 10 分钟内只发一次私信：一个坏参数会把 owner 刷屏
_outward_notice_seen: dict[tuple[int, str], float] = {}
OUTWARD_NOTICE_INTERVAL_SECONDS = 600


async def _notify_outward_failure(db, agent, failures: list[dict]) -> None:
    """对外动作失败 → 给 owner 发一条私信（模型已收到提醒，用户也要知道）。

    「以为发了」比「没发」更难查：这条私信回答的是「用户为什么没收到」。
    """
    if not failures:
        return
    first = failures[0]
    tool = str(first.get("tool") or "")
    key = (agent.id, tool)
    now = time.monotonic()
    last = _outward_notice_seen.get(key)
    if last is not None and now - last < OUTWARD_NOTICE_INTERVAL_SECONDS:
        return
    _outward_notice_seen[key] = now
    detail = "；".join(
        f"{f.get('tool')}: {f.get('detail')}" for f in failures[:3])
    await _send_system_error(
        db, agent, "tool_outward_failed", detail, tool_name=tool)


async def _save_compressed_segment(db, agent, *, conversation_type, group_id,
                                   session_id, before, segment_ref) -> None:
    """把折叠前的原文存成一段，供 expand_context 按段号取回。

    存段是旁路：失败只记一行日志，不能让这一轮回复跟着失败——压缩本身已经成功，
    原文丢了顶多是 AI 之后看不到细节。
    """
    try:
        from app.services.history.context_segment_service import save
        await save(
            db, segment_id=segment_ref, agent_id=agent.id,
            context_ref=(f"group:{group_id}" if conversation_type == "group"
                         else f"dm:{session_id}"),
            messages=before,
            conversation_type=conversation_type,
            group_id=group_id, session_id=session_id,
        )
    except Exception as e:  # noqa: BLE001 —— 旁路失败不影响本轮
        logger.warning("压缩段保存失败（不影响本轮）: %s", e)


async def _seal_turn(db, agent, *, group_id, session_id, conversation_type,
                     tool_log: list[dict], settlement: dict, reasoning: str = "",
                     cutoff: str = "", unsent: tuple[str, str] | None = None) -> None:
    """**轮末封存**：把轮内的东西（工具轮历史 + 轮末结算）写进账本。

    不封存的话，AI 下一轮只看得见消息、看不见自己上一轮干了什么——工具轮历史原本只活在当轮
    messages 里，轮一结束就没了。这里是它进账本的唯一入口。

    key_note 有两处去处，别只看到账本那条：它还写一行经历（别的会话里的自己想起它）。
    经历写入失败不回滚账本——「这一轮干了什么」当场要留，跨场景那一路只是锦上添花。
    """
    from app.services.history.context_sync import append_events
    from app.tools.base import ToolRegistry
    from app.utils.pure.history import (
        cutoff_entry, handoff_entry, ledger_worthy_tools, thinking_entry, tools_entry,
        unsent_text_entry,
    )

    context_ref = _context_ref(group_id, session_id, conversation_type)

    entries = []
    # 账本只留失败与有持久副作用的结果：读类调用成功只回一个 ok，进账本没有信息量，
    # 而压缩时事件条目原样搬运，每轮一条会越积越长（线上单段会话见过 164 条）。
    notable = ledger_worthy_tools(tool_log or [], ToolRegistry.has_persistent_effect)
    if notable:
        entries.append(tools_entry(notable))
    if unsent:
        # 只说了话没发出去：把那句话和平台当时的提醒一起留给下一个自己（见 history.unsent_text_entry）
        entries.append(unsent_text_entry(*unsent))
    if cutoff:
        entries.append(cutoff_entry(cutoff))
    note = (settlement.get("key_note") or "").strip()
    if note:
        entries.append(handoff_entry(note))
    if settlement.get("keep_thinking") and (reasoning or "").strip():
        entries.append(thinking_entry(reasoning))
    if entries:
        await append_events(db, agent, context_ref, entries)
    if note:
        await _record_key_note_experience(db, agent, context_ref, group_id, note, tool_log)


async def _record_key_note_experience(db, agent, context_ref: str, group_id: int | None,
                                      note: str, tool_log: list[dict]) -> None:
    """把 key_note 落成一行经历：别的场景里的自己据此想起「我在那儿干过什么」。

    场景取**当前帧**（当前帧）的 context_ref，不取会话键：插件帧、世界帧都是自己的一段，
    在插件帧里干的事要落在插件帧的场景上，而不是落回它所在的群。
    """
    from app.utils.pure.history import tools_summary

    try:
        from app.models.agent_experience import OWNER_AI
        from app.services.agent.state_stack_service import current_scene
        from app.services.experience import experience_service

        active = await current_scene(db, agent.id)
        scene = active.get("context_ref") or context_ref
        await experience_service.record(
            db, owner_type=OWNER_AI, owner_id=agent.id, context_ref=scene,
            group_id=group_id, me_user_id=agent.user_id or 0, summary=note,
            source=experience_service.SOURCE_KEY_NOTE, tools=tools_summary(tool_log),
            frame_type=active.get("type") or "", label=active.get("label") or "",
        )
    except Exception as e:
        logger.warning(f"轮末经历写入失败（非致命）: {e}")


async def _unlock_context(db, agent, *, group_id, session_id, conversation_type,
                          summary: str, trigger_user_id: int | None = None,
                          api_base_url: str | None = None,
                          api_key: str | None = None) -> tuple[str, ...]:
    """解锁点收尾——实现在 services/history/context_unlock.py（工具压缩也要走同一份）

    api_base_url / api_key 是这一轮真正走的通道：压缩后要按它卸载远端图片，
    换过 key 的话旧作用域的行也会一起减掉（见 deepseek_files.release_context_images）。
    """
    return await unlock_context(
        db, agent,
        group_id=group_id, session_id=session_id, conversation_type=conversation_type,
        summary=summary, trigger_user_id=trigger_user_id,
        api_base_url=api_base_url, api_key=api_key,
    )


def _is_conversation_idle(messages: list[dict], hours: int = 12) -> bool:
    """检查对话是否闲置——缓存大概率已过期，应强制压缩。

    两种判定（2026-08-13 补：对话跨度）：
    1. 最后一条消息距现在超过 N 小时（原逻辑：长时间没对话）
    2. 对话跨度超过 N 小时（首条 user 消息到最后一条，跨天堆积也触发）——
       修复场景：12 天没对话但今天刚发消息，历史堆积 138 条不压缩，直接带全量硬跑
    """
    # 消息内容里的时间戳是**北京时间**（format_time_shanghai）：以前当 UTC 裸时间比，
    # 只要"上海时刻 > 当前 UTC 时刻"（东八区，一天里大半时间成立）就被判成"未来"、
    # 走年-1 兜底 → 变成去年 → 空闲判定恒真、每轮都压缩（2026-09-25 用户实测）
    tz_cn = get_zoneinfo("Asia/Shanghai")
    now = datetime.now(tz_cn)

    def _parse_ts(content: str):
        m2 = re.search(r'\[([A-Za-z_]+) (\d{2}-\d{2} \d{2}:\d{2})\]', content)
        if not m2:
            return None
        time_str = m2.group(2)
        for offset in (0, -1):
            try:
                year = now.year + offset
                msg_time = datetime.strptime(f"{year}-{time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=tz_cn)
                if msg_time > now:
                    continue          # 真·未来：只可能是跨年，退一年再看
                return msg_time
            except ValueError:
                pass
        return None

    first_ts = None
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        ts = _parse_ts(content)
        if ts is not None:
            first_ts = ts
            break

    last_ts = None
    for m in reversed(messages):
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        ts = _parse_ts(content)
        if ts is not None:
            last_ts = ts
            break

    # 判定 1：最后消息距今超过 N 小时
    if last_ts is not None and (now - last_ts).total_seconds() > hours * 3600:
        return True
    # 判定 2：对话跨度超过 N 小时（历史堆积跨天也压缩）
    if first_ts is not None and last_ts is not None:
        span = (last_ts - first_ts).total_seconds()
        if span > hours * 3600:
            return True
    return False
