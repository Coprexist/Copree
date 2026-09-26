"""
通用代码沙箱宿主 —— 「在某个目录里跑一段不受信 Python」的唯一实现。

世界代码与 AI 脚本的差别只有三处：工作目录、环境变量、隔离档位；做成参数放在这里，
配额与 kill 逻辑就不会演化成两套。隔离档位经环境变量传给子进程（父进程到不了那里）。
详见 docs/dev/code_sandbox.md。
"""
from __future__ import annotations

import asyncio
import logging
import os
import resource
import signal
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_MB = 64          # 无人/后台内存配额（sleep_memory_mb 可覆盖）
# ⚠️ 2026-08-05 实测：24MB 下 python -I 连标准库 import 都跑不动（RLIMIT_AS 虚拟内存口径，解释器约需 ≥32MB）。
# 产品拍板：多群多解释器（默认形态）→ 上限 64MB；单解释器共享场景 → 32MB（policy 硬下限）。
DEFAULT_RUNTIME_MEMORY_MB = 128  # 有人在线内存配额（runtime_memory_mb 可覆盖）——产品 2026-08-05 定
DEFAULT_TIMEOUT_SECONDS = 10.0  # 默认墙钟超时
DEFAULT_CPU_SECONDS = 5.0       # 默认 CPU 时间上限
MAX_FSIZE_BYTES = 4 * 1024 * 1024   # 单文件写入上限 4MB（防写爆磁盘）
MAX_NPROC = 16                  # 子进程数上限（防 fork 炸弹）
MAX_OUTPUT_CHARS = 20000        # stdout/stderr 各截断长度

# 全局沙箱并发上限（产品 2026-08-05 拍板方案 1：各用各的解释器 + 排队）
# - 排队的是「一次代码执行任务」（短任务，有超时兜底），不是人/群
# - 在线不占位：只有执行中的那几秒占一个槽位，跑完释放
# - 管理员可配：环境变量 SANDBOX_MAX_CONCURRENT（默认 4，范围 1-32）
# - 世界与 AI 共用一个池：都是「跑几秒的脚本」，分开只是把上限拆小、让两边都更容易排队
DEFAULT_MAX_CONCURRENT = 4
_sandbox_sem: asyncio.Semaphore | None = None


def _max_concurrent() -> int:
    try:
        return max(1, min(int(os.environ.get("SANDBOX_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT)), 32))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT


def acquire_slot() -> asyncio.Semaphore:
    """全局沙箱信号量（单 worker 进程内生效）：并发执行上限，超出排队等待"""
    global _sandbox_sem
    if _sandbox_sem is None:
        _sandbox_sem = asyncio.Semaphore(_max_concurrent())
    return _sandbox_sem


@dataclass
class Policy:
    """沙箱配额（集中配置，参考 sandtrap Policy 模式）

    memory_mb=None 表示不设内存上限（保留能力，当前默认有人在线 128MB）
    """
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int | None = DEFAULT_RUNTIME_MEMORY_MB
    cpu_seconds: float = DEFAULT_CPU_SECONDS


def apply_rlimits(policy: Policy) -> None:
    """子进程内设置资源限制（preexec_fn 中执行，必须在 exec 之前）"""
    if policy.memory_mb is not None:
        mem_bytes = policy.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    cpu = int(policy.cpu_seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FSIZE_BYTES, MAX_FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_NPROC, MAX_NPROC))
    except (ValueError, OSError):
        pass  # 部分系统不可用（如非 root 调整 hard limit），尽力而为


def lib_dir() -> str:
    """隔离库所在目录——沙箱子进程 -I 模式下 sys.path 不含脚本目录，只能靠它 import"""
    return str(Path(__file__).parent)


def base_env() -> dict:
    """env 白名单基座：不继承后端密钥（DATABASE_URL/JWT_SECRET/API Key 等）。

    调用方在此之上补自己的变量（世界：WORLD_*；AI：AGENT_*）；协议式子进程（skill runner）
    也拿 SANDBOX_LIB_DIR 去 import 隔离库。
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TZ": os.environ.get("TZ", "Asia/Shanghai"),
        "HOME": "/tmp",
        "SANDBOX_LIB_DIR": lib_dir(),
    }


def truncate(s: str) -> str:
    if len(s) > MAX_OUTPUT_CHARS:
        return s[:MAX_OUTPUT_CHARS] + f"\n…[输出已截断，前 {MAX_OUTPUT_CHARS} 字符]"
    return s


# 被信号杀掉时 stderr 常为空，只剩负的退出码；翻译成人话，否则写脚本的 AI 只能靠猜
_SIGNAL_HINTS = {
    signal.SIGXCPU: "CPU 时间超限",
    signal.SIGKILL: "被强制终止（超时或内存超限）",
    signal.SIGSEGV: "段错误（多见于内存超限）",
    signal.SIGXFSZ: "写入超过单文件上限",
    signal.SIGBUS: "内存访问异常（多见于内存超限）",
}


def exit_reason(returncode: int, policy: Policy) -> str:
    """退出码 → 可读原因（正常退出/未知信号也有兜底文案）"""
    if returncode >= 0:
        return f"进程退出码 {returncode}"
    sig = -returncode
    hint = _SIGNAL_HINTS.get(sig)
    name = signal.Signals(sig).name if sig in {s.value for s in signal.Signals} else f"信号 {sig}"
    detail = f"：{hint}" if hint else ""
    quota = f"（本次配额 CPU {policy.cpu_seconds:g}s / 内存 {policy.memory_mb}MB）" if hint else ""
    return f"进程被 {name} 终止{detail}{quota}"


# ── 子进程入口：先隔离，再跑目标 ──
# 目标既可以是调用方给的脚本/入口，也可以是平台生成的 harness（协议式入口）。
# 隔离只在这里施加一次，harness 内不再重复（两处施加=两处要维护一致）。
_RUNNER_TEMPLATE = r'''
import os, runpy, sys

def _main():
    target = sys.argv[1]
    work_dir = os.environ.get("SANDBOX_DIR", "")
    sys.path.insert(0, os.environ.get("SANDBOX_LIB_DIR", ""))
    from sandbox_isolate import apply_isolate
    apply_isolate(
        work_dir=work_dir,
        readonly=os.environ.get("SANDBOX_READONLY") == "1",
        deny_net=os.environ.get("SANDBOX_DENY_NET") == "1",
        deny_process_creation=os.environ.get("SANDBOX_DENY_FORK") == "1",
    )
    sys.path.insert(0, work_dir)
    runpy.run_path(target, run_name="__main__")

_main()
'''


def _fail(reason: str, *, exit_code: int = -1, duration_ms: int = 0,
          timed_out: bool = False, stdout: str = "") -> dict:
    return {"success": False, "stdout": stdout, "stderr": "", "exit_code": exit_code,
            "duration_ms": duration_ms, "timed_out": timed_out, "reason": reason}


async def run_code(
    workdir: Path | str,
    *,
    code: str | None = None,
    entry: str | None = None,
    policy: Policy,
    env: dict | None = None,
    readonly: bool = False,
    deny_net: bool = False,
    deny_fork: bool = False,
    stdin_text: str | None = None,
    harness: str | None = None,
    tag: str = "沙箱",
) -> dict:
    """在 workdir 内执行代码（调用方负责在 acquire_slot() 内排队）。

    code=脚本正文 / entry=workdir 内已存在的入口文件 / harness=平台生成的入口模板
    （含 __ENTRY__，用于"导入入口模块 + stdin 收事件 + stdout 出 JSON"这类协议）。
    返回 {success, stdout, stderr, exit_code, duration_ms, timed_out, reason}。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    tmp_file: Path | None = None
    target: Path | None = None
    try:
        if harness:
            if not entry:
                return _fail("harness 模式必须给 entry")
            target = (workdir / entry).resolve()
            if not str(target).startswith(str(workdir)):
                return _fail(f"入口文件越界: {entry}")
            if not target.exists():
                return _fail(f"入口文件不存在: {entry}")
            tmp_file = workdir / f".sandbox_{uuid.uuid4().hex[:8]}.py"
            tmp_file.write_text(harness.replace("__ENTRY__", target.stem), encoding="utf-8")
            target = tmp_file
        elif entry:
            target = (workdir / entry).resolve()
            # 防越界：入口必须在工作目录内
            if not str(target).startswith(str(workdir)):
                return _fail(f"入口文件越界: {entry}")
            if not target.exists():
                return _fail(f"入口文件不存在: {entry}")
        elif code:
            tmp_file = workdir / f".sandbox_{uuid.uuid4().hex[:8]}.py"
            tmp_file.write_text(code, encoding="utf-8")
            target = tmp_file
        else:
            return _fail("code 和 entry 至少给一个")

        cmd = [sys.executable, "-I", "-X", "utf8", "-c", _RUNNER_TEMPLATE, str(target)]
        if readonly:
            cmd.insert(2, "-B")   # 只读运行不写 .pyc（-I 忽略 PYTHON* 环境变量，只能用命令行开关）
        child_env = {
            **base_env(),
            "SANDBOX_DIR": str(workdir),
            "SANDBOX_READONLY": "1" if readonly else "0",
            "SANDBOX_DENY_NET": "1" if deny_net else "0",
            "SANDBOX_DENY_FORK": "1" if deny_fork else "0",
            **(env or {}),
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir),
            env=child_env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,          # 独立进程组 → 超时可 killpg 连子进程一起杀
            preexec_fn=lambda: apply_rlimits(policy),
        )

        stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
        t0 = asyncio.get_event_loop().time()
        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_bytes), timeout=policy.timeout_seconds
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # 强杀整个进程组
            except ProcessLookupError:
                pass
            stdout_b, stderr_b = await proc.communicate()
        duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)

        stdout = truncate(stdout_b.decode("utf-8", errors="replace"))
        stderr = truncate(stderr_b.decode("utf-8", errors="replace"))
        return {
            "success": proc.returncode == 0 and not timed_out,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "reason": "执行超时，已强制终止进程组" if timed_out else
                      ("" if proc.returncode == 0 else (stderr.strip()[:200] or exit_reason(proc.returncode, policy))),
        }
    except Exception as e:  # noqa: BLE001 —— 沙箱自身故障不拖垮调用方
        logger.warning(f"🛡️ {tag}执行异常: {e}")
        return _fail(f"沙箱异常: {str(e)[:200]}")
    finally:
        if tmp_file is not None:
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
