"""
AI 代码沙箱 —— 在 AI 自己的文件空间里执行代码。

AI 的文件空间（data/agents/{id}/）本来就有；这层把它锁成代码能碰到的全部世界。
决策技能 do.run_script 也走这里：脚本经 DECISION_CTX 拿事件上下文，要说的话 print 成
JSON 由宿主代发。详见 docs/dev/code_sandbox.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.paths import agent_dir
from app.services.sandbox.runner import Policy, fail_result
from app.services.sandbox.runner import run_code as _run_sandbox_code

logger = logging.getLogger(__name__)

AGENT_TIMEOUT_SECONDS = 10.0     # 单段脚本墙钟上限：决策技能要秒级返回，不能拖住消息链路
AGENT_MEMORY_MB = 96             # 解释器约需 ≥32MB，留出数据处理余量
AGENT_CPU_SECONDS = 5.0
MAX_CTX_CHARS = 8000             # 事件上下文注入上限（防超长消息把 env 撑爆）
LOCK_WAIT_SECONDS = 20.0         # 等同一个 AI 上一个脚本让出文件空间的墙钟上限（两倍单脚本上限）

# 每个 AI 一把脚本锁。文件空间只有一份，脚本又惯用「读 JSON → 改 → 写回」，
# 而同一个 AI 的脚本能从好几条链路同时起：本体的 run_script、各群的决策技能、闹钟与好友申请情景。
# 接话判定只按 (AI × 群) 串行，跨群与跨情景不串——两份脚本各自读到旧账本再各写一遍，账就丢了。
# 锁放在这里而不是决策层：共用这份文件空间的调用方不止决策。
_agent_locks: dict[int, asyncio.Lock] = {}


def agent_lock(agent_id: int) -> asyncio.Lock:
    """这个 AI 的脚本锁（单事件循环内 setdefault 是原子的，不需要再加同步）"""
    return _agent_locks.setdefault(agent_id, asyncio.Lock())


def script_path(agent_id: int, rel_path: str) -> Path:
    """把沙箱内的相对路径解析成绝对路径（防越界；目录不存在则建）"""
    root = agent_dir(agent_id)
    target = (root / str(rel_path or "").strip()).resolve()
    if not str(target).startswith(str(root) + "/") and target != root:
        raise ValueError(f"路径越界：只能写你自己的沙箱目录（{root}）")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def agent_policy() -> Policy:
    return Policy(
        timeout_seconds=AGENT_TIMEOUT_SECONDS,
        memory_mb=AGENT_MEMORY_MB,
        cpu_seconds=AGENT_CPU_SECONDS,
    )


async def run_agent_code(
    agent_id: int,
    *,
    code: str | None = None,
    entry: str | None = None,
    ctx: dict | None = None,
    dry_run: bool = False,
    deny_net: bool = True,
    deny_fork: bool = True,
) -> dict:
    """在 AI 自己的文件空间里执行代码（返回沙箱统一结果字典）。

    - code：脚本正文；entry：文件空间内已存在的入口文件（如 scripts/daily.py）
    - ctx：事件上下文，经 DECISION_CTX 传给脚本（决策技能命中时用）
    - deny_net / deny_fork：默认全禁。AI 要联网有 web_search 等工具，不该从脚本里出网；
      要并发有平台的消息链路，脚本本身不需要线程
    同一个 AI 的脚本在这里串行（见 _agent_locks）：脚本读写的是同一份文件空间，
    并行跑就是拿旧账本互相覆盖。等不到就如实失败，不让调用方以为脚本跑过了。
    """
    env = {"AGENT_ID": str(agent_id), "AGENT_DIR": str(agent_dir(agent_id))}
    if ctx is not None:
        env["DECISION_CTX"] = json.dumps(ctx, ensure_ascii=False, default=str)[:MAX_CTX_CHARS]
    if dry_run:
        # 试跑（决策技能 test_decision_skill）：脚本可据此跳过落库/发消息这类副作用
        env["DRY_RUN"] = "1"
    lock = agent_lock(agent_id)
    try:
        await asyncio.wait_for(lock.acquire(), LOCK_WAIT_SECONDS)
    except asyncio.TimeoutError:
        # 等不到就如实失败：这次不跑只是少做一件事，两份脚本各写一遍是把账弄坏
        logger.warning(f"🐍 AI #{agent_id} 的脚本排队超时（{LOCK_WAIT_SECONDS:g}s 没轮到）")
        return fail_result(f"你上一个脚本跑了超过 {LOCK_WAIT_SECONDS:g} 秒还没结束，这次没轮上；稍后再试")
    try:
        return await _run_sandbox_code(
            agent_dir(agent_id),
            code=code,
            entry=entry,
            policy=agent_policy(),
            env=env,
            deny_net=deny_net,
            deny_fork=deny_fork,
            tag=f"AI #{agent_id}",
        )
    finally:
        lock.release()
