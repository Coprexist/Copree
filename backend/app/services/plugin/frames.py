"""插件帧：插件替 AI 建状态帧。

帧是 AI 的——「它此刻在哪个状态」，账本、提示词前缀、工具白名单都跟着帧走
（见 docs/dev/frame_lifecycle.md）。这些帧 AI 自己就能管：list_states 看全量、pop_state 回
到某一层、close_state 收掉、finish_frame 销掉（tools/self_management/）。插件多出来的一条
路是**代建**：AI 在做插件这件事时（比如看一条视频），插件替它把状态帧建好、把内容写进去，
省掉 AI 自己拼一帧的参数。

边界三条：

1. **命名空间**：context_ref 必须以 `<plugin_id>:` 开头。群帧（group:）、私信（dm:）、
   世界帧（world:）仍然由平台与 AI 自己管，插件只代建「自己这件事」的帧；
2. **先声明**：帧类型写进 plugin.json 的 `frames` 段。平台只认声明过的类型，类型名同时是
   前端与日志里的显示标识（配 title 给三语）；
3. **平台落地**：链、容量、锁、账本一条都不在这里实现，全部转交 state_stack_service。

AI 的管理不受影响：插件建的帧照常出现在 list_states 里，AI 可以 pop_state 回去、close_state
收掉、finish_frame 销掉。这里的 leave_frame 只是插件替它收尾的一种方式，不是唯一出口。

工具白名单不另造机制：声明里的 tools 建帧时写进 `frame["tools"]`，`frame_tool_gate` 本来
就按这个字段隔离（世界帧走同一套）。所以插件想让 AI 在一帧里只看见自己的工具，写声明即可。

语义：**身份是 context_ref**，同一段插件会话永远只有一帧。再次进入同一 context_ref 是
**复活**原帧（帧 id 不变，插件可以长期记着它），而不是压第二条。认帧走 pure.frame_of——
与切会话、AI 自己压帧用的是同一个口，所以三条路径都不会为同一段会话各建一帧。
switch=False 只建沉睡帧，不把 AI 从现在所在的地方拽走。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 帧类型名：小写 + 下划线。它要进日志、前端状态列表与 convlog 的分组键，不做二次转义
FRAME_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
NAMESPACE_SEP = ":"


class FrameError(Exception):
    """插件帧的契约被违反。消息写给插件作者看，调用方不要吞成通用错误。"""


def _plugin_id() -> str:
    from app.services.plugin.api import get_current_plugin

    plugin_id = get_current_plugin()
    if not plugin_id:
        raise FrameError("插件帧接口只能在插件加载上下文里调用（api.set_current_plugin 未设置）")
    return plugin_id


def declared_frames(plugin_id: str) -> dict[str, dict[str, Any]]:
    """manifest 里 `frames` 段的规范化视图：类型 → {title, tools, skills}。

    返回新对象，不碰 catalog 的缓存（scan_disk 的嵌套块是共享的，只能读）。
    类型名不合法或声明不是对象的条目不认，记一条 warning——manifest 是作者手写的，
    静默丢弃比报错更难查。
    """
    from app.services.plugin import catalog

    manifest = catalog.scan_disk().get(plugin_id) or {}
    raw = manifest.get("frames")
    if not isinstance(raw, dict):
        return {}
    declared: dict[str, dict[str, Any]] = {}
    for type_name, spec in raw.items():
        name = str(type_name or "")
        if not FRAME_TYPE_RE.match(name) or not isinstance(spec, dict):
            logger.warning("插件 %s 的帧声明无效，已忽略：%r", plugin_id, type_name)
            continue
        declared[name] = {
            "title": str(spec.get("title") or name),
            "title_en": str(spec.get("title_en") or spec.get("title") or name),
            "title_ja": str(spec.get("title_ja") or spec.get("title") or name),
            "tools": [str(t) for t in (spec.get("tools") or [])],
            "skills": [str(s) for s in (spec.get("skills") or [])],
        }
    return declared


async def enter_frame(
    db: AsyncSession, agent, *, context_ref: str, type: str = "",
    label: str = "", why: str = "", doing: str = "", todo: str = "", plan: str = "",
    switch: bool = True,
) -> dict[str, Any]:
    """进一个插件帧，返回帧记录（含 id；插件应当存下来给 update_frame / leave_frame 用）。

    - 同一 context_ref 已有帧（哪怕已经结束）→ 复活它并就地更新这次给的内容，帧 id 不变；
    - 没有 → 新建；
    - switch=False → 只建沉睡帧（status=ended，进历史区），当前帧不动，适合预建。

    工具 / 技能白名单每次进入都按声明重新盖上去：声明改了就生效，不会冻住一份旧清单
    （世界帧现算技能是同一个理由）。
    """
    from app.services.agent.state_stack_service import (
        get_frames, park_frame, push_state, restore_frame,
    )
    from app.utils.pure.state_stack import frame_of, make_state_frame

    plugin_id = _plugin_id()
    declared = declared_frames(plugin_id)
    if not declared:
        raise FrameError(f"插件 {plugin_id} 没有在 plugin.json 的 frames 段声明任何帧类型")
    kind = type or next(iter(declared))
    spec = declared.get(kind)
    if spec is None:
        raise FrameError(
            f"插件 {plugin_id} 没有声明帧类型 {kind!r}（可选：{', '.join(sorted(declared))}）"
        )
    ref = _own_ref(plugin_id, context_ref)
    text = {k: v for k, v in {
        "label": label, "why": why, "doing": doing, "todo": todo, "plan": plan,
    }.items() if v}
    isolation = {"tools": spec["tools"] or None, "skills": spec["skills"] or None}

    existing = frame_of(await get_frames(db, agent.id), ref)
    if existing is not None:
        if existing.get("type") != kind:
            raise FrameError(
                f"{ref} 上已有的帧类型是 {existing.get('type')!r}，不能改成 {kind!r}："
                "一段会话只有一帧，要换类型先 leave_frame"
            )
        updates = {**text, **isolation}
        if not switch:
            # 白名单不在文本字段里，显式放行 tools / skills
            from app.services.agent.state_stack_service import mark_dormant
            from app.services.agent.state_stack_service import update_frame as _update
            from app.utils.pure.state_stack import FRAME_TEXT_FIELDS

            updated = await _update(
                db, agent.id, str(existing["id"]), updates,
                allow=(*FRAME_TEXT_FIELDS, "tools", "skills"),
            )
            # switch=False 的意思是"预建好、别把他拽走"：这一帧因此是沉睡的，清单里要出现
            await mark_dormant(db, agent.id, str(existing["id"]))
            return updated
        entered = await restore_frame(db, agent.id, str(existing["id"]), updates=updates)
        await _notify_entered(db, agent, entered)
        return entered

    frame = make_state_frame(kind, ref, **text)
    frame.update(isolation)
    if not switch:
        await park_frame(db, agent.id, frame)
        return frame
    await push_state(db, agent.id, frame)
    # push_state 之后以库里的为准：认帧走 frame_of，拿到的就是这段会话真正激活的那帧
    entered = frame_of(await get_frames(db, agent.id), ref) or frame
    await _notify_entered(db, agent, entered)
    return entered


async def update_frame(db: AsyncSession, agent, frame_id: str, **fields) -> dict[str, Any]:
    """改一帧的文本字段（label / why / doing / todo / plan / journal），不动当前帧。"""
    from app.services.agent.state_stack_service import get_frames
    from app.services.agent.state_stack_service import update_frame as _update

    plugin_id = _plugin_id()
    frame = _frame_by_id(await get_frames(db, agent.id), frame_id)
    if frame is None:
        raise FrameError(f"帧 {frame_id} 不存在")
    _require_own(plugin_id, frame)
    updated = await _update(db, agent.id, str(frame_id), fields)
    if updated is None:
        raise FrameError(f"帧 {frame_id} 已不存在")
    return updated


async def leave_frame(db: AsyncSession, agent, frame_id: str = "") -> dict[str, Any]:
    """结束一帧（status=ended）。记录保留：下次进同一 context_ref 会复活它。

    frame_id 为空时关**当前帧**，但这要求当前帧就是本插件的帧——他不会帮别的状态收尾。
    """
    from app.services.agent.state_stack_service import close_state, get_frames

    plugin_id = _plugin_id()
    frames = await get_frames(db, agent.id)
    frame = _frame_by_id(frames, frame_id) if frame_id else _own_current(plugin_id, frames)
    if frame is None:
        raise FrameError("没有找到要结束的插件帧：给 frame_id，或先 enter_frame")
    _require_own(plugin_id, frame)
    await close_state(db, agent.id, str(frame["id"]))
    return frame


async def list_frames(db: AsyncSession, agent, type: str = "") -> list[dict[str, Any]]:
    """列这个插件建过的帧（含已结束的，按存储顺序），可只列某个类型。"""
    from app.services.agent.state_stack_service import get_frames

    plugin_id = _plugin_id()
    kind = str(type or "")
    return [
        frame for frame in await get_frames(db, agent.id)
        if _in_namespace(plugin_id, frame) and (not kind or frame.get("type") == kind)
    ]


async def _notify_entered(db, agent, frame: dict) -> None:
    """告诉插件"他进来了"（可选钩子 on_frame_enter）：补消息、写账本都在这里发生。"""
    from app.services.plugin import frame_hooks

    await frame_hooks.entered(db, agent, frame)


# ── 内部：命名空间与查找 ──

def _namespace(plugin_id: str) -> str:
    return f"{plugin_id}{NAMESPACE_SEP}"


def _own_ref(plugin_id: str, context_ref: str) -> str:
    """校验并返回 context_ref：插件只能在自己命名空间里建帧。"""
    ref = str(context_ref or "").strip()
    if not ref:
        raise FrameError("context_ref 不能为空")
    if not ref.startswith(_namespace(plugin_id)):
        raise FrameError(
            f"context_ref 必须以 {_namespace(plugin_id)!r} 开头："
            "插件只能在自己的命名空间里建帧（群 / 私信 / 世界帧不归插件管）"
        )
    return ref


def _in_namespace(plugin_id: str, frame: dict | None) -> bool:
    return str((frame or {}).get("context_ref") or "").startswith(_namespace(plugin_id))


def _require_own(plugin_id: str, frame: dict) -> None:
    if not _in_namespace(plugin_id, frame):
        raise FrameError(f"帧 {frame.get('id')} 不属于插件 {plugin_id}")


def _frame_by_id(stack: list[dict], frame_id: str) -> dict | None:
    from app.utils.pure.state_stack import by_id

    return by_id(stack, str(frame_id or ""))


def _own_current(plugin_id: str, stack: list[dict]) -> dict | None:
    from app.utils.pure.state_stack import current

    frame = current(stack)
    return frame if _in_namespace(plugin_id, frame) else None
