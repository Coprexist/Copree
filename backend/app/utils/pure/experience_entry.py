"""经历条目的纯函数 —— 无 IO、无 DB 依赖（对齐 memory_entry.py）

与记忆投递的差别只有一处：记忆会被改（改正文、改锚点），所以 ref 里带内容指纹，
改过就补一条「以它为准」；经历行写成即定，ref 只认 id，于是「同一上下文投过就不再投」
是结构的必然，不靠额外状态，也不需要指纹那一套。

一串 id 就够判断「这个上下文看过哪几行」：账本条目只增不改，ref 是服务端字段，
渲染给模型的 content 里不出现 id。
"""
from __future__ import annotations

from app.utils.pure.history import make_entry

EXP_KIND = "experience"
EXP_REF_PREFIX = "exp:"

# 一行经历的正文上限：它是「指针」，说清在哪儿干了什么就够，原文细节用 search_messages 回查
EXP_BODY_CHARS = 400

# 每个上下文第一次投经历时说一次边界（之后不再重复；条目本身留在账本里直到解锁）
EXPERIENCE_LEAD = "这些是你自己在别的会话里做过的事，不是你在这里做的："


def experience_ref(exp) -> str:
    """账本里的键：`exp:<id>`。"""
    return f"{EXP_REF_PREFIX}{int((exp or {}).get('id') or 0)}"


def delivered_experiences(entries) -> set[str]:
    """账本里投过的经历 id 集合（同一 id 出现多次也只算一次）。"""
    out: set[str] = set()
    for e in entries or []:
        ref = str((e or {}).get("ref") or "")
        if not ref.startswith(EXP_REF_PREFIX):
            continue
        exp_id = ref[len(EXP_REF_PREFIX):]
        if exp_id.isdigit():
            out.add(exp_id)
    return out


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def format_experience_body(exp) -> str:
    """单条经历的正文：`**11-01 学生群**` + 一行摘要。注入文案的唯一来源。

    日期与场景进标题（一眼知道「哪儿、什么时候」），摘要进正文；换行压成空格——
    这是账本里的一条，摆成多段会把相邻条目挤散。
    """
    exp = exp or {}
    label = str(exp.get("scene_label") or "").strip() or str(exp.get("scene_key") or "").strip()
    when = str(exp.get("when_label") or "").strip()
    head = " ".join(x for x in (when, label) if x)
    summary = " ".join(str(exp.get("summary") or "").split())
    lines = [f"**{head}**" if head else ""]
    if summary:
        lines.append(_clip(summary, EXP_BODY_CHARS))
    return "\n".join(l for l in lines if l)


def make_experience_entry(exp, *, lead: bool = False) -> dict:
    """一条经历的账本条目：第一次进这个上下文时投，之后不再投。

    `lead` 只在「这个上下文第一次见到经历」时打开，把那句边界说一次：
    经历讲的是别处的事，别当成这里发生的，也别拿它回应这里的人。每条都带一遍就成了噪音，
    所以一次上下文说一次就够（条目进账本后不会消失，除非解锁）。

    `drop_on_unlock` 让它在解锁（compact / 超时压缩）时离场——那之后这个上下文就算
    「没看过」，再次相干时可以重投；这正是「compact 了不算」的实现方式。
    """
    body = format_experience_body(exp)
    if lead:
        body = EXPERIENCE_LEAD + "\n" + body
    return make_entry(
        EXP_KIND, "【想起】" + body, actor="system",
        ref=experience_ref(exp), flags={"drop_on_unlock": True},
    )


def belongs_elsewhere(exp, context_ref: str) -> bool:
    """这行该不该投进这个上下文：本场自己产出的行不回灌本场。

    本场的 key_note 本来就在它的上下文里（[上一轮交接] 条目），再补一条「【想起】」
    说的是同一件事，纯噪声；而且它不满足「在别处干过什么」这个定义。
    """
    scene_key = str((exp or {}).get("scene_key") or "")
    return not scene_key or scene_key != str(context_ref or "")
