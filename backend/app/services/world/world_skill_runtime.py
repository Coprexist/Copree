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

from app.paths import AI_SKILLS_DIR, world_dir

logger = logging.getLogger(__name__)

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

# ── 能力清单（唯一一份：manifest 校验、文档、错误提示都从这里取）──
PERMISSIONS = frozenset({
    "file:list", "file:read", "file:write", "file:delete",
    "world:read", "world:update",
    "data:read", "data:write",
    "group:send",
})


def validate_manifest(m) -> tuple[bool, str]:
    """manifest 结构校验 —— 写入与加载共用这一份判定，返回 (ok, error)。

    为什么单列：写入时能拦下的错误（名字含连字符、权限拼错），加载时只会让技能"消失"；
    两边各写一套判定，必然一边松一边紧，而失败又是静默的。
    """
    if not isinstance(m, dict):
        return False, "manifest 必须是 JSON 对象"
    name = str(m.get("name") or "").strip()
    if not name:
        if isinstance(m.get("skills"), list):
            return False, "旧格式：一个 manifest 里写了一列技能；应每个技能一个目录（skills/<name>/manifest.json + code.py）"
        return False, "缺 name"
    if not name.replace("_", "").isalnum():
        return False, f"name 非法：{name}（只能字母、数字、下划线）"
    if not str(m.get("description") or "").strip():
        return False, "缺 description（模型靠它决定何时调用）"
    args = m.get("arguments")
    if args is not None:
        if not isinstance(args, dict):
            return False, "arguments 必须是 JSON Schema 对象"
        if args.get("type") not in (None, "object"):
            return False, "arguments.type 只能是 object"
        if args.get("properties") is not None and not isinstance(args.get("properties"), dict):
            return False, "arguments.properties 必须是对象"
    perms = m.get("permissions") or []
    if not isinstance(perms, list):
        return False, "permissions 必须是数组"
    unknown = [str(p) for p in perms if str(p) not in PERMISSIONS]
    if unknown:
        return False, "未知权限：" + "、".join(unknown) + "（可选：" + "、".join(sorted(PERMISSIONS)) + "）"
    types = m.get("types")
    if types is not None and not isinstance(types, list):
        return False, 'types 必须是数组（省略或 ["*"] = 所有类型通用）'
    return True, ""


def validate_skill_code(code: str) -> tuple[bool, str]:
    """skill 代码校验：语法 + import 白名单 + 必须 async def run(args, ctx)。

    与执行期同一套规则（_check_imports / skill_runner），差别只是提前到写入时——
    否则居民那边只会收到「沙箱里炸了」，看不出是 import 越界。
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return False, f"语法错误：{e}"
    try:
        _check_imports(code or "", "code.py")
    except SkillSecurityError as e:
        return False, str(e)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            return False, "run 必须写成 async def run(args, ctx)"
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            if len(node.args.args) < 2:
                return False, "run 需要两个参数：run(args, ctx)"
            return True, ""
    return False, "缺 async def run(args, ctx)"

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


def _load_manifest_ex(dir_path: Path, scope: str) -> tuple[SkillDef | None, str]:
    """读一个技能目录 → (SkillDef, "") 或 (None, 跳过原因)。

    加载与 list_world_skills_detail 共用：以前跳过时只有一行日志，造物主自己看不到
    「我颁布了什么、哪条没加载」，44 世界十套技能全灭就是这样被忽略的。
    """
    manifest_path = dir_path / "manifest.json"
    code_path = dir_path / "code.py"
    if not manifest_path.exists():
        return None, "缺 manifest.json"
    if not code_path.exists():
        return None, "缺 code.py"
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"manifest 解析失败：{e}"
    ok, err = validate_manifest(m)
    if not ok:
        return None, err
    return SkillDef(
        name=str(m["name"]).strip(),
        description=str(m.get("description") or "").strip(),
        arguments=m.get("arguments") or {"type": "object", "properties": {}},
        permissions=[str(p) for p in (m.get("permissions") or [])],
        code_path=code_path,
        scope=scope,
        types=_parse_types(m.get("types")),
    ), ""


def _load_manifest(dir_path: Path, scope: str) -> SkillDef | None:
    skill, err = _load_manifest_ex(dir_path, scope)
    if skill is None:
        logger.warning(f"📦 skill 跳过 {dir_path}: {err}")
    return skill


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
    skills_dir = world_dir(world_id) / "skills"
    if not skills_dir.is_dir():
        return []
    return [
        s for d in sorted(skills_dir.iterdir())
        if d.is_dir() and (s := _load_manifest(d, "world")) is not None
    ]

def list_world_skills_detail(world_id: int) -> dict:
    """造物主视角的技能清单：已加载的 + 被跳过的目录与原因。

    与 list_world_skills 同一份加载判定（_load_manifest_ex），区别只在把失败原因带出来——
    "居民为什么没有这个工具"要能当场回答，不能只剩一行后端日志。
    """
    skills_dir = world_dir(world_id) / "skills"
    loaded: list[dict] = []
    skipped: list[dict] = []
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if d.is_file() and d.name in ("manifest.json", "code.py"):
                # 旧世界常见错法：两个文件直接放在 skills/ 根下，加载器只认子目录
                skipped.append({"dir": f"skills/{d.name}", "reason": "技能要放进 skills/<name>/ 子目录"})
                continue
            if not d.is_dir() or d.name.startswith("__"):
                continue
            skill, err = _load_manifest_ex(d, "world")
            if skill is None:
                skipped.append({"dir": f"skills/{d.name}", "reason": err})
                continue
            loaded.append({
                "name": skill.name,
                "description": skill.description,
                "parameters": list(((skill.arguments or {}).get("properties") or {})),
                "permissions": skill.permissions,
                "types": skill.types or ["*"],
            })
    return {"success": True, "skills": loaded, "skipped": skipped}


def write_world_skill(world_id: int, *, name: str, description: str, code: str,
                      arguments: dict | None = None, permissions: list | None = None,
                      types: list | None = None) -> dict:
    """颁布/更新一个世界侧技能：一次调用写 manifest + code，写完立即按加载器规则回读。

    写入走 world_file_service（与世界文件同一套路径/扩展名/大小校验），所以颁布者不必知道
    data/worlds/<id> 在哪；校验与加载同源（validate_manifest / validate_skill_code），
    写进去的东西一定加载得出来，不会出现「写了但居民看不见」。
    """
    from app.services.world import world_file_service as fs

    spec = {
        "name": str(name or "").strip(),
        "description": str(description or "").strip(),
        "arguments": arguments if arguments is not None else {"type": "object", "properties": {}},
        "permissions": [str(p) for p in (permissions or [])],
        "types": [str(t) for t in (types or []) if str(t).strip()] or ["*"],
    }
    ok, err = validate_manifest(spec)
    if not ok:
        return {"success": False, "error": err}
    ok, err = validate_skill_code(code or "")
    if not ok:
        return {"success": False, "error": err}

    rel_dir = f"skills/{spec['name']}"
    try:
        fs.write_file(world_id, f"{rel_dir}/manifest.json",
                      json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
        fs.write_file(world_id, f"{rel_dir}/code.py", (code or "").rstrip("\n") + "\n")
    except ValueError as e:  # 越界路径 / 扩展名 / 大小：原样回给颁布者
        return {"success": False, "error": str(e)}

    skill, load_err = _load_manifest_ex(world_dir(world_id) / "skills" / spec["name"], "world")
    if skill is None:  # 写进去却读不回来：平台自身问题，如实报，别假装成功
        return {"success": False, "error": f"已写入但加载失败：{load_err}", "path": rel_dir}
    return {
        "success": True,
        "result": f"已颁布 skills/{skill.name}/（居民可直接工具调用）",
        "path": rel_dir,
        "name": skill.name,
        "permissions": skill.permissions,
        "types": skill.types or ["*"],
        "next": f"用 run_world_skill 传 name={skill.name} 试跑一次",
    }


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
async def execute_skill(db, world, name: str, arguments: str, scope: str | None = None,
                      caller: dict | None = None) -> dict | None:
    """执行文件式 skill（隔离沙箱子进程）；名字不匹配（或不在 scope 内）返回 None（由调用方走未知工具兜底）

    scope：'ai' = 只执行设计侧（世界 AI 用）；'world' = 只执行世界侧（群 AI 用）；None = 两者（默认）
    caller：调用者身份（{sender_type, sender_id, agent_id, name, group_id}）→ skill 里的 ctx.caller；
            群 AI 的 tools 分发会带上，设计侧 AI 的调用不带（它不是在群里替谁发言）
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
        return await run_skill_in_sandbox(db, world, skill, args, caller=caller)
    except Exception as e:  # noqa: BLE001 —— 沙箱故障兜底
        logger.warning(f"🌐 skill {name} 沙箱执行异常: {e!r}")
        return {"success": False, "error": f"skill 执行出错: {e}"}
