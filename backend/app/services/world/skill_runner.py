"""
skill 子进程入口 — 在隔离沙箱中执行 skill 的 code.py（2026-08-07 沙箱加固）

协议（stdin/stdout JSON 行，宿主 = skill_sandbox.run_skill_in_sandbox）：
1. 首行 = 元数据：{"world_dir", "skill_dir", "args", "permissions",
   "safe_imports", "forbidden_imports", "forbidden_builtins"}
2. 执行中 ctx 请求：子进程 → 宿主 {"type":"call","id":N,"op":...,...}
   宿主 → 子进程 {"id":N,"ok":true,"data":...} 或 {"id":N,"ok":false,"error":"..."}
3. 结束：{"type":"result","value":...} 或 {"type":"error","message":...}

隔离（本文件内，自上而下）：
- 预加载白名单标准库模块（sys.modules 缓存 → 隔离后函数内 import 不触发文件访问）
- apply_isolate：Landlock 锁世界目录（读写）+ skill 目录（只读）+ seccomp 禁网络/进程
- import 白名单（ast 扫描）+ builtins 收紧（纵深防御，即使逃逸也拿不到宿主对象）
- ctx 是纯协议代理：一切 IO（文件/世界/数据/群消息）转发宿主校验执行
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

# -I 隔离模式下 sys.path 不含脚本目录：手动加回隔离库目录（宿主经 SANDBOX_LIB_DIR 告知，
# 见 sandbox/runner.base_env）——沙箱隔离库已收归 app/services/sandbox/，不在本目录
sys.path.insert(0, os.environ.get("SANDBOX_LIB_DIR", ""))


def _read_meta() -> dict:
    line = sys.stdin.buffer.readline()
    if not line:
        raise RuntimeError("skill 宿主未发送元数据")
    meta = json.loads(line.decode("utf-8"))
    if not isinstance(meta, dict):
        raise RuntimeError("skill 元数据格式错误")
    return meta


def _preload_safe_modules(safe_imports: list[str]) -> None:
    """预加载全部白名单顶层模块：之后 code.py 里任何白名单 import 都命中 sys.modules，
    不触发文件访问（Landlock/seccomp 已生效时 import 会 EACCES）。"""
    for m in safe_imports:
        top = m.split(".")[0]
        try:
            importlib.import_module(top)
        except Exception:  # noqa: BLE001 —— 个别模块预加载失败不影响其他
            pass


# ── 隔离应用（Landlock + seccomp）──
def _apply_isolate(meta: dict) -> None:
    from sandbox_isolate import apply_isolate
    apply_isolate(
        work_dir=meta.get("world_dir") or None,
        read_dirs=[meta["skill_dir"]],
        deny_net=True,  # skill 纯计算 + 协议 IO，网络禁死
    )


# ── 安全加载（import 白名单 + builtins 收紧）──
class SkillSecurityError(ValueError):
    pass


def _check_imports(code_src: str, path: str, safe: set[str], forbidden: set[str]) -> None:
    try:
        tree = ast.parse(code_src)
    except SyntaxError as e:
        raise SkillSecurityError(f"skill 代码语法错误: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name, path, safe, forbidden)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _check_module(node.module, path, safe, forbidden)


def _check_module(module: str, path: str, safe: set[str], forbidden: set[str]) -> None:
    top = module.split(".")[0]
    if top in forbidden or module not in safe:
        raise SkillSecurityError(f"skill {path} 禁止导入模块: {module}")


def _load_code(meta: dict):
    """安全加载 code.py → 返回 run 函数（async def run(args, ctx)）"""
    from pathlib import Path
    code_path = Path(meta["skill_dir"]) / "code.py"
    code_src = code_path.read_text(encoding="utf-8")
    safe = set(meta.get("safe_imports") or [])
    forbidden = set(meta.get("forbidden_imports") or [])
    _check_imports(code_src, code_path.name, safe, forbidden)

    safe_builtins = dict(vars(builtins))
    for k in (meta.get("forbidden_builtins") or []):
        safe_builtins.pop(k, None)

    def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level != 0:
            raise SkillSecurityError("相对导入禁止")
        _check_module(name, code_path.name, safe, forbidden)
        return _real_import(name, globals, locals, fromlist, level)

    _real_import = builtins.__import__
    safe_builtins["__import__"] = _safe_import

    namespace = {
        "__builtins__": safe_builtins,
        "__name__": f"world_skill_{meta.get('name', 'skill')}",
        "__file__": str(code_path),
    }
    exec(compile(code_src, str(code_path), "exec"), namespace)
    run_fn = namespace.get("run")
    if not callable(run_fn):
        raise SkillSecurityError(f"skill 未定义 async def run(args, ctx)")
    return run_fn


# ── ctx 协议代理（一切 IO 转发宿主，宿主校验权限后执行）──
class _CtxProxy:
    def __init__(self, permissions: list[str]):
        self._perms = set(permissions)
        self._id = 0

    def _need(self, perm: str) -> None:
        if perm not in self._perms:
            raise PermissionError(f"未声明权限 {perm}（manifest.permissions 里加上才能用）")

    def _call(self, op: str, **kw):
        self._id += 1
        req = {"type": "call", "id": self._id, "op": op}
        req.update(kw)
        sys.stdout.write(json.dumps(req, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        line = sys.stdin.buffer.readline()
        if not line:
            raise RuntimeError("skill 宿主连接中断")
        resp = json.loads(line.decode("utf-8"))
        if not resp.get("ok"):
            raise PermissionError(resp.get("error", "操作被拒绝"))
        return resp.get("data")

    # file
    def file_list(self, prefix: str = "") -> list:
        self._need("file:list")
        return self._call("file.list", prefix=prefix)

    def file_read(self, path: str) -> str:
        self._need("file:read")
        return self._call("file.read", path=path)

    def file_write(self, path: str, content: str) -> None:
        self._need("file:write")
        self._call("file.write", path=path, content=content)

    def file_delete(self, path: str) -> None:
        self._need("file:delete")
        self._call("file.delete", path=path)

    # world
    def world_get(self) -> dict:
        self._need("world:read")
        return self._call("world.get")

    def world_update(self, **patch) -> dict:
        self._need("world:update")
        return self._call("world.update", patch=patch)

    # data
    def data_get(self, key: str):
        self._need("data:read")
        return self._call("data.get", key=key)

    def data_set(self, key: str, value) -> dict:
        self._need("data:write")
        return self._call("data.set", key=key, value=value)

    def data_delete(self, key: str) -> bool:
        self._need("data:write")
        return self._call("data.delete", key=key)

    # group
    def group_send(self, content: str) -> dict:
        self._need("group:send")
        return self._call("group.send", content=content)

    # log
    def log(self, *a) -> None:
        self._call("log", text=" ".join(str(x) for x in a))


def _build_ctx(permissions: list[str]) -> SimpleNamespace:
    ctx: dict = {"log": _CtxProxy(permissions).log}
    proxy = _CtxProxy(permissions)
    p = set(permissions)
    if p & {"file:read", "file:write", "file:list", "file:delete"}:
        ctx["file"] = SimpleNamespace(
            list=proxy.file_list, read=proxy.file_read,
            write=proxy.file_write, delete=proxy.file_delete,
        )
    if p & {"world:read", "world:update"}:
        ctx["world"] = SimpleNamespace(get=proxy.world_get, update=proxy.world_update)
    if "data:read" in p or "data:write" in p:
        ctx["data"] = SimpleNamespace(
            get=proxy.data_get, set=proxy.data_set, delete=proxy.data_delete,
        )
    if "group:send" in p:
        ctx["group"] = SimpleNamespace(send=proxy.group_send)
    return SimpleNamespace(**ctx)


def _main() -> None:
    try:
        meta = _read_meta()
        _preload_safe_modules(meta.get("safe_imports") or [])
        _apply_isolate(meta)
        run_fn = _load_code(meta)
        args = meta.get("args") or {}
        ctx = _build_ctx(meta.get("permissions") or [])

        result = asyncio.run(run_fn(args, ctx))
        if not isinstance(result, dict):
            result = {"success": True, "result": result}
        sys.stdout.write(json.dumps({"type": "result", "value": result}, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
    except PermissionError as e:
        sys.stdout.write(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except SkillSecurityError as e:
        sys.stdout.write(json.dumps({"type": "error", "message": f"skill 加载失败: {e}"}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception as e:  # noqa: BLE001 —— skill 代码是外部输入，全部兜住
        sys.stdout.write(json.dumps({"type": "error", "message": f"skill 执行出错: {e}"}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _main()
