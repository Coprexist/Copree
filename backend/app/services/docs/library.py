"""
平台文档库 — AI 能查的那几份文档的唯一来源。

为什么单独成模块：主站 AI 的 read_manual、创建助手（写人设前要查写法）、群视界机器人
（查平台功能）读的是同一批 markdown，各自实现一遍检索必然走形——截断长度、目录阈值、
找不到时的说法会各写一套。这里只保留一份：路径解析、目录提取、关键词命中。

路径不写死：容器里 docs 是单独只读挂载的（/app/docs），本地从源码跑则在 backend 同级的
../docs。找不到时返回明确的位置清单，方便部署侧判断是没挂载还是挂错。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 允许 AI 读的文档：id 是稳定键（工具参数用它，改标题不动 id），path 相对 docs 根
DOC_DEFS: dict[str, dict] = {
    "manual": {
        "title": "用户手册",
        "path": "guides/用户手册.md",
        "desc": "平台功能与操作：彩色文字、Markdown、文件转发、闹钟、记忆、联邦、权限",
    },
    "ai-guide": {
        "title": "创建 AI 参考",
        "path": "guides/创建AI参考.md",
        "desc": "AI 人设怎么写：四层结构、长度预算、去 AI 感清单、可复制模板、发布前自检",
    },
    "world-skill": {
        "title": "世界 AI 技能参考",
        "path": "guides/世界AI技能参考.md",
        "desc": "给 AI 居民颁布世界侧技能：五条原则、manifest/ctx 协议、沙箱边界、骨架示例、自检清单",
    },
}

# 目录概览与关键词检索的截断：AI 读到的内容进上下文，宁短勿长
_TOC_MAX_LINES = 40
_TOC_MAX_CHARS = 2000
_HITS_MAX = 8
_HITS_MAX_CHARS = 4000
_SNIPPET_HEAD = 2   # 命中行前带几行
_SNIPPET_TAIL = 6   # 命中行后带几行


def candidates() -> list[Path]:
    """文档根目录的候选位置，按优先级。环境变量优先——别的部署能把它挂到任意位置。"""
    cands: list[Path] = []
    env = os.environ.get("COPREE_DOCS_DIR", "").strip()
    if env:
        cands.append(Path(env))
    cands.append(Path("/app/docs"))       # 容器：docker-compose 把 ./docs 只读挂到这里
    cands.append(Path("/copree/docs"))    # 老部署约定的位置，留着以免挂载方式不同
    # 源码运行：backend/app/services/docs/library.py → 上溯四级就是仓库根
    cands.append(Path(__file__).resolve().parents[4] / "docs")
    return cands


def resolve_root() -> Path | None:
    """第一个确实有文档的候选目录；都没有返回 None（不返回猜测值，报错时要说清找过哪）。"""
    for cand in candidates():
        if (cand / "guides" / "用户手册.md").is_file():
            return cand
    return None


def list_docs() -> list[dict]:
    root = resolve_root()
    return [
        {
            "id": doc_id,
            "title": spec["title"],
            "desc": spec["desc"],
            "available": bool(root and (root / spec["path"]).is_file()),
        }
        for doc_id, spec in DOC_DEFS.items()
    ]


def _toc(content: str) -> str:
    """目录：一到三级标题。够 AI 判断该查什么，又不至于把整篇塞进上下文。"""
    lines = [
        line.strip()
        for line in content.split("\n")
        if line.strip().startswith(("# ", "## ", "### "))
    ]
    text = "\n".join(lines[:_TOC_MAX_LINES])
    if len(text) > _TOC_MAX_CHARS:
        text = text[:_TOC_MAX_CHARS] + "\n\n…（省略）"
    return text


_SPLIT = re.compile(r"[\s,，、/|]+")


def keywords(keyword: str) -> list[str]:
    """关键词拆词：模型常写「人设 四层结构」，文档里没有这一整串，整串去比必然落空。"""
    return [t.lower() for t in _SPLIT.split((keyword or "").strip()) if t]


def _search(content: str, tokens: list[str]) -> str:
    """关键词命中：按行定位，命中行前后各带几行，避免只有半句话。

    多词时保留命中词最多的那些行（同分按先后）：要求每个词都落在同一行，等于把
    「人设 四层结构」这种写法判死——2026-10-19 创建助手连查两次落空，白烧两轮。
    """
    lines = content.split("\n")
    sections: list[str] = []
    section = ""
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            section = line.strip()
        sections.append(section)

    low_lines = [line.lower() for line in lines]
    # 稀有词优先：多词时「人设」满篇都是、「四层结构」只出现在该出现的几节，
    # 光按命中词数排，结果会被满篇出现的常用词占满。
    rarity = {t: 1.0 / max(1, sum(1 for low in low_lines if t in low)) for t in tokens}
    scored = []
    for i, low in enumerate(low_lines):
        matched = [t for t in tokens if t in low]
        if matched:
            scored.append((len(matched), sum(rarity[t] for t in matched), i))
    if not scored:
        return ""
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    # 权重掉到最高那条四分之一以下的不带：那种行只是顺手提了一句关键词，带上去只会把
    # 真正的答案挤出上下文（实测「人设 四层结构」会多带三条文档头）
    floor = scored[0][1] * 0.25
    kept = [s for s in scored if s[1] >= floor]
    if kept:
        scored = kept
    hits = [
        f"【{sections[i]}】\n" + "\n".join(lines[max(0, i - _SNIPPET_HEAD): i + _SNIPPET_TAIL])
        for _, _, i in scored[:_HITS_MAX]
    ]
    text = "\n\n---\n\n".join(hits)
    if len(text) > _HITS_MAX_CHARS:
        text = text[:_HITS_MAX_CHARS] + "\n\n…（更多结果被截断）"
    return text


def read(doc_id: str = "manual", keyword: str = "") -> dict:
    """读一份文档：不传关键词给目录，传了给命中片段。

    返回统一是 {ok, doc, title, ...}：工具层与创建助手共用，出错的措辞只有一份。
    """
    spec = DOC_DEFS.get(doc_id or "manual")
    if spec is None:
        return {"ok": False, "error": f"没有这份文档：{doc_id}（可选：{' / '.join(DOC_DEFS)}）"}

    root = resolve_root()
    if root is None:
        looked = "、".join(str(c) for c in candidates())
        return {"ok": False, "error": f"文档目录没找到，找过：{looked}；检查 docker-compose 是否把 ./docs 挂进容器"}

    path = root / spec["path"]
    if not path.is_file():
        return {"ok": False, "error": f"《{spec['title']}》不在 {path}"}

    content = path.read_text(encoding="utf-8")
    keyword = (keyword or "").strip()

    if not keyword:
        return {"ok": True, "doc": doc_id, "title": spec["title"], "keyword": None, "result": _toc(content)}

    hits = _search(content, keywords(keyword))
    if not hits:
        # 没命中就把目录一并带上：让模型为了看一眼目录再花一轮，是白烧一次调用
        return {
            "ok": True, "doc": doc_id, "title": spec["title"], "keyword": keyword,
            "result": f"《{spec['title']}》里没有「{keyword}」。下面是目录，挑个更准的词再查：\n\n{_toc(content)}",
        }
    return {"ok": True, "doc": doc_id, "title": spec["title"], "keyword": keyword, "result": hits}
