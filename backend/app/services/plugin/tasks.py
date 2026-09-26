"""插件后台任务的托管：留下强引用、停止前等它们跑完

asyncio.create_task 只把任务交给事件循环调度，不保留强引用 —— 循环持的是弱引用，
任务可能执行到一半就被 GC 回收，且不留任何日志（表现是"偶尔有条回复没发出去"）。
另外 stop() 紧接着会 aclose() 掉在飞请求要用的 HTTP 客户端，所以停之前必须先把它们等完。

两件事共用同一份状态：谁在飞，既决定"要不要保引用"，也决定"关闭前要等谁"。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


class PluginTasks:
    """一个插件实例的后台任务：spawn 登记（带一句"这是在做什么"），drain 等完"""

    def __init__(self) -> None:
        # task → 它为什么被起：异常时日志要说得清是哪一条回复没发出去
        self._tasks: dict[asyncio.Task, str] = {}

    def spawn(self, coro: Coroutine[Any, Any, Any], label: str = "") -> None:
        task = asyncio.create_task(coro)
        self._tasks[task] = label
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        label = self._tasks.pop(task, "")
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # 发送层自己没兜住的异常不能静默：否则只剩 GC 时才打一行，还看不出是谁
            logger.warning(f"后台任务异常退出（{label or '未标注'}）：{type(error).__name__}: {error}")

    async def drain(self) -> None:
        """等在飞的任务跑完再返回：调用方紧接着要关掉它们依赖的客户端"""
        pending = list(self._tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def __len__(self) -> int:
        return len(self._tasks)
