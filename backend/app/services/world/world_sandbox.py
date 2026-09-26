"""
世界代码沙箱 —— 世界侧适配层。

执行/隔离/配额/超时都在 app/services/sandbox/runner.py；这里只留世界独有的三件事：
工作目录（data/worlds/{id}）、WORLD_* 环境（含受控 API token）、worlds.config 配额，
外加跑完的语法自检。详见 docs/dev/code_sandbox.md。
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from app.services.sandbox.runner import (
    DEFAULT_CPU_SECONDS,
    DEFAULT_MEMORY_MB,
    DEFAULT_RUNTIME_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    Policy,
    acquire_slot,
    base_env,
)
from app.services.sandbox.runner import run_code as _run_sandbox_code

logger = logging.getLogger(__name__)


def policy_for_world(world, background: bool = False) -> Policy:
    """世界配额（worlds.config 可配）：
    - 无人/后台（background=True）：内存 = sleep_memory_mb（默认 24MB）
    - 有人/前台（background=False）：内存 = runtime_memory_mb（默认 128MB）
    超时/CPU 恒生效（保护宿主不受死循环拖累）。
    """
    cfg = world.config or {}
    try:
        timeout = float(cfg.get("sandbox_timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        cpu = float(cfg.get("cpu_quota") or DEFAULT_CPU_SECONDS)
    except (TypeError, ValueError):
        cpu = DEFAULT_CPU_SECONDS

    if background:
        try:
            memory = int(cfg.get("sleep_memory_mb") or DEFAULT_MEMORY_MB)
        except (TypeError, ValueError):
            memory = DEFAULT_MEMORY_MB
        # 解释器硬下限：python -I + 标准库 import 约需 32MB 虚拟内存，低于则直接启动失败（2026-08-05）
        memory = max(32, min(memory, 512))
    else:
        try:
            memory = int(cfg.get("runtime_memory_mb") or DEFAULT_RUNTIME_MEMORY_MB)
        except (TypeError, ValueError):
            memory = DEFAULT_RUNTIME_MEMORY_MB
        memory = max(16, min(memory, 2048))

    return Policy(
        timeout_seconds=max(1.0, min(timeout, 120.0)),
        memory_mb=memory,
        cpu_seconds=max(1.0, min(cpu, 60.0)),
    )


def _world_dir(world_id: int) -> Path:
    """世界文件夹（与 world_file_service 同源：data/worlds/{id}/）"""
    return (Path("data/worlds") / str(world_id)).resolve()


def _sanitized_env(world, *, readonly: bool = False) -> dict:
    """env 白名单：不继承后端密钥（DATABASE_URL/JWT_SECRET/API Key 等），只给运行必需项。

    2.3 受控数据 API：注入 WORLD_API_TOKEN / WORLD_API_BASE（世界代码经代理访问
    世界数据/对话状态；token 每世界一个、只对本世界数据有效，不是后端密钥）。

    readonly（计划模式强制的只读运行）：
    - token 加 `ro_` 前缀——前缀只声明"本次只读"，token 本体不变；受控 API 的写端点
      （world_data PUT/DELETE、记忆、状态、群聊写）据此回 403。世界代码还有两条写路径
      不经文件系统（受控数据 API / 群消息），只锁文件系统封不住，必须同时封到这里。
    - PYTHONDONTWRITEBYTECODE=1 + 子进程加 -B：不写 __pycache__，否则撞上写保护报的是
      假错，AI 白花轮次排查（python -I 会忽略 PYTHON* 环境变量，故必须同时加 -B）。
    """
    env = {
        **base_env(),                      # PATH/LANG/TZ/HOME + 隔离库目录（沙箱层给）
        "WORLD_ID": str(world.id),
        "WORLD_DIR": str(_world_dir(world.id)),
    }
    if readonly:
        # 子进程入口读它决定 apply_isolate 是否给世界目录写权限（隔离在子进程内施加，
        # 父进程的 readonly 参数到不了那里，只能经 env 传）
        env["WORLD_READONLY"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    cfg = world.config or {}
    token = cfg.get("api_token")
    if isinstance(token, str) and token:
        env["WORLD_API_TOKEN"] = f"ro_{token}" if readonly else token
        env["WORLD_API_BASE"] = os.environ.get(
            "WORLD_API_BASE", f"http://127.0.0.1:8000/world/{world.id}/api"
        )
    return env


# ═══════════════════════════════════════════════════════════════
# 跑完自检：脚本直接写文件会绕过 file_write/file_edit 的落盘校验
# ═══════════════════════════════════════════════════════════════
# 批量替换脚本一次能改几百处、写坏了只能靠事后回读发现，所以跑完后把本次改动的代码
# 文件过一遍语法自检，当场把坏文件报回去（只报不拦，文件已落盘）。
_LINT_SKIP_DIRS = {"__pycache__", "node_modules", "dist", "build", ".git", ".venv", ".mypy_cache"}
_LINT_FILE_LIMIT = 3000     # 快照文件数上限（大世界不为了自检扫穿磁盘）
_LINT_REPORT_LIMIT = 5      # 单次最多报几个坏文件


def _code_snapshot(workdir: Path) -> dict[str, tuple[int, int]]:
    """代码文件的 (mtime_ns, size) 快照，用于跑完后找出被脚本改动的文件"""
    from app.services.world.code_lint import is_lintable
    snap: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in _LINT_SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith(".sandbox"):
                continue
            rel = (Path(root) / name).relative_to(workdir).as_posix()
            if not is_lintable(rel):
                continue
            try:
                st = (Path(root) / name).stat()
            except OSError:
                continue
            snap[rel] = (st.st_mtime_ns, st.st_size)
            if len(snap) >= _LINT_FILE_LIMIT:
                return snap
    return snap


def _lint_changed(workdir: Path, before: dict[str, tuple[int, int]]) -> list[dict]:
    """对本次跑脚本动过的代码文件做语法自检（最多报 _LINT_REPORT_LIMIT 个）"""
    from app.services.world.code_lint import lint_code
    problems: list[dict] = []
    for rel, sig in _code_snapshot(workdir).items():
        if before.get(rel) == sig:
            continue
        try:
            content = (workdir / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        problem = lint_code(rel, content)
        if problem:
            problems.append({"path": rel, **problem})
            if len(problems) >= _LINT_REPORT_LIMIT:
                break
    return problems


async def run_world_code(
    world,
    code: str | None = None,
    entry: str | None = None,
    background: bool = False,
    readonly: bool = False,
) -> dict:
    """
    在沙箱中运行世界 Python 代码（全局并发上限内排队执行）。

    - code：直接执行的脚本（自动写入世界目录临时文件再跑，世界内相对导入可用）
    - entry：世界文件夹内的入口文件（相对路径，如 main.py）
    - background：True=无人/后台执行（内存按 sleep_memory_mb，默认 64MB）；False=有人在线
    - readonly：只读运行（计划模式强制）：世界目录只读 + 受控 API token 带只读前缀 +
      不写 .pyc；由平台决定，调用方不能借它关闭只读
    二者必给其一（entry 优先）。

    返回：{success, stdout, stderr, exit_code, duration_ms, timed_out, reason}
    （duration_ms = 执行耗时；queued_ms = 全局并发排队等待耗时，两者相加 = 请求总耗时）
    """
    _t0 = asyncio.get_event_loop().time()
    workdir = _world_dir(world.id)
    before = {} if readonly else _code_snapshot(workdir)      # 只读运行不会写盘，不必快照
    async with acquire_slot():
        _result = await _run_world_code(world, code=code, entry=entry, background=background, readonly=readonly)
    _result["queued_ms"] = max(0, int((asyncio.get_event_loop().time() - _t0) * 1000) - int(_result.get("duration_ms") or 0))
    if before:
        problems = _lint_changed(workdir, before)
        if problems:
            _result["lint_problems"] = problems
            _result["lint_note"] = ("⚠️ 本次脚本改动的代码文件有语法错误（已落盘，请立刻修）："
                                    + "；".join(f"{p['path']} 第 {p['line']} 行 {p['error']}" for p in problems))
    return _result


async def _run_world_code(
    world,
    code: str | None = None,
    entry: str | None = None,
    background: bool = False,
    readonly: bool = False,
) -> dict:
    """世界代码执行：世界目录 + 世界环境 + 世界配额 → 共用沙箱（sandbox.runner）。

    隔离档位：世界代码要出网（受控 API 是 HTTP）、可能用线程池，故两者都保留；
    内存/CPU/进程数仍由 Policy 与 rlimit 锁死。
    """
    return await _run_sandbox_code(
        _world_dir(world.id),
        code=code,
        entry=entry,
        policy=policy_for_world(world, background=background),
        env=_sanitized_env(world, readonly=readonly),
        readonly=readonly,
        deny_net=False,
        deny_fork=False,
        tag=f"世界 #{world.id}",
    )


# ── 2.2 触发文件约定 ──
# 世界目录 main.py 实现 handle(event) -> dict（可 async），平台 harness 导入调用，
# 世界代码零框架依赖。隔离与 sys.path 由共用沙箱层施加，harness 内不重复。
_TRIGGER_HARNESS_TEMPLATE = r'''
import asyncio, contextlib, importlib, io, json, sys

ENTRY = "__ENTRY__"

def _main():
    try:
        mod = importlib.import_module(ENTRY)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "入口导入失败: %s" % e}, ensure_ascii=False))
        return
    if not hasattr(mod, "handle"):
        print(json.dumps({"ok": False, "error": "入口缺少 handle(event) 函数"}, ensure_ascii=False))
        return
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if not isinstance(event, dict):
            event = {}
    except Exception:
        event = {}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = mod.handle(event)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
        print(json.dumps({"ok": True, "result": result, "stdout": buf.getvalue()}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "stdout": buf.getvalue()}, ensure_ascii=False))

_main()
'''


def _fail(reason: str, *, exit_code: int = -1, duration_ms: int = 0, timed_out: bool = False, stdout: str = "") -> dict:
    return {"success": False, "result": None, "stdout": stdout, "error": reason,
            "exit_code": exit_code, "duration_ms": duration_ms, "timed_out": timed_out, "reason": reason}


async def run_world_trigger(
    world,
    event: dict | None = None,
    entry: str = "main.py",
    background: bool = False,
    readonly: bool = False,
) -> dict:
    """
    2.2 触发文件：执行世界入口的 handle(event)，返回其结果（JSON 序列化）。
    全局并发上限内排队执行（同 run_world_code）。

    - entry：世界文件夹内入口（默认 main.py），需暴露 handle(event) -> dict（可 async）
    - event：触发事件 dict（经 stdin 注入 harness）
    - background：配额语义同 run_world_code（无人/后台 64MB，有人 128MB）
    - readonly：只读运行（计划模式强制），语义同 run_world_code

    返回：{success, result, stdout, error, exit_code, duration_ms, timed_out, reason}
    （duration_ms = 执行耗时；queued_ms = 全局并发排队等待耗时）
    """
    _t0 = asyncio.get_event_loop().time()
    async with acquire_slot():
        _result = await _run_world_trigger(world, event=event, entry=entry, background=background, readonly=readonly)
    _result["queued_ms"] = max(0, int((asyncio.get_event_loop().time() - _t0) * 1000) - int(_result.get("duration_ms") or 0))
    return _result


async def _run_world_trigger(
    world,
    event: dict | None = None,
    entry: str = "main.py",
    background: bool = False,
    readonly: bool = False,
) -> dict:
    """世界触发器执行：harness 导入入口的 handle(event)，stdin 喂事件、stdout 收 JSON。"""
    result = await _run_sandbox_code(
        _world_dir(world.id),
        entry=entry,
        harness=_TRIGGER_HARNESS_TEMPLATE,
        stdin_text=json.dumps(event or {}, ensure_ascii=False),
        policy=policy_for_world(world, background=background),
        env=_sanitized_env(world, readonly=readonly),
        readonly=readonly,
        deny_net=False,
        deny_fork=False,
        tag=f"世界 #{world.id} 触发",
    )
    duration_ms = int(result.get("duration_ms") or 0)
    stdout = (result.get("stdout") or "").strip()
    if not result.get("success"):
        # 沙箱层的失败原因已经是人话（超时/退出码+stderr 摘要/入口越界），原样带出去
        return _fail(result.get("reason") or "执行失败", exit_code=int(result.get("exit_code") or -1),
                     duration_ms=duration_ms, timed_out=bool(result.get("timed_out")), stdout=stdout)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _fail("入口无有效 JSON 结果", exit_code=0, duration_ms=duration_ms, stdout=stdout)
    if not payload.get("ok"):
        err = payload.get("error", "未知错误")
        return {"success": False, "result": None, "stdout": payload.get("stdout", ""), "error": err,
                "exit_code": 0, "duration_ms": duration_ms, "timed_out": False, "reason": err}
    return {"success": True, "result": payload.get("result"), "stdout": payload.get("stdout", ""),
            "error": "", "exit_code": 0, "duration_ms": duration_ms, "timed_out": False, "reason": ""}
