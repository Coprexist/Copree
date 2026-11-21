"""前缀（message 0）：一个出口、一份清单、一道守卫。

为什么要有这一层：进前缀的内容一旦变动，整段缓存从第 0 字节失效。2026-11-14 实测，
一天里 send_voice / file_fetch / 大肥鱼表情包三次改动，活跃会话失效 7 次、多付约 283k 未命中。
根因不是"没锁"，而是锁的是**输入**（版本号、工具名单）：前缀里的目录/索引/清单都是渲染出来的
文字，渲染代码里任何一处现算都会改字节，版本号没变也照样断。

所以这里锁**输出**，并把契约写成三样东西：

1. `locked()`：锁定态前缀的唯一读写口。命中快照就返回字节，连 render 都不调——
   "不现算"是结构事实，不靠调用方自觉。
2. `SECTIONS`：什么能进前缀、每一段属于哪一档（有版本源 / 仅锁定）。
3. `notice_sources()`：变更通知的源清单只有这一份。通知与"谁有版本"同源，
   新增一段有源的内容只改这里，就自动有了版本与通知。

不靠自觉的部分由 `guard()` 兜住：任何绕过 `locked()` 往 message 0 里塞东西的路径，
发出去的字节与快照不一致时当场报错（而不是悄悄让所有人的缓存失效）。
测试 `backend/tests/test_prefix_contract.py` 另钉住：可配置段与 `SECTIONS` 必须一一对应。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from app.services.capability_versioning import (
    SOURCE_PLATFORM, emoji_packs_source, freeze_prefix, frozen_prefix,
    memory_index_source, prefix_key,
)

logger = logging.getLogger(__name__)

#: 前缀段清单：键与 `ContextConfig.segment_order` 同名（那段配置决定"渲染哪些段"）。
#: 值 = 这一段的档位：
#:   "source"  有版本源：每轮探测、变更走【能力变更通知】账本条目
#:   "locked"  无版本源：本身不常变（管理员覆盖之类），靠快照保证字节不动
#: 新增可配置段时这里必须同步——漏了 test_prefix_contract 会当场红。
SECTIONS: dict[str, str] = {
    "core_identity": "locked",
    "protocol": "locked",
    "personality": "source",
    "tools": "source",
    "injected_skills": "source",
}


class PrefixDrift(RuntimeError):
    """锁定态的前缀字节与快照不一致——说明有路径绕过了 `locked()`。"""


def locked(holder, context_ref: str, variant: str, rendered: str) -> str:
    """锁定态前缀：首见把这一份钉住，之后一律用那份字节。

    收的是**已经拼好的文本**，不是回调——拼装照旧在调用点跑（版本登记就发生在那些构建
    函数里，探测与渲染只有一处真理）。代价是每轮白算几次查询，换来的是不必维护第二套
    "只探测不渲染"的路径。
    """
    frozen = frozen_prefix(holder, context_ref, variant)
    return frozen if frozen is not None else freeze_prefix(
        holder, context_ref, rendered, variant)


def guard(holder, context_ref: str, variant: str, text: str) -> None:
    """发出去之前核对：锁定态下，实际内容必须等于快照（不一致 = 有人绕过了 locked）。"""
    frozen = frozen_prefix(holder, context_ref, variant)
    if frozen is not None and frozen != text:
        raise PrefixDrift(
            f"锁定态前缀被改写：会话 {context_ref}（{prefix_key(context_ref, variant)}），"
            f"快照 {len(frozen)} 字 → 实际 {len(text)} 字。"
            "进前缀的内容必须走 ai.prefix.locked()，变更走【能力变更通知】，解锁才换字节。"
        )


def notice_sources(agent_id: int, prompt_source: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """变更通知要比对的能力源清单——**只此一份**（探测、解锁、通知都从这里取）。"""
    return [SOURCE_PLATFORM, prompt_source, memory_index_source(agent_id),
            emoji_packs_source(), *extra]
