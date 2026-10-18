"""
update_self_config 工具 — AI 修改自己的配置参数
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.utils.pure import model_params

logger = logging.getLogger(__name__)


class UpdateSelfConfig(ToolPlugin):
    name = "update_self_config"
    description = ("修改自己的配置参数。可以调整性格、温度、推理模式、工具调用轮次等。\n"
                   "注意：config_profile 设为 \"chat\"/\"immersive\"/\"digital_life\" 表示切换到对应预设档位，预设只是起点，之后可自由调参。")
    segment = "self_config"
    parameters = {
        "system_prompt": {"type": "string", "nullable": True, "description": "新的系统提示词（性格描述）"},
        # 采样参数：区间与后果取自 model_params（API 允许的范围比该填的范围宽得多）
        "temperature": {"type": "number", "nullable": True,
                        "description": f"采样温度。{model_params.describe('temperature')}"},
        "top_p": {"type": "number", "nullable": True,
                  "description": f"核采样。{model_params.describe('topP')}"},
        "presence_penalty": {"type": "number", "nullable": True,
                             "description": f"话题新鲜度惩罚。{model_params.describe('presencePenalty')}"},
        "frequency_penalty": {"type": "number", "nullable": True,
                              "description": f"重复惩罚。{model_params.describe('frequencyPenalty')}"},
        "thinking_enabled": {"type": "boolean", "nullable": True, "description": "是否开启深度推理模式"},
        "config_profile": {
            "type": "string", "nullable": True,
            "description": "配置档位：chat（聊天档）/ immersive（深度沉浸档）/ digital_life（数字生命档）",
        },
        "hide_ai_identity": {"type": "boolean", "nullable": True, "description": "是否隐藏 AI 身份（隐藏后你的系统提示词不会提及你是 AI）"},
        "max_tool_rounds": {"type": "integer", "nullable": True, "description": "单次回复最大工具调用轮次，范围 1-20。谨慎调高，每轮都会消耗 token"},
        "alarm_max_tool_rounds": {"type": "integer", "nullable": True, "description": "闹钟/心跳任务的最大工具调用轮次，范围 1-30"},
        "force_alarm_on_end": {"type": "boolean", "nullable": True, "description": "对话结束时是否必须设定闹钟。开启后每次回复结束前要 set_alarm"},
        "plan_injection_enabled": {"type": "boolean", "nullable": True, "description": "是否把你自己排的计划与闹钟投进上下文（会在历史尾部显示一块计划板）"},
        "retire_handover_self": {"type": "boolean", "nullable": True, "description": "状态帧的后事是否由你自己办：开=帧位满时挂起等你处置（整理锚点与记忆，办完调 finish_frame 销掉）；关=平台代销，只给你一条告知"},
        "frame_capacity": {"type": "integer", "nullable": True, "description": "最多保留多少个状态帧，范围 2-500（默认 31）。超出的挂起或代销，取决于 retire_handover_self"},
        "max_alarms": {"type": "integer", "nullable": True, "description": "最多可设多少个活跃闹钟，范围 1-50"},
        "delay_reply_enabled": {"type": "boolean", "nullable": True, "description": "是否启用延迟回复功能（需要管理员开启全局开关）"},
    }
    required = []
    states = ["active"]
    admin_description = "修改自己的配置（系统提示词、温度参数、行为协议等）。每次修改自动保存历史快照，支持回滚到之前的版本。"
    trigger_condition = "AI 自主优化 / 管理员手动调整配置"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent.agent_service import update_agent_config

        _self_config_fields = [
            "system_prompt", "temperature", "top_p", "presence_penalty",
            "frequency_penalty", "thinking_enabled", "config_profile",
            "hide_ai_identity", "max_tool_rounds", "alarm_max_tool_rounds",
            "force_alarm_on_end", "plan_injection_enabled", "retire_handover_self",
            "frame_capacity", "max_alarms", "delay_reply_enabled",
            "allow_friend_requests", "auto_respond_friend_request",
        ]

        # 采样参数按安全区间收敛：调满（temperature 2 / 惩罚 2）会让输出退化成不成句的字串，
        # 2026-10-18 有个 AI 就是这么把自己弄"傻"的。越界不拒绝，改成收敛并如实告知改成了什么。
        snake_to_camel = {v: k for k, v in model_params.SELF_CONFIG_NAMES.items()}
        converged: list[str] = []

        updates = {}
        for field in _self_config_fields:
            if field not in arguments or arguments[field] is None:
                continue
            value = arguments[field]
            camel = snake_to_camel.get(field)
            if camel and isinstance(value, (int, float)) and not isinstance(value, bool):
                safe = model_params.clamp(camel, value)
                if float(safe) != float(value):
                    converged.append(f"{field} {value}→{safe}")
                    value = safe
            updates[field] = value

        if not updates:
            return {"error": True, "message": "没有需要更新的配置项"}

        try:
            await update_agent_config(
                db, agent_id=agent_id, operator_id=agent_id,
                updates=updates, is_admin=False,
            )
            await db.commit()
            if "system_prompt" in updates:
                # 自己刚改的人格：轮内喊一声；下一轮的变更通知只说已生效（别把改前改后念一遍）
                from app.ai.executor import shout_change
                from app.services.capability_versioning import agent_prompt_source, mark_self_change
                mark_self_change(agent_id, agent_prompt_source(agent_id))
                await shout_change(agent_id, "你刚改的人格提示词已生效（下次 compact / 清空上下文时整体换上）。")
            message = f"已更新，会在之后同步给你（{', '.join(updates.keys())}）"
            if converged:
                message += (
                    "。越界的采样参数已按安全值收敛：" + "、".join(converged)
                    + "（拉到上限时输出会退化成不成句的字串；理由见《创建 AI 参考》的采样参数一节）"
                )
            return {"success": True, "message": message}
        except ValueError as e:
            return {"error": True, "message": str(e)}


ToolRegistry.register(UpdateSelfConfig)
