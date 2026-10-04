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
from app.utils.pure.conditions import match_conditions  # noqa: E402


# ═══════════════════════════════════════════════════════════
# 技能校验
# ═══════════════════════════════════════════════════════════

_MAX_RULES = 20          # 每实体技能上限
_MAX_DO_SCRIPT = 4000    # run_script 脚本/回复文本上限（字符）

_DO_ACTIONS = ("reply_template", "call_tool", "run_script", "silent")

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
        f"结构：{{name, when:{{event, conditions}}, do:{{action,...}}, notify}}。event 支持：{events}。"
        "所有情景都能读到的公共字段：now（HH:MM）/ today / weekday / hour。"
        "conditions 是递归条件树：{\"and\":[...]}/{\"or\":[...]}/{\"not\":{...}} 自由嵌套（可多选、可非与或）；"
        "叶子 {\"字段\":值} = 全等（整条消息就等于它；建议判 content_clean，@ 令牌已去掉）、"
        "{\"字段_contains\":\"子串\"}、{\"字段_starts_with\":\"前缀\"}、{\"字段_matches\":\"正则\"}、"
        "{\"字段_gt/gte/lt/lte\":数值}（长度限制：{\"content_len_lte\":20}）、"
        "{\"field\":\"content_clean\",\"op\":\"similar\",\"value\":\"签到\"} = 相似命中"
        "（默认阈值 0.8，也可写 {\"text\":\"签到\",\"ratio\":0.75}；错别字、多几个字都认）。"
        "do 四选一：reply_template（{action, reply} 固定回复，零成本；可用占位 "
        "{sender_name} {sender_id} {group_id} {content} {now}）/ call_tool（{action, name, arguments} 调平台工具）/ "
        "run_script（{action, code} 沙箱脚本：在你自己的文件空间里跑，不能联网。本次事件的全部字段由"
        "环境变量 DECISION_CTX 给到（JSON）：json.loads(os.environ[\"DECISION_CTX\"]) 里就有 sender_id / "
        "sender_name / group_id / content_clean / now 等，按人分开记账靠的就是它；要说的话 print 成 JSON {\"reply\":\"...\"}）/ "
        "silent（{action} 静默：这条消息不回、也不唤醒你本体，用来声明「这种消息不值得理」）。"
        "notify=true = 命中后仍唤醒本体（执行结果会作为一条系统提示给你）；false = 程序处理完即止。"
        "同名覆盖更新，上限 20 条。示例：签到自动回复（零唤醒、按人回名字）= "
        "{\"name\":\"签到\",\"when\":{\"event\":\"group_message\",\"conditions\":{\"and\":[{\"content_clean_contains\":\"签到\"},{\"not\":{\"is_mention\":true}}]}},"
        "\"do\":{\"action\":\"reply_template\",\"reply\":\"{sender_name} 已记录签到\"},\"notify\":false}"
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
    do = rule.get("do") or {}
    action = str(do.get("action") or "")
    if action not in _DO_ACTIONS:
        return False, f"do.action 必须是 {_DO_ACTIONS} 之一"
    if action == "silent" and rule.get("notify"):
        return False, "silent 与 notify=true 冲突：静默处理与唤醒本体只能选一个"
    if action == "reply_template":
        if not str(do.get("reply") or "").strip():
            return False, "reply_template 需要 reply 文本"
        if len(str(do["reply"])) > _MAX_DO_SCRIPT:
            return False, f"reply 过长（≤{_MAX_DO_SCRIPT} 字）"
    if action == "call_tool":
        if not str(do.get("name") or "").strip():
            return False, "call_tool 需要 name（平台工具名）"
    if action == "run_script":
        code = str(do.get("code") or "")
        if not code.strip():
            return False, "run_script 需要 code（Python 脚本）"
        if len(code) > _MAX_DO_SCRIPT:
            return False, f"code 过长（≤{_MAX_DO_SCRIPT} 字）"
    return True, ""


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


async def save_decision_rule(db, kind: str, entity_id: int, rule: dict) -> tuple[bool, str]:
    """新增/更新（同名覆盖）一个决策技能。"""
    db = _ensure_repo(db)
    ok, err = validate_rule(rule)
    if not ok:
        return False, err
    name = str(rule["name"]).strip()
    if kind == "group_assistant":
        from app.models.world import GroupAssistant
        ga = await db.get(GroupAssistant, entity_id)
        if ga is None:
            return False, "群助手不存在"
        rules = list((ga.config or {}).get("decision_rules") or [])
        rules = [r for r in rules if r.get("name") != name]
        if len(rules) >= _MAX_RULES:
            return False, f"决策技能已达上限（{_MAX_RULES} 条）"
        rules.append(rule)
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
            row.config = rule
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
# 决策引擎：事件上下文 → 技能匹配
# ═══════════════════════════════════════════════════════════

async def run_decision_engine(
    db, kind: str, entity_id: int, world, event_type: str, ctx: dict,
    *, rules: list[dict] | None = None,
) -> dict:
    """高层决策入口：取规则 → 匹配 → 执行 do。

    rules：调用方批量预取的规则（全量消息下每条消息要给一群 AI 过一遍，逐个查库不划算）。
    三态返回、notify 语义与分派表见 docs/dev/decision_layer.md。

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
        if bool(rule.get("notify")):
            return {"hit": True, "handled": False, "name": str(rule.get("name") or ""),
                    "result": result, "note": notify_note(rule, result)}
        return {"hit": True, "handled": True, "reply": result.get("reply") or ""}
    except Exception as e:
        logger.warning(f"🎲 决策引擎异常（{kind} {entity_id}）: {e}")
        return {"hit": False}


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
    from app.chat.gm import send_gm_message
    from app.routers.ws import manager
    msg = await send_gm_message(db, group_id, sender_type, sender_id, content,
                                source="world", allow_non_member=allow_non_member)
    await db.flush()
    try:
        await manager.broadcast_to_group(group_id, {"type": "message", "data": {"id": msg.id, "content": content}})
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
            if not dec.get("hit"):
                continue
            if dec.get("handled"):
                if dec.get("reply"):
                    await send_group_reply(db, group_id, user_id, dec["reply"])
                logger.info(f"🎲 AI #{agent_id} 决策技能命中 {event_type}，程序化处理（不唤醒）")
            elif dec.get("note"):
                await _leave_ledger_notice(db, agent_id, group_id, dec["note"])
    except Exception as e:  # noqa: BLE001 —— 决策层故障不影响成员变更本身
        logger.warning(f"🎲 决策技能 {event_type} 派发异常（group={group_id}）: {e}")


async def _leave_ledger_notice(db, agent_id: int, group_id: int, note: str) -> None:
    """把 notify=true 的执行结果记进该 AI 的账本（这类情景没有唤醒链路，只能留给下一轮）"""
    from app.models.agent import Agent
    from app.services.history import history_service as hs  # noqa: F401 —— 账本读取由 append_events 负责
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
            if kind == "agent":
                from app.services.sandbox.agent_sandbox import run_agent_code
                return _script_result(await run_agent_code(entity_id, code=code, ctx=ctx))
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
    """
    stdout = (raw.get("stdout") or "").strip()
    if not raw.get("success"):
        return {"success": False, "error": raw.get("reason") or "脚本执行失败", "stdout": stdout}
    reply = ""
    try:
        payload = json.loads(stdout.splitlines()[-1]) if stdout else {}
        if isinstance(payload, dict):
            reply = str(payload.get("reply") or "")
    except (json.JSONDecodeError, IndexError):
        pass
    return {"success": True, "reply": reply, "result": {"stdout": stdout[:2000]}}


# ═══════════════════════════════════════════════════════════
# 平台工具（AI 自配置决策技能，注入群 AI / 群助手）
# ═══════════════════════════════════════════════════════════

DECISION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_decision_skills",
            "description": "查看你自己的决策技能列表（什么情景由程序自动处理、什么情景才触发你本体）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_decision_skill",
            "description": rule_schema_desc(),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule": {"type": "object", "description": "完整决策技能对象（见 description）"},
                },
                "required": ["rule"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_decision_skill",
            "description": "删除你自己的一个决策技能（按 name）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的技能名"},
                },
                "required": ["name"],
            },
        },
    },
]
