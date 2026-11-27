"""测试替身工厂：把「异步接口 / 同步结果」的分工配准。

背景：`AsyncSession.execute()` 是协程，但它返回的 `Result` 是**同步对象**
（`.scalar_one_or_none()`、`.scalars()` 都不是协程）。把整个 db 配成裸 `AsyncMock`，
`execute()` 的返回值也会是 `AsyncMock`，于是生产代码里 `result.scalar_one_or_none()`
这个同步调用会造出一个没人 await 的协程——测试照样通过，但 GC 时会报
`coroutine 'AsyncMockMixin._execute_mock_call' was never awaited`：
既污染 CI 输出，也会把真正漏掉的 await 一起淹没。

所以替身要按真实形状配：db 的异步方法用 AsyncMock，它们返回的结果对象用 MagicMock。
"""

from unittest.mock import AsyncMock, MagicMock


def _async_ctx() -> MagicMock:
    """async context manager 替身（``__aenter__`` / ``__aexit__`` 才是协程）。"""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=None)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def async_db(*, active: bool = True, execute_result=None):
    """一个形状正确的 AsyncSession 替身。

    - ``active``：事务是否仍然可用（False 让 ``_recover_session`` 走回滚分支）
    - ``execute_result``：``scalar_one_or_none()`` 的返回值，默认 None（查不到）

    ``begin_nested()`` / ``begin()`` 在真实 AsyncSession 上是**同步方法**，
    返回的才是 async context manager；配成 AsyncMock 会造出没人 await 的协程。
    """
    db = AsyncMock()
    db.is_active = active
    # 状态闸读不到 agent 时不拦（各测试按需覆盖）
    db.get = AsyncMock(return_value=None)
    result = MagicMock()
    result.scalar_one_or_none.return_value = execute_result
    db.execute.return_value = result
    db.begin_nested = MagicMock(return_value=_async_ctx())
    db.begin = MagicMock(return_value=_async_ctx())
    return db
