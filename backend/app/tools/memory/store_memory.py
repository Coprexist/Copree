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
        "存储一条长期记忆（以后还能被想起来的事）；带 memory_id = 改已有的那条。\n"
        "**改**：memory_id 用 recall_memory 查，只改你传了的字段——锚错了改锚点、权值给错了改权值、"
        "标题写歪了改标题。改比删好：换来的还是同一条记忆，不必担心删了又想不起来。\n"
        "**存**：三个字段各管一段，别写重：\n"
        "1) 类型 mem_type：这是哪种记忆 —— " + _TYPE_HINT + "。\n"
        "2) 权值 weight（可不填）：这条值多少 —— " + _WEIGHT_HINT + "。\n"
        "   权值越低越容易淡出：流水那一档会随静默时间与聊天量被清掉腾出空间，"
        "其余只是不再自动进上下文，显式检索仍可取回。被想起过就重新计时，权值本身不随时间改动。\n"
        "3) 焦段锚点：这条记忆该在哪些地方被想起 ——\n"
        "   · session_foci：会话焦段 id 列表（一组同类对话，如「学生群」）；\n"
        "   · semantic_foci：语义焦段 id 列表（一段话题，如「化学教学」）；\n"
        "   · 两个都不填 = 只在本会话与当前语义焦段下可见（最窄的一档，换个地方就想不起来）；\n"
        "   · 想让任何会话都能想起，锚预置的「所有聊天」。\n"
        "   焦段 id 用 list_focus 查，别凭空写。改锚点时两个一起给，缺的那个按空算（= 只在本会话可见）。\n"
        f"标题是一句概括（≤ {MAX_TITLE_CHARS} 字），内容只写要点（≤ {MAX_CONTENT_CHARS} 字）；"
        "别把原文整段搬进来——原文本来就在对话里。超了这次也照原样存下，但会提醒你，下次请压短。\n"
        "同一件事只存一次，重复存会各留一份。"
    )
    segment = "memory"
    parameters = {
        "memory_id": {
            "type": "integer", "nullable": True,
            "description": "要改的那条记忆 id（recall_memory 的返回里有）；带上它就不再新建，只改你传了的字段",
        },
        "title": {"type": "string", "nullable": True, "description": "记忆标题（简短概括）；改标题会重算向量"},
        "content": {"type": "string", "nullable": True, "description": "记忆详细内容"},
        "scope": {
            "type": "string", "enum": ["private", "group"], "nullable": True,
            "description": "可见范围：private 仅自己可见，group 群内成员可见",
        },
        "mem_type": {
            "type": "string", "enum": list(MEMORY_TYPES), "nullable": True,
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
    # 两条路要的字段不同（改只要 memory_id，存要标题+正文+范围+类型），所以必填放在 execute 里判，
    # 免得"改一条权值"被迫连正文一起重交一遍
    required = []
    states = ["active"]
    admin_description = "将重要信息存入长期记忆（向量数据库）。记忆带类型、权值与焦段锚点，构成 AI 的经历；带 memory_id 时改已有的那条。"
    trigger_condition = "AI 认为信息值得长期记忆时 / 发现已有的记忆该修正时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        if arguments.get("memory_id"):
            result = await self._update(db, agent_id, group_id, arguments, context)
        else:
            result = await self._store(db, agent_id, group_id, arguments, context)
        if result.get("success"):
            # 写成功才喊：轮内一句「已生效」+ 给下一轮的变更通知打「自己改的」记号
            from app.services.memory.memory_service import note_memory_changed
            await note_memory_changed(agent_id, group_id)
        return result

    async def _store(self, db: AsyncSession, agent_id: int, group_id: int | None,
                     arguments: dict, context: dict) -> dict:
        from app.services.agent import focus_service
        from app.services.memory.memory_buffer import enqueue_memory
        from app.models.agent import Agent

        missing = [k for k in ("title", "content", "scope", "mem_type") if not arguments.get(k)]
        if missing:
            return {"error": True, "message": f"新建记忆缺字段：{', '.join(missing)}"}

        title = arguments["title"]
        content = arguments["content"]
        scope = arguments["scope"]
        mem_group_id = arguments.get("group_id", group_id if scope == "group" else None)

        mem_type = str(arguments["mem_type"]).strip()
        weight = clamp_weight(arguments.get("weight") or default_weight(mem_type))

        # 锚点：校验焦段（不存在的丢掉、轴放错的挪回来）+ 空集物化为"当前会话 + 当前语义焦段"，
        # 并如实告诉 AI 最后落成了什么——锚点现在真的决定这条记忆在哪儿能被想起
        session_refs, session_foci, semantic_foci, problems = await focus_service.resolve_anchors(
            db, agent_id, group_id, context,
            arguments.get("session_foci"), arguments.get("semantic_foci"))

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
            session_refs=session_refs, session_foci=session_foci, semantic_foci=semantic_foci,
        )

        result = {
            "success": True, "title": title, "queued": True,
            "mem_type": mem_type, "weight": weight,
            "anchors": {"refs": session_refs, "session": session_foci, "semantic": semantic_foci},
        }
        notes = list(problems)
        if warning := pure_focus.anchor_warning(session_foci, semantic_foci):
            notes.append(warning)
        notes.extend(shape_warnings(title, content))
        if notes:
            result["message"] = "；".join(notes)
        return result

    async def _update(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        """改一条已有记忆：字段原样交给服务层（它知道列怎么摆、向量什么时候要重算）。"""
        from app.services.agent import focus_service
        from app.services.memory import memory_service

        scope = arguments.get("scope")
        anchors = None
        problems: list[str] = []
        if arguments.get("session_foci") is not None or arguments.get("semantic_foci") is not None:
            session_refs, session_foci, semantic_foci, problems = await focus_service.resolve_anchors(
                db, agent_id, group_id, context,
                arguments.get("session_foci"), arguments.get("semantic_foci"))
            anchors = (session_refs, session_foci, semantic_foci)

        out = await memory_service.update_memory(
            db, agent_id, int(arguments["memory_id"]),
            title=arguments.get("title"),
            content=arguments.get("content"),
            mem_type=arguments.get("mem_type"),
            weight=arguments.get("weight"),
            scope=scope,
            group_id=arguments.get("group_id", group_id if scope == "group" else None),
            anchors=anchors,
            api_base_url=context.get("api_base_url", "https://api.deepseek.com"),
            api_key=context.get("api_key"),
        )
        if not out.get("ok"):
            return {"error": True, "message": out.get("message", "没改成")}
        if not out["changed"]:
            return {"success": True, "id": out["id"], "title": out["title"],
                    "message": "这些字段跟原来一样，等于没改"}
        result = {"success": True, "id": out["id"], "title": out["title"],
                  "changed": out["changed"]}
        notes = list(problems) + list(out.get("notes") or [])
        if anchors is not None:
            result["anchors"] = {"refs": anchors[0], "session": anchors[1], "semantic": anchors[2]}
        if notes:
            result["message"] = "；".join(notes)
        return result


ToolRegistry.register(StoreMemory)
