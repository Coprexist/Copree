"""
技能目录与要点：纯渲染，不碰数据库、不碰注册表。

为什么单列成纯函数：这段文字每轮进前缀，字节必须**完全确定**——排序一旦依赖
set/dict 遍历顺序，同一状态两次渲染就可能不同，整段前缀就白命中不了。
所以渲染只吃排好序的数据，自己不做任何"按当前状态再挑一遍"的决定。

三层来源：
- 段元数据（SKILL_SEGMENT_META）：铁律/场景这类平台口径
- 工具插件（ToolPlugin.usage_notes）：这个工具怎么用才对
- 工具定义（description/parameters）：一行用途 + 最小调用形态
"""
from __future__ import annotations

# 段序：单一来源。渲染顺序与"重复表述取谁"都看它，不能改用注册顺序
# （插件加载顺序会随目录扫描变化，一抖就是整段 miss）。
SEGMENT_ORDER: tuple[str, ...] = (
    "chat_social",
    "file_operations",
    "memory",
    "group_management",
    "self_config",
    "self_management",
)

# 要点层级：值小者先渲染；预算不够时从尾部砍，砍掉的必须是最不要紧的
NOTE_LEVELS: dict[int, str] = {100: "铁律", 200: "场景", 300: "用法", 400: "提示"}
LEVEL_MIN, LEVEL_MAX = 100, 400

# 目录每行描述的字数上限（工具名与调用形态不截：目录丢了工具等于没这个能力）
CATALOG_DESC_WIDTH = 46
NOTES_BUDGET = 1500


def segment_index(segment: str) -> int:
    """段序；未知段排最后（按名字再定序，保证确定）"""
    try:
        return SEGMENT_ORDER.index(segment)
    except ValueError:
        return len(SEGMENT_ORDER)


def call_shape(properties: dict | None, required: list | None) -> str:
    """最小调用形态：f(a, b?, c) —— 必填在前、可选在后，顺序照声明。

    properties 是 ToolPlugin.parameters（参数名 → schema），不是整份 JSON Schema。
    """
    props = properties or {}
    if not props:
        return ""
    req = [str(r) for r in (required or []) if str(r) in props]
    opt = [str(k) for k in props if str(k) not in req]
    return "(" + ", ".join([*req, *[f"{k}?" for k in opt]]) + ")"


def _one_line(text: str, width: int) -> str:
    """压成一行并截断：目录是索引，不是说明书"""
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[: max(1, width - 1)] + "…"


def scene_ok(scenes, scene: str) -> bool:
    """空 = 全场景；否则必须命中当前场景"""
    if not scenes:
        return True
    if not scene:
        return True
    return scene in scenes


def render_catalog(entries: list[dict], allowed: set[str], *, width: int = CATALOG_DESC_WIDTH) -> list[str]:
    """技能目录：每段一段头 + 每个可用工具一行（名字 + 用途 + 调用形态）。

    entries 必须已按 (段序, 工具名) 排好——调用方负责排序，这里只渲染。
    """
    lines: list[str] = []
    current = None
    for e in entries:
        if e["name"] not in allowed:
            continue
        if e["segment"] != current:
            current = e["segment"]
            lines.append("")
            lines.append(f"【{e['segment_name']}】{_one_line(e['segment_desc'], 60)}")
        shape = call_shape(e.get("parameters"), e.get("required"))
        head = f"{e['name']}{shape}"
        lines.append(f"  {head:<28} {_one_line(e.get('description'), width)}")
    return lines


def collect_notes(entries: list[dict], segment_notes: list[dict], *, scene: str) -> list[tuple]:
    """把两处来源的要点合成一条有序列表：[(层级, 段序, 工具名, 序号, 文本)]

    去重按「同一句话只留最靠前的那条」：层级高的优先，同层级段序靠前的优先。
    """
    seen: set[str] = set()
    raw: list[tuple] = []

    def add(level: int, seg: str, tool: str, order: int, text: str):
        body = " ".join(str(text or "").split())
        if not body or body in seen:
            return
        seen.add(body)
        raw.append((int(level), segment_index(seg), tool, order, body))

    for n in segment_notes:
        if not scene_ok(n.get("scenes"), scene):
            continue
        for i, text in enumerate(n.get("notes") or ()):
            add(n.get("level", 200), n.get("segment", ""), "", i, text)
    for e in entries:
        if not scene_ok(e.get("usage_scenes"), scene):
            continue
        for i, (level, text) in enumerate(e.get("usage_notes") or ()):
            add(level, e["segment"], e["name"], i, text)
    # 只按 (层级, 段序, 工具名, 序号) 排：同层级按声明顺序，不按正文的字典序——
    # 正文里中英文混排会让「先后」变成 ASCII 巧合，作者写的顺序才是意图。
    return sorted(raw, key=lambda n: n[:4])


def render_notes(notes: list[tuple], *, budget: int = NOTES_BUDGET) -> tuple[list[str], int]:
    """要点正文 + 被预算砍掉的条数（砍掉的要明说，不静默给半截）"""
    lines: list[str] = []
    used = 0
    dropped = 0
    for level, _seg, _tool, _order, text in notes:
        tag = NOTE_LEVELS.get(level) or NOTE_LEVELS.get(
            max(LEVEL_MIN, min(LEVEL_MAX, level)), str(level))
        line = f"·（{tag}）{text}"
        if used + len(line) > budget:
            dropped += 1
            continue
        used += len(line)
        lines.append(line)
    return lines, dropped
