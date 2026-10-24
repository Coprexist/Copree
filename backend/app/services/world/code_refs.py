"""跨文件引用自检（启发式；只提示，不拦写入）

单文件语法检查抓不到「导出名与调用名对不上」——世界跑起来才报 `UI.emptyBox is not a function`，
还被页面自己的 try/catch 兜成一句「这一步没走通」（世界 AI 2026-10 在 js/ui.js 与 js/views/*.js 上踩到：
导出表里写的是 `empty`，调用方全写 `emptyBox`）。

这里做一件事的最小版本：世界自己挂在 window 上的命名空间（`window.UI = {...}`）的键表 ⨯ 各文件的
`UI.x` 调用。**只下确定的结论**：定义里有展开（...）、计算键（[k]）、字符串键，或同一个名字被
多处定义时，整条跳过——提示误报比漏报贵，一次误报就会让它开始怀疑整个自检。

只认 .js：世界代码只有 js/py 两种，py 侧的同名问题由运行时错得足够清楚。
"""

from __future__ import annotations

import logging
import os
import re

from app.paths import world_dir
from app.services.world.code_lint import blank_code

logger = logging.getLogger(__name__)

__all__ = ["ref_warnings"]

_JS_EXT = {".js", ".mjs", ".cjs"}
_MAX_SCAN_BYTES = 512 * 1024     # 单文件超过这个就不扫了（引用自检是提示，不值得为它读大文件）
_MAX_WARNINGS = 3                # 最多提示几条（再多就成噪声，模型也不看）
_MAX_KEYS_SHOWN = 8              # 列可用键时最多列几个

# 定义处：window.<Name> = {   ——只认对象字面量，函数/数组等一律不收录（判不了键）
_EXPORT_RE = re.compile(r"window\s*\.\s*([A-Za-z_$][\w$]*)\s*=\s*\{")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*\Z")


def _member_rx(name: str, *, assign: bool = False) -> re.Pattern:
    """`<Name>.<key>` 的成员访问；assign=True 时只匹配赋值（= 改成员，即追加键）。

    前置字符排除标识符字符（`LongUI.x` 不算 `UI.x`），但允许 `window.UI.x`。
    """
    tail = r"\s*=(?!=)" if assign else ""
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(name)}\s*\.\s*([A-Za-z_$][\w$]*){tail}")


def _top_level_key(chunk: str) -> str | None:
    """对象字面量的一个顶层片段 → 键名；空片段返回 ""，动态/看不懂返回 None。"""
    chunk = chunk.strip()
    if not chunk:
        return ""
    head = chunk.split(":", 1)[0].strip()      # `key: value` / 简写 `key`
    return head if _IDENT.match(head) else None


def _object_keys(blanked: str, open_at: int) -> set[str] | None:
    """从 '{' 起读对象字面量的顶层键名；拼不出完整键表返回 None。

    抹白后字符串键只剩空白、展开与计算键保留运算符，都会被 `_top_level_key` 判成 None。
    """
    depth = 0
    keys: set[str] = set()
    start = open_at + 1
    for i in range(open_at, len(blanked)):
        c = blanked[i]
        if c in "{[(":
            depth += 1
            continue
        if c in "}])":
            depth -= 1
            if depth == 0:
                last = _top_level_key(blanked[start:i])
                return None if last is None else keys | ({last} if last else set())
            continue
        if c == "," and depth == 1:
            key = _top_level_key(blanked[start:i])
            if key is None:
                return None
            if key:
                keys.add(key)
            start = i + 1
    return None                                  # 括号没配平（前面已抹白，正常不会到这里）


def _merge(defined: dict[str, set[str] | None], name: str, keys: set[str]) -> None:
    """合并同一命名空间的多处定义：键取并集，任一处判不了 → 整条 None（宁可不下结论）

    `var UI = window.UI || (window.UI = {});` 这种自举写法让同一个名字有两处定义，
    按「重复即存疑」处理会让检查在世界代码里全线失效，所以这里取并集。
    """
    if name in defined and defined[name] is None:
        return
    if keys is None:
        defined[name] = None
        return
    defined[name] = (defined[name] if name in defined else set()) | keys


def _definitions(blanked: str) -> dict[str, set[str] | None]:
    """一份文件里的 window 命名空间定义（同名多处取并集；有一处解析不了 → None）"""
    out: dict[str, set[str] | None] = {}
    for m in _EXPORT_RE.finditer(blanked):
        _merge(out, m.group(1), _object_keys(blanked, m.end() - 1))
    return out


def _is_dynamic(name: str, texts: dict[str, str]) -> bool:
    """这个世界有没有用「算出来的」方式给该命名空间加成员（Object.assign / defineProperty）。

    有就整条不下结论：`Object.defineProperty(window.App, 'route', ...)` 挂上的键静态读不到，
    照字面表报「没有这个键」就是误报（世界 AI 2026-10 的 js/app.js 正是这种写法）。
    """
    rx = re.compile(rf"Object\.(?:assign|defineProperty)\(\s*(?:window\s*\.\s*)?{re.escape(name)}\b")
    return any(rx.search(text) for text in texts.values())


def _tables(texts: dict[str, str]) -> dict[str, set[str]]:
    """全局名 → 键表：字面量顶层键 ∪ 全世界的 `Name.key = ...` 追加；动态挂载的整条跳过"""
    defined: dict[str, set[str] | None] = {}
    for text in texts.values():
        for name, keys in _definitions(text).items():
            _merge(defined, name, keys)
    tables: dict[str, set[str]] = {}
    for name, keys in defined.items():
        if not keys or _is_dynamic(name, texts):
            continue                             # 解析不了 / 空表 / 动态挂载 → 判不了，跳过
        table = set(keys)
        rx = _member_rx(name, assign=True)
        for text in texts.values():
            table.update(m.group(1) for m in rx.finditer(text))
        tables[name] = table
    return tables


def _world_js(world_id: int) -> dict[str, str]:
    """世界里的 .js → 抹白后的文本（跳过产物目录与超大文件；读不了/扫不通的整份跳过）"""
    from app.services.world.world_file_service import is_artifact_path   # 产物清单只有一份，别在这里抄

    base = world_dir(world_id)
    out: dict[str, str] = {}
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _JS_EXT:
            continue
        rel = str(p.relative_to(base))
        if is_artifact_path(rel):
            continue
        try:
            if p.stat().st_size > _MAX_SCAN_BYTES:
                continue
            blanked = blank_code(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if blanked:
            out[rel] = blanked
    return out


def _render(found: dict[tuple[str, str], list[str]], tables: dict[str, set[str]]) -> list[str]:
    """命中 → 人话提示（同名同键合并：一处写错往往在好几个文件里同时出现）"""
    out: list[str] = []
    for (name, key), where in list(found.items())[:_MAX_WARNINGS]:
        sites = "、".join(where[:2]) + (f" 等 {len(where)} 处" if len(where) > 2 else "")
        keys = sorted(tables[name])
        shown = "、".join(keys[:_MAX_KEYS_SHOWN])
        if len(keys) > _MAX_KEYS_SHOWN:
            shown += f" 等 {len(keys)} 个"
        out.append(f"{name}.{key} 在 {sites} 被调用，但世界里的 window.{name} 没有这个键"
                   f"（现有：{shown}）——确认导出表与调用名是否一致")
    if len(found) > _MAX_WARNINGS:
        out.append(f"…另有 {len(found) - _MAX_WARNINGS} 处引用存疑，可用 file_grep 逐个核对")
    return out


def ref_warnings(world_id: int, path: str, content: str) -> list[str]:
    """这次写入会让调用方找不到成员吗：返回提示列表（最多 3 条；空列表 = 没发现）。

    content 是**即将落盘**的内容：盘上还是旧版，必须以这次要写的为准，否则永远滞后一步。
    这是提示不是拦截——返回值放进工具结果的 warnings，写不写由模型判断。
    """
    if os.path.splitext(path)[1].lower() not in _JS_EXT:
        return []
    try:
        blanked = blank_code(content)
        if blanked is None:
            return []
        texts = _world_js(world_id)
        texts[path] = blanked
        tables = _tables(texts)
        if not tables:
            return []
        found: dict[tuple[str, str], list[str]] = {}
        for rel, text in texts.items():
            for name, table in tables.items():
                for m in _member_rx(name).finditer(text):
                    key = m.group(1)
                    if key not in table:
                        line = text.count("\n", 0, m.start()) + 1
                        found.setdefault((name, key), []).append(f"{rel}:{line}")
        return _render(found, tables)
    except Exception as e:                       # 自检自身绝不拖垮写入
        logger.warning(f"🌐 世界 #{world_id} 引用自检失败（跳过）: {e}")
        return []
