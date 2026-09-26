"""
世界常驻进程管理（阶段 2.5）— 世界代码常驻待机，支持动态推演

设计：
- 世界 config `resident: true` 开启常驻；`tick_interval`（秒，默认 30）定时推演
- 常驻进程 = harness + 世界 main.py：
    handle(event)   群消息/触发事件（进程内队列，即时响应）
    on_tick()       定时推演（时间流动/NPC 自主/剧情演化），可选
    on_stop()       优雅退出前保存状态，可选
- 生命周期：手动唤醒 → 启动常驻；手动休眠 → 优雅停止；后端重启 → 恢复常驻世界
- 通信：stdin/stdout 行协议（JSON 行进，结果/日志行出），零依赖
- 配额：64MB（sleep_memory_mb）；handle/tick 单次异常不杀进程（日志记录），
  死循环由 RLIMIT_CPU 兜底；常驻个数默认不限（residents_max 预留可配）
"""
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL = 30.0   # 秒

_RESIDENT_HARNESS = r'''
import asyncio, importlib, json, os, signal, sys

# 防孤儿：父进程（backend）退出时自动收 SIGTERM 自杀（PR_SET_PDEATHSIG）
# backend --reload 重启/停止时，常驻进程不再残留
import ctypes
try:
    ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)  # PR_SET_PDEATHSIG = 1
except Exception:
    pass

sys.path.insert(0, os.environ.get("WORLD_DIR", os.path.dirname(os.path.abspath(__file__))))
ENTRY = "__ENTRY__"
TICK = float(os.environ.get("WORLD_TICK_INTERVAL", "30"))

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)

async def _call(fn):
    r = fn()
    if asyncio.iscoroutine(r):
        r = await r
    return r

async def main():
    try:
        mod = importlib.import_module(ENTRY)
    except Exception as e:
        _emit({"type": "fatal", "error": "入口导入失败: %s: %s" % (type(e).__name__, e)})
        return

    queue = asyncio.Queue()

    def on_stdin():
        line = sys.stdin.readline()
        if line:
            queue.put_nowait(line)
        # EOF（stdin 关闭）→ 置 None 哨兵退出
        else:
            queue.put_nowait(None)

    loop = asyncio.get_event_loop()
    loop.add_reader(sys.stdin.fileno(), on_stdin)

    # 定时推演（可选 on_tick）
    async def ticker():
        if not hasattr(mod, "on_tick"):
            return
        while True:
            await asyncio.sleep(TICK)
            try:
                await _call(mod.on_tick)
            except Exception as e:
                _emit({"type": "tick_error", "error": "%s: %s" % (type(e).__name__, e)})

    if hasattr(mod, "on_tick"):
        asyncio.create_task(ticker())
    _emit({"type": "ready"})

    # 事件循环：行协议（{"type":"event",...} / {"type":"stop"}）
    while True:
        line = await queue.get()
        if line is None:
            break  # EOF
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("type") == "stop":
            if hasattr(mod, "on_stop"):
                try:
                    await _call(mod.on_stop)
                except Exception as e:
                    _emit({"type": "stop_error", "error": "%s: %s" % (type(e).__name__, e)})
            break
        if msg.get("type") == "event":
            event = msg.get("event", {})
            if not hasattr(mod, "handle"):
                _emit({"type": "result", "ok": False, "error": "入口缺少 handle(event)"})
                continue
            try:
                result = await _call(lambda: mod.handle(event))
                _emit({"type": "result", "ok": True, "result": result})
            except Exception as e:
                _emit({"type": "result", "ok": False, "error": "%s: %s" % (type(e).__name__, e)})

asyncio.run(main())
'''


def _world_dir(world_id: int) -> Path:
    return (Path("data/worlds") / str(world_id)).resolve()


def tick_interval_for(world) -> float:
    """worlds.config.tick_interval（非法值回退默认）"""
    try:
        v = float((world.config or {}).get("tick_interval", DEFAULT_TICK_INTERVAL))
    except (TypeError, ValueError):
        v = DEFAULT_TICK_INTERVAL
    return max(1.0, min(v, 3600.0))


class ResidentManager:
    """常驻进程管理器（单例）：启动/停止/投递/恢复

    - 每常驻世界一个子进程（隔离优先；不共享解释器）
    - 事件经 stdin 行协议投递，进程内 asyncio 队列排队处理
    - 常驻个数默认不限（预留 residents_max 可配能力）
    """

    def __init__(self) -> None:
        self._procs: dict[int, asyncio.subprocess.Process] = {}
        self._writers: dict[int, asyncio.StreamWriter] = {}

    # ── 状态查询 ──

    def is_running(self, world_id: int) -> bool:
        p = self._procs.get(world_id)
        return p is not None and p.returncode is None

    @staticmethod
    def is_resident(world) -> bool:
        return bool((world.config or {}).get("resident"))

    # ── 生命周期 ──

    async def start(self, db, world) -> bool:
        """启动常驻进程（已在跑则跳过）。返回是否新启动。"""
        if self.is_running(world.id):
            return False
        # 确保常驻进程 env 注入 WORLD_API_TOKEN / WORLD_API_BASE（懒生成）
        try:
            from app.routers.world_proxy import ensure_world_api_token
            await ensure_world_api_token(db, world)
            await db.commit()
        except Exception as e:
            logger.warning(f"🌐 世界 #{world.id} 常驻启动：token 准备失败 {e}")
        workdir = _world_dir(world.id)
        target = (workdir / "main.py").resolve()
        if not str(target).startswith(str(workdir)) or not target.exists():
            logger.info(f"🌐 世界 #{world.id} 常驻启动跳过：main.py 不存在")
            return False

        harness = Path("/tmp") / f"resident_{world.id}.py"
        harness.write_text(_RESIDENT_HARNESS.replace("__ENTRY__", target.stem), encoding="utf-8")
        try:
            env = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "TZ": os.environ.get("TZ", "Asia/Shanghai"),
                "HOME": "/tmp",
                "WORLD_ID": str(world.id),
                "WORLD_DIR": str(workdir),      # 世界目录（harness 在 /tmp，靠它定位 main.py）
                "WORLD_TICK_INTERVAL": str(tick_interval_for(world)),
            }
            token = (world.config or {}).get("api_token")
            if isinstance(token, str) and token:
                env["WORLD_API_TOKEN"] = token
                env["WORLD_API_BASE"] = os.environ.get("WORLD_API_BASE", f"http://127.0.0.1:8000/world/{world.id}/api")

            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-X", "utf8", str(harness),
                cwd=str(workdir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._procs[world.id] = proc
            self._writers[world.id] = proc.stdin
            # stdout 日志转发（ready/result/tick_error → 后端日志）
            asyncio.create_task(self._pump_stdout(world.id, proc))
            logger.info(f"🌐 世界 #{world.id} 常驻进程已启动（tick={tick_interval_for(world)}s）")
            return True
        except Exception as e:
            logger.warning(f"🌐 世界 #{world.id} 常驻启动失败: {e}")
            return False
        # 注意：/tmp/resident_{id}.py 保留不删（子进程 exec 与 unlink 存在竞争，
        # 删除会导致 exec 时文件不存在 exit 2；下次启动直接覆盖写入）

    async def stop(self, world_id: int, timeout: float = 5.0) -> None:
        """优雅停止：发 stop → 等退出；超时强杀进程组"""
        proc = self._procs.get(world_id)
        writer = self._writers.get(world_id)
        if proc is None or proc.returncode is not None:
            self._procs.pop(world_id, None)
            self._writers.pop(world_id, None)
            return
        try:
            if writer is not None:
                writer.write((json.dumps({"type": "stop"}) + "\n").encode())
                await writer.drain()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._procs.pop(world_id, None)
        self._writers.pop(world_id, None)
        logger.info(f"🌐 世界 #{world_id} 常驻进程已停止")

    async def dispatch(self, world_id: int, event: dict) -> bool:
        """向常驻进程投递事件（进程内队列排队处理）。不在跑返回 False。"""
        writer = self._writers.get(world_id)
        if writer is None:
            return False
        try:
            writer.write((json.dumps({"type": "event", "event": event}, ensure_ascii=False) + "\n").encode())
            await writer.drain()
            return True
        except Exception as e:
            logger.warning(f"🌐 世界 #{world_id} 常驻投递失败: {e}")
            self._procs.pop(world_id, None)
            self._writers.pop(world_id, None)
            return False

    # ── 内部 ──

    async def _pump_stdout(self, world_id: int, proc) -> None:
        """常驻进程 stdout → 后端日志 + WebSocket 广播

        行协议：{"type":"result","ok":true,"result":{...}} → 广播给所有 WebSocket 客户端
        """
        try:
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                logger.info(f"🌐 世界 #{world_id} 常驻: {text[:300]}")

                # ── 关键桥接：result → WebSocket 广播 ──
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                if msg.get("type") == "result" and msg.get("ok"):
                    result = msg.get("result")
                    if isinstance(result, dict):
                        try:
                            from app.services.world.realtime_connection_manager import realtime_manager
                            await realtime_manager.broadcast_state(world_id, result)
                        except Exception as e:
                            logger.warning(f"🌐 世界 #{world_id} result→WS 广播失败: {e}")
        except Exception:
            pass
        finally:
            # 进程退出（崩溃/被杀）清理
            self._procs.pop(world_id, None)
            self._writers.pop(world_id, None)
            logger.warning(f"🌐 世界 #{world_id} 常驻进程退出（code={proc.returncode}）")

    async def restore_all(self, db) -> int:
        """后端启动时恢复所有常驻世界（config.resident=true 且 main.py 存在）"""
        from sqlalchemy import cast, select, String
        from app.models.world import World
        rows = (await db.execute(
            select(World).where(cast(World.config["resident"], String) == "true")
        )).scalars().all()
        started = 0
        for w in rows:
            if await self.start(db, w):
                started += 1
        if started:
            logger.info(f"🌐 常驻世界恢复完成：{started} 个")
        return started


# 全局单例
manager = ResidentManager()
