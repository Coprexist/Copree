"""记忆注入条目的纯函数 —— 无 IO、无 DB 依赖。

记忆是**补丁**，不是重播：一条记忆第一次进上下文时投全文，之后只在这条记忆真的
改过时才补一条「以它为准」。判据是**内容指纹**，不是时间戳——权重衰减、每日整理、
被想起时刷新时间基准都会写记忆行，拿时间戳当版本会把它们全当成"改过了"。

为什么指纹要进 ref：账本条目只增不改，要回答"这条上下文手里是哪一版"，只能让条目
自己带着版本走。ref 是账本的服务端字段（渲染给模型的只有 content），不进请求体。
"""
from __future__ import annotations

import hashlib

from app.utils.pure.history import make_entry

# 记忆是一条"顺手记下的事"，不是文档：注入正文超过这个长度就截断
MEM_BODY_CHARS = 300
MEM_KIND = "memory"
MEM_REF_PREFIX = "mem:"


def memory_fingerprint(mem) -> str:
    """内容指纹：标题 + 正文 + 锚点。

    只认"这条记忆说了什么、该在哪儿被想起"。权重、最后被想起的时间、归档状态都不算
    ——它们变了不需要打扰 AI。
    """
    mem = mem or {}
    parts = [
        str(mem.get("title") or "").strip(),
        str(mem.get("content") or "").strip(),
        ",".join(mem.get("session_refs") or []),
        ",".join(mem.get("session_foci") or []),
        ",".join(mem.get("semantic_foci") or []),
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:8]


def memory_ref(mem) -> str:
    """账本里的键：mem:<id>@<指纹>。"""
    return f"{MEM_REF_PREFIX}{(mem or {}).get('id')}@{memory_fingerprint(mem)}"


def delivered_memories(entries) -> dict[str, str]:
    """账本里投过的记忆 → {id: 指纹}（同一 id 只留最后一次投的版本）。"""
    out: dict[str, str] = {}
    for e in entries or []:
        ref = str((e or {}).get("ref") or "")
        if not ref.startswith(MEM_REF_PREFIX):
            continue
        mem_id, _, fp = ref[len(MEM_REF_PREFIX):].partition("@")
        if mem_id:
            out[mem_id] = fp
    return out


def format_memory_body(mem) -> str:
    """单条记忆的正文（标题 + 内容），注入文案的唯一来源。"""
    mem = mem or {}
    title = str(mem.get("title") or "").strip()
    lines = [f"**{title}**" if title else ""]
    content = str(mem.get("content") or "").strip()
    if content:
        if len(content) > MEM_BODY_CHARS:
            content = content[:MEM_BODY_CHARS].rstrip() + "…"
        lines.append(content)
    return "\n".join(l for l in lines if l)


def make_memory_entry(mem, *, changed: bool = False) -> dict:
    """一条记忆的账本条目。

    第一次投的是全文；改过的投的是"以新的为准"——旧版还留在历史中段（改不了，
    改了就是改中段、断前缀），所以只需补一句作废 + 新内容，不必重投全文。
    """
    head = "【记忆更新】这条改动过，以它为准：" if changed else "【想起】"
    return make_entry(
        MEM_KIND, head + format_memory_body(mem), actor="system",
        ref=memory_ref(mem), flags={"drop_on_unlock": True},
    )
