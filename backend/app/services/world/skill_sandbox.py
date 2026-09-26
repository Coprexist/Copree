"""
skill 沙箱宿主侧 — 起隔离子进程执行 skill，ctx 请求协议转发回宿主校验执行

2026-08-07 沙箱加固（v2）：
- 子进程：python -I（不吃 site/环境）+ 独立进程组 + rlimit（内存/CPU/文件大小/进程数）
  + Landlock（文件系统锁死在世界目录）+ seccomp（禁网络/进程/危险调用）
- ctx 能力全部协议转发回宿主：文件/世界/数据/群消息操作在宿主侧做路径校验与权限检查，
  子进程零宿主引用——内省逃逸拿不到任何东西，IO 必须走受控协议
- 超时 killpg 强杀整个进程组
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import resource
import signal
import sys
from pathlib import Path

from app.repositories.world_repo import SQLAlchemyWorldRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyWorldRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyWorldRepository(db_or_repo)
    return db_or_repo


SKILL_RUNNER_PATH = Path(__file__).parent / "skill_runner.py"
AI_SKILLS_DIR = Path("data/world_ai_skills")
WORLDS_DIR = Path("data/worlds")

SKILL_TIMEOUT = 30            # 单次执行墙钟超时（秒）
SKILL_MEMORY_MB = 64          # 内存配额（RLIMIT_AS 虚拟内存口径）
SKILL_CPU_SECONDS = 10.0      # CPU 时间上限
MAX_FSIZE_BYTES = 4 * 1024 * 1024
MAX_NPROC = 16
MAX_REPLY_CHARS = 20000       # 单次 ctx 返回数据截断（防子进程拖垮宿主）


def _skill_env() -> dict:
    """纯净 env：不泄漏 DATABASE_URL/JWT 等后端密钥；只给最小运行所需。

    SANDBOX_LIB_DIR：隔离库目录（沙箱子进程 -I 模式下靠它 import sandbox_isolate）。
    """
    from app.services.sandbox.runner import lib_dir
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "TZ": os.environ.get("TZ", "UTC"),
        "SANDBOX_LIB_DIR": lib_dir(),
    }


def _apply_rlimits() -> None:
    mem = SKILL_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    cpu = int(SKILL_CPU_SECONDS)  # setrlimit 只收 int
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FSIZE_BYTES, MAX_FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_NPROC, MAX_NPROC))
    except (ValueError, OSError):
        pass


def _truncate(obj, limit: int = MAX_REPLY_CHARS):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > limit:
        return {"__truncated__": True, "len": len(s), "head": s[:limit]}
    return obj


# ── ctx 宿主侧执行（权限校验 + 路径隔离）──
async def _handle_call(db, world, permissions: set[str], req: dict) -> dict:
    db = _ensure_repo(db)
    op = req.get("op")
    try:
        if op == "log":
            logger.info("🌐 skill[%s] %s", world.id, str(req.get("text", ""))[:500])
            return {"ok": True}

        if op in ("file.list", "file.read", "file.write", "file.delete"):
            perm = {"file.list": "file:list", "file.read": "file:read",
                    "file.write": "file:write", "file.delete": "file:delete"}[op]
            if perm not in permissions:
                raise PermissionError(f"未声明权限 {perm}（manifest.permissions 里加上才能用）")
            from app.services.world import world_file_service as fs
            if op == "file.list":
                return {"ok": True, "data": fs.list_files(world.id, req.get("prefix", ""))}
            if op == "file.read":
                r = fs.read_file(world.id, req.get("path", ""))
                return {"ok": True, "data": r.get("content", "")}
            if op == "file.write":
                fs.write_file(world.id, req.get("path", ""), req.get("content", ""))
                return {"ok": True}
            fs.delete_file(world.id, req.get("path", ""))
            return {"ok": True}

        if op == "world.get":
            if "world:read" not in permissions:
                raise PermissionError("未声明权限 world:read")
            return {"ok": True, "data": {"id": world.id, "name": world.name,
                                         "description": world.description, "status": world.status}}

        if op == "world.update":
            if "world:update" not in permissions:
                raise PermissionError("未声明权限 world:update")
            from app.services.world.world_service import update_world
            patch = req.get("patch") or {}
            r = await update_world(db, world.id, world.owner_id, **patch)
            return {"ok": True, "data": r}

        if op in ("data.get", "data.set", "data.delete"):
            need = "data:read" if op == "data.get" else "data:write"
            if need not in permissions:
                raise PermissionError(f"未声明权限 {need}")
            from app.services.world.world_service import get_world_data, set_world_data, delete_world_data
            key = str(req.get("key", ""))
            if op == "data.get":
                row = await get_world_data(db, world.id, key)
                return {"ok": True, "data": row["value"] if row else None}
            if op == "data.set":
                r = await set_world_data(db, world.id, key, req.get("value"))
                return {"ok": True, "data": r}
            r = await delete_world_data(db, world.id, key)
            return {"ok": True, "data": r}

        if op == "group.send":
            if "group:send" not in permissions:
                raise PermissionError("未声明权限 group:send")
            from app.chat.gm import send_gm_message, get_group
            from app.models.world import WorldBinding
            from sqlalchemy import select
            rows = (await db.execute(
                select(WorldBinding.group_id).where(WorldBinding.world_id == world.id)
            )).scalars().all()
            if not rows:
                return {"ok": False, "error": "本世界未绑定任何群聊"}
            gid = rows[0]
            group = await get_group(db.session, gid)
            if not group:
                return {"ok": False, "error": "绑定群不存在"}
            msg = await send_gm_message(db.session, gid, world.owner_id, str(req.get("content", "")),
                                       sender_type="user", sender_id=str(world.owner_id))
            return {"ok": True, "data": {"success": True, "message_id": getattr(msg, "id", None)}}

        return {"ok": False, "error": f"未知协议操作: {op}"}
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.warning("🌐 skill ctx 操作失败 %s: %r", op, e)
        return {"ok": False, "error": f"{op} 失败: {e}"}


async def run_skill_in_sandbox(db, world, skill, args: dict) -> dict:
    """在隔离沙箱中执行 skill（返回 execute_skill 兼容的 dict）"""
    world_dir = str(WORLDS_DIR / str(world.id)) if world is not None else None
    skill_dir = str(skill.code_path.parent)
    permissions = list(skill.permissions or [])

    meta = {
        "name": skill.name,
        "world_dir": world_dir,
        "skill_dir": skill_dir,
        "args": args,
        "permissions": permissions,
    }
    # 宿主侧白名单/禁列表（与 world_skill_runtime 同源），runner 内联校验用
    from app.services.world import world_skill_runtime as wsr
    meta["safe_imports"] = sorted(wsr.SAFE_IMPORTS)
    meta["forbidden_imports"] = sorted(wsr.FORBIDDEN_IMPORTS)
    meta["forbidden_builtins"] = sorted(wsr.FORBIDDEN_BUILTINS)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-u", "-X", "utf8", str(SKILL_RUNNER_PATH),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=_skill_env(),
        preexec_fn=_apply_rlimits,
    )
    stderr_chunks: list[bytes] = []

    async def _drain_stderr():
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        except Exception:  # noqa: BLE001
            pass

    stderr_task = asyncio.ensure_future(_drain_stderr())

    try:
        proc.stdin.write((json.dumps(meta, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        t0 = asyncio.get_event_loop().time()
        timed_out = False
        result: dict = {"success": False, "error": "skill 无输出"}
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=SKILL_TIMEOUT)
                if not line:
                    break  # EOF：子进程退出
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "call":
                    reply = await _handle_call(db, world, set(permissions), msg)
                    reply["id"] = msg.get("id")
                    proc.stdin.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                elif msg.get("type") == "result":
                    result = {"success": True, **(msg.get("value") or {})}
                    break
                elif msg.get("type") == "error":
                    result = {"success": False, "error": msg.get("message", "skill 执行出错")}
                    break
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        if timed_out:
            result = {"success": False, "error": f"skill {skill.name} 执行超时（{SKILL_TIMEOUT}s）"}
        if result.get("success") is False and not result.get("error"):
            stderr_tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-2000:]
            result["error"] = f"skill 子进程异常退出" + (f": {stderr_tail}" if stderr_tail.strip() else "")
        result["duration_ms"] = duration_ms
        return result
    finally:
        stderr_task.cancel()
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, ProcessLookupError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:  # noqa: BLE001
                pass
