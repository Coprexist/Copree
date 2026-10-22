"""AI 知识库服务 — 每个 AI 自己的文档库

一份文档 = 一行 agent_knowledge + 文件空间里的一份正文（data/agents/{id}/knowledge/<path>）。
正文放文件而不是 DB：文档动辄几十 KB，塞进提示词只会挤爆上下文；AI 本来就有 file_read，
索引里给标题 + 摘要 + 路径，需要细节时自己去读。

索引与「记忆索引」同段注入（ai/llm.py::_build_memory_index），一起走能力版本链冻结：
标题/摘要变了才换版本，正文改几行不动前缀。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import select

from app.models.agent_knowledge import AgentKnowledge
from app.paths import agent_dir

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = "knowledge"
MAX_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".json", ".csv", ".yaml", ".yml",
    ".html", ".htm", ".xml", ".log", ".py", ".js", ".ts", ".css",
}


def knowledge_root(agent_id: int) -> Path:
    """知识库目录（AI 文件空间下的 knowledge/，file_read 视角就是 knowledge/xxx.md）"""
    d = agent_dir(int(agent_id)) / KNOWLEDGE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    """文件名清洗：去掉路径分隔与控制字符；保留中文（世界是中文的）"""
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", (name or "").strip())
    base = base.strip(" .-")[:80]
    return base or "document"


def _safe_rel(agent_id: int, rel: str) -> str:
    """相对路径校验：只能落在本 AI 的 knowledge/ 内，且是文本后缀"""
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/") or rel.startswith("."):
        raise ValueError("非法路径")
    root = knowledge_root(agent_id).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError("非法路径")
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("知识库只收文本文件（md / txt / json / csv / yaml / html / 代码）")
    return str(target.relative_to(root))


def _row(entry: AgentKnowledge) -> dict:
    return {
        "id": entry.id,
        "agent_id": entry.agent_id,
        "title": entry.title,
        "summary": entry.summary or "",
        "path": entry.path,
        "size": entry.size or 0,
        "enabled": bool(entry.enabled),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _notify_index_changed(agent_id: int) -> None:
    """索引换了新版本：让这个 AI 下一次刷新上下文时知道（和它自己 store_memory 同一条通道）"""
    try:
        from app.services.capability_versioning import mark_self_change, memory_index_source

        mark_self_change(agent_id, memory_index_source(agent_id))
    except Exception as e:  # noqa: BLE001 —— 通知失败不影响文档已落盘
        logger.debug("知识库索引变更标记失败（非致命）: %s", e)


async def list_entries(db, agent_id: int) -> list[dict]:
    """这个 AI 的知识库清单（含未启用的条目——界面要显示开关）"""
    rows = (await db.execute(
        select(AgentKnowledge)
        .where(AgentKnowledge.agent_id == int(agent_id))
        .order_by(AgentKnowledge.created_at, AgentKnowledge.id)
    )).scalars().all()
    return [_row(r) for r in rows]


async def get_entry(db, agent_id: int, knowledge_id: int) -> AgentKnowledge | None:
    return (await db.execute(
        select(AgentKnowledge).where(
            AgentKnowledge.id == int(knowledge_id),
            AgentKnowledge.agent_id == int(agent_id),
        )
    )).scalar_one_or_none()


async def add_entry(db, agent_id: int, *, title: str, summary: str = "",
                    filename: str | None = None, content: str) -> dict:
    """新增一份文档：写文件 + 落元数据。同名自动加 -2/-3，不覆盖已有文档。"""
    content = content or ""
    raw = content.encode("utf-8")
    if not raw.strip():
        raise ValueError("文档内容不能为空")
    if len(raw) > MAX_BYTES:
        raise ValueError("文档太大（上限 2MB）")

    name = _slug(filename or title)
    if not Path(name).suffix:
        name += ".md"
    root = knowledge_root(agent_id)
    rel = name
    i = 2
    while (root / rel).exists():
        p = Path(name)
        rel = f"{p.stem}-{i}{p.suffix}"
        i += 1

    target = root / _safe_rel(agent_id, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    entry = AgentKnowledge(
        agent_id=int(agent_id),
        title=(str(title or "").strip() or Path(rel).stem)[:120],
        summary=(str(summary or "").strip())[:1000],
        path=rel,
        size=len(raw),
        enabled=True,
    )
    db.add(entry)
    await db.flush()
    # 时间戳由 server_default 生成，flush 后是"过期"状态；这里刷一次，返回给界面的行才完整
    await db.refresh(entry)
    _notify_index_changed(agent_id)
    logger.info("📚 AI %s 知识库新增《%s》(%s，%s 字节)", agent_id, entry.title, rel, len(raw))
    return _row(entry)


async def update_entry(db, agent_id: int, knowledge_id: int, *,
                       title: str | None = None, summary: str | None = None,
                       enabled: bool | None = None, content: str | None = None) -> dict:
    entry = await get_entry(db, agent_id, knowledge_id)
    if entry is None:
        raise ValueError("文档不存在")
    if title is not None:
        entry.title = str(title).strip()[:120] or entry.title
    if summary is not None:
        entry.summary = str(summary).strip()[:1000]
    if enabled is not None:
        entry.enabled = bool(enabled)
    if content is not None:
        raw = content.encode("utf-8")
        if not raw.strip():
            raise ValueError("文档内容不能为空")
        if len(raw) > MAX_BYTES:
            raise ValueError("文档太大（上限 2MB）")
        (knowledge_root(agent_id) / entry.path).write_text(content, encoding="utf-8")
        entry.size = len(raw)
    await db.flush()
    await db.refresh(entry)
    _notify_index_changed(agent_id)
    return _row(entry)


async def delete_entry(db, agent_id: int, knowledge_id: int) -> None:
    entry = await get_entry(db, agent_id, knowledge_id)
    if entry is None:
        raise ValueError("文档不存在")
    try:
        (knowledge_root(agent_id) / entry.path).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("删除知识库文件失败 %s: %s", entry.path, e)
    await db.delete(entry)
    await db.flush()
    _notify_index_changed(agent_id)


async def read_entry(db, agent_id: int, knowledge_id: int) -> dict:
    entry = await get_entry(db, agent_id, knowledge_id)
    if entry is None:
        raise ValueError("文档不存在")
    path = knowledge_root(agent_id) / entry.path
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    return {**_row(entry), "content": content}


async def knowledge_index_text(db, agent_id: int) -> str:
    """进系统提示的索引：只列启用的文档（标题 + 摘要 + 读取路径）"""
    rows = (await db.execute(
        select(AgentKnowledge)
        .where(AgentKnowledge.agent_id == int(agent_id), AgentKnowledge.enabled.is_(True))
        .order_by(AgentKnowledge.created_at, AgentKnowledge.id)
    )).scalars().all()
    if not rows:
        return ""
    lines = ["## 知识库（你的资料；需要细节用 file_read 取正文，别整段背）"]
    for r in rows:
        summary = (r.summary or "").strip()
        hint = f"：{summary}" if summary else ""
        lines.append(f"- 《{r.title}》{hint} → file_read \"knowledge/{r.path}\"")
    return "\n".join(lines)
