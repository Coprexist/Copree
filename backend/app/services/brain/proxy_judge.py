"""代理 LLM —— 判断「这条消息值不值得拉起本尊」。

位置：`ai/decider.py` 的 Gate 4/5。前面的确定性门（静音 / 屏蔽 / 勿扰 / 离线 /
mention_only / @ 本人 / @all / 群公告 / 特别关心）在它之前，它看不到也不该看到——
那些是硬否决，LLM 没资格翻。它只回答最后一句：要不要起身。

上下文按稳定度从高到低拼（角色 → 心情 → 场景 → 规则 → 消息）：缓存命中的是最长公共
前缀，顺序颠倒等于白付缓存。最近记忆取**账本最近几条**，不按「与本条消息的相关度」现查——
那样每条消息前缀都变。

失败即回退：超时、解析失败、没有可用 Key 一律返回 None，调用方退回 calc_reply_willingness。
口径见 docs/dev/decision_proxy_llm.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import desc, select

from app.config import settings

logger = logging.getLogger(__name__)

# 角色自述在提示里的上限：它只是「这个人是谁」的一句话摘要，不是设定全文
ROLE_BRIEF_MAX_CHARS = 160
# 最近的对话：时间窗与条数**取小**。只看条数，寂寞群会给很久以前的话；
# 只看时间，热闹群一口气灌进几十条。两个都对才叫"刚才在聊什么"。
RECENT_LEDGER_LIMIT = 10
RECENT_LEDGER_WINDOW_MINUTES = 5

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass
class ProxyVerdict:
    """一次判定的结果"""

    wake: bool
    reason: str
    urgency: str = "normal"
    fallback: bool = False
    model: str = ""
    latency_ms: int = 0


# 账本 actor 记号 → 给代理看的人话（"self" 是 AI 自己，绝不能原样丢给模型猜）
_SPEAKER = {"self": "你", "user": "群成员", "world": "群成员"}


def _collapse(text: str, limit: int) -> str:
    """压成一行并截断：提示里的每个块都要能估出长度"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _parse_verdict(raw: str) -> ProxyVerdict | None:
    """从模型输出里取 JSON。带 ``` 围栏、带前后废话都认；取不到就当作判不了。"""
    if not raw:
        return None
    text = raw.strip()
    try:
        data = json.loads(text)
    except Exception:
        match = _JSON_BLOCK.search(text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(data, dict) or "wake" not in data:
        return None
    urgency = str(data.get("urgency") or "normal").strip().lower()
    return ProxyVerdict(
        wake=bool(data.get("wake")),
        reason=_collapse(str(data.get("why") or data.get("reason") or ""), 40),
        urgency=urgency if urgency in ("low", "normal", "high") else "normal",
    )


def build_context(agent, frame: dict, history: list[dict], message_text: str,
                  *, group_rules: str = "", announcement: str = "",
                  attachment_note: str = "") -> list[dict]:
    """极简上下文（纯函数）：规则 → 场景 → 群规 → 公告 → 角色 → 心情 → 消息。

    顺序按变化频率从低到高排：判定规则全局同一份，场景一帧内不动，群规按群，
    角色按人，心情按此刻，对话每条都变。越靠前的部分能命中的前缀越长。

    group_rules / announcement：群主写在群上的标准与公告（后者是否给代理看由群开关决定）。
    没有就整段不出现——空串不是"这个群没有规矩"，是"这里没东西可写"。
    """
    name = getattr(agent, "name", "") or "?"
    role = _collapse(getattr(agent, "current_system_prompt", "") or "", ROLE_BRIEF_MAX_CHARS)

    scene_lines = [f"你在群「{frame.get('label') or frame.get('context_ref') or ''}」。"]
    for key, title in (("doing", "正在做"), ("why", "为什么在这"), ("todo", "回去要做")):
        value = _collapse(frame.get(key) or "", 120)
        if value:
            scene_lines.append(f"{title}：{value}")

    parts = [
        (
            f"判断下面这条群消息，要不要让「{name}」进来说话或做事。以这段说明为准，"
            "下面的情形只是参考，用得上的时候再套，不必逐条对。\n"
            '回一个 JSON：{"wake": true|false, "why": "不超过20字", "urgency": "low|normal|high"}\n'
            "\n"
            "起身一次有成本：读一遍上下文、调一次模型。值得才起身。\n"
            "\n"
            "通常不必起身：话是对别人说的；明显没有内容；你正忙要紧的事，而它只是群里的气氛话。\n"
            "通常可以起身：叫你、接你的话、问你、派活；群里的招呼、道晚安、感慨这类，你在场就接得上。\n"
            "同伴发来图片或文件（哪怕没配文字）通常是给你看东西，值得起身看一眼；"
            "只在明显与你无关、或你正忙更要紧的事时才不必。\n"
            "下面「你：」开头的那几行是你自己在这个群里说过的话。新消息如果是在接你的话"
            "（你请对方发东西、你问了对方），通常该起身。\n"
            "\n"
            "起身是为了接得上或者有事要做，不是为了表示在场。"
        ),
        "\n".join(scene_lines),
    ]

    owner_rules = _collapse(group_rules or "", 300)
    if owner_rules:
        parts.append(f"群主给这个群定的规矩（判定时以它为准）：{owner_rules}")
    if announcement:
        parts.append(f"本群公告：{_collapse(announcement, 240)}")

    rules = _collapse(frame.get("proxy_rules") or "", 300)
    if rules:
        parts.append(f"这个群的规矩（你自己定过）：{rules}")

    who = [f"你是「{name}」。"]
    if role:
        who.append(f"你是谁：{role}")
    mood = _collapse(frame.get("emotion_text") or "", 80)
    if mood:
        who.append(f"此刻心情：{mood}")
    parts.append("\n".join(who))

    # 账本里的 actor 是英文记号：给代理看的人话——"self"必须说成"你"，否则它读成一个叫
    # self 的陌生人，判"没叫我"就完全合理了（2026-11-14 真机：AI 刚说"你随便发一张，我当场
    # 念给你听"，下一张图代理却判"只是发图，没叫我"，正是把它自己的话看成了别人说的）
    tail = ["群消息（时间从早到晚）："]
    for entry in history:
        text = _collapse(entry.get("content") or "", 200)
        if not text:
            continue
        speaker = _SPEAKER.get(str(entry.get("actor") or ""), "群成员")
        tail.append(f"- {speaker}：{text}")
    # 附件必须点明：正文里图片会被压成 [图片]，不写清"带了什么"，代理就会当成"没内容"
    # （2026-11-14 真机：代理判"只是发图，没叫我或派活"→ 不唤醒，AI 因此一直没看到那张图）
    # @ 到谁由调用方在正文里注好（decider._mention_annotated）：<@!id>（名字）（不是你）。
    # 裸令牌加上去，模型会拿它跟"我是谁"比对——2026-11-14 群 77 就是这么把它判成点名的
    tail.append(f"- 刚到的这条：{_collapse(message_text, 400)}{attachment_note}")

    return [
        {"role": "system", "content": "\n\n".join(parts)},
        {"role": "user", "content": "\n".join(tail)},
    ]


async def recent_ledger(
    db, agent_id: int, context_ref: str, *,
    limit: int = RECENT_LEDGER_LIMIT,
    minutes: int = RECENT_LEDGER_WINDOW_MINUTES,
    exclude_text: str = "",
    message_id: int | None = None,
) -> list[dict]:
    """最近的对话（新的在后）：最多 limit 条、只取 minutes 分钟内，两个上限取小。

    只认 `kind="message"`——账本里还混着缺口、便签、提醒与工具总账，那些不是"他们在聊什么"，
    掺进去只会让判定分心。exclude_text 用来去掉"待判的那条已经在账本尾巴里"的重复。
    """
    from app.models.agent import AgentHistoryEntry
    from app.utils.pure.timeutil import utc_now

    if not context_ref:
        return []
    try:
        rows = (await db.execute(
            select(AgentHistoryEntry)
            .where(AgentHistoryEntry.agent_id == agent_id,
                   AgentHistoryEntry.context_ref == context_ref,
                   AgentHistoryEntry.kind == "message")
            .order_by(desc(AgentHistoryEntry.seq))
            .limit(limit)
        )).scalars().all()
    except Exception as e:  # noqa: BLE001 —— 账本读失败不该拦住判定
        logger.debug("代理 LLM：读账本失败（非致命）: %s", e)
        return []

    cutoff = utc_now() - timedelta(minutes=max(1, minutes))
    # 时间在 Python 侧比：created_at 是库里的裸 UTC，交给数据库做时间算术就得按方言写
    picked = [r for r in rows if r.created_at is not None and r.created_at >= cutoff]
    out = [{"actor": r.actor, "content": r.content, "ref": r.ref} for r in reversed(picked)]

    # 待判消息通常还没进账本（账本在开轮时同步），但重投/重排时可能已经在了。
    # 按**消息 id** 去掉它自己：按正文比对会误伤——图片那条正文就是 "[图片]"，" [图片]" 这种
    # 短正文很容易命中上一条真历史（2026-11-14 发现：代理因此少看一句上下文）。
    if out and message_id and str(out[-1].get("ref") or "") == str(message_id):
        out.pop()
        return out
    trigger = _collapse(exclude_text, 80)
    if out and trigger and trigger in out[-1]["content"]:
        out.pop()
    return out


async def judge(
    db, agent, frame: dict, *,
    message_text: str,
    api_base: str,
    api_key: str | None,
    group_rules: str = "",
    announcement: str = "",
    attachment_note: str = "",
    message_id: int | None = None,
) -> ProxyVerdict | None:
    """问一次代理：要不要拉起本尊。判不了返回 None（调用方回退启发式）。

    group_rules / announcement：群主给这个群定的标准与公告（后者受群开关控制）。
    """
    if not api_key:
        return None

    model = settings.proxy_wake_model or settings.default_chat_model
    history = await recent_ledger(
        db, agent.id, str(frame.get("context_ref") or ""), exclude_text=message_text,
        message_id=message_id,
    )
    messages = build_context(agent, frame, history, message_text,
                             group_rules=group_rules, announcement=announcement,
                             attachment_note=attachment_note)

    from app.ai.llm import chat_completion   # 延迟导入：避免 ai.llm ↔ services 循环

    started = time.monotonic()
    try:
        response = await asyncio.wait_for(
            chat_completion(
                messages=messages,
                model=model,
                api_base_url=api_base,
                api_key=api_key,
                temperature=0.0,
                max_tokens=120,
                response_format={"type": "json_object"},
                thinking_enabled=False,   # 短判断：思考只会抢预算
                stream=False,
            ),
            timeout=settings.proxy_wake_timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        logger.info("代理 LLM 超时（%sms），回退启发式", settings.proxy_wake_timeout_ms)
        return None
    except Exception as e:  # noqa: BLE001 —— 代理挂了不能拦住唤醒
        logger.warning("代理 LLM 调用失败，回退启发式: %s", e)
        return None

    verdict = _parse_verdict((response or {}).get("content") or "")
    if verdict is None:
        logger.info("代理 LLM 输出解析失败，回退启发式: %s", str((response or {}).get("content"))[:120])
        return None
    verdict.model = model
    verdict.latency_ms = int((time.monotonic() - started) * 1000)
    return verdict


async def cached_verdict(db, agent_id: int, message_id: int | None) -> dict | None:
    """同一条消息判过就复用（重投/重排不该再问一次）"""
    if message_id is None:
        return None
    from app.models.proxy_judgement import ProxyJudgement

    row = (await db.execute(
        select(ProxyJudgement).where(
            ProxyJudgement.agent_id == agent_id,
            ProxyJudgement.message_id == message_id,
        )
    )).scalars().first()
    if row is None:
        return None
    return {"wake": row.wake, "reason": row.reason, "urgency": row.urgency,
            "fallback": row.fallback, "model": row.model, "latency_ms": row.latency_ms}


async def record(db, agent_id: int, frame: dict, *, message_id: int | None,
                 verdict: ProxyVerdict) -> None:
    """留痕：一行一次判断。唯一的写入口，失败只记日志。"""
    from app.models.proxy_judgement import ProxyJudgement

    try:
        existing = await cached_verdict(db, agent_id, message_id)
        if existing is not None:
            return
        db.add(ProxyJudgement(
            agent_id=agent_id,
            frame_type=str(frame.get("type") or ""),
            context_ref=str(frame.get("context_ref") or ""),
            message_id=message_id,
            wake=bool(verdict.wake),
            reason=verdict.reason,
            urgency=verdict.urgency,
            fallback=bool(verdict.fallback),
            model=verdict.model,
            latency_ms=int(verdict.latency_ms or 0),
        ))
        await db.flush()
    except Exception as e:  # noqa: BLE001 —— 留痕失败不影响这一轮的判定
        logger.warning("代理 LLM 判定留痕失败（非致命）: %s", e)


async def stats(db, agent_id: int) -> dict:
    """最近窗口的判定统计（自动停用的唯一数据源）"""
    from app.models.proxy_judgement import ProxyJudgement

    window = max(1, int(settings.proxy_wake_sample_window))
    rows = (await db.execute(
        select(ProxyJudgement)
        .where(ProxyJudgement.agent_id == agent_id)
        # 按自增 id 取窗口：created_at 同秒的行分不出先后，窗口会随查询计划漂
        .order_by(desc(ProxyJudgement.id))
        .limit(window)
    )).scalars().all()
    judged = [r for r in rows if not r.fallback]
    total = len(judged)
    woke = sum(1 for r in judged if r.wake)
    return {
        "total": total,
        "wake": woke,
        "saving": total - woke,
        "wake_ratio": (woke / total) if total else 0.0,
        "saving_ratio": ((total - woke) / total) if total else 0.0,
    }


def _group_ids(raw: str) -> set[int]:
    """逗号/空白分隔的群 id 名单。认不出的项忽略——配置是手写的，坏一项不该整条失效。"""
    return {int(part) for part in re.split(r"[,\s]+", raw or "") if part.isdigit()}


def shadow_groups() -> set[int]:
    """留白名单（PROXY_WAKE_SHADOW_GROUPS）：总开关打开时仍只判不改的群。"""
    return _group_ids(settings.proxy_wake_shadow_groups)


def enforced_for(group_id: int | None) -> bool:
    """这一条消息的判定要不要真的生效。

    总开关没开就一律影子；开了以后，留白名单里的群继续只判不改——氛围群的日常接话
    正是代理会压掉的那部分，先把它们留出来，其余群按判定执行。名单外判错了影响面最小，
    回滚就是把群号从名单里删掉（或关总开关）。
    """
    if not settings.proxy_wake_enforce:
        return False
    return group_id is None or int(group_id) not in shadow_groups()


async def worthwhile(db, agent_id: int) -> bool:
    """还值不值得经代理：满样本量后，拦下率不到下限就停用（直接放行）。

    94% 与 6% 是同一个门槛的两面，这里只认 min_saving_ratio。
    """
    sample = await stats(db, agent_id)
    if sample["total"] < max(1, int(settings.proxy_wake_sample_min)):
        return True
    return sample["saving_ratio"] >= float(settings.proxy_wake_min_saving_ratio)