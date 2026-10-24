"""世界代码落盘前的语法自检（零依赖）

世界 AI 做整文件级批量替换时最容易写坏两处：整体括号/关键字被吃掉（少一个 else）、
插值结果直接落进样式值（padding: 12pxNone）。这类损坏靠它事后回读才发现，代价是一整轮排查。

这里在 file_write / file_edit 落盘前跑一次轻量解析：
- .py：标准库 ast（真解析器，零误报）
- .js / .css：零依赖结构扫描——注释/字符串/正则/模板串先等长抹白（保留换行，行号 1:1），
  再做括号配平 + 关键字缺失签名 + 插值残留检查

**只拦确定的语法损坏**：扫描器拿不准的一律放行——误拦会让世界 AI 卡在写不进去上，
比坏文件更贵（有 skip_lint 兜底）。容器内禁止装依赖，所以不用 node / esprima。
"""

from __future__ import annotations

import ast
import os
import re

__all__ = ["lint_code", "is_lintable", "blank_code"]

_PY_EXT = {".py"}
_JS_EXT = {".js", ".mjs", ".cjs"}
_CSS_EXT = {".css"}

_EXCERPT_LIMIT = 160

# CSS 关键字全小写：出现大写 None / NaN / undefined / [object Object] 只可能是 JS 插值没算完
_CSS_ARTIFACT_RE = re.compile(r"None|NaN|undefined|\[object Object\]")
# JS 里没有 None（Python 习惯或插值残留），运行必炸
_JS_ARTIFACT_RE = re.compile(r"(?<![\w$.])None(?![\w$])")
# 批量替换吃掉 else / do / try 的签名：} 之后紧跟 {（要求有空白，放行压缩过的库）
_JS_MISSING_KEYWORD_RE = re.compile(r"\}[ \t]+\{|\}[ \t]*\n[ \t]*\{")

_PAIRS = {")": "(", "]": "[", "}": "{"}
_OPENERS = "([{"

# / 之前出现这些字符或关键字时，它是正则字面量的开始而不是除号
_REGEX_PREV = set("(,=:[!&|?{};+-*%~^<>")
_REGEX_KEYWORDS = ("return", "typeof", "instanceof", "case", "in", "of", "new",
                   "delete", "void", "do", "else", "yield", "await")


def lint_code(path: str, content: str) -> dict | None:
    """落盘前语法自检：通过返回 None，不通过返回 {"error", "line", "excerpt"}。

    只按扩展名分发：非 .py / .js / .css 一律放行（返回 None）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _PY_EXT:
        return _lint_python(path, content)
    if ext in _JS_EXT:
        return _lint_js(path, content)
    if ext in _CSS_EXT:
        return _lint_css(path, content)
    return None


def is_lintable(path: str) -> bool:
    """本模块会做语法自检的扩展名（沙箱跑完后扫描被改动的文件也用它筛）"""
    return os.path.splitext(path)[1].lower() in (_PY_EXT | _JS_EXT | _CSS_EXT)


def blank_code(text: str) -> str | None:
    """把 .js 的注释/字符串/正则/模板串抹成等长空白（行号 1:1）；扫描出确定问题（未闭合）返回 None。

    给「只关心代码骨架」的检查用（如引用自检）：抹白后 `UI.emptyBox` 这种成员调用还在，
    而字符串里出现的同名字样不会被误当成调用。
    """
    stripped, err = _strip(text, js=True)
    return None if err else stripped


# ═══════════════════════════════════════════════════════════════
# .py：交给标准库解析器
# ═══════════════════════════════════════════════════════════════

def _lint_python(path: str, content: str) -> dict | None:
    try:
        ast.parse(content, filename=path)
    except SyntaxError as e:
        detail = f"（{e.text.strip()[:60]}）" if e.text and e.text.strip() else ""
        return _problem(f"Python 语法错误：{e.msg}{detail}", e.lineno or 1, content)
    except ValueError as e:  # 源码含 NUL 等
        return _problem(f"Python 源码无法解析：{e}", 1, content)
    return None


# ═══════════════════════════════════════════════════════════════
# .js / .css：抹白 + 结构检查
# ═══════════════════════════════════════════════════════════════

def _lint_js(path: str, content: str) -> dict | None:
    stripped, err = _strip(content, js=True)
    if err:
        return _problem(err["error"], err["line"], content)
    unbalanced = _check_balance(stripped)
    if unbalanced:
        return _problem(unbalanced["error"], unbalanced["line"], content)
    m = _JS_ARTIFACT_RE.search(stripped)
    if m:
        return _problem("出现 None：JS 里没有 None（Python 习惯或插值残留，运行会 ReferenceError）",
                        _line_of(stripped, m.start()), content)
    m = _JS_MISSING_KEYWORD_RE.search(stripped)
    if m:
        return _problem("} 后面紧跟 {：像是批量替换吃掉了 else / do / try 之类的关键字",
                        _line_of(stripped, m.start()), content)
    return None


def _lint_css(path: str, content: str) -> dict | None:
    stripped, err = _strip(content, js=False)
    if err:
        return _problem(err["error"], err["line"], content)
    unbalanced = _check_balance(stripped)
    if unbalanced:
        return _problem(unbalanced["error"], unbalanced["line"], content)
    m = _CSS_ARTIFACT_RE.search(stripped)
    if m:
        return _problem(f"样式值里出现 {m.group(0)}：像是 JS 插值没算完就落盘（如 12pxNone）",
                        _line_of(stripped, m.start()), content)
    return None


def _strip(text: str, *, js: bool) -> tuple[str, dict | None]:
    """注释 / 字符串 / 正则 / 模板串 → 等长空白（保留换行），行号因此 1:1 可映射。

    返回 (stripped, error)；error 只包含扫描本身确定发现的问题（未闭合的字符串/注释）。
    宁可漏判不可误判：正则与除号分不清、模板串嵌套等拿不准的情况一律当普通代码放行。
    """
    src = list(text)
    n = len(text)
    i = 0
    line = 1
    prev = ""                      # 上一个有意义字符（判断 / 的正则/除号身份）
    stack: list[str] = []          # "tpl" = 模板串正文 / "expr" = 模板串里的插值表达式
    expr_depth = 0

    def blank(start: int, end: int) -> None:
        for k in range(start, end):
            if src[k] != "\n":
                src[k] = " "

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue

        # 模板串正文：只有转义、反引号收尾、插值开始有意义
        if stack and stack[-1] == "tpl":
            if c == "\\":
                blank(i, i + 2)
                i += 2
                continue
            if c == "\`":
                src[i] = " "
                stack.pop()
                i += 1
                prev = "\`"
                continue
            if c == "$" and i + 1 < n and text[i + 1] == "{":
                src[i] = src[i + 1] = " "
                i += 2
                stack.append("expr")
                expr_depth = 0
                prev = "{"
                continue
            src[i] = " "
            i += 1
            continue

        if c == "/" and js and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end < 0:
                return "", {"error": "块注释未闭合（缺 */）", "line": line}
            blank(i, end + 2)
            i = end + 2
            continue

        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "\n":
                    break
                j += 1
            if j >= n or text[j] != c:
                return "", {"error": f"字符串未闭合（第 {line} 行的 {c} 起）", "line": line}
            blank(i, j + 1)
            i = j + 1
            prev = "s"
            continue

        if c == "\`" and js:
            src[i] = " "
            stack.append("tpl")
            i += 1
            prev = "\`"
            continue

        if c == "/" and js and _looks_like_regex(text, i, prev):
            j = i + 1
            in_class = False
            closed = False
            while j < n:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "\n":
                    break
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    closed = True
                    break
                j += 1
            if closed:
                blank(i, j + 1)
                i = j + 1
                prev = "r"
                continue

        # 插值表达式里的收尾 } 不算外层括号
        if stack and stack[-1] == "expr":
            if c == "{":
                expr_depth += 1
            elif c == "}":
                if expr_depth == 0:
                    src[i] = " "
                    stack.pop()
                    i += 1
                    prev = "}"
                    continue
                expr_depth -= 1
            elif c == "\`":
                src[i] = " "
                stack.append("tpl")
                i += 1
                prev = "\`"
                continue

        if c.strip():
            prev = c
        i += 1

    if stack:
        return "", {"error": "模板字符串未闭合（反引号缺收尾）", "line": line}
    return "".join(src), None


def _looks_like_regex(text: str, i: int, prev: str) -> bool:
    """斜杠是正则开始还是除号：拿不准就当除号（漏判好过误判）"""
    if not prev or prev in _REGEX_PREV:
        return True
    j = i - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    k = j
    while k >= 0 and (text[k].isalnum() or text[k] in "_$"):
        k -= 1
    return text[k + 1:j + 1] in _REGEX_KEYWORDS


def _check_balance(stripped: str) -> dict | None:
    """括号配平：多余 / 错配 / 未闭合都算硬错误"""
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in stripped:
        if ch == "\n":
            line += 1
        elif ch in _OPENERS:
            stack.append((ch, line))
        elif ch in _PAIRS:
            if not stack or stack[-1][0] != _PAIRS[ch]:
                return {"error": f"多余的 {ch}（没有与之配对的 {_PAIRS[ch]}）", "line": line}
            stack.pop()
    if stack:
        ch, ln = stack[0]
        return {"error": f"{ch} 没有闭合（第 {ln} 行开的口子）", "line": ln}
    return None


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _problem(message: str, line: int, content: str) -> dict:
    lines = content.splitlines()
    raw = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    excerpt = raw[:_EXCERPT_LIMIT] + ("…" if len(raw) > _EXCERPT_LIMIT else "")
    return {"error": message, "line": line, "excerpt": excerpt}
