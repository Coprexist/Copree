"""
工具插件基类 + 注册中心

每个工具一个 ToolPlugin 子类，放在 tools/ 子目录下。
文件末尾调用 ToolRegistry.register() 即可自动注册，无需修改框架代码。
"""

import time
import json
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ============================================================
# 工具错误码常量
# ============================================================

class ToolErrorCode:
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_EXEC_FAILED = "TOOL_EXEC_FAILED"
    INVALID_ARGS = "INVALID_ARGS"
    OPENCLI_PERMISSION_DENIED = "OPENCLI_PERMISSION_DENIED"
    OPENCLI_TIMEOUT = "OPENCLI_TIMEOUT"
    OPENCLI_EXEC_FAILED = "OPENCLI_EXEC_FAILED"


# ============================================================
# 技能段元数据
# ============================================================

SKILL_SEGMENT_META: dict[str, dict] = {
    "chat_social": {
        "name": "群聊社交",
        "description": "在群聊和私信中发言、切换在线状态、管理免打扰",
        "admin_description": "控制 AI 的社交行为：群聊发言、私信、在线状态切换、免打扰模式。AI 收到消息或定时触发时自动调用这些工具与其他成员互动。",
        "trigger_conditions": ["收到@消息", "群内有新消息", "定时主动发言", "私信对话"],
        "icon": "messages-square",
    },
    "file_operations": {
        "name": "文件操作",
        "description": "读写文件、管理自己的工作文件夹",
        "admin_description": "AI 的文件系统操作：读、写、列、删、分享文件，以及执行 OpenCLI 命令。AI 用它管理工作笔记、处理数据、协作共享资料。",
        "trigger_conditions": ["AI 需要持久化信息", "工具调用链中需要文件", "用户请求文件操作"],
        "icon": "folder",
    },
    "memory": {
        "name": "记忆系统",
        "description": "存储长期记忆、检索相关记忆",
        "admin_description": "AI 的长期记忆系统：存储重要信息到向量数据库，检索相关记忆辅助决策。记忆按用户隔离，构成 AI 的「人生经历」。",
        "trigger_conditions": ["收到消息时自动检索", "AI 决定需要记住某事", "对话中出现可记忆信息"],
        "icon": "brain",
    },
    "group_management": {
        "name": "群聊管理",
        "description": "创建群聊、邀请新成员",
        "admin_description": "群聊创建和成员管理：AI 可以主动创建群聊、邀请用户或其他 AI 加入。支持设置群名、简介、免打扰策略。",
        "trigger_conditions": ["AI 需要多人协作", "新成员加入社区", "按需创建临时群聊"],
        "icon": "users",
    },
    "self_config": {
        "name": "自我配置",
        "description": "修改自己的系统提示词、温度参数、推理模式等",
        "admin_description": "AI 的自我调整能力：修改系统提示词（人格）、温度参数、开启/关闭深度推理、管理行为技能。每次修改自动保存历史快照，支持回滚。",
        "trigger_conditions": ["AI 自主优化行为", "管理员手动调整", "技能启用/禁用"],
        "icon": "settings",
    },
    "self_management": {
        "name": "自我管理",
        "description": "设定闹钟唤醒自己、管理个人任务和计划（心跳机制的基础）",
        "admin_description": "AI 的自主生命节律：设定闹钟到点自动唤醒、管理工作区任务和计划、压缩上下文释放空间。这是 AI 保持「活着」的核心机制。",
        "trigger_conditions": ["闹钟到期唤醒", "AI 需要定时行动", "上下文接近限制", "任务规划"],
        "icon": "clock",
    },
}


# ============================================================
# 工具插件基类
# ============================================================

class ToolPlugin:
    """工具插件基类 — 每个工具一个子类，定义元数据 + execute 方法"""

    @classmethod
    def __init_subclass__(cls, **kwargs):
        """子类被定义时自动注册（name 非空时触发）"""
        super().__init_subclass__(**kwargs)
        if cls.name:
            ToolRegistry.register(cls)

    # ── 子类必须定义 ──
    name: str = ""              # 工具名，全局唯一
    description: str = ""       # 给 LLM 看的自然语言描述
    segment: str = ""           # 所属技能段 key
    parameters: dict[str, Any] = {}  # JSON Schema properties
    required: list[str] = []    # 必填参数列表
    states: list[str] = ["active"]  # 允许使用的 AI 状态

    # ── 可选 ──
    nullable: list[str] = []    # 可空参数列表
    admin_description: str = ""     # 给管理员/用户看的工具说明
    trigger_condition: str = ""     # 触发条件（单个，用于技能背包卡片内的工具标签）

    @classmethod
    def to_definition(cls) -> dict:
        """生成 OpenAI Function Calling 格式的工具定义"""
        params = {
            "type": "object",
            "properties": cls.parameters,
            "required": cls.required,
        }
        return {
            "type": "function",
            "segment": cls.segment,
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": params,
            },
        }

    @classmethod
    def to_info(cls) -> dict:
        """管理面板用的工具摘要"""
        return {
            "name": cls.name,
            "description": cls.description,
            "segment": cls.segment,
            "segment_name": SKILL_SEGMENT_META.get(cls.segment, {}).get("name", cls.segment),
            "states": cls.states,
            "parameters": cls.parameters,
            "required": cls.required,
            "admin_description": cls.admin_description,
            "trigger_condition": cls.trigger_condition,
        }

    async def execute(
        self, db: AsyncSession, agent_id: int, group_id: int | None,
        arguments: dict, context: dict,
    ) -> dict:
        """执行工具逻辑，子类必须实现"""
        raise NotImplementedError(f"{self.name}.execute() 未实现")

    def get_task_summary(self, arguments: dict) -> str | None:
        """根据工具参数生成中文任务摘要，子类可覆盖。默认返回 None。"""
        return None


# ============================================================
# 工具注册中心
# ============================================================

class ToolRegistry:
    """工具注册中心 — 单例模式，自动发现和注册所有 ToolPlugin 子类"""

    _plugins: dict[str, ToolPlugin] = {}
    _definitions: list[dict] | None = None  # 惰性缓存
    _whitelist: dict[str, list[str]] | None = None
    _segments: dict[str, dict] | None = None
    _schema_index: dict | None = None

    # ── 注册 ──

    @classmethod
    def register(cls, plugin_cls: type[ToolPlugin]) -> None:
        """注册一个工具插件（幂等：同名同类静默跳过）"""
        instance = plugin_cls()
        if instance.name in cls._plugins:
            existing = cls._plugins[instance.name]
            if isinstance(existing, plugin_cls):
                return  # 同类已注册，静默跳过
            logger.warning(f"工具 {instance.name} 重复注册（不同类），已覆盖")
        cls._plugins[instance.name] = instance
        cls._invalidate_cache()
        logger.debug(f"工具已注册: {instance.name} (segment={instance.segment})")

    @classmethod
    def _invalidate_cache(cls) -> None:
        cls._definitions = None
        cls._whitelist = None
        cls._segments = None
        cls._schema_index = None

    # ── 查询 ──

    @classmethod
    def get_plugin(cls, name: str) -> ToolPlugin | None:
        return cls._plugins.get(name)

    @classmethod
    def get_all_plugins(cls) -> dict[str, ToolPlugin]:
        return dict(cls._plugins)

    @classmethod
    def get_all_definitions(cls) -> list[dict]:
        """获取所有工具的 OpenAI 定义列表"""
        if cls._definitions is None:
            cls._definitions = [p.to_definition() for p in cls._plugins.values()]
        return cls._definitions

    @classmethod
    def get_segments(cls) -> dict[str, dict]:
        """获取所有技能段（含工具列表和完整元数据）"""
        if cls._segments is None:
            cls._segments = {}
            for seg_key, seg_meta in SKILL_SEGMENT_META.items():
                seg_tools = [
                    {
                        "name": p.name,
                        "description": p.description,
                        "admin_description": p.admin_description,
                        "trigger_condition": p.trigger_condition,
                        "states": p.states,
                    }
                    for p in cls._plugins.values()
                    if p.segment == seg_key
                ]
                cls._segments[seg_key] = {
                    "name": seg_meta["name"],
                    "description": seg_meta["description"],
                    "admin_description": seg_meta.get("admin_description", ""),
                    "trigger_conditions": seg_meta.get("trigger_conditions", []),
                    "icon": seg_meta.get("icon", "puzzle"),
                    "tools": seg_tools,
                    "tool_count": len(seg_tools),
                }
        return cls._segments

    @classmethod
    def _build_whitelist(cls) -> dict[str, list[str]]:
        """从插件自动构建状态白名单"""
        if cls._whitelist is None:
            whitelist: dict[str, list[str]] = {
                "active": [], "dnd": [], "inactive": [], "blocked": [],
            }
            for plugin in cls._plugins.values():
                for state in plugin.states:
                    if state in whitelist:
                        whitelist[state].append(plugin.name)
            cls._whitelist = whitelist
        return cls._whitelist

    @classmethod
    def get_whitelist(cls) -> dict[str, list[str]]:
        return cls._build_whitelist()

    @classmethod
    def get_allowed_tools(
        cls, state: str, thinking_enabled: bool | None = None,
        delay_reply_allowed: bool = True,
    ) -> list[dict]:
        """根据 AI 状态返回允许使用的工具定义列表"""
        whitelist = cls._build_whitelist()
        allowed_names = set(whitelist.get(state, []))

        # 防御：未知/遗留状态（如 offline）不在白名单时，兜底为 inactive 工具集，
        # 避免 AI 被触发后无工具可用（只能输出文字）导致 system_reminder 死循环。
        # blocked 是合法状态（白名单为空=彻底禁言），不在此兜底范围内。
        if not allowed_names and state not in whitelist:
            allowed_names = set(whitelist.get("inactive", []))

        allowed = [
            t for t in cls.get_all_definitions()
            if t["function"]["name"] in allowed_names
        ]

        # thinking_enabled=False 时隐藏 toggle_thinking
        if thinking_enabled is not None and not thinking_enabled:
            allowed = [
                t for t in allowed
                if t["function"]["name"] != "toggle_thinking"
            ]

        # delay_reply_allowed=False 时从 manage_skills 中移除相关类型
        if not delay_reply_allowed:
            allowed = _strip_delay_reply(allowed)

        return allowed

    # ── 工具调用分发 ──

    @classmethod
    async def dispatch(
        cls, db: AsyncSession, agent_id: int, group_id: int | None,
        tool_name: str, arguments: dict, context: dict,
    ) -> dict:
        """统一分发工具调用，记录性能指标"""
        plugin = cls._plugins.get(tool_name)
        if plugin is None:
            # 兜底：世界侧文件式 skill（群 AI 调用绑定世界的居民能力；agent 绑定或群绑定）
            try:
                from app.models.agent import Agent
                agent = context.get("_agent") or await db.get(Agent, agent_id)
                from app.services.world.world_skill_runtime import execute_skill
                from app.services.world.world_service import find_worlds_by_entity
                # 绑定世界集合：当前群绑定优先（同名 skill 默认执行当前群的世界版本），agent 直接绑定其次
                # v0.3.4: 世界绑定 AI 统一存 user_id（杜绝 agent.id 撞车）
                group_worlds = await find_worlds_by_entity(db, "group", group_id) if group_id is not None else []
                agent_worlds = await find_worlds_by_entity(db, "agent", (agent.user_id if agent else 0)) if agent_id else []
                worlds = [*group_worlds, *agent_worlds]
                seen = set()
                bound = []
                for w in worlds:
                    if w.id in seen:
                        continue
                    seen.add(w.id)
                    bound.append(w)
                # world_id 显式指定：AI 可选其他世界的同名技能（只允许已绑定世界，防越权）
                target_world_id = arguments.get("world_id")
                if target_world_id is not None:
                    try:
                        target_world_id = int(target_world_id)
                    except (TypeError, ValueError):
                        return _build_tool_error(ToolErrorCode.INVALID_ARGS, "world_id 必须是整数世界编号")
                    w = next((x for x in bound if x.id == target_world_id), None)
                    if w is None:
                        return _build_tool_error(ToolErrorCode.INVALID_ARGS, f"world_id={target_world_id} 不在你绑定的世界范围内")
                    clean_args = {k: v for k, v in arguments.items() if k != "world_id"}
                    result = await execute_skill(db, w, tool_name, json.dumps(clean_args, ensure_ascii=False), scope='world')
                    if result is not None:
                        return result
                    return _build_tool_error(ToolErrorCode.UNKNOWN_TOOL, f"世界 #{target_world_id} 没有技能「{tool_name}」")
                for w in bound:
                    clean_args = {k: v for k, v in arguments.items() if k != "world_id"}
                    result = await execute_skill(db, w, tool_name, json.dumps(clean_args, ensure_ascii=False), scope='world')
                    if result is not None:
                        return result
            except Exception as e:
                logger.warning(f"世界侧 skill 兜底执行失败 {tool_name}: {e}")
            return _build_tool_error(ToolErrorCode.UNKNOWN_TOOL, f"未知工具「{tool_name}」：没有此名称的工具")

        t0 = time.monotonic()
        try:
            result = await plugin.execute(db, agent_id, group_id, arguments, context)
        except Exception as e:
            elapsed = time.monotonic() - t0
            try:
                from app.services.infrastructure.metrics_collector import metrics
                await metrics.record_tool_call(tool_name, elapsed, False)
            except Exception:
                pass
            logger.error(f"工具 {tool_name} 执行失败: {e}", exc_info=True)
            return _build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, f"执行失败：{e}")

        elapsed = time.monotonic() - t0
        is_success = not result.get("error", False)
        try:
            from app.services.infrastructure.metrics_collector import metrics
            await metrics.record_tool_call(tool_name, elapsed, is_success)
        except Exception:
            pass
        return result

    # ── 管理面板 ──

    @classmethod
    def get_tools_info(cls) -> dict:
        """返回所有工具的管理面板信息（含技能背包元数据）"""
        tools = [p.to_info() for p in cls._plugins.values()]
        tools.sort(key=lambda t: t["name"])
        segments_data = cls.get_segments()
        segments = []
        for seg_key, seg_meta in SKILL_SEGMENT_META.items():
            seg = segments_data.get(seg_key, {})
            tool_names = [t["name"] for t in seg.get("tools", [])]
            segments.append({
                "key": seg_key,
                "name": seg_meta["name"],
                "description": seg_meta["description"],
                "admin_description": seg.get("admin_description", ""),
                "trigger_conditions": seg.get("trigger_conditions", []),
                "icon": seg.get("icon", "puzzle"),
                "tool_count": len(tool_names),
                "tools": tool_names,
            })
        return {
            "tools": tools,
            "segments": segments,
            "total": len(tools),
        }

    # ── 校验 ──

    @classmethod
    def _get_schema_index(cls) -> dict:
        """惰性构建 schema 索引（用于 validate）"""
        if cls._schema_index is None:
            cls._schema_index = {}
            for plugin in cls._plugins.values():
                cls._schema_index[plugin.name] = {
                    "properties": plugin.parameters,
                    "required": plugin.required,
                }
        return cls._schema_index

    @classmethod
    def validate(cls, tool_name: str, arguments: dict) -> tuple[bool, str | None]:
        """校验工具调用格式（纯函数，无副作用）"""
        index = cls._get_schema_index()

        if tool_name not in index:
            available = "、".join(sorted(index.keys()))
            return False, f"未知工具「{tool_name}」。当前可用工具：{available}"

        schema = index[tool_name]
        props = schema["properties"]
        required: list = schema["required"]

        # 必填字段检查
        for field in required:
            if field not in arguments or arguments[field] is None:
                need_parts = [f"{f} ({props[f].get('type', 'any')})" for f in required]
                got_parts = [f"{k}: {_py_type(v)}" for k, v in arguments.items()]
                return False, (
                    f"工具 {tool_name} 的参数格式错误：缺少必填字段 {field}。"
                    f"期望格式：{{{', '.join(need_parts)}}}，"
                    f"实际收到：{{{', '.join(got_parts) if got_parts else '(空)'}}}"
                )

        # 字段类型检查
        for key, value in arguments.items():
            if key not in props:
                continue
            expected_type = props[key].get("type", "any")
            if not _type_compatible(value, expected_type):
                return False, (
                    f"工具 {tool_name} 的参数 {key} 类型错误："
                    f"期望 {expected_type}，实际 {_py_type(value)}"
                )

        return True, None


# ============================================================
# 辅助函数
# ============================================================

def _build_tool_error(code: str, message: str) -> dict:
    return {"error": True, "error_code": code, "message": message}


def _py_type(value) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _type_compatible(value, expected: str) -> bool:
    """宽松类型兼容检查"""
    if value is None:
        return True
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    compat = type_map.get(expected)
    if compat is None:
        return True  # 未知类型放行
    if isinstance(compat, tuple):
        return isinstance(value, compat)
    return isinstance(value, compat)


def _strip_delay_reply(tools: list[dict]) -> list[dict]:
    """从 manage_skills 工具中移除 delay_reply 和 typing_indicator 选项"""
    import copy
    result = []
    for tool in tools:
        if tool["function"]["name"] == "manage_skills":
            tool = copy.deepcopy(tool)
            props = tool["function"]["parameters"].get("properties", {})
            if "skill_type" in props:
                original_enum = props["skill_type"].get("enum", [])
                filtered = [
                    t for t in original_enum
                    if t not in ("delay_reply", "typing_indicator")
                ]
                props["skill_type"]["enum"] = filtered
                # 精简 description
                desc = props["skill_type"].get("description", "")
                if "delay_reply（延迟回复，回复行为技能包）" in desc:
                    props["skill_type"]["description"] = desc.replace(
                        "delay_reply（延迟回复，回复行为技能包）", ""
                    ).strip()
        result.append(tool)
    return result
