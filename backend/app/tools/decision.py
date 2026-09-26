"""
决策技能工具 — AI 自配置「什么情景程序处理、什么情景才唤醒我本体」

见 world_decision_skill.md 阶段二：
- 世界体系给 AI 提供配置自己决策技能的能力（list/write/delete_decision_skill）
- 存储：agent_skills(skill_type='decision')，config 即技能对象 {name, when, do, notify}
- 执行：决策引擎（decision_skill.run_decision_engine）在群触发链路优先匹配
- 群助手（非 agent 实体）由世界链路单独处理同三个工具（kind=group_assistant）
"""
import logging
import json

from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

from app.services.world.decision_skill import rule_schema_desc  # noqa: E402


class ListDecisionSkills(ToolPlugin):
    name = "list_decision_skills"
    description = "查看你自己的决策技能列表（什么情景由程序自动处理、什么情景才触发你本体）。"
    parameters: dict = {}
    required: list = []

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import get_decision_rules
        rules = await get_decision_rules(db, "agent", agent_id)
        return {"success": True, "rules": rules, "count": len(rules)}


class WriteDecisionSkill(ToolPlugin):
    name = "write_decision_skill"
    description = rule_schema_desc()
    parameters: dict = {
        "rule": {"type": "object", "description": "完整决策技能对象（见描述）"},
    }
    required: list = ["rule"]

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
    parameters: dict = {
        "name": {"type": "string", "description": "要删除的技能名"},
    }
    required: list = ["name"]

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        from app.services.world.decision_skill import delete_decision_rule
        await delete_decision_rule(db, "agent", agent_id, str(arguments.get("name") or ""))
        return {"success": True}


async def handle_decision_tool(
    db, kind: str, entity_id: int, name: str, arguments_json: str,
) -> dict:
    """群助手等非 agent 实体的决策工具执行入口（同三个工具语义）。"""
    try:
        args = json.loads(arguments_json or "{}") if isinstance(arguments_json, str) else (arguments_json or {})
    except json.JSONDecodeError:
        return {"success": False, "error": "参数解析失败"}
    from app.services.world.decision_skill import (
        get_decision_rules, save_decision_rule, delete_decision_rule,
    )
    if name == "list_decision_skills":
        rules = await get_decision_rules(db, kind, entity_id)
        return {"success": True, "rules": rules, "count": len(rules)}
    if name == "write_decision_skill":
        rule = args.get("rule") or {}
        ok, err = await save_decision_rule(db, kind, entity_id, rule)
        return {"success": ok, "name": str(rule.get("name") or "").strip()} if ok else {"success": False, "error": err}
    if name == "delete_decision_skill":
        await delete_decision_rule(db, kind, entity_id, str(args.get("name") or ""))
        return {"success": True}
    return {"success": False, "error": f"未知决策工具 {name}"}
