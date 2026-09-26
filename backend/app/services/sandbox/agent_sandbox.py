"""
AI 代码沙箱 —— 在 AI 自己的文件空间里执行代码。

AI 的文件空间（data/agents/{id}/）本来就有；这层把它锁成代码能碰到的全部世界。
决策技能 do.run_script 也走这里：脚本经 DECISION_CTX 拿事件上下文，要说的话 print 成
JSON 由宿主代发。详见 docs/dev/code_sandbox.md。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings
from app.services.sandbox.runner import Policy
from app.services.sandbox.runner import run_code as _run_sandbox_code

logger = logging.getLogger(__name__)

AGENT_TIMEOUT_SECONDS = 10.0     # 单段脚本墙钟上限：决策技能要秒级返回，不能拖住消息链路
AGENT_MEMORY_MB = 96             # 解释器约需 ≥32MB，留出数据处理余量
AGENT_CPU_SECONDS = 5.0
MAX_CTX_CHARS = 8000             # 事件上下文注入上限（防超长消息把 env 撑爆）


def agent_dir(agent_id: int) -> Path:
    """AI 文件空间（沙箱目录）——与 file_* 工具、OpenCLI 同源的唯一解析入口"""
    return (Path(settings.agents_dir) / str(agent_id)).resolve()


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
    deny_net: bool = True,
    deny_fork: bool = True,
) -> dict:
    """在 AI 自己的文件空间里执行代码（返回沙箱统一结果字典）。

    - code：脚本正文；entry：文件空间内已存在的入口文件（如 scripts/daily.py）
    - ctx：事件上下文，经 DECISION_CTX 传给脚本（决策技能命中时用）
    - deny_net / deny_fork：默认全禁。AI 要联网有 web_search 等工具，不该从脚本里出网；
      要并发有平台的消息链路，脚本本身不需要线程
    """
    env = {"AGENT_ID": str(agent_id), "AGENT_DIR": str(agent_dir(agent_id))}
    if ctx is not None:
        env["DECISION_CTX"] = json.dumps(ctx, ensure_ascii=False, default=str)[:MAX_CTX_CHARS]
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
