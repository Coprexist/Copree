"""
决策技能工具 — AI 自配置「什么情景程序处理、什么情景才唤醒我本体」

见 world_decision_skill.md 阶段二：
- 世界体系给 AI 提供配置自己决策技能的能力（list/write/delete_decision_skill）
- 存储：agent_skills(skill_type='decision')，config 即技能对象 {name, when, do, notify}
- 执行：决策引擎（decision_skill.run_decision_engine）在群触发链路优先匹配
- 群助手（非 agent 实体）由世界链路单独处理同三个工具（kind=group_assistant）

四件套归 `self_management` 段：段不是标签，它决定 AI 系统提示的「技能背包」与管理员背包里
有没有这几个工具（`ai/llm._build_tools_segment` / `routers/admin.admin_tool_segments` 都遍历
`ToolRegistry.get_segments()`），空段就是从两处视图里漏掉。schema 的唯一出处是本文件的插件；
群助手那份按名字从注册表派生（`decision_skill.decision_tools()`），不再手抄参数。
"""
import logging
import json

from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

from app.services.world.decision_skill import rule_schema_desc, test_rule_desc  # noqa: E402


class ListDecisionSkills(ToolPlugin):
    name = "list_decision_skills"
    description = "查看你自己的决策技能列表（什么情景由程序自动处理、什么情景才触发你本体）。"
    segment = "self_management"
    parameters: dict = {}
    required: list = []
    admin_description = "查看 AI 自配的决策技能：哪些情景由程序直接处理、哪些情景才唤醒本体。"
    trigger_condition = "需要核对 AI 的自动化规则时"

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import get_decision_rules, rule_brief
        rules = await get_decision_rules(db, "agent", agent_id)
        return {"success": True, "rules": [rule_brief(r) for r in rules], "count": len(rules)}


class WriteDecisionSkill(ToolPlugin):
    name = "write_decision_skill"
    description = rule_schema_desc()
    segment = "self_management"
    parameters: dict = {
        "rule": {"type": "object", "description": "完整决策技能对象（见描述）"},
    }
    required: list = ["rule"]
    admin_description = "给 AI 配置一条决策技能（情景 + 动作 + 是否唤醒本体），同名覆盖，每个 AI 上限 20 条。"
    trigger_condition = "需要让 AI 自己处理某类消息时"

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import save_decision_rule
        rule = arguments.get("rule") or {}
        ok, err = await save_decision_rule(db, "agent", agent_id, rule)
        if not ok:
            return {"success": False, "error": err}
        return {"success": True, "name": str(rule.get("name") or "").strip()}


class DeleteDecisionSkill(ToolPlugin):
    name = "delete_decision_skill"
    description = "删除你自己的一个决策技能（按 name）。"
    segment = "self_management"
    parameters: dict = {
        "name": {"type": "string", "description": "要删除的技能名"},
    }
    required: list = ["name"]
    admin_description = "删除 AI 的一条决策技能（按名称）。"
    trigger_condition = "规则作废或要重写时"

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import delete_decision_rule
        await delete_decision_rule(db, "agent", agent_id, str(arguments.get("name") or ""))
        return {"success": True}


class TestDecisionSkill(ToolPlugin):
    name = "test_decision_skill"
    description = test_rule_desc()
    segment = "self_management"
    parameters: dict = {
        "event": {"type": "string", "description": "情景，默认 group_message"},
        "content": {"type": "string", "description": "样例消息正文"},
        "sender_name": {"type": "string", "description": "样例发送者的名字"},
        "sender_id": {"type": "integer", "description": "样例发送者的 id"},
        "is_mention": {"type": "boolean", "description": "样例算不算 @ 了你"},
        "rule": {"type": "object", "description": "要试的草稿规则；不传就按已存的规则逐条试"},
        "execute": {"type": "boolean", "description": "run_script 是否真跑（默认 false 只回显）"},
    }
    required: list = []
    admin_description = "拿一条样例消息试跑决策技能：只回答会不会命中、会怎么做，不落库、不代发、不唤醒本体。"
    trigger_condition = "规则写完不确定会不会命中时"

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import preview_decision
        return await preview_decision(
            db, "agent", agent_id, None, arguments.get("event") or "group_message",
            arguments, rule=arguments.get("rule"), execute=bool(arguments.get("execute")),
            group_id=group_id,
        )


async def handle_decision_tool(
    db, kind: str, entity_id: int, name: str, arguments_json: str,
) -> dict:
    """群助手等非 agent 实体的决策工具执行入口（同三个工具语义）。"""
    try:
        args = json.loads(arguments_json or "{}") if isinstance(arguments_json, str) else (arguments_json or {})
    except json.JSONDecodeError:
        return {"success": False, "error": "参数解析失败"}
    from app.services.world.decision_skill import (
        get_decision_rules, save_decision_rule, delete_decision_rule, rule_brief,
    )
    if name == "list_decision_skills":
        rules = await get_decision_rules(db, kind, entity_id)
        return {"success": True, "rules": [rule_brief(r) for r in rules], "count": len(rules)}
    if name == "write_decision_skill":
        rule = args.get("rule") or {}
        ok, err = await save_decision_rule(db, kind, entity_id, rule)
        return {"success": ok, "name": str(rule.get("name") or "").strip()} if ok else {"success": False, "error": err}
    if name == "delete_decision_skill":
        await delete_decision_rule(db, kind, entity_id, str(args.get("name") or ""))
        return {"success": True}
    if name == "test_decision_skill":
        from app.services.world.decision_skill import preview_decision
        return await preview_decision(
            db, kind, entity_id, None, args.get("event") or "group_message",
            args, rule=args.get("rule"), execute=bool(args.get("execute")),
        )
    return {"success": False, "error": f"未知决策工具 {name}"}
