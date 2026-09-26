"""
文件式 skill/tool 运行时 — 世界能力动态扩展（2026-08-06）

两个作用域（造物主 ≠ 居民）：
- 设计侧：data/world_ai_skills/<name>/   → 世界设计 AI（造物主）的工具库，全局共享，
  造物主在世界之外设计世界，其工具不混入世界内容
- 世界侧：data/worlds/{id}/skills/<name>/ → 世界颁布的能力（居民/管理者/物品），
  world_command 等世界内能力属于这里

skill 目录约定（每目录一个 skill）：
- manifest.json:
    {
      "name": "skill 名（工具名）",
      "description": "给 LLM 看的描述",
      "arguments": { "type": "object", "properties": {...}, "required": [...] },  # json-schema
      "permissions": ["file:read", "file:write", "world:read", "group:send", ...] # 能力清单
    }
- code.py:
    async def run(args: dict, ctx) -> dict
    ctx 只包含 manifest.permissions 声明过的能力（capability-based，能力最小化）

安全模型（v2 子进程沙箱，2026-08-07 加固）：
- 子进程执行（python -I + rlimit + 独立进程组 + killpg 超时强杀）
- Landlock：文件系统锁死在世界目录（读写）+ skill 目录（只读），其他路径全 EACCES
- seccomp：禁网络/进程/挂载/ptrace/内核接口等危险系统调用
- import 白名单（ast 扫描）+ builtins 收紧（纵深防御，逃逸也拿不到宿主对象）
- ctx 能力协议转发回宿主校验执行（capability-based，能力最小化）
- 信任边界：世界创作者（或其 AI）编写的代码——防"AI 生成代码越权乱来"；
  对抗性恶意代码的终极隔离（容器）仍后置
"""
from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

AI_SKILLS_DIR = Path("data/world_ai_skills")
WORLDS_DIR = Path("data/worlds")

# ── import 白名单（标准库安全子集：不碰文件系统/网络/进程） ──
SAFE_IMPORTS = {
    "abc", "array", "asyncio", "base64", "binascii", "bisect", "collections",
    "contextlib", "copy", "dataclasses", "datetime", "decimal", "difflib",
    "enum", "functools", "hashlib", "heapq", "itertools", "json", "math",
    "operator", "random", "re", "statistics", "string", "textwrap", "time",
    "typing", "unicodedata", "urllib.parse", "uuid", "zoneinfo",
}
# 明确拒绝（防御性名单，白名单之外本就拒绝）
FORBIDDEN_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "glob", "io",
    "pickle", "marshal", "shelve", "sqlite3", "ctypes", "cffi", "importlib",
    "builtins", "threading", "multiprocessing", "signal", "resource",
    "http", "urllib.request", "urllib.error", "requests", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "ssl", "cryptography", "tempfile",
    "platform", "pwd", "grp", "webbrowser", "tkinter", "curses",
}
# builtins 禁用清单（与子进程 import 白名单同为纵深防御；逃逸兜底靠 Landlock/seccomp）
FORBIDDEN_BUILTINS = {
    "open", "eval", "exec", "compile", "input", "breakpoint", "help",
    "memoryview", "exit", "quit", "copyright", "credits", "license",
}


@dataclass
class SkillDef:
    name: str
    description: str
    arguments: dict
    permissions: list[str] = field(default_factory=list)
    code_path: Path = None  # type: ignore[assignment]
    scope: str = "world"  # "ai" = 设计侧（造物主） | "world" = 世界侧（居民）
    types: list[str] | None = None  # 适用类型 slug 列表（None 或 ["*"] = 所有类型通用；["blacksmith"] = 仅铁匠可用）


# ── manifest 解析 ──
def _parse_types(raw) -> list[str] | None:
    """解析 manifest.types：省略/["*"]/空 = None（所有类型通用）；列表 = 仅这些类型可用。

    设计口径："设计Skill的时候就可选这是给所有类型通用的skill还是只有哪些类型可用的skill"。
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    types = [str(t).strip() for t in raw if str(t).strip()]
    if not types or "*" in types:
        return None  # 通配 = 通用
    return types


def _load_manifest(dir_path: Path, scope: str) -> SkillDef | None:
    manifest_path = dir_path / "manifest.json"
    code_path = dir_path / "code.py"
    if not (manifest_path.exists() and code_path.exists()):
        return None
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        name = str(m.get("name") or "").strip()
        if not name or not name.replace("_", "").isalnum():
            logger.warning(f"📦 skill manifest 非法名字: {manifest_path}")
            return None
        return SkillDef(
            name=name,
            description=str(m.get("description") or "").strip(),
            arguments=m.get("arguments") or {"type": "object", "properties": {}},
            permissions=[str(p) for p in (m.get("permissions") or [])],
            code_path=code_path,
            scope=scope,
            types=_parse_types(m.get("types")),
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"📦 skill manifest 解析失败 {manifest_path}: {e}")
        return None


def list_ai_skills() -> list[SkillDef]:
    """设计侧（造物主工具库，全局共享，所有世界的设计 AI 可用）"""
    if not AI_SKILLS_DIR.is_dir():
        return []
    return [
        s for d in sorted(AI_SKILLS_DIR.iterdir())
        if d.is_dir() and (s := _load_manifest(d, "ai")) is not None
    ]


def list_world_skills(world_id: int) -> list[SkillDef]:
    """世界侧（世界颁布的居民能力）"""
    skills_dir = WORLDS_DIR / str(world_id) / "skills"
    if not skills_dir.is_dir():
        return []
    return [
        s for d in sorted(skills_dir.iterdir())
        if d.is_dir() and (s := _load_manifest(d, "world")) is not None
    ]


def _to_tools(skills: list[SkillDef], world_id: int | None = None) -> list[dict]:
    """skill → function calling 工具定义。

    world_id 非空（世界侧）：自动追加可选 world_id 参数——同一 AI 绑定多个世界且
    多个世界颁布同名 skill 时，AI 可用 world_id 指定执行哪个世界的版本（缺省=当前对话世界）。
    """
    tools = []
    for s in skills:
        params = dict(s.arguments or {"type": "object", "properties": {}})
        if world_id is not None:
            props = dict(params.get("properties") or {})
            props["world_id"] = {
                "type": "integer",
                "description": "可选：指定执行该技能的世界 id（当多个世界颁布同名技能时用）。缺省 = 当前对话群绑定/关联的世界。",
            }
            params = {"type": "object", "properties": props}
        tools.append({
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": params,
            },
        })
    return tools


def build_ai_tools() -> list[dict]:
    """设计侧（造物主工具库，全局共享）→ 世界 AI 的工具定义"""
    return _to_tools(list_ai_skills())


def build_world_tools(world_id: int) -> list[dict]:
    """世界侧（世界颁布的居民能力）→ 群 AI 的工具定义（含可选 world_id 指定参数）"""
    return _to_tools(list_world_skills(world_id), world_id=world_id)


def build_world_tools_for_type(world_id: int, type_slug: str | None) -> list[dict]:
    """按类型过滤的世界侧工具：skill.types 声明了该类型才注入（分层注入）。

    type_slug 为 None（未绑定类型）→ 只给通用 skill（types=None 的）；
    type_slug 有值 → 通用 skill + 声明了该类型的 skill。
    """
    skills = list_world_skills(world_id)
    if type_slug:
        skills = [s for s in skills if s.types is None or type_slug in s.types]
    else:
        skills = [s for s in skills if s.types is None]
    return _to_tools(skills, world_id=world_id)


# ── 安全加载 ──
class SkillSecurityError(ValueError):
    pass


def _check_imports(code_src: str, path: str) -> None:
    """ast 扫描 import：白名单之外一律拒绝"""
    try:
        tree = ast.parse(code_src)
    except SyntaxError as e:
        raise SkillSecurityError(f"skill 代码语法错误: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0] if alias.name else ""
                _check_module(mod, path)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _check_module(node.module, path)


def _check_module(module: str, path: str) -> None:
    top = module.split(".")[0]
    if top in FORBIDDEN_IMPORTS or module not in SAFE_IMPORTS:
        raise SkillSecurityError(f"skill {path} 禁止导入模块: {module}")


# ── 执行入口（沙箱化：skill_sandbox 子进程 + Landlock/seccomp + 协议转发）──
async def execute_skill(db, world, name: str, arguments: str, scope: str | None = None) -> dict | None:
    """执行文件式 skill（隔离沙箱子进程）；名字不匹配（或不在 scope 内）返回 None（由调用方走未知工具兜底）

    scope：'ai' = 只执行设计侧（世界 AI 用）；'world' = 只执行世界侧（群 AI 用）；None = 两者（默认）
    """
    skill = None
    if scope in (None, 'ai'):
        skill = next((s for s in list_ai_skills() if s.name == name), None)
    if skill is None and scope in (None, 'world'):
        skill = next((s for s in list_world_skills(world.id) if s.name == name), None)
    if skill is None:
        return None
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else (arguments or {})
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        return {"success": False, "error": "参数解析失败"}

    # 宿主侧预检：语法 + import 白名单（快速失败，不进子进程）
    try:
        code_src = skill.code_path.read_text(encoding="utf-8")
        _check_imports(code_src, skill.code_path.name)
    except SkillSecurityError as e:
        logger.warning(f"🌐 skill {name} 预检拒绝: {e}")
        return {"success": False, "error": f"skill 加载失败: {e}"}
    except OSError as e:
        logger.warning(f"🌐 skill {name} 读取失败: {e}")
        return {"success": False, "error": f"skill 读取失败: {e}"}

    from app.services.world.skill_sandbox import run_skill_in_sandbox
    try:
        return await run_skill_in_sandbox(db, world, skill, args)
    except Exception as e:  # noqa: BLE001 —— 沙箱故障兜底
        logger.warning(f"🌐 skill {name} 沙箱执行异常: {e!r}")
        return {"success": False, "error": f"skill 执行出错: {e}"}
