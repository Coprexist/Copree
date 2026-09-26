"""代码沙箱契约：AI 的文件空间就是 AI 的沙箱；世界与 AI 共用同一个执行层。

任一条隔离松掉都等于把宿主暴露给 AI 写的脚本。设计见 docs/dev/code_sandbox.md。
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services.sandbox import runner
from app.services.sandbox.agent_sandbox import agent_dir, run_agent_code
from app.services.world.world_sandbox import run_world_code, run_world_trigger

# 探针用不存在的编号：跑完清干净，不碰任何真实 AI/世界的目录
PROBE_ID = 999999
PROBE_AGENT = SimpleNamespace(id=PROBE_ID, config={})


def _cleanup(workdir: Path, names: list[str]) -> None:
    for name in names:
        try:
            (workdir / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        workdir.rmdir()          # 只在空目录时成功，真实目录不会被误删
    except OSError:
        pass


async def test_agent_script_runs_inside_its_own_file_space():
    """AI 脚本的工作目录 = 它的文件空间，写文件落在这里（不需要另造一块存储）"""
    workdir = agent_dir(PROBE_ID)
    try:
        result = await run_agent_code(PROBE_ID, code=(
            "import os\nprint('cwd', os.getcwd())\n"
            "open('probe_agent.txt', 'w').write('ok')\nprint('wrote')"
        ))
        assert result["success"], result
        assert str(workdir) in result["stdout"], result["stdout"]
        assert (workdir / "probe_agent.txt").read_text() == "ok"
    finally:
        _cleanup(workdir, ["probe_agent.txt"])


async def test_agent_script_cannot_read_outside_its_space():
    """越界读一律 EACCES——这就是「存储空间变沙箱」的实质"""
    result = await run_agent_code(PROBE_ID, code="print(open('/etc/hostname').read())")
    assert not result["success"], result
    assert "PermissionError" in result["stderr"] or "PermissionError" in result["reason"], result


async def test_agent_script_has_no_network():
    """脚本不能出网：要联网走平台工具（web_search 等），不从脚本开洞"""
    result = await run_agent_code(PROBE_ID, code="import socket; socket.socket()")
    assert not result["success"], result
    assert "PermissionError" in result["stderr"] or "PermissionError" in result["reason"], result


async def test_agent_script_receives_event_context():
    """决策技能传进来的事件上下文经 DECISION_CTX 到达脚本"""
    result = await run_agent_code(
        PROBE_ID,
        code="import os, json; print(os.environ.get('DECISION_CTX', ''))",
        ctx={"event": "group_message", "content": "签到"},
    )
    assert result["success"], result
    assert '"content": "签到"' in result["stdout"], result["stdout"]


async def test_runaway_script_is_killed_with_a_readable_reason():
    """死循环撞 CPU 上限：必须被杀，且原因说得清（不能只留一个空 reason）"""
    workdir = Path("/tmp") / f"copree_sandbox_probe_{PROBE_ID}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = await runner.run_code(
            workdir, code="while True: pass",
            policy=runner.Policy(timeout_seconds=8.0, memory_mb=96, cpu_seconds=1.0),
        )
        assert not result["success"], result
        assert "CPU" in result["reason"] or "SIGXCPU" in result["reason"], result
    finally:
        _cleanup(workdir, [])


async def test_wall_clock_timeout_kills_the_process_group():
    """睡过墙钟上限：timed_out 置位（不靠 CPU 上限兜底，卡在 IO 上也要能收回）"""
    workdir = Path("/tmp") / f"copree_sandbox_probe_{PROBE_ID}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = await runner.run_code(
            workdir, code="import time; time.sleep(30)",
            policy=runner.Policy(timeout_seconds=1.0, memory_mb=96, cpu_seconds=10.0),
        )
        assert result["timed_out"] and not result["success"], result
        assert "超时" in result["reason"], result
    finally:
        _cleanup(workdir, [])


def test_world_and_agent_share_one_execution_layer():
    """世界代码与 AI 脚本必须共用一个执行层，隔离只允许在两个入口施加。

    施加隔离的入口有且只有两个，都是「受信宿主 → 不受信代码」的过桥点：
    - sandbox/runner.py：一次性执行（跑完收结果）——世界 run_world_code / AI run_script 都走它
    - world/skill_runner.py：协议式执行（stdin/stdout JSON 行，边跑边与宿主对话）
    多出第三个就说明有人另起了一套沙箱（配额/隔离会各自演化），这条用例会当场拦住。
    """
    services = Path(__file__).resolve().parents[1] / "app" / "services"
    texts = {p.relative_to(services).as_posix(): p.read_text(encoding="utf-8")
             for p in services.rglob("*.py")}
    bridged = {rel for rel, text in texts.items() if "from sandbox_isolate import apply_isolate" in text}
    assert bridged == {"sandbox/runner.py", "world/skill_runner.py"}, bridged

    # 两个 owner 适配层都只做「参数化」，执行本身委托给共用层
    for rel in ("world/world_sandbox.py", "sandbox/agent_sandbox.py"):
        assert "from app.services.sandbox.runner import" in texts[rel], rel


async def test_world_code_still_locked_to_its_own_directory():
    """重构后世界沙箱行为不变：目录内可写、目录外一律拒绝"""
    workdir = Path("data/worlds") / str(PROBE_ID)
    try:
        ok = await run_world_code(PROBE_AGENT, code=(
            "open('probe_world.txt', 'w').write('ok'); print('wrote')"
        ))
        assert ok["success"], ok
        assert (workdir / "probe_world.txt").read_text() == "ok"

        blocked = await run_world_code(PROBE_AGENT, code="print(open('/etc/hostname').read())")
        assert not blocked["success"], blocked
    finally:
        _cleanup(workdir, ["probe_world.txt"])


async def test_world_trigger_runs_entry_handle():
    """触发器协议（harness 导入入口 + stdin 喂事件 + stdout 收 JSON）重构后照旧"""
    workdir = Path("data/worlds") / str(PROBE_ID)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "main.py").write_text(
        "def handle(event):\n    return {'echo': event.get('type')}\n", encoding="utf-8"
    )
    try:
        result = await run_world_trigger(PROBE_AGENT, event={"type": "probe"}, entry="main.py")
        assert result["success"], result
        assert result["result"] == {"echo": "probe"}, result

        missing = await run_world_trigger(PROBE_AGENT, event={}, entry="nope.py")
        assert not missing["success"] and "不存在" in missing["reason"], missing
    finally:
        _cleanup(workdir, ["main.py", "__pycache__"])

async def test_script_path_stays_inside_the_sandbox():
    """run_script 的 path 落在沙箱里（存的地方就是跑的地方），越界当场拒绝"""
    import shutil

    from app.services.sandbox.agent_sandbox import script_path

    workdir = agent_dir(PROBE_ID)
    try:
        target = script_path(PROBE_ID, "scripts/probe.py")
        assert target.parent.exists() and str(target).startswith(str(workdir)), target
        try:
            script_path(PROBE_ID, "../../escape.py")
        except ValueError as e:
            assert "越界" in str(e), e
        else:
            raise AssertionError("越界路径没有被拒绝")
    finally:
        shutil.rmtree(workdir / "scripts", ignore_errors=True)
        _cleanup(workdir, [])
