"""
决策技能（Decision Skill）— 事件 → AI 自写规则 → 程序化处理 or 唤醒本体。

归属 AI 个体：群 AI（agent 居民）与群助手各自持有自己的规则，用平台工具
（list / write / delete_decision_skill）自配置。存储：AI → agent_skills(skill_type='decision')，
群助手 → group_assistants.config['decision_rules']。情景表、三态返回、do 的分派
与触发点见 docs/dev/decision_layer.md。
"""
from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.repositories.world_repo import SQLAlchemyWorldRepository
from app.utils.display_name import display_names
from app.utils.pure.timeutil import local_time_fields
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyWorldRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyWorldRepository(db_or_repo)
    return db_or_repo


# ═══════════════════════════════════════════════════════════
# 条件 DSL 解析（递归逻辑树 + 字段运算）
# ═══════════════════════════════════════════════════════════

# 求值器只有一份（utils/pure/conditions.py）：触发组合规则与决策技能共用同一套条件语义。
# 这里保留同名导入，决策层内外沿用 match_conditions 这个名字，不必知道它搬去了哪。
from app.utils.pure.conditions import explain_conditions, match_conditions  # noqa: E402


# ═══════════════════════════════════════════════════════════
# 技能校验
# ═══════════════════════════════════════════════════════════

_MAX_RULES = 20             # 每实体技能上限
# 两个上限不是一回事，别合成一个：reply 是一条群消息的量级，脚本正文是源代码。
# 脚本正文放宽到 5 万字也不会撑上下文——前提是列表/试跑回显走 brief_do（见下）。
_MAX_REPLY_CHARS = 4000     # reply_template 代发文本上限（字符）
_MAX_SCRIPT_CHARS = 50000   # run_script 脚本正文上限（字符）
_BRIEF_CODE_CHARS = 1000    # 回显时脚本正文保留的长度，超出只给开头 + 全文长度

# 命中统计（平台写、AI 只读）：存在规则 config 的 _stats 键里，跟着规则走，不需要新表。
# 读法是「哪条一直在动、哪条从没动过」——没有它，线上命中/未命中都不留痕，规则死没死只能靠猜。
_STATS_KEY = "_stats"
_STATS_RECENT = 20          # 每条规则最近命中的保留条数（"一直在误触"靠它看出来）
_STATS_PREVIEW = 60         # 命中日志里正文的截断长度
_STATS_BRIEF_RECENT = 5     # 列表回显只给最后几条：20 条全量回显会把上下文撑满
_VERIFY_MAX_SAMPLES = 8     # 一次启用前检验最多跑几条样例（几条正例 + 几条反例就够）

_DO_ACTIONS = ("reply_template", "call_tool", "run_script", "silent")
# do 没办成时谁接这件事：默认交回本体（不吞错），另外两档是写规则的人自己的偏好。
# fallback_reply 需要 error_reply 文本；silent 与 fallback_reply 都必定写一条账本通知。
_ON_ERROR = ("wake", "fallback_reply", "silent")

# 预置情景：写错的 event 等于永远不触发，故当场拒绝并列出可选值（情景表见 docs/dev/decision_layer.md）
SCENARIOS: dict[str, str] = {
    "group_message": "群消息（content/content_clean/content_len/sender_id/sender_name/sender_type/group_id/is_mention/is_at_all/group_type）",
    "member_join": "有人入群（member_id/member_name/operator_id/operator_name）",
    "member_leave": "有人退群（member_id/member_name/operator_id/operator_name）",
    "scheduled": "定时到点（trigger/task）",
    "friend_request": "收到好友申请（requester_id/requester_name/message/request_id）",
    "world_event": "世界事件（name/title/world_id/group_id/payload_*）",
}


def rule_schema_desc() -> str:
    """决策技能的结构说明 —— 工具描述的唯一来源（改情景只改 SCENARIOS）"""
    events = "；".join(f"{name}（{fields}）" for name, fields in SCENARIOS.items())
    return (
        "配置你自己的决策技能：声明「遇到什么情景我干什么、是否必须唤醒我本体」。"
        f"结构：{{name, when:{{event, conditions}}, do:{{action,...}}, notify, on_error}}。"
        f"event 支持：{events}。"
        "所有情景都能读到的公共字段：now（HH:MM）/ today / weekday / hour。"
        "触发条件两种最常用写法（content_clean = 去掉 @ 令牌后的正文）："
        "「整句才触发」= {\"content_clean\":\"签到\"}；"
        "「提到就触发且不通知你」= {\"content_clean_contains\":\"签到\"}（慎用，可能会误触正常聊天）。"
        "要带运算就写 {field, op, value}，照抄改值即可："
        "{\"field\":\"content_clean\",\"op\":\"starts_with\",\"value\":\"训练\"}、"
        "{\"field\":\"content_clean\",\"op\":\"matches\",\"value\":\"^(训练|游玩)\"}、"
        "{\"field\":\"content_len\",\"op\":\"gt\",\"value\":0}"
        "（op 可 eq/ne/contains/starts_with/matches/similar/gt/gte/lt/lte/in；similar 认错别字）；"
        "也可写 {\"content_clean\":{\"starts_with\":\"训练\"}}，一个字段挂多个运算 = 同时满足。"
        "并可 and/or/not 自由嵌套；拿不准先 test_decision_skill 试跑一条样例。"
        "启用前检验 verify 建议给数组（正例 + 反例都要）：每条 {event, content}，"
        "expect 默认 \"hit\"（该命中），反例写 \"miss\"（不该命中）——"
        "平台全跑一遍，任何一条不符预期就拒绝落地；只验正例只证明会命中，证明不了不误触。"
        "do 四选一：reply_template（{action, reply} 固定回复，零成本；可用占位 "
        "{sender_name} {sender_id} {group_id} {content} {now}）/ call_tool（{action, name, arguments} 调平台工具）/ "
        "run_script（{action, code} 或 {action, entry} 沙箱脚本：在你自己的文件空间里跑，不能联网。"
        f"code 是脚本正文（≤{_MAX_SCRIPT_CHARS} 字）；更长的脚本先用 run_script 工具存成文件（path），"
        "技能里只写 entry 指它（如 scripts/daily.py）——存的地方就是跑的地方。"
        "本次事件的全部字段由环境变量 DECISION_CTX 给到（JSON，字段见 test_decision_skill 的返回；"
        "要说的话 print 成 JSON {\"reply\":\"...\"}）。列表里过长的正文只回显开头）/ "
        "silent（{action} 静默：这条消息不回、也不唤醒你本体，用来声明「这种消息不值得理」）。"
        "notify=true = 命中后仍唤醒本体（执行结果会作为一条系统提示给你）；false = 程序处理完即止。"
        "脚本没跑成（报错/超时/工具失败）时按 on_error 走三档："
        "\"wake\"（默认）= 把失败原因交回你本体（notify=false 挂了也一样叫醒你，不吞错）；"
        "\"fallback_reply\" = 代发 error_reply 里那句兜底话，不唤醒你；"
        "\"silent\" = 不唤醒也不代发，只往账本记一条——不叫醒不等于不知道，下一轮你能看到。"
        "fallback_reply 与 silent 只在 do 没办成时生效，成功时仍按 notify。"
        "未 @ 你的消息到不到得了你，取决于这个群/通道的消息覆盖面。"
        "每条规则平台都记命中统计（列表里 stats.hit_count / last_hit_at / recent，"
        "最近 20 条命中）；hit_count=0 就是「这条从没触发过」，该去试跑里查条件了。"
        "同名覆盖更新（命中统计会跟着保留），上限 20 条。"
    )


def test_rule_desc() -> str:
    """试跑工具的描述 —— 与写规则共用同一段说明，别再手抄一份"""
    return (
        "试跑一条决策技能：给一条样例消息，回「会不会命中、没中的原因、脚本收到什么、会回什么」。"
        "不发消息、不落库、不唤醒你本体。不传 rule 就按你已存的规则逐条试（草稿可以只传 rule 不存）。"
        "run_script 默认只回显不执行，execute=true 才真跑（脚本里能读到 DRY_RUN=1）。"
        "返回里的 ctx 就是脚本环境变量 DECISION_CTX 的全部字段。"
        "字段分两类：ctx/checked/reply/script(execute=true) 是真跑出来的，hit/call/script(execute=false) "
        "只是规则回显（没执行）；would 是计划动作，on_error 是这条规则失败时的去向（wake/fallback_reply/silent）。"
        "wakes_owner 按真跑口径给，但 on_error=fallback_reply/silent 时脚本没跑成也不唤醒。"
    )


def validate_rule(rule: dict) -> tuple[bool, str]:
    """校验决策技能结构，返回 (ok, error)。"""
    if not isinstance(rule, dict):
        return False, "技能必须是对象"
    name = str(rule.get("name") or "").strip()
    if not name or len(name) > 50:
        return False, "name 必填且 ≤50 字"
    when = rule.get("when") or {}
    if not isinstance(when, dict) or not str(when.get("event") or "").strip():
        return False, "when.event 必填（如 group_message）"
    event = str(when["event"]).strip()
    if event not in SCENARIOS:
        return False, f"when.event 只能是 {list(SCENARIOS)} 之一"
    conditions = when.get("conditions")
    if conditions is not None and not isinstance(conditions, dict):
        return False, "when.conditions 必须是条件对象"
    on_error = str(rule.get("on_error") or "wake").strip()
    if on_error not in _ON_ERROR:
        return False, f"on_error 只能是 {_ON_ERROR} 之一（默认 wake）"
    if on_error == "fallback_reply":
        if not str(rule.get("error_reply") or "").strip():
            return False, ("on_error=fallback_reply 需要 error_reply：脚本挂掉时代你说的一句话"
                           '（如 "这次卡住了，等下再来找我"）')
        if len(str(rule.get("error_reply"))) > _MAX_REPLY_CHARS:
            return False, f"error_reply 过长（≤{_MAX_REPLY_CHARS} 字）"
    do = rule.get("do") or {}
    action = str(do.get("action") or "")
    if action not in _DO_ACTIONS:
        return False, f"do.action 必须是 {_DO_ACTIONS} 之一"
    if action == "silent" and rule.get("notify"):
        return False, "silent 与 notify=true 冲突：静默处理与唤醒本体只能选一个"
    if action == "reply_template":
        if not str(do.get("reply") or "").strip():
            return False, "reply_template 需要 reply 文本"
        if len(str(do["reply"])) > _MAX_REPLY_CHARS:
            return False, f"reply 过长（≤{_MAX_REPLY_CHARS} 字）"
    if action == "call_tool":
        if not str(do.get("name") or "").strip():
            return False, "call_tool 需要 name（平台工具名）"
    if action == "run_script":
        code = str(do.get("code") or "")
        entry = str(do.get("entry") or "")
        if not code.strip() and not entry.strip():
            return False, "run_script 需要 code（脚本正文）或 entry（文件空间里已存的脚本文件）"
        if code.strip() and entry.strip():
            # 沙箱的入口判定是 entry 优先：两个都给会跑 entry，code 静默失效——不如当场说清
            return False, "code 与 entry 二选一（同时给会跑 entry，code 不会被执行）"
        if len(code) > _MAX_SCRIPT_CHARS:
            return False, f"code 过长（≤{_MAX_SCRIPT_CHARS} 字；更长的脚本先存成文件，用 entry 跑）"
        if entry:
            parts = [seg for seg in entry.replace("\\", "/").split("/") if seg not in ("", ".")]
            if entry.startswith("/") or ".." in parts:
                return False, "entry 只能是文件空间内的相对路径（如 scripts/daily.py）"
    return True, ""


def brief_do(do: dict) -> dict:
    """do 的回显摘要：脚本正文过长只给开头 + 全文长度（列表与试跑回显的唯一出处）。

    技能存在 config jsonb 里、正文可以很长；原样回显就是每次列表都把这几十万字灌进
    上下文。正文本身在库里一字不少，要改就重写，要跑就走 code/entry。
    """
    do = dict(do or {})
    if str(do.get("action") or "") != "run_script":
        return do
    code = str(do.get("code") or "")
    if len(code) > _BRIEF_CODE_CHARS:
        do["code"] = (code[:_BRIEF_CODE_CHARS]
                      + f"\n…（脚本正文共 {len(code)} 字，此处只回显前 {_BRIEF_CODE_CHARS} 字）")
        do["code_chars"] = len(code)
    return do


def rule_stats(rule: dict) -> dict:
    """规则的命中统计（平台写，AI 只读）。没有统计就是空 dict，别造一个假的 0 出来。"""
    stats = (rule or {}).get(_STATS_KEY)
    return dict(stats) if isinstance(stats, dict) else {}


def rule_brief(rule: dict) -> dict:
    """技能对象的回显摘要（list_decision_skills 用）。

    统计挂到 stats 上给 AI 看：hit_count=0 说明这条从没触发过，recent 里是最近几条命中的正文，
    "一直在误触"能从那里看出来。原始 config 里的 _stats 不直接回显（那是平台内部键）。
    """
    out = {key: value for key, value in dict(rule or {}).items() if key != _STATS_KEY}
    if isinstance(out.get("do"), dict):
        out["do"] = brief_do(out["do"])
    stats = rule_stats(rule)
    hits = int(stats.get("hit_count") or 0)
    out["stats"] = {
        "hit_count": hits,
        "last_hit_at": str(stats.get("last_hit_at") or ""),
        "recent": list(stats.get("recent") or [])[-_STATS_BRIEF_RECENT:],
        "never_hit": hits == 0,
    }
    return out


# ═══════════════════════════════════════════════════════════
# 存储读写（群助手 / 群 AI 通用）
# ═══════════════════════════════════════════════════════════

async def get_decision_rules(db, kind: str, entity_id: int) -> list[dict]:
    """读某实体的决策技能列表。kind: group_assistant | agent"""
    db = _ensure_repo(db)
    if kind == "group_assistant":
        from app.models.world import GroupAssistant
        ga = await db.get(GroupAssistant, entity_id)
        return list((ga.config or {}).get("decision_rules") or []) if ga else []
    if kind == "agent":
        from app.models.agent_skill import AgentSkill
        from sqlalchemy import select
        rows = (await db.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == entity_id,
                AgentSkill.skill_type == "decision",
            ).order_by(AgentSkill.id)
        )).scalars().all()
        return [dict(r.config or {}) for r in rows]
    return []


def _carry_stats(rule: dict, previous: dict | None) -> dict:
    """同名覆盖时把命中统计带到新规则上：改一条条件不该让它的历史归零。"""
    stats = rule_stats(previous or {})
    return {**rule, _STATS_KEY: stats} if stats else dict(rule)


async def save_decision_rule(db, kind: str, entity_id: int, rule: dict) -> tuple[bool, str]:
    """新增/更新（同名覆盖）一个决策技能。"""
    db = _ensure_repo(db)
    ok, err = validate_rule(rule)
    if not ok:
        return False, err
    # 统计是平台写的：AI 自己带一份进来不认（否则自己就能把"从没命中"改成命中过）
    rule = {key: value for key, value in dict(rule or {}).items() if key != _STATS_KEY}
    name = str(rule["name"]).strip()
    if kind == "group_assistant":
        from app.models.world import GroupAssistant
        ga = await db.get(GroupAssistant, entity_id)
        if ga is None:
            return False, "群助手不存在"
        old_rules = list((ga.config or {}).get("decision_rules") or [])
        previous = next((r for r in old_rules if r.get("name") == name), None)
        rules = [r for r in old_rules if r.get("name") != name]
        if len(rules) >= _MAX_RULES:
            return False, f"决策技能已达上限（{_MAX_RULES} 条）"
        rules.append(_carry_stats(rule, previous))
        cfg = dict(ga.config or {})
        cfg["decision_rules"] = rules
        ga.config = cfg
        await db.commit()
        return True, ""
    if kind == "agent":
        from app.models.agent_skill import AgentSkill
        from sqlalchemy import select
        row = (await db.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == entity_id,
                AgentSkill.skill_type == "decision",
                AgentSkill.name == name,
            )
        )).scalar_one_or_none()
        if row is not None:
            row.config = _carry_stats(rule, row.config or {})
        else:
            count = (await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == entity_id,
                    AgentSkill.skill_type == "decision",
                )
            )).scalars().all()
            if len(count) >= _MAX_RULES:
                return False, f"决策技能已达上限（{_MAX_RULES} 条）"
            db.add(AgentSkill(agent_id=entity_id, name=name, skill_type="decision", config=rule))
        await db.commit()
        return True, ""
    return False, f"未知实体类型 {kind}"


async def delete_decision_rule(db, kind: str, entity_id: int, name: str) -> bool:
    """删除一个决策技能。"""
    db = _ensure_repo(db)
    if kind == "group_assistant":
        from app.models.world import GroupAssistant
        ga = await db.get(GroupAssistant, entity_id)
        if ga is None:
            return False
        rules = [r for r in (ga.config or {}).get("decision_rules") or [] if r.get("name") != name]
        cfg = dict(ga.config or {})
        cfg["decision_rules"] = rules
        ga.config = cfg
        await db.commit()
        return True
    if kind == "agent":
        from app.models.agent_skill import AgentSkill
        from sqlalchemy import select
        row = (await db.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == entity_id,
                AgentSkill.skill_type == "decision",
                AgentSkill.name == name,
            )
        )).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
            await db.commit()
        return True
    return False


# ═══════════════════════════════════════════════════════════
# 命中统计：哪条一直在动、哪条从没动过
# ═══════════════════════════════════════════════════════════

def _bump_stats(old, entry: dict) -> dict:
    """命中一次之后的统计对象（纯函数，读旧写新，唯一出处）。"""
    stats = dict(old) if isinstance(old, dict) else {}
    recent = [item for item in (stats.get("recent") or []) if isinstance(item, dict)]
    recent.append(entry)
    return {
        "hit_count": int(stats.get("hit_count") or 0) + 1,
        "first_hit_at": str(stats.get("first_hit_at") or entry.get("at") or ""),
        "last_hit_at": str(entry.get("at") or ""),
        "recent": recent[-_STATS_RECENT:],
    }


def _now_text() -> str:
    """统计里的时间用本地时间：AI 读它的时候跟 now/today 那几个字段是同一套口径。"""
    from datetime import datetime

    from app.utils.pure.timeutil import get_zoneinfo
    return datetime.now(get_zoneinfo(settings.display_timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _plain_session(db):
    """仓库包装 → 底层会话（统计是旁路写，不能另开事务把调用方的半成品一起提交）。"""
    return getattr(db, "session", None) or db


async def _merge_stats(db, kind: str, entity_id: int, name: str, entry: dict) -> None:
    """把一次命中并进规则 config 的 _stats。规则刚被删掉就安静返回。"""
    session = _plain_session(db)
    if session is None or not hasattr(session, "get"):
        return
    if kind == "agent":
        from app.models.agent_skill import AgentSkill
        from sqlalchemy import select
        row = (await session.execute(select(AgentSkill).where(
            AgentSkill.agent_id == entity_id,
            AgentSkill.skill_type == "decision",
            AgentSkill.name == name,
        ))).scalar_one_or_none()
        if row is None:
            return
        cfg = dict(row.config or {})
        row.config = {**cfg, _STATS_KEY: _bump_stats(cfg.get(_STATS_KEY), entry)}
        return
    if kind == "group_assistant":
        from app.models.world import GroupAssistant
        ga = await session.get(GroupAssistant, entity_id)
        if ga is None:
            return
        cfg = dict(ga.config or {})
        rules = list(cfg.get("decision_rules") or [])
        for item in rules:
            if isinstance(item, dict) and str(item.get("name") or "") == name:
                item[_STATS_KEY] = _bump_stats(item.get(_STATS_KEY), entry)
        cfg["decision_rules"] = rules
        ga.config = cfg


async def record_hit(db, kind: str, entity_id: int, rule: dict, ctx: dict, result: dict, *,
                     handled: bool | None = None) -> None:
    """记一次命中（平台写、AI 只读）。

    写在调用方这段工作里、不自己 commit：命中发生在消息链路里，那里的一次 commit 会把同一会话
    里别人的半成品一起提交；工作出块时随它一起落库，回滚了就丢一次统计——统计是旁路，不能影响
    消息本身。任何异常都只记 debug 日志。
    """
    name = str((rule or {}).get("name") or "")
    if not name:
        return
    do = (rule or {}).get("do") or {}
    entry = {
        "at": _now_text(),
        "content": str((ctx or {}).get("content_clean") or (ctx or {}).get("content") or "")[:_STATS_PREVIEW],
        "action": str(do.get("action") or ""),
        "ok": (result or {}).get("success") is not False,   # do 本身办成没有
        "handled": handled,                                # 这件事由程序接住了吗（没交回本体）
    }
    try:
        await _merge_stats(db, kind, entity_id, name, entry)
    except Exception as e:  # noqa: BLE001 —— 统计是旁路，不能拖垮消息链路
        logger.debug("决策技能命中统计写入失败（%s %s「%s」）: %s", kind, entity_id, name, e)


async def run_decision_engine(
    db, kind: str, entity_id: int, world, event_type: str, ctx: dict,
    *, rules: list[dict] | None = None,
) -> dict:
    """高层决策入口：取规则 → 匹配 → 执行 do。

    rules：调用方批量预取的规则（全量消息下每条消息要给一群 AI 过一遍，逐个查库不划算）。
    返回的四种情况（未命中 / 办成了 / 要本体判断 / 没办成）、notify 语义与分派表见
    docs/dev/decision_layer.md。

    ctx 在这里补上公共的时间字段（now/today/weekday/hour）：六个情景各写一遍迟早漏一个，
    调用方自己给了同名字段就以调用方为准。条件与脚本读的是同一份 ctx。
    """
    try:
        if rules is None:
            rules = await get_decision_rules(db, kind, entity_id)
        if not rules:
            return {"hit": False}
        ctx = {**local_time_fields(settings.display_timezone), **(ctx or {})}
        rule = find_hit(rules, event_type, ctx)
        if rule is None:
            return {"hit": False}
        do = rule.get("do") or {}
        result = await execute_do(db, world, do, ctx, kind=kind, entity_id=entity_id)
        # 没办成时的去向由 on_error 定（唯一出处）：默认交回本体，不许当成"办完了"咽下去
        outcome = (await _failure_outcome(db, kind, entity_id, rule, ctx, result)
                   if result.get("success") is False else None)
        # 命中即记一次（含没办成的那次）：统计读的是"哪条一直在动、哪条从没动过"，不是成功率
        await record_hit(db, kind, entity_id, rule, ctx, result,
                         handled=bool(outcome["handled"]) if outcome is not None
                         else not bool(rule.get("notify")))
        if outcome is not None:
            return outcome
        if bool(rule.get("notify")):
            return {"hit": True, "handled": False, "name": str(rule.get("name") or ""),
                    "result": result, "note": notify_note(rule, result)}
        return {"hit": True, "handled": True, "reply": result.get("reply") or ""}
    except Exception as e:
        logger.warning(f"🎲 决策引擎异常（{kind} {entity_id}）: {e}")
        # 判定都没做成就等于这个技能瞎了；调用方据此照常唤醒本体，note 顺带说明原因
        return {"hit": False, "error": str(e)[:200], "note": engine_error_note(e)}


async def send_dm_reply(db, sender_user_id: int, target_user_id: int, content: str) -> dict:
    """代发一条私信回复，返回 {sent, reason}。

    对方还不是好友时（AI 主动私信生人会被拒）不抛错，只回报原因——调用方要把它写进
    给 AI 的提示里，别让它以为话说出口了。规则见 chat/dm.ensure_dm_allowed。
    """
    from app.chat.dm import get_or_create_dm_session, send_dm_message
    try:
        dm = await get_or_create_dm_session(db, sender_user_id, target_user_id)
        await send_dm_message(db, dm["session_id"], sender_id=sender_user_id, content=content)
        return {"sent": True, "reason": ""}
    except ValueError as e:
        return {"sent": False, "reason": str(e)}


async def send_group_reply(db, group_id: int, sender_id: int, content: str,
                          *, sender_type: str = "ai", allow_non_member: bool = False) -> None:
    """代发一条决策技能回复：标 source="world"（不回灌世界程序；AI 唤醒链只看人类消息，
    也追不到这里），并广播给在线成员。两条链路共用，避免各写一份发送+广播。"""
    from app.chat.gm import gm_message_to_dict, send_gm_message
    from app.routers.ws import manager
    from app.utils.display_name import sender_names

    msg = await send_gm_message(db, group_id, sender_type, sender_id, content,
                                source="world", allow_non_member=allow_non_member)
    await db.flush()
    # 广播完整视图：只推 id+正文时，在线的人收到的是一条没有发送者类型的消息
    #（前端按 sender_type/sender_name 渲染，缺了就显示成 "undefined:undefined"）
    name = (await sender_names(db, {(sender_type, sender_id)})).get((sender_type, sender_id))
    try:
        await manager.broadcast_to_group(
            group_id, {"type": "message", "data": gm_message_to_dict(msg, sender_name=name)})
    except Exception:  # noqa: BLE001 —— 广播失败不影响消息已落库
        pass


async def emit_member_event(db, group_id: int, event_type: str, member_id: int,
                            operator_id: int | None = None) -> None:
    """入群/退群情景：交给本群所有 AI 的决策技能，命中即代发到群。

    这类事件没有唤醒链路（只有人类消息才进唤醒队列），notify=true 的结果只能留在账本里。
    """
    from app.models.agent import Agent as AgentModel
    from app.models.group import GroupMember
    from sqlalchemy import select

    try:
        members = (await db.execute(
            select(GroupMember.member_id).where(
                GroupMember.group_id == group_id,
                GroupMember.member_type == "ai",
            )
        )).scalars().all()
        rows = (await db.execute(
            select(AgentModel.id, AgentModel.user_id).where(AgentModel.user_id.in_(list(members)))
        )).all() if members else []
        if not rows:
            return
        rules_by_agent = await load_rules_map(db, "agent", [r[0] for r in rows])
        names = await display_names(db, {member_id, operator_id} - {None})
        ctx = {
            "event": event_type,
            "group_id": group_id,
            "member_id": member_id,
            "member_name": names.get(member_id, ""),
            "operator_id": operator_id,
            "operator_name": names.get(operator_id or 0, ""),
        }
        for agent_id, user_id in rows:
            dec = await run_decision_engine(db, "agent", agent_id, None, event_type, ctx,
                                           rules=rules_by_agent.get(agent_id))
            if dec.get("handled"):
                if dec.get("reply"):
                    await send_group_reply(db, group_id, user_id, dec["reply"])
                logger.info(f"🎲 AI #{agent_id} 决策技能命中 {event_type}，程序化处理（不唤醒）")
            elif dec.get("note"):
                # notify=true 或没办成：这个情景本来没人叫它，只能把话记进账本留给下一轮
                await leave_ledger_notice(db, agent_id, group_id, dec["note"])
    except Exception as e:  # noqa: BLE001 —— 决策层故障不影响成员变更本身
        logger.warning(f"🎲 决策技能 {event_type} 派发异常（group={group_id}）: {e}")


async def leave_ledger_notice(db, agent_id: int, group_id: int, note: str) -> None:
    """把决策层的这句话记进该 AI 的账本，留给它下一轮看到（唯一出处）。

    两种来源：notify=true 的执行结果；以及"没人叫它"的失败——入群退群没有唤醒链路，
    群消息被触发模式拦下时同样没有，不记进账本它就永远不会知道技能没办成。

    条目带 drop_on_unlock，**投递过才离场**（flags.seen 由 executor 在 LLM 响应回来时打）：
    "看过一次就不再重复"由它保证，而"还没投出去"的条目在解锁重写时原样搬进新账本——
    本函数正是落在轮次之外（被拦下 / 入群退群都没有唤醒链路），所以原来"解锁即丢"会让
    这条通知在 AI 看见之前就没了。
    """
    from app.models.agent import Agent
    from app.services.history.context_sync import append_events, context_ref
    from app.utils.pure.history import make_entry

    agent = await db.get(Agent, agent_id)
    if agent is None:
        return
    await append_events(db, agent, context_ref(group_id=group_id),
                        [make_entry("notice", note, flags={"decision_skill": True, "drop_on_unlock": True})])
    await db.commit()


def notify_note(rule: dict, result: dict) -> str:
    """notify=true 命中后给本体的系统提示（唯一出处，两条链路共用一句文案）"""
    detail = (result or {}).get("reply") or (result or {}).get("result") or (result or {}).get("error") or "（无返回）"
    return (f"- 你的决策技能「{str((rule or {}).get('name') or '')}」已命中并执行，结果：{str(detail)[:300]}。"
            "这条消息仍需你亲自判断（技能已经做完的事不必重复）。")


def failure_note(rule: dict, result: dict) -> str:
    """技能没办成时给本体的提示（唯一出处，五个调用点共用一句文案）。

    为什么 handled 翻回 false 而不是静默跳过：技能说好要办这件事，结果脚本崩了/工具报错——
    再当成"办完了"就是把这个事件吃掉（人还在等回应、申请还挂着），本体永远不知道。
    交回本体走原来的唤醒路径，note 说清是技能挂了，不是没人找它。
    """
    why = str((result or {}).get("error") or "没有返回可代发的内容")[:300]
    return (f"- 你的决策技能「{str((rule or {}).get('name') or '')}」命中了，但没办成：{why}。"
            "这件事还没有处理，你自己判断要不要接上（不想管也可以不管）。")


def silent_failure_note(rule: dict, result: dict, mode: str) -> str:
    """on_error 不是 wake 时记进账本的那句话（唯一出处）。

    与 failure_note 的差别只在结尾：那条是"交给你接上"，这条要说明"为什么没吵醒你、事后怎么找"。
    """
    why = str((result or {}).get("error") or "没有返回可代发的内容")[:300]
    said = "已经代你说了一句兜底话" if mode == "fallback_reply" else "这次没有代发任何内容"
    return (f"- 你的决策技能「{str((rule or {}).get('name') or '')}」命中了，但没办成：{why}。"
            f"按 on_error={mode} 没有唤醒你，{said}；这件事还没处理，能接上就接上。")


async def _failure_outcome(db, kind: str, entity_id: int, rule: dict, ctx: dict, result: dict) -> dict:
    """do 没办成之后谁接这件事 —— on_error 三档的唯一分派处。

    wake（默认）：翻回 handled=False 走原唤醒路径，note 说清是技能挂了，不吞错。
    fallback_reply：代发 error_reply，不唤醒；账本照样留一条（事情没办成，它该知道）。
    silent：不唤醒也不代发，只留账本——"不叫醒我"不等于"我不知道"。

    账本只对入驻 AI 有意义（群助手不是 agent 行，账本挂不到它名下），所以按 kind 收口：
    群助手选非 wake 档时就只是"不唤醒、不代发"，失败原因仍进日志。
    """
    mode = str((rule or {}).get("on_error") or "wake").strip()
    if mode not in _ON_ERROR:            # 校验期已经挡过；旧数据兜底按最安全的 wake 走
        mode = "wake"
    if mode == "wake":
        return {"hit": True, "handled": False, "name": str(rule.get("name") or ""),
                "result": result, "note": failure_note(rule, result)}
    note = silent_failure_note(rule, result, mode)
    if kind == "agent":
        try:
            await leave_ledger_notice(_plain_session(db), entity_id, (ctx or {}).get("group_id"), note)
        except Exception as e:  # noqa: BLE001 —— 账本写不进去也不能把消息吃掉
            logger.warning("决策技能失败通知写账本失败（agent %s）: %s", entity_id, e)
    else:
        logger.info("🎲 决策技能没办成（%s %s，on_error=%s），群助手无账本，只记日志: %s",
                    kind, entity_id, mode, str((result or {}).get("error") or "")[:200])
    reply = str((rule or {}).get("error_reply") or "") if mode == "fallback_reply" else ""
    return {"hit": True, "handled": True, "reply": reply, "note": ""}


def engine_error_note(exc: Exception) -> str:
    """判定阶段就出错时给本体的提示：这不是"没命中"（唯一出处）"""
    return (f"- 你的决策技能这次没能判定（{str(exc)[:200]}）。"
            "这不是没命中，是执行出错——需要的话检查一下规则是不是写坏了。")


async def load_rules_map(db, kind: str, entity_ids) -> dict[int, list[dict]]:
    """批量取一批实体的决策技能（群助手数量少逐个读；AI 走一条 in 查询）"""
    ids = [int(i) for i in entity_ids]
    if not ids:
        return {}
    if kind != "agent":
        return {i: await get_decision_rules(db, kind, i) for i in ids}
    from app.models.agent_skill import AgentSkill
    from sqlalchemy import select
    rows = (await db.execute(
        select(AgentSkill).where(
            AgentSkill.agent_id.in_(ids),
            AgentSkill.skill_type == "decision",
        ).order_by(AgentSkill.id)
    )).scalars().all()
    out: dict[int, list[dict]] = {i: [] for i in ids}
    for row in rows:
        out.setdefault(int(row.agent_id), []).append(dict(row.config or {}))
    return out


def build_world_event_ctx(world_id: int, event: dict) -> dict:
    """world_event 情景的上下文：固定字段 + payload 展平成 payload_*（DSL 只认扁平字段）"""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    ctx = {
        "event": "world_event",
        "world_id": world_id,
        "name": str(event.get("name") or ""),
        "title": str(event.get("title") or ""),
        "group_id": event.get("group_id"),
        "payload": payload,
    }
    for key, value in payload.items():
        ctx[f"payload_{key}"] = value
    return ctx


def build_friend_request_ctx(requester_id: int, requester_name: str,
                             message: str, request_id: int | None) -> dict:
    """friend_request 情景的上下文"""
    return {
        "event": "friend_request",
        "requester_id": requester_id,
        "requester_name": requester_name or "",
        "message": message or "",
        "request_id": request_id,
    }


def clean_message_text(content: str) -> str:
    """消息正文的"干净"版本：去掉 @ 令牌与开头的 @名字，空白收成单空格。

    为什么条件里要按它判：QQ 那边用户 @ 你是常态，正文物化后是「<@!1> 签到」——
    拿原串做全等匹配永远对不上，AI 只能退而求其次用 contains。content 仍是原样。
    """
    from app.utils.text import MENTION_TOKEN_RE

    text = MENTION_TOKEN_RE.sub(" ", str(content or ""))
    text = re.sub(r"^\s*@[^\s@]{1,32}\s*", "", text)      # 开头的旧写法 @名字
    return " ".join(text.split())


def build_group_message_ctx(
    content: str, sender_id: int | None, sender_name: str,
    sender_type: str, group_id: int | None, is_mention: bool = False,
    is_at_all: bool = False, group_type: dict | None = None,
) -> dict:
    """group_message 情景的事件上下文（条件 DSL 字段来源）。"""
    clean = clean_message_text(content)
    return {
        "event": "group_message",
        "content": content or "",
        # content_clean / content_len：全等、相似、长度限制都判它们（见 clean_message_text）
        "content_clean": clean,
        "content_len": len(clean),
        "sender_id": sender_id,
        "sender_name": sender_name or "",
        "sender_type": sender_type or "human",
        "group_id": group_id,
        "is_mention": bool(is_mention),
        "is_at_all": bool(is_at_all),
        "group_type": (group_type or {}).get("slug") if group_type else None,
        "group_type_name": (group_type or {}).get("name") if group_type else None,
    }


def find_hit(rules: list[dict], event_type: str, ctx: dict) -> dict | None:
    """按序匹配：返回第一个 when.event 相符且 conditions 命中的技能（无 then 返回 None）。"""
    for rule in rules:
        when = rule.get("when") or {}
        if str(when.get("event") or "") != event_type:
            continue
        conditions = when.get("conditions")
        if conditions is None or match_conditions(conditions, ctx):
            return rule
    return None


# ═══════════════════════════════════════════════════════════
# do 执行
# ═══════════════════════════════════════════════════════════

# reply_template 能用的占位；认不出的 {…} 原样留着（正文里的花括号可能是字面意思）
_TEMPLATE_FIELDS = ("sender_name", "sender_id", "group_id", "content", "now", "today", "weekday")
_TEMPLATE_RE = re.compile(r"\{([a-z_]+)\}")


def render_reply_template(text, ctx: dict) -> str:
    """把 {字段} 换成这次事件的值 —— 零唤醒的固定回复也能叫出对方名字。

    认不出的占位一字不动：花括号在正文里可能是字面意思（JSON 例子、代码片段），
    宁可留着让人看见，也别替掉或者当场拒掉一条合法的回复。
    """
    def one(match: re.Match) -> str:
        key = match.group(1)
        value = (ctx or {}).get(key)
        return str(value) if key in _TEMPLATE_FIELDS and value is not None else match.group(0)

    return _TEMPLATE_RE.sub(one, str(text or ""))


async def execute_do(db, world, do: dict, ctx: dict, *, kind: str = "", entity_id: int = 0) -> dict:
    """执行决策技能的动作，返回 {success, reply?, result?, error?}。

    call_tool / run_script 按归属分派：入驻 AI 用平台身份 + 自己的文件空间沙箱；
    群助手是世界实体，仍用世界工具与世界沙箱。分派表见 docs/dev/decision_layer.md。
    """
    action = str(do.get("action") or "")
    try:
        if action == "silent":
            # 空 reply 即不代发；handled=True 让调用方连唤醒一起跳过
            return {"success": True, "reply": ""}
        if action == "reply_template":
            # 占位换成这次事件的值（见 render_reply_template）
            return {"success": True, "reply": render_reply_template(do.get("reply"), ctx).strip()}
        if action == "call_tool":
            name = str(do.get("name") or "")
            arguments = do.get("arguments") or {}
            if kind == "agent":
                from app.tools.base import ToolRegistry
                result = await ToolRegistry.dispatch(
                    db, entity_id, ctx.get("group_id"), name,
                    arguments if isinstance(arguments, dict) else {"value": arguments},
                    {"source": "decision_skill"},
                )
                return {"success": not result.get("error"), "result": result}
            from app.repositories.world_repo import SQLAlchemyWorldRepository
            from app.tools.world import run_world_tool
            import json as _json
            result = await run_world_tool(
                SQLAlchemyWorldRepository(db), world, name,
                _json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments),
            )
            return {"success": bool(result.get("success")), "result": result}
        if action == "run_script":
            code = str(do.get("code") or "")
            entry = str(do.get("entry") or "")
            if kind == "agent":
                from app.services.sandbox.agent_sandbox import run_agent_code
                # entry：正文太长塞不进技能时，脚本先落到文件空间（run_script 的 path），技能里只留入口
                return _script_result(await run_agent_code(
                    entity_id, code=code or None, entry=entry or None, ctx=ctx))
            if entry:
                return {"success": False, "error": "entry 只适用于 AI 自己的文件空间，群助手请用 code"}
            from app.services.world.skill_sandbox import run_skill_in_sandbox
            result = await run_skill_in_sandbox(db, world, {"name": "_decision_script", "code": code}, {"ctx": ctx})
            return {"success": bool(result.get("success", result.get("ok"))), "result": result}
    except Exception as e:
        logger.warning(f"🎲 决策技能 do 执行失败（{action}）: {e}")
        return {"success": False, "error": str(e)[:200]}
    return {"success": False, "error": f"未知动作 {action}"}


def _script_result(raw: dict) -> dict:
    """沙箱结果 → do 结果：脚本 print 的 JSON {"reply": "..."} 就是要代发的话。

    脚本没有联网与平台句柄，「说什么」只能从返回值走——能力边界保持在「算」。
    三种情况分得开：跑挂（error）、什么都没 print（正当的静默）、print 了却没有能当话用的
    JSON（格式写错了）——最后这种以前是静音的，写脚本的那个自己只会以为"说了但没人收到"。
    """
    stdout = (raw.get("stdout") or "").strip()
    if not raw.get("success"):
        return {"success": False, "error": _failure_reason(raw), "stdout": stdout}
    payload = _last_json_object(stdout)
    if payload is None and stdout:
        return {"success": False, "stdout": stdout[:2000],
                "error": '脚本跑完了，但输出里没有可代发的 JSON（要说话就 print {"reply": "..."}）'}
    return {"success": True, "reply": str((payload or {}).get("reply") or ""),
            "result": {"stdout": stdout[:2000]}}


def _failure_reason(raw: dict) -> str:
    """失败原因里"到底哪儿错了"的那一句。

    沙箱的 reason 取的是 stderr 开头 200 字符——那是 traceback 的样板（File/runpy 那几行），
    真正的原因在最后一行。本体只看到样板字，等于没说。
    """
    lines = [ln.strip() for ln in (raw.get("stderr") or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else str(raw.get("reason") or ""))[:200] or "脚本执行失败"


def _last_json_object(stdout: str) -> dict | None:
    """从最后一行往回找第一个 JSON 对象。

    为什么不在最后一行上认死：脚本常在结果后面再 print 一句给人看的日志（"已记好"），
    只认最后一行会把要说的话吃掉。
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


# ═══════════════════════════════════════════════════════════
# 试跑：这条规则在这条样例上会怎么走
# ═══════════════════════════════════════════════════════════

# explain_conditions 的原因码 → 给 AI 看的一句话（码是机器口径，这里是文案口径）
_REASON_TEXT = {
    "event_not_matching": "when.event 不是这个情景",
    "conditions_false": "条件不成立",
    "pattern_rejected": "正则被拒（过长或含灾难性回溯形状）",
    "conditions_too_large": "条件树超过规模上限（层数/节点数）",
    "op_unknown": "引用了未注册的运算或判词",
    "rule_invalid": "规则结构不合法",
}

# 试跑样例里属于"工具参数"而不是"事件字段"的键
_SAMPLE_CONTROL_KEYS = ("rule", "execute")


def preview_sample_ctx(event_type: str, sample: dict, group_id: int | None = None) -> dict:
    """样例 → 事件上下文。group_message 走真实链路的同一个构造器，别的门类按字段直给。"""
    sample = {k: v for k, v in dict(sample or {}).items() if k not in _SAMPLE_CONTROL_KEYS}
    if str(event_type or "") == "group_message":
        return build_group_message_ctx(
            sample.get("content") or "", sample.get("sender_id"),
            sample.get("sender_name") or "群成员", sample.get("sender_type") or "human",
            sample.get("group_id") or group_id,
            is_mention=bool(sample.get("is_mention")), is_at_all=bool(sample.get("is_at_all")),
        )
    ctx = {"event": event_type, **sample}
    if group_id is not None:
        ctx.setdefault("group_id", group_id)
    return ctx


async def verify_before_save(
    db, kind: str, entity_id: int, rule: dict, verify: dict | list, *,
    group_id: int | None = None,
) -> tuple[bool, str, dict]:
    """启用前检验：拿 AI 自己给的样例跑一遍引擎，全部符合预期才许落地。

    为什么必须有这一步：决策代理是「程序替它行动」的东西，写错了它自己也看不见——规则不命中
    时没有任何反馈，只有某天它发现「我明明配了，为什么没动」。样例由写规则的 AI 给（它最清楚
    想让它替什么），平台只负责跑一遍并如实回报。

    verify 收单条样例对象，也收数组（推荐）：每条形如
    {"event": "group_message", "content": "签到", "expect": "hit"}；expect 默认 "hit"（应当命中），
    "miss" 是反例（不该命中）。只验正例等于只证明了「会命中」，证明不了「不误触」。
    """
    ok, err, samples = _normalize_verify_samples(verify)
    if not ok:
        return False, err, {}
    rows: list[dict] = []
    for index, item in enumerate(samples, 1):
        sample, expect = item["sample"], item["expect"]
        out = await preview_decision(
            db, kind, entity_id, None, str(sample.get("event") or "group_message"),
            sample, rule=rule, execute=False, group_id=group_id,
        )
        if not out.get("success", True):
            return False, f"第 {index} 条样例没能执行：{out.get('error') or '未知原因'}", {"samples": rows}
        hit = out.get("hit") is not None
        rows.append({
            "index": index,
            "expect": expect,
            "hit": hit,
            "content": str(sample.get("content") or "")[:60],
            "why": "" if hit else str(out.get("why") or ""),
        })
        if (expect == "hit") != hit:
            return False, _sample_mismatch(index, expect, sample, out), {"samples": rows}
    return True, "", {"samples": rows}


def _normalize_verify_samples(verify) -> tuple[bool, str, list[dict]]:
    """verify → 样例列表。单条对象按「一条正例」处理（旧写法照旧能用）。"""
    items = verify if isinstance(verify, list) else [verify]
    if not items:
        return False, ('缺 verify：要给至少一条检验样例，形如 {"event": "group_message", "content": "签到"}；'
                       '要证明不误触，再给一条反例 {"content": "今天训练了个模型", "expect": "miss"}'), []
    if len(items) > _VERIFY_MAX_SAMPLES:
        return False, f"verify 最多 {_VERIFY_MAX_SAMPLES} 条样例（正例几条 + 反例几条就够）", []
    samples: list[dict] = []
    for index, item in enumerate(items, 1):
        if isinstance(item, dict) and len(item) == 1 and isinstance(next(iter(item.values())), list):
            # {"samples": [...]} 这类包一层的写法：静默当成一条空样例等于让人以为通过了，
            # 当场说清要的是顶层数组
            return False, (f"verify 第 {index} 条是包了一层的写法（{list(item)[0]}: [...]），"
                           "直接给顶层数组：[{\"content\": \"签到\"}, "
                           "{\"content\": \"今天训练了个模型\", \"expect\": \"miss\"}]"), []
        if not isinstance(item, dict) or not item:
            return False, f"verify 第 {index} 条样例必须是对象（形如 {{...}}）", []
        expect = str(item.get("expect") or "hit").strip().lower()
        if expect not in ("hit", "miss"):
            return False, f"verify 第 {index} 条样例的 expect 只能是 hit 或 miss，收到 {expect!r}", []
        sample = {key: value for key, value in item.items() if key != "expect"}
        sample.setdefault("event", "group_message")
        samples.append({"sample": sample, "expect": expect})
    return True, "", samples


def _sample_mismatch(index: int, expect: str, sample: dict, out: dict) -> str:
    """样例不符预期时给 AI 的那句话：哪条、为什么、怎么改。"""
    content = str(sample.get("content") or "")
    if expect == "hit":
        why = str(out.get("why") or "条件不成立")
        return (f"第 {index} 条样例（应当命中）没有命中：{why}。样例消息：{content!r}；"
                "检查 when 条件与样例是否对得上（@ 令牌要用 content_clean 判）。")
    name = str((out.get("hit") or {}).get("name") or "")
    return (f"第 {index} 条反例（不该命中）却命中了规则「{name}」——这类消息会被程序吃掉：{content!r}。"
            "把条件收紧（比如用 content_clean 判整句/开头，别用 _contains），再重新提交。")


async def preview_decision(
    db, kind: str, entity_id: int, world, event_type: str, sample: dict, *,
    rule: dict | None = None, execute: bool = False, group_id: int | None = None,
) -> dict:
    """试跑：返回「这条样例会怎么走」——不产生任何外部效果。

    匹配与渲染都走真跑那条路（find_hit / explain_conditions / render_reply_template），
    所以试跑结果就是真跑结果；差别只在 do 不落外部效果：run_script 默认不执行
    （execute=True 才跑，并给脚本 DRY_RUN=1），call_tool 永不执行。

    返回字段分两类，读的时候别混（wakes_owner 曾经只照抄 notify，与 script.success=false
    自相矛盾，写规则的 AI 据此以为失败会被静默吞掉）：
      真跑出来  ctx（真实链路的上下文构造器）/ checked（真匹配与原因）/ reply（reply_template
                真渲染）/ script（execute=true 的真结果）/ wakes_owner（脚本没跑成时给 true：
                失败与 notify 无关，一律交回本体）
      配置回显  hit（命中的那条规则）/ call（call_tool 永不执行）/ script（execute=false 时只是回显）
      would 是判定出的计划动作，不是结果。
    """
    event_type = str(event_type or "group_message")
    if event_type not in SCENARIOS:
        return {"success": False, "error": f"event 只能是 {list(SCENARIOS)} 之一"}
    ctx = {**local_time_fields(settings.display_timezone),
           **preview_sample_ctx(event_type, sample, group_id)}
    rules = [rule] if isinstance(rule, dict) else await get_decision_rules(db, kind, entity_id)

    checked: list[dict] = []
    hit: dict | None = None
    for item in rules:
        when = (item or {}).get("when") or {}
        name = str((item or {}).get("name") or "")
        if str(when.get("event") or "") != event_type:
            checked.append({"name": name, "hit": False, "reason": "event_not_matching",
                            "why": _REASON_TEXT["event_not_matching"]})
            continue
        conditions = when.get("conditions")
        ok, reason = (True, "") if conditions is None else explain_conditions(conditions, ctx)
        checked.append({"name": name, "hit": bool(ok), "reason": reason,
                        "why": "" if ok else _REASON_TEXT.get(reason, "条件不成立")})
        if ok:
            hit = item
            break

    out: dict = {"success": True, "event": event_type, "ctx": ctx, "checked": checked,
                 "hit": None, "wakes_owner": False, "would": "none", "reply": ""}
    if hit is None:
        out["why"] = "没有规则命中这条样例" if rules else "你还没有决策技能"
        return out

    do = hit.get("do") or {}
    action = str(do.get("action") or "")
    out["hit"] = {"name": str(hit.get("name") or ""), "do": brief_do(do), "notify": bool(hit.get("notify"))}
    # 失败时的去向也是"这条规则会怎么走"的一部分：试跑时一并回显，别等真挂了才知道
    out["on_error"] = str(hit.get("on_error") or "wake")
    if out["on_error"] == "fallback_reply":
        out["error_reply"] = str(hit.get("error_reply") or "")
    out["wakes_owner"] = bool(hit.get("notify"))
    if action == "reply_template":
        out["would"], out["reply"] = "reply", render_reply_template(do.get("reply"), ctx).strip()
        if not out["reply"]:
            out["why"] = "渲染结果是空串——真跑时不会有消息发出，也不会唤醒你"
    elif action == "silent":
        out["would"], out["why"] = "silent", "静默：这条消息不回、也不唤醒你本体"
    elif action == "call_tool":
        out["would"] = "call_tool"
        out["call"] = {"name": str(do.get("name") or ""), "arguments": do.get("arguments") or {}}
        out["why"] = "试跑不执行 call_tool（避免真改数据）；要验证就让它在真实事件上跑一次"
    elif action == "run_script":
        out["would"] = "run_script"
        code = str(do.get("code") or "")
        entry = str(do.get("entry") or "")
        if not execute:
            out["script"] = {"executed": False, "code_len": len(code), "entry": entry,
                             "note": "未执行；execute=true 才真跑（脚本会看到 DRY_RUN=1）"}
        elif kind == "agent":
            from app.services.sandbox.agent_sandbox import run_agent_code
            out["script"] = {"executed": True,
                             **_script_result(await run_agent_code(
                                 entity_id, code=code or None, entry=entry or None,
                                 ctx=ctx, dry_run=True))}
            out["reply"] = str(out["script"].get("reply") or "")
            if out["script"].get("success") is False:
                # 失败时的去向按 on_error 走（wake 才交回本体）。试跑若只照抄 notify，
                # 就会出现「脚本没跑成 + wakes_owner=false」的自相矛盾回显。
                if out["on_error"] == "wake":
                    out["wakes_owner"] = True
                    out["why"] = "脚本没跑成；真跑时这件事会交回你（notify=false 也交）"
                elif out["on_error"] == "fallback_reply":
                    out["wakes_owner"] = False
                    out["why"] = (f"脚本没跑成；真跑时代发兜底话「{out.get('error_reply') or ''}」，"
                                  "不唤醒你")
                else:
                    out["wakes_owner"] = False
                    out["why"] = "脚本没跑成；真跑时不代发也不唤醒，只记进账本（下一轮你能看到）"
        else:
            out["script"] = {"executed": False, "note": "群助手的脚本请在真实事件上验证"}
    else:
        out["why"] = f"未知动作 {action}"
    return out


# ═══════════════════════════════════════════════════════════
# 平台工具（AI 自配置决策技能，注入群 AI / 群助手）
# ═══════════════════════════════════════════════════════════

# 名字列表是唯一一处；schema 现取注册表（app/tools/decision.py 那四个插件），
# 从前这里另抄了一份参数 schema——描述走了函数复用，参数没有，改一处漏一处。
DECISION_TOOL_NAMES = (
    "list_decision_skills",
    "write_decision_skill",
    "delete_decision_skill",
    "test_decision_skill",
)


def decision_tools() -> list[dict]:
    """决策四件套的 OpenAI 定义（AI 走注册表拿，群助手走这条拿同一份）。

    惰性取：本模块被 app/tools/decision.py 顶层导入，顶层再反过来 import app.tools.base 会成环；
    群助手不是注册表实体（kind=group_assistant），只有它需要在这里现取一份。
    """
    from app.tools.base import ToolRegistry

    out: list[dict] = []
    for name in DECISION_TOOL_NAMES:
        plugin = ToolRegistry.get_plugin(name)
        if plugin is not None:
            out.append(plugin.to_definition())
    return out
