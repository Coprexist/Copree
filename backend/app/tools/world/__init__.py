"""世界工具包 —— 一个工具一个文件，导入即注册。

对外只有三个入口（别从其他模块直接 import 具体工具）：
- WORLD_TOOLS：给 LLM 的 function calling 定义（只含对外暴露的，缓存）
- run_world_tool / execute_world_tool：唯一执行入口（插件 → 世界自定义 skill → 未知工具）
- tool_result_summary / tool_result_detail：唯一展示入口（卡片折叠行 + 展开详情）

性能：分发是字典查表；定义列表只构建一次；发现（import 各工具模块）只在首次导入本包时做一次。
"""
from __future__ import annotations

import json
import logging

from app.tools.world.base import (
    SEGMENTS,
    WorldToolContext,
    WorldToolPlugin,
    WorldToolRegistry,
    default_detail,
    result_gist,
)

logger = logging.getLogger(__name__)

def __getattr__(name: str):
    """模块级惰性属性：WORLD_TOOLS 在首次访问时才构建。

    发现（import 各工具模块）由 app/tools/__init__.py 统一负责，这里不再自己扫一遍；
    惰性也保证首次访问一定发生在所有工具注册之后，不会拿到半份清单。
    """
    if name == "WORLD_TOOLS":
        return WorldToolRegistry.definitions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def run_world_tool(world_repo, world, name: str, arguments: str,
                         turn_state: dict | None = None, on_progress=None,
                         approved: bool = False) -> dict:
    """执行一次工具调用（不去重）。命令、代理等确定性场景用这个。

    approved：已获用户同意（自动模式或门禁批准）——传给工具，免得它自己再问一遍。
    """
    plugin = WorldToolRegistry.get(name)
    if plugin is not None:
        ctx = WorldToolContext(
            world_repo=world_repo,
            world=world,
            arguments=arguments,
            args=_parse_args(arguments),
            turn_state=turn_state,
            on_progress=on_progress,
            approved=approved,
        )
        return await plugin.execute(ctx)

    # 世界自定义 skill：名字与返回值都由世界自己定，能力归世界（文件式 skill，沙箱执行）
    from app.services.world.world_skill_runtime import execute_skill
    result = await execute_skill(world_repo.session, world, name, arguments, scope="ai")
    if result is not None:
        return result
    return {"success": False, "error": f"未知工具: {name}"}


async def execute_world_tool(world_repo, world, name: str, arguments: str,
                             turn_state: dict | None = None, on_progress=None,
                             approved: bool = False) -> dict:
    """AI 调用入口：run_world_tool + 温和去重。

    5 分钟内重复调用且结果与上次完全一致才提示跳过：list_world_files 这类可能是 AI 在
    验证写入结果，结果变了就不算重复；超过 5 分钟（如用户手动改了文件）允许重跑。
    """
    import time as _time

    result = await run_world_tool(world_repo, world, name, arguments, turn_state,
                                  on_progress=on_progress, approved=approved)
    if turn_state is None:
        return result
    executed = turn_state.setdefault("executed", {})
    key = f"{name}|{arguments}"
    now = _time.monotonic()
    prev = executed.get(key)
    if prev is not None and (now - prev["ts"]) <= 300 and prev["result"] == result:
        return {
            **result,
            "skipped": True,
            "note": "该操作本次对话已执行过（5 分钟内且结果相同），请直接总结或执行新操作，不要重复。",
        }
    # 只缓存成功结果：失败常常是模型改一个字符后的重试，拦下来只会白耗一轮
    # （世界 AI 2026-09-21 反馈）。键里已含参数，这里补的是"失败永不拦截"。
    if result.get("success"):
        executed[key] = {"result": result, "ts": now}
    else:
        executed.pop(key, None)
    return result


def tool_result_summary(name: str, result: dict) -> str:
    """卡片折叠态那一行。插件必须自带；世界自定义 skill 走这里统一定义的规则。"""
    plugin = WorldToolRegistry.get(name)
    if plugin is not None:
        return plugin.summary(result)
    if not result.get("success"):
        return f"{name} 失败：{result.get('error', '未知错误')}"
    gist = result_gist(result)
    return f"{name}：{gist}" if gist else f"{name} 执行完成"


def tool_result_detail(name: str, arguments, result: dict) -> str:
    """卡片展开后的详情。插件可自定义；技能走通用渲染。

    arguments 可为原始 JSON 字符串或已解析的 dict——调用方通常只有原始那份。
    """
    args = _parse_args(arguments) if isinstance(arguments, str) else (arguments or {})
    plugin = WorldToolRegistry.get(name)
    if plugin is not None:
        return plugin.detail(args, result)
    return default_detail(args, result)


def tool_label(name: str) -> str:
    """工具的中文名（卡片标题；世界自定义 skill 没有 label，返回空串让前端回退）"""
    plugin = WorldToolRegistry.get(name)
    return plugin.label if plugin is not None else ""


def _parse_args(arguments: str) -> dict:
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}


__all__ = [
    "SEGMENTS",
    "WORLD_TOOLS",
    "WorldToolContext",
    "WorldToolPlugin",
    "WorldToolRegistry",
    "execute_world_tool",
    "run_world_tool",
    "tool_label",
    "tool_result_detail",
    "tool_result_summary",
]
