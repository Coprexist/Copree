#!/usr/bin/env python3
"""CHANGELOG 的唯一写入与校验入口：sections / add / edit / move / delete / merge / check / release。

为什么要有这个脚本：CHANGELOG 是手写的，每次写入前要先通读四千多行、找到该插的位置、再把缩进
和空行对齐一遍。两类错都真的发生过：
- 位置错：[未发布] 里同一个分节出现了两次，分节顺序也从没定过；
- 格式错：条目之间的空行、续行缩进各写各的，同一个文件里三种写法。

这里把「哪一节、插到哪、写成什么样」从记忆搬进代码：分节表是唯一来源（含历史别名），同一个版本
内按写入先后追加到该节末尾，写完立刻 check，结构错了当场退出码 1。

**搬移只搬行区间，不重写正文**：合并/重排是把这个分节的原始行按新顺序拼接，文字一个字都不动，
所以历史版本里那些长段落、块引用、代码块不会被重新格式化。

用法（仓库根目录）：

    python scripts/changelog.py sections
    python scripts/changelog.py add -v 未发布 -s 修复 --from /tmp/entry.md
    python scripts/changelog.py add -v 未发布 -s 修复 --from -        # 从 stdin
    python scripts/changelog.py edit -v 未发布 --match "协议端控制台" --from /tmp/new.md
    python scripts/changelog.py move -v 未发布 -s 修复 --to 新增 --match "..."
    python scripts/changelog.py delete -v 未发布 --match "..."
    python scripts/changelog.py merge -v 未发布      # 合并重复分节 + 规范顺序 + 别名归一
    python scripts/changelog.py merge --all --dry-run
    python scripts/changelog.py check --all
    python scripts/changelog.py rename --all     # 历史英文壳换中文前缀（只改 ### 行）
    python scripts/changelog.py format            # 规范 [未发布] 的空行（--all 连历史一起）

退出码：0 通过；1 结构有问题；2 参数或文件错误。
"""
from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
BAK = CHANGELOG.with_suffix(".md.bak")

NL = chr(10)
TAB = chr(9)

# 分节的规范顺序与别名。别名只影响「你写哪个名字」，落盘一律用规范名。
# 顺序 = 读者关心的顺序：先说加了什么，再说修了什么，然后才是内部调整。
SECTIONS = [
    ("✨ 新增", ["✨ 新增", "✨ 新增功能", "🚀 新功能", "🆕 新增", "Added", "新增", "新功能"]),
    ("🐛 修复", ["🐛 修复", "🐞 修复", "🐛 修复的 Bug", "🐞 修复的 Bug", "🐞 修复（2026-10-30 重打包）", "Fixed", "修复"]),
    ("🔧 变更", ["🔧 变更", "Changed", "变更"]),
    ("⚡ 性能", ["⚡ 性能", "⚡ 优化", "⚡ 性能优化", "性能", "优化"]),
    ("♻️ 重构", ["♻️ 重构", "重构"]),
    ("🗑 移除", ["🗑 移除", "🗑️ 移除", "Removed", "移除"]),
    ("🎨 界面与交互", ["🎨 界面与交互", "🎨 界面", "🎨 UI", "界面与交互"]),
    ("🔒 安全", ["🔒 安全", "Security", "安全"]),
    ("🧹 清理", ["🧹 清理", "清理"]),
    ("🧪 测试", ["🧪 测试", "测试"]),
    ("📝 文档与工程", ["📝 文档与工程", "📝 文档与插件", "📝 文档", "📚 文档更新", "📚 文档", "Docs", "文档与工程"]),
    ("🔧 开发工具与测试", ["🔧 开发工具与测试", "🔧 开发工具", "开发工具与测试"]),
    ("📦 依赖更新", ["📦 依赖更新", "依赖更新"]),
]
ALIAS = {alias: canon for canon, aliases in SECTIONS for alias in aliases}
ORDER = {canon: i for i, (canon, _) in enumerate(SECTIONS)}
UNRELEASED = "未发布"


class Fail(Exception):
    """参数或文件结构错误：打印后用退出码 2 结束，不写文件。"""


def version_of(line):
    """## [v0.4.16] - 2026-10-30 -> v0.4.16；不是版本标题返回 None。"""
    if not line.startswith("## ["):
        return None
    close = line.find("]")
    if close < 0:
        return None
    return line[4:close]


class Section:
    __slots__ = ("name", "lines", "start")

    def __init__(self, name, lines, start):
        self.name = name
        self.lines = lines   # 原文行（含 ### 标题行与其后的空行），只搬不改
        self.start = start

    def items(self):
        """条目 = 以 '- ' 开头的行 + 其后缩进的续行。返回 [(start, end, first_line)]，区间含原行。"""
        out = []
        i = 0
        lines = self.lines
        while i < len(lines):
            if lines[i].startswith("- "):
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or lines[j][:1] in (" ", TAB)):
                    if not lines[j].strip() and (j + 1 >= len(lines) or not lines[j + 1][:1] in (" ", TAB)):
                        break
                    j += 1
                out.append((i, j, lines[i]))
                i = j
            else:
                i += 1
        return out


class Block:
    __slots__ = ("start", "end", "head", "version", "sections", "extra")

    def __init__(self, start, head, version):
        self.start = start
        self.end = start + 1
        self.head = head
        self.version = version
        self.sections = []
        self.extra = []   # (行号, 行)：块头与首节之间 / 末节之后的内容

    @property
    def released(self):
        return self.version.strip() != UNRELEASED


def parse(lines):
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        ver = version_of(line)
        if ver is None:
            i += 1
            continue
        block = Block(i, line.rstrip(), ver)
        j = i + 1
        while j < n and not lines[j].startswith("## "):
            if lines[j].startswith("### "):
                k = j + 1
                while k < n and not lines[k].startswith("## ") and not lines[k].startswith("### "):
                    k += 1
                block.sections.append(Section(lines[j][4:].strip(), lines[j:k], j))
                j = k
            else:
                block.extra.append((j, lines[j]))
                j += 1
        block.end = j
        blocks.append(block)
        i = j
    return blocks


PREFIX_ALIASES = sorted(ALIAS, key=len, reverse=True)


def canonical(name):
    """分节名 = 类别 或 类别 — 主题（前缀决定归类，后缀是可选主题）。"""
    name = name.strip()
    if name in ALIAS:
        return ALIAS[name]
    if " — " in name:
        head = name.split(" — ")[0].strip()
        if head in ALIAS:
            return ALIAS[head]
    for alias in PREFIX_ALIASES:
        if name.startswith(alias) and len(name) > len(alias) and name[len(alias)] in "（(：: —":
            return ALIAS[alias]
    return None


# 早期「每主题一节」的历史写法：按内容归到规范类（一次性映射，不再新增）
LEGACY_SECTIONS = [
    ("🖥️ 桌面启动器 UI 改进", "🎨 界面与交互"),
    ("🎨 应用入口优化", "⚡ 性能"),
    ("🏗️ 架构重构：Repository 化", "♻️ 重构"),
    ("Backend API", "🔧 变更"),
]


# 英文壳换中文前缀（只改 ### 行，正文不动）
LEGACY_PREFIXES = [
    ("Added — ", "✨ 新增 — "),
    ("Changed — ", "🔧 变更 — "),
    ("Fixed — ", "🐛 修复 — "),
    ("Fixed（", "🐛 修复（"),
    ("Docs（", "📝 文档与工程（"),
    ("Added（续）", "✨ 新增（续）"),
    ("Added（", "✨ 新增（"),
    ("Changed（", "🔧 变更（"),
    ("Changed", "🔧 变更"),
    ("Added", "✨ 新增"),
    ("Fixed", "🐛 修复"),
    ("Docs", "📝 文档与工程"),
    ("Removed", "🗑 移除"),
    ("Security", "🔒 安全"),
]


def migrate_name(name):
    """把一个历史分节名翻成规范名；无映射返回 None。"""
    name = name.strip()
    for old, canon in LEGACY_SECTIONS:
        if name.startswith(old):
            return canon
    for old, new in LEGACY_PREFIXES:
        if name.startswith(old):
            return new + name[len(old):]
    return None


def order_of(name):
    canon = canonical(name)
    return ORDER.get(canon, len(SECTIONS)) if canon else len(SECTIONS)


def trim_tail(lines):
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def trim_head(lines):
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    return out


def render_block(block):
    out = [block.head]
    first = block.sections[0].start if block.sections else block.end
    last = block.sections[-1].start if block.sections else block.end
    for idx, line in block.extra:
        if idx < first:
            out.append(line)
    for sec in block.sections:
        out.extend(sec.lines)
    for idx, line in block.extra:
        if idx > last:
            out.append(line)
    if block.sections:
        out.append("")
    return trim_tail(out) + [""]


def write_lines(path, lines, dry_run):
    text = NL.join(lines)
    if not text.endswith(NL):
        text += NL
    if dry_run:
        old = path.read_text(encoding="utf-8")
        sys.stdout.writelines(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=path.name + " (当前)",
                tofile=path.name + " (写入后)",
                n=2,
            )
        )
        return
    if not BAK.exists():
        shutil.copy2(path, BAK)
    path.write_text(text, encoding="utf-8")


def write_blocks(path, lines, blocks, dry_run, drop=None):
    """一次性重建整个文件：按 blocks 顺序重渲染，其余行原样。drop 里的块直接丢弃。

    为什么必须一次写完：逐块写回时，写第一块会让后面所有块的行号偏移，下一块就会写到错位置
    （行被重复或吞掉）。所以所有命令都先把改动做完，再调这里写一次。
    """
    drop = drop or set()
    out = []
    cursor = 0
    for block in sorted(blocks, key=lambda b: b.start):
        out.extend(lines[cursor:block.start])
        if id(block) not in drop:
            out.extend(render_block(block))
        cursor = block.end
    out.extend(lines[cursor:])
    write_lines(path, out, dry_run)
    return out


def load():
    if not CHANGELOG.exists():
        raise Fail("找不到 " + str(CHANGELOG))
    return CHANGELOG, CHANGELOG.read_text(encoding="utf-8").splitlines()


def norm_version(text):
    v = text.strip().strip("[]").strip()
    if v.lower() in ("未发布", "unreleased", "un", "-"):
        return UNRELEASED
    return v if v.startswith("v") else "v" + v


def get_block(blocks, version):
    want = norm_version(version)
    for b in blocks:
        if norm_version(b.version) == want:
            return b
    known = "、".join(b.version for b in blocks[:5])
    raise Fail("没有版本块 [" + want + "]（现有前几个：" + known + "）")


def get_section(block, name):
    canon = canonical(name)
    if canon is None:
        raise Fail("不认识的分类 " + repr(name) + "，先用 sections 看现有标题")
    for sec in block.sections:
        if canonical(sec.name) == canon:
            return sec
    return None


def read_text(src, inline):
    if inline is not None:
        return inline
    if src == "-":
        return sys.stdin.read()
    p = Path(src)
    if not p.exists():
        raise Fail("读不到 " + str(p))
    return p.read_text(encoding="utf-8")


def normalize_entry(raw):
    """把任意缩进的文本规范成一条条目：'- ' 起，续行两个空格，条目内空行保留。"""
    lines = [ln.rstrip() for ln in raw.strip(NL).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise Fail("条目是空的")
    head = lines[0].strip()
    if not head.startswith("- "):
        head = "- " + head
    out = [head]
    for ln in lines[1:]:
        if not ln.strip():
            out.append("")
        elif ln[:1] in (" ", TAB):
            out.append(ln.replace(TAB, "  ", 1))
        else:
            out.append("  " + ln)
    return out


def append_entry(sec, entry):
    body = trim_tail(sec.lines)
    sec.lines = body + [""] + entry + [""]


def new_section(block, canon, entry):
    sec = Section(canon, ["### " + canon, ""] + entry + [""], -1)
    pos = len(block.sections)
    for i, other in enumerate(block.sections):
        if order_of(other.name) > ORDER[canon]:
            pos = i
            break
    block.sections.insert(pos, sec)
    return sec


def match_entry(section, needle):
    hits = [it for it in section.items() if needle in it[2]]
    if not hits:
        raise Fail("没有匹配 " + repr(needle) + " 的条目")
    if len(hits) > 1:
        preview = NL.join("  " + it[2][:80] for it in hits)
        raise Fail(repr(needle) + " 命中 " + str(len(hits)) + " 条，请给更长的前缀：" + NL + preview)
    return hits[0]


def merge_sections(block, reorder=True):
    """合并重复分节（含别名指向同一节），可重排。只搬行区间，不改正文。返回报告行。"""
    report = []
    grouped = {}
    merged = []
    unknown = False
    for sec in block.sections:
        canon = canonical(sec.name)
        if canon is None:
            unknown = True
            merged.append(sec)
            continue
        if canon in grouped:
            first = grouped[canon]
            body = trim_head(trim_tail(sec.lines)[1:])
            if body:
                first.lines = trim_tail(first.lines) + [""] + body + [""]
                report.append("  合并 " + sec.name + " -> " + first.name + "（" + str(len(body)) + " 行）")
            else:
                report.append("  合并空分节 " + sec.name + " -> " + first.name)
        else:
            if sec.name != canon:
                old_name = sec.name
                sec.name = canon
                sec.lines = ["### " + canon] + sec.lines[1:]
                report.append("  改名 " + old_name + " -> " + canon)
            grouped[canon] = sec
            merged.append(sec)
    if reorder and not unknown:
        before = [sec.name for sec in merged]
        merged.sort(key=lambda s: order_of(s.name))
        if [sec.name for sec in merged] != before:
            report.append("  重排分节顺序")
    elif unknown:
        report.append("  有不认识的分节，跳过重排")
    block.sections = merged
    return report


def format_section(sec):
    """标题后一个空行、结尾一个空行；条目之间的疏密按原文保留（历史是紧凑列表，不强加空行）。"""
    body = trim_head(sec.lines[1:])
    sec.lines = ["### " + sec.name, ""] + trim_tail(body) + [""]


def cmd_format(args):
    path, lines = load()
    blocks = parse(lines)
    targets = blocks if args.all else [b for b in blocks if not b.released]
    dirty = []
    for block in targets:
        before = [list(sec.lines) for sec in block.sections]
        for sec in block.sections:
            format_section(sec)
        if [list(sec.lines) for sec in block.sections] != before:
            dirty.append(block)
    if not dirty:
        print("空行已经是规范的")
        return 0
    print("规范空行：" + "、".join(b.version for b in dirty))
    write_blocks(path, lines, dirty, args.dry_run)
    if args.dry_run:
        return 0
    return cmd_check(argparse.Namespace(all=args.all, fix=False, quiet=False))


def cmd_rename(args):
    """把历史分节名翻成规范名（只改 ### 行）。"""
    path, lines = load()
    blocks = parse(lines)
    targets = blocks if args.all else [b for b in blocks if not b.released]
    changed = []
    for block in targets:
        for sec in block.sections:
            new = migrate_name(sec.name)
            if new and new != sec.name:
                changed.append((block.version, sec.name, new))
                sec.name = new
                sec.lines = ["### " + new] + sec.lines[1:]
    if not changed:
        print("分节名已经是规范的")
        return 0
    for version, old, new in changed:
        print("  [" + version + "] " + old + " -> " + new)
    print("共 " + str(len(changed)) + " 个分节改名")
    touched = [b for b in targets if any(b.version == v for v, _o, _n in changed)]
    write_blocks(path, lines, touched, args.dry_run)
    if args.dry_run:
        return 0
    return cmd_check(argparse.Namespace(all=args.all, fix=False, quiet=False))


def cmd_sections(args):
    _, lines = load()
    blocks = parse(lines)
    seen = {}
    for b in blocks:
        for sec in b.sections:
            canon = canonical(sec.name) or sec.name
            seen.setdefault(canon, [])
            seen[canon].append(sec.name)
    print("规范顺序（--section 可写别名，落盘用规范名）：")
    print("")
    for canon, _aliases in SECTIONS:
        names = seen.get(canon, [])
        variant = sorted({n for n in names if n != canon})
        extra = ("   历史写法：" + " / ".join(variant)) if variant else ""
        print("  " + str(ORDER[canon]) + ". " + canon.ljust(16) + " 出现 " + str(len(names)).rjust(2) + " 次" + extra)
    for canon in [k for k in seen if k not in ORDER]:
        print("  ?  " + canon.ljust(16) + " 出现 " + str(len(seen[canon])).rjust(2) + " 次  （不认识）")
    return 0


def cmd_add(args):
    path, lines = load()
    blocks = parse(lines)
    block = get_block(blocks, args.version)
    if block.released and not args.force:
        raise Fail("[" + block.version + "] 已发布，不往里补条目（确需追加加 --force）")
    entry = normalize_entry(read_text(args.src, args.text))
    sec = get_section(block, args.section)
    if sec is None:
        sec = new_section(block, canonical(args.section), entry)
        print("该节不存在，按规范位置新建")
    else:
        for _s, _e, first in sec.items():
            if first[:60] == entry[0][:60]:
                print("已存在同一条目，跳过：" + entry[0][:80])
                return 0
        append_entry(sec, entry)
    write_blocks(path, lines, [block], args.dry_run)
    if args.dry_run:
        return 0
    print("已写入 [" + block.version + "] " + sec.name)
    return cmd_check(argparse.Namespace(all=False, fix=False, quiet=False))


def cmd_edit(args):
    path, lines = load()
    blocks = parse(lines)
    block = get_block(blocks, args.version)
    candidates = [get_section(block, args.section)] if args.section else block.sections
    for s in candidates:
        if s is None:
            continue
        try:
            start, end, _first = match_entry(s, args.match)
        except Fail:
            continue
        s.lines[start:end] = normalize_entry(read_text(args.src, args.text)) + [""]
        write_blocks(path, lines, [block], args.dry_run)
        if args.dry_run:
            return 0
        print("已替换 [" + block.version + "] " + s.name)
        return cmd_check(argparse.Namespace(all=False, fix=False, quiet=False))
    raise Fail("[" + block.version + "] 里没有含 " + repr(args.match) + " 的条目")


def cmd_move(args):
    path, lines = load()
    blocks = parse(lines)
    block = get_block(blocks, args.version)
    src = get_section(block, args.section)
    if src is None:
        raise Fail("[" + block.version + "] 没有 " + str(canonical(args.section)) + " 这一节")
    start, end, _first = match_entry(src, args.match)
    entry = trim_tail(src.lines[start:end])
    del src.lines[start:end]
    dst = get_section(block, args.to)
    if dst is None:
        new_section(block, canonical(args.to), entry)
    else:
        append_entry(dst, entry)
    write_blocks(path, lines, [block], args.dry_run)
    if args.dry_run:
        return 0
    print("已移动：" + src.name + " -> " + str(canonical(args.to)))
    return cmd_check(argparse.Namespace(all=False, fix=False, quiet=False))


def cmd_delete(args):
    path, lines = load()
    blocks = parse(lines)
    block = get_block(blocks, args.version)
    for s in block.sections:
        try:
            start, end, first = match_entry(s, args.match)
        except Fail:
            continue
        del s.lines[start:end]
        write_blocks(path, lines, [block], args.dry_run)
        print("已删除 [" + block.version + "] " + s.name + "：" + first[:80])
        return 0
    raise Fail("[" + block.version + "] 里没有含 " + repr(args.match) + " 的条目")


def cmd_dedupe(args):
    path, lines = load()
    blocks = parse(lines)
    groups = {}
    for block in blocks:
        groups.setdefault(block.version, []).append(block)
    targets = [(v, bs) for v, bs in groups.items() if len(bs) > 1]
    if args.version:
        want = norm_version(args.version)
        targets = [(v, bs) for v, bs in targets if norm_version(v) == want]
    if not targets:
        print("没有重复的版本块")
        return 0
    drop = set()
    for version, bs in targets:
        first = bs[0]
        for extra_block in bs[1:]:
            if lines[first.start:first.end] == lines[extra_block.start:extra_block.end]:
                print("  [" + version + "] 整块重复 " + str(extra_block.end - extra_block.start) + " 行，直接丢弃")
            else:
                for sec in extra_block.sections:
                    canon = canonical(sec.name)
                    same = None
                    if canon is not None:
                        for have in first.sections:
                            if canonical(have.name) == canon:
                                same = have
                                break
                    if same is not None:
                        body = trim_head(trim_tail(sec.lines)[1:])
                        if body:
                            same.lines = trim_tail(same.lines) + [""] + body + [""]
                    else:
                        first.sections.append(sec)
                print("  [" + version + "] 内容并进第一个块")
            drop.add(id(extra_block))
        merge_sections(first)
    print("合并 " + str(len(drop)) + " 个重复块")
    write_blocks(path, lines, blocks, args.dry_run, drop=drop)
    if args.dry_run:
        return 0
    return cmd_check(argparse.Namespace(all=True, fix=False, quiet=False))


def cmd_merge(args):
    path, lines = load()
    blocks = parse(lines)
    targets = blocks if args.all else [get_block(blocks, args.version)]
    dirty = []
    for block in targets:
        report = merge_sections(block)
        if report:
            dirty.append(block)
            print("[" + block.version + "]")
            print(NL.join(report))
    if not dirty:
        print("没有需要整理的分节")
        return 0
    write_blocks(path, lines, dirty, args.dry_run)
    if args.dry_run:
        return 0
    return cmd_check(argparse.Namespace(all=args.all, fix=False, quiet=False))


def semver_key(version):
    base = version.split("-")[0]
    nums = []
    cur = ""
    for ch in base:
        if ch.isdigit():
            cur += ch
        elif cur:
            nums.append(int(cur))
            cur = ""
    if cur:
        nums.append(int(cur))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def check_block(block, strict):
    errors = []
    warns = []
    seen = {}
    last_order = -1
    for sec in block.sections:
        canon = canonical(sec.name)
        if canon is None:
            if strict:
                errors.append("[" + block.version + "] 分节 " + repr(sec.name) + " 不认识（先加进 SECTIONS）")
            else:
                warns.append("[" + block.version + "] 历史分节 " + repr(sec.name) + " 不在规范表里")
            continue
        if sec.name != canon:
            warns.append("[" + block.version + "] 用了历史写法 " + repr(sec.name) + "，规范名 " + repr(canon))
        if canon in seen:
            errors.append("[" + block.version + "] 分节重复：" + repr(canon) + " 出现两次（merge 可合并）")
        seen[canon] = sec
        items = sec.items()
        if not items:
            errors.append("[" + block.version + "] 分节 " + repr(sec.name) + " 是空的")
        o = ORDER[canon]
        if o < last_order:
            (errors if strict else warns).append("[" + block.version + "] 分节顺序不对：" + repr(sec.name) + " 应更靠前")
        last_order = max(last_order, o)
        body = sec.lines[1:]
        if strict and body and body[0].strip():
            errors.append("[" + block.version + "] " + sec.name + " 标题后缺空行")
        for ln in body:
            if ln.startswith("- ") or not ln.strip():
                continue
            # 分节里的 Markdown 结构：四级标题、分隔线、引用、代码围栏、表格
            if ln.startswith(("#### ", "---", ">", "|", "```")):
                continue
            if ln[:1] not in (" ", TAB):
                (errors if strict else warns).append("[" + block.version + "] " + sec.name + " 有散行：" + repr(ln[:60]))
    return errors, warns


def cmd_check(args):
    _, lines = load()
    blocks = parse(lines)
    if not blocks:
        print("✘ 一个版本块都没解析出来")
        return 1
    errors = []
    warns = []
    if blocks[0].released:
        errors.append("文件最上面应该先是 [未发布]")
    unreleased = [b for b in blocks if not b.released]
    if len(unreleased) > 1:
        errors.append("[未发布] 出现了 " + str(len(unreleased)) + " 次，只能有一个")
    released = [b for b in blocks if b.released]
    for prev, cur in zip(released, released[1:]):
        if semver_key(cur.version) > semver_key(prev.version):
            errors.append("版本块顺序不对：" + prev.version + " 排在 " + cur.version + " 上面（应新到旧）")
    seen_versions = {}
    for b in released:
        if b.version in seen_versions:
            errors.append("版本块重复：[" + b.version + "] 出现了两次（内容应并进同一块）")
        seen_versions[b.version] = True
    targets = blocks if args.all else [b for b in blocks if not b.released]
    for block in targets:
        e, w = check_block(block, strict=not block.released)
        errors.extend(e)
        warns.extend(w)
    if args.fix:
        dirty = []
        for block in [b for b in blocks if not b.released]:
            if merge_sections(block):
                dirty.append(block)
        if dirty:
            write_blocks(CHANGELOG, lines, dirty, dry_run=False)
            print("已整理 [未发布]（合并重复分节 + 规范顺序 + 别名归一），备份在 CHANGELOG.md.bak")
            print("")
            return cmd_check(argparse.Namespace(all=args.all, fix=False, quiet=args.quiet))
    if args.quiet:
        return 1 if errors else 0
    for w in warns:
        print("! " + w)
    for e in errors:
        print("✘ " + e)
    if not errors:
        scope = "全部版本块" if args.all else "[未发布]"
        tail = ("（" + str(len(warns)) + " 条提示）") if warns else ""
        print("结构正常：" + scope + "，共 " + str(len(blocks)) + " 个版本块" + tail)
        return 0
    print("")
    print("共 " + str(len(errors)) + " 个问题。加 --fix 可自动整理 [未发布]，历史块用 merge --all。")
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="CHANGELOG 写入与校验（唯一入口）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sections", help="列出分节规范名、别名与出现次数")
    p.set_defaults(func=cmd_sections)

    p = sub.add_parser("add", help="往某版本某分节末尾追加一条")
    p.add_argument("-v", "--version", required=True)
    p.add_argument("-s", "--section", required=True)
    p.add_argument("--from", dest="src", default="-", help="条目正文文件，- 表示 stdin")
    p.add_argument("--text", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="允许写进已发布的版本块")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("edit", help="按前缀替换一条")
    p.add_argument("-v", "--version", required=True)
    p.add_argument("--match", required=True)
    p.add_argument("-s", "--section", default=None)
    p.add_argument("--from", dest="src", default="-")
    p.add_argument("--text", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("move", help="把一条改到另一个分节")
    p.add_argument("-v", "--version", required=True)
    p.add_argument("-s", "--section", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--match", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("delete", help="删掉一条")
    p.add_argument("-v", "--version", required=True)
    p.add_argument("--match", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("merge", help="合并重复分节并按规范顺序重排")
    p.add_argument("-v", "--version", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("rename", help="把历史分节名翻成规范名")
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("format", help="规范空行（标题后、条目之间）")
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_format)

    p = sub.add_parser("dedupe", help="合并重复的版本块")
    p.add_argument("-v", "--version", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("check", help="校验结构")
    p.add_argument("--all", action="store_true")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Fail as exc:
        print("✘ " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
