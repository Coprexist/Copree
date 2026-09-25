"""无 pytest 环境下的最小运行器。

后端容器不含 pytest。本脚本只实现现有用例实际用到的两件事 —— pytest.fixture 与
pytest.mark —— 外加 conftest 中的 migrated_db。需要参数化、插件或覆盖率统计时请安装
pytest，不要扩展本脚本。

用法（在后端容器内，指向测试库）：

    docker exec -w /app \
      -e TEST_DATABASE_URL=<测试库> -e TEST_DATABASE_URL_SYNC=<测试库(sync)> \
      ai_group_backend python tests/run_without_pytest.py [选择器]

选择器按子串匹配 文件名::用例名，省略则运行全部。

启动闸：目标库名必须以 _test 结尾。运行过程包含 drop_all 与 TRUNCATE，而生产库与
测试库位于同一 PostgreSQL 实例，仅库名不同。
"""
from __future__ import annotations

import asyncio
import importlib.util
import time
import inspect
import os
import sys
import traceback
import types
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


class _Marker:
    """pytest.mark.<name>：既作值使用（pytestmark = pytest.mark.anyio），也作装饰器。"""

    def __init__(self, name: str):
        self.name = name

    def __call__(self, *args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn


class _MarkRegistry:
    def __getattr__(self, name: str) -> _Marker:
        return _Marker(name)


def _install_pytest_stub() -> None:
    """必须在导入 conftest 之前调用（conftest 顶部即 import pytest）。"""
    pytest = types.ModuleType("pytest")

    def fixture(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn

    pytest.mark = _MarkRegistry()
    pytest.fixture = fixture
    sys.modules["pytest"] = pytest


def _guard_test_db() -> None:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        sys.exit("缺少 TEST_DATABASE_URL：本运行器只允许指向测试库（会 drop_all + TRUNCATE）")
    dbname = url.rsplit("/", 1)[-1].split("?")[0]
    if not dbname.endswith("_test"):
        sys.exit(f"拒绝启动：库名必须以 _test 结尾（会 drop_all + TRUNCATE），当前 = {dbname}")


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _FixtureResolver:
    """按名解析 conftest 中的 fixture，仅支持现有用例用到的形态。"""

    def __init__(self, conftest):
        self._conftest = conftest
        self._cache: dict[str, object] = {}
        self._teardowns: list = []

    async def resolve(self, name: str):
        if name in self._cache:
            return self._cache[name]
        fn = getattr(self._conftest, name, None)
        if fn is None:
            raise LookupError(f"未定义的 fixture: {name}")
        if inspect.isasyncgenfunction(fn):
            gen = fn()
            value = await gen.__anext__()
            self._teardowns.append(gen)
        elif inspect.iscoroutinefunction(fn):
            value = await fn()
        else:
            value = fn()
        self._cache[name] = value
        return value

    async def teardown(self) -> None:
        for gen in reversed(self._teardowns):
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass
            except Exception:
                traceback.print_exc()


async def _run(selector: str) -> int:
    conftest = _load("conftest", TESTS_DIR / "conftest.py")
    resolver = _FixtureResolver(conftest)

    passed, failures, matched = 0, [], 0
    timings: list[tuple[float, str]] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        # 选择器不含 :: 时按文件名过滤，避免白导入无关模块
        if selector and "::" not in selector and selector not in path.stem:
            continue
        module = _load(path.stem, path)
        for name in sorted(n for n in dir(module) if n.startswith("test_")):
            fn = getattr(module, name)
            if not callable(fn):
                continue
            node = f"{path.stem}::{name}"
            if selector and selector not in node:
                continue
            matched += 1
            t0 = time.monotonic()
            try:
                kwargs = {
                    p: await resolver.resolve(p)
                    for p in inspect.signature(fn).parameters
                }
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    await result
                passed += 1
                cost = time.monotonic() - t0
                timings.append((cost, node))
                # 每个用例耗时都打出来：无 pytest 时排查"整套为什么慢"只能靠这个
                print(f"  PASS  {node}  ({cost:.2f}s)")
            except Exception:
                failures.append(node)
                print(f"  FAIL  {node}")
                traceback.print_exc()
    await resolver.teardown()

    if matched == 0:
        print(f"没有匹配的用例：选择器 = {selector!r}")
        return 1

    print()
    slowest = sorted(timings, reverse=True)[:8]
    total = sum(t for t, _ in timings)
    print(f"用例耗时合计 {total:.1f}s；最慢 8 个：")
    for cost, node in slowest:
        print(f"  {cost:6.2f}s  {node}")
    print(f"RESULT passed={passed} failed={len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    selector = sys.argv[1] if len(sys.argv) > 1 else ""
    _guard_test_db()
    _install_pytest_stub()
    sys.exit(asyncio.run(_run(selector)))
