"""
store_memory 工具 — AI 存储长期记忆
"""
import logging
from sqlalchemy import select as _sel
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.utils.pure import focus as pure_focus
from app.utils.pure.memory_shape import (
    MAX_CONTENT_CHARS, MAX_TITLE_CHARS, shape_warnings,
)
from app.utils.pure.memory_weight import (
    MEMORY_TYPES, clamp_weight, default_weight,
)

logger = logging.getLogger(__name__)

_TYPE_HINT = ("person 人物（是谁、什么关系）/ relationship 关系变化 / promise 约定（答应过什么）/ "
              "event 共同经历 / preference 偏好 / daily 日常流水")
_WEIGHT_HINT = "设定权值 1-5；不填按类型默认（人物与关系 5、约定 4、经历与偏好 3、流水 1）"


class StoreMemory(ToolPlugin):
    name = "store_memory"
    description = (
        "存储一条长期记忆（以后还能被想起来的事）。\n"
        "三个字段各管一段，别写重：\n"
        "1) 类型 mem_type：这是哪种记忆 —— " + _TYPE_HINT + "。\n"
        "2) 权值 weight（可不填）：这条值多少 —— " + _WEIGHT_HINT + "。\n"
        "   权值越低越容易淡出：流水那一档会随静默时间与聊天量被清掉腾出空间，"
        "其余只是不再自动进上下文，显式检索仍可取回。被想起过就重新计时，权值本身不随时间改动。\n"
        "3) 焦段锚点：这条记忆该在哪些地方被想起 ——\n"
        "   · session_foci：会话焦段 id 列表（一组同类对话，如「学生群」）；\n"
        "   · semantic_foci：语义焦段 id 列表（一段话题，如「化学教学」）；\n"
        "   · 两个都不填 = 只在本会话与当前语义焦段下可见（最窄的一档，换个地方就想不起来）；\n"
        "   · 想让任何会话都能想起，锚预置的「所有聊天」。\n"
        "   焦段 id 用 list_focus 查，别凭空写。\n"
        f"标题是一句概括（≤ {MAX_TITLE_CHARS} 字），内容只写要点（≤ {MAX_CONTENT_CHARS} 字）；"
        "别把原文整段搬进来——原文本来就在对话里。超了这次也照原样存下，但会提醒你，下次请压短。\n"
        "同一件事只存一次，重复存会各留一份。"
    )
    segment = "memory"
    parameters = {
        "title": {"type": "string", "description": "记忆标题（简短概括）"},
        "content": {"type": "string", "description": "记忆详细内容"},
        "scope": {
            "type": "string", "enum": ["private", "group"],
            "description": "可见范围：private 仅自己可见，group 群内成员可见",
        },
        "mem_type": {
            "type": "string", "enum": list(MEMORY_TYPES),
            "description": "记忆类型：" + _TYPE_HINT,
        },
        "weight": {"type": "integer", "nullable": True, "description": _WEIGHT_HINT},
        "session_foci": {
            "type": "array", "items": {"type": "string"}, "nullable": True,
            "description": "会话焦段 id 列表；不填则只算当前会话",
        },
        "semantic_foci": {
            "type": "array", "items": {"type": "string"}, "nullable": True,
            "description": "语义焦段 id 列表；聊到这些话题时能被想起",
        },
        "group_id": {
            "type": "integer", "nullable": True,
            "description": "群聊 ID（scope=group 时需要）",
        },
    }
    required = ["title", "content", "scope", "mem_type"]
    states = ["active"]
    admin_description = "将重要信息存入长期记忆（向量数据库）。记忆带类型、权值与焦段锚点，构成 AI 的经历。"
    trigger_condition = "AI 认为信息值得长期记忆时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent import focus_service
        from app.services.memory.memory_buffer import enqueue_memory
        from app.models.agent import Agent

        title = arguments["title"]
        content = arguments["content"]
        scope = arguments["scope"]
        mem_group_id = arguments.get("group_id", group_id if scope == "group" else None)

        mem_type = (arguments.get("mem_type") or "daily").strip()
        weight = clamp_weight(arguments.get("weight") or default_weight(mem_type))

        # 锚点按当前焦段表校验：不存在的丢掉、轴放错的挪回来，并如实告诉 AI
        session_foci, semantic_foci, problems = await focus_service.check_anchors(
            db, agent_id, arguments.get("session_foci"), arguments.get("semantic_foci"))

        agent_row = await db.execute(_sel(Agent).where(Agent.id == agent_id))
        agent_obj = agent_row.scalar_one_or_none()
        ai_type = agent_obj.ai_type if agent_obj else "resonance"
        trigger_user_id = context.get("trigger_user_id")

        api_key = context.get("api_key")
        api_base = context.get("api_base_url", "https://api.deepseek.com")

        await enqueue_memory(
            agent_id=agent_id, title=title, content=content,
            scope=scope, group_id=mem_group_id,
            api_base_url=api_base, api_key=api_key,
            trigger_user_id=trigger_user_id, ai_type=ai_type,
            source="tool", low_value=False,
            mem_type=mem_type, weight=weight,
            session_foci=session_foci, semantic_foci=semantic_foci,
        )

        result = {
            "success": True, "title": title, "queued": True,
            "mem_type": mem_type, "weight": weight,
            "anchors": {"session": session_foci, "semantic": semantic_foci},
        }
        notes = list(problems)
        if warning := pure_focus.anchor_warning(session_foci, semantic_foci):
            notes.append(warning)
        notes.extend(shape_warnings(title, content))
        if notes:
            result["message"] = "；".join(notes)
        return result


ToolRegistry.register(StoreMemory)
