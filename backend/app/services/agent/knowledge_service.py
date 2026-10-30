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
from app.services.content.file_service import (
    ai_delete_file, ai_move_file, ai_stored_path, ai_view_path, ai_write_file,
)

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
    """相对路径校验：只能落在本 AI 的 knowledge/ 内，且是文本后缀

    路径换算不自己写：file_service.ai_stored_path 是 AI 文件空间的唯一入口，
    它已处理「开头的 / 按文件空间根理解」与「爬出自己空间当场拒绝」。
    这里只补两条它不管的规矩：必须仍在 knowledge/ 下、必须是文本后缀。
    """
    view = ai_view_path(agent_id, ai_stored_path(agent_id, f"{KNOWLEDGE_DIR}/{rel}"))
    prefix = f"{KNOWLEDGE_DIR}/"
    inner = view[len(prefix):] if view.startswith(prefix) else ""
    if not inner:
        raise ValueError("非法路径")
    if Path(inner).suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("知识库只收文本文件（md / txt / json / csv / yaml / html / 代码）")
    return inner


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


def _unique_rel(agent_id: int, rel: str) -> str:
    """同名不覆盖：目标已存在就试 a-2.md、a-3.md…（新增与移动共用这一条规矩）"""
    root = knowledge_root(agent_id)
    candidate = rel
    i = 2
    p = Path(rel)
    while (root / candidate).exists():
        # 保留父目录：带子目录的路径不能塌回根目录（with_name 而不是裸拼文件名）
        candidate = str(p.with_name(f"{p.stem}-{i}{p.suffix}"))
        i += 1
    return candidate


def _text_bytes(content: str) -> bytes:
    """正文校验（新增与改正文共用）：非空、不超上限，返回 UTF-8 字节数"""
    raw = (content or "").encode("utf-8")
    if not raw.strip():
        raise ValueError("文档内容不能为空")
    if len(raw) > MAX_BYTES:
        raise ValueError("文档太大（上限 2MB）")
    return raw


async def add_entry(db, agent_id: int, *, title: str, summary: str = "",
                    filename: str | None = None, content: str) -> dict:
    """新增一份文档：写文件 + 落元数据。同名自动加 -2/-3，不覆盖已有文档。"""
    content = content or ""
    raw = _text_bytes(content)

    name = _slug(filename or title)
    if not Path(name).suffix:
        name += ".md"
    rel = _unique_rel(agent_id, _safe_rel(agent_id, name))

    # 正文交给文件服务写：它同时登记 files 行，AI 的 file_list 与存储页才看得见这份文档
    await ai_write_file(db, agent_id, f"{KNOWLEDGE_DIR}/{rel}", content)

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
    logger.info("📚 AI %s 知识库新增《%s》(%s，%s 字节)", agent_id, entry.title, rel, len(raw))
    return _row(entry)


async def restore_entry(db, agent_id: int, *, path: str, title: str, content: str,
                        summary: str = "", enabled: bool = True,
                        created_at=None, updated_at=None) -> dict:
    """按原路径还原一份文档（导入灵魂档案专用）：路径原封不动，同名同路覆盖

    与 add_entry 分开是因为两者要的东西不一样：add_entry 面向"用户新增一份资料"，
    由标题推文件名、撞名加 -2；还原要的是**同一个路径**——索引里写的是
    file_read "knowledge/xxx.md"，路径一变，读的人就扑空。
    """
    rel = _safe_rel(agent_id, path)
    raw = _text_bytes(content)
    await ai_write_file(db, agent_id, f"{KNOWLEDGE_DIR}/{rel}", content)

    entry = (await db.execute(
        select(AgentKnowledge).where(
            AgentKnowledge.agent_id == int(agent_id),
            AgentKnowledge.path == rel,
        )
    )).scalar_one_or_none()
    if entry is None:
        entry = AgentKnowledge(agent_id=int(agent_id), path=rel)
        db.add(entry)
    entry.title = (str(title or "").strip() or Path(rel).stem)[:120]
    entry.summary = (str(summary or "").strip())[:1000]
    entry.size = len(raw)
    entry.enabled = bool(enabled)
    # 时间戳能还原就还原：档案里的"什么时候放进来的"也是这份资料的一部分
    if created_at is not None:
        entry.created_at = created_at
    if updated_at is not None:
        entry.updated_at = updated_at

    await db.flush()
    await db.refresh(entry)
    logger.info("📚 AI %s 知识库还原《%s》(%s，%s 字节)", agent_id, entry.title, rel, len(raw))
    return _row(entry)


# 上传的文本按这两种编码解：UTF-8（含 BOM）是主流，GB18030 兜住 Windows 中文环境存出来的文件
UPLOAD_ENCODINGS = ("utf-8-sig", "gb18030")


def _decode(raw: bytes, filename: str) -> str:
    for enc in UPLOAD_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"《{filename}》不是文本文件（只收 UTF-8 / GB18030 文本）")


async def add_entry_from_upload(db, agent_id: int, *, filename: str, data: bytes,
                                title: str | None = None, summary: str = "") -> dict:
    """上传一份文本文件：解码后走 add_entry——落文件与落元数据的规矩只在 add_entry 里有一份"""
    if not data:
        raise ValueError("上传的文件是空的")
    if len(data) > MAX_BYTES:
        raise ValueError("文档太大（上限 2MB）")
    content = _decode(data, filename or "上传的文档")
    return await add_entry(
        db, agent_id,
        title=(title or "").strip(),
        summary=summary or "",
        filename=filename,
        content=content,
    )


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
        raw = _text_bytes(content)
        await ai_write_file(db, agent_id, f"{KNOWLEDGE_DIR}/{entry.path}", content)
        entry.size = len(raw)
    await db.flush()
    await db.refresh(entry)
    return _row(entry)


async def move_entry(db, agent_id: int, knowledge_id: int, new_path: str) -> dict:
    """换位置 / 改名：正文文件挪过去，元数据跟着改。

    新位置只写目录（如 "平台"）时保留原文件名；同名不覆盖（自动 -2），
    路径校验与新增同一套（_safe_rel），所以爬不出 knowledge/。
    """
    entry = await get_entry(db, agent_id, knowledge_id)
    if entry is None:
        raise ValueError("文档不存在")
    raw = str(new_path or "").strip().lstrip("/").rstrip("/")
    if not Path(raw).suffix:
        # 只给了目录：文件名沿用原来的
        raw = f"{raw}/{Path(entry.path).name}" if raw else Path(entry.path).name
    rel = _safe_rel(agent_id, raw)
    if rel != entry.path:
        rel = _unique_rel(agent_id, rel)
        await ai_move_file(db, agent_id, f"{KNOWLEDGE_DIR}/{entry.path}", f"{KNOWLEDGE_DIR}/{rel}")
        entry.path = rel
    await db.flush()
    await db.refresh(entry)
    return _row(entry)


async def delete_entry(db, agent_id: int, knowledge_id: int) -> None:
    entry = await get_entry(db, agent_id, knowledge_id)
    if entry is None:
        raise ValueError("文档不存在")
    try:
        await ai_delete_file(db, agent_id, f"{KNOWLEDGE_DIR}/{entry.path}")
    except ValueError:
        # 早于"文件服务写入"的文档没有 files 登记行，直接删文件
        try:
            (knowledge_root(agent_id) / _safe_rel(agent_id, entry.path)).unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            logger.warning("删除知识库文件失败 %s: %s", entry.path, e)
    await db.delete(entry)
    await db.flush()


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
