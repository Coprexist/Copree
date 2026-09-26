"""记忆的文字约束 — 无 IO、无 DB 依赖。

一条记忆要能被快速想起来，标题得是一句概括、内容得是要点，而不是把原文搬进来。
这里只给"多少字以内"和"超了怎么说"：**超过不动内容**，只把提示交回给 AI，
让它自己下一轮收敛——平台不替它改字。
"""
from __future__ import annotations

MAX_TITLE_CHARS = 30
MAX_CONTENT_CHARS = 200


def over_limit(name: str, text: str, limit: int) -> str:
    """字段超限的提示；没超返回空串。

    措辞要说清两件事：这条已经按原样存下了，以及下次该压到多少。
    """
    size = len(text or "")
    if size <= limit:
        return ""
    return f"{name} {size} 字，超过 {limit} 字上限——这条已按原样存下，下次请压到 {limit} 字以内"


def shape_warnings(title: str, content: str) -> list[str]:
    """一条记忆（标题 + 内容）的超限提示。"""
    return [w for w in (over_limit("标题", title, MAX_TITLE_CHARS),
                        over_limit("内容", content, MAX_CONTENT_CHARS)) if w]
