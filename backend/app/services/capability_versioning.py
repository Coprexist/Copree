"""
能力懒加载：skills/tools 版本化 + 增量变更注入（2026-08-06 产品方案）

能力源：platform（内置工具）/ world-{id}（世界 skills 转出的工具定义）。
每个 AI 两个进度（agents.cap_known_versions / cap_effective_versions，JSONB {source: version}）：
- known（告知进度）：已注入变更通知的版本 —— 落后则注入"增量 changelog"（只有新变化），注入后更新
- effective（生效进度）：请求实际使用的定义版本 —— compact 时切到最新

2026-08-12 扩展：前缀内容（用户 system_prompt / 强注入段 / 昵称）也纳入同一机制。
新增：ensure_text_source_version / get_effective_text / apply_pending_changes（解锁）/ guard_apply_change（防御）。

不变式：
- 请求 payload 的 tools = effective 版本的定义快照（compact 前不动 → 前缀缓存稳定）
- 变更告知 = 动态尾部 system 消息（不影响前缀）
- 旧版本定义永远保留在 capability_versions（平台发布后旧对话继续用旧定义请求）
- 锁定态（对话中）前缀永不因外部变更而变；compact / clear 是唯一解锁点
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging

from sqlalchemy import select
from app.repositories.capability_repo import CapabilityRepository

logger = logging.getLogger(__name__)

SOURCE_PLATFORM = "platform"


def memory_index_source(agent_id: int) -> str:
    """记忆索引（目录树）的版本源——与提示词/能力**共用同一条版本链**：

    冻结（锁定态取 effective 快照）+ 差分通知（尾部 changelog）都是现成的，不新造机制。
    """
    return f"memory-index-{agent_id}"

# 解锁上下文标记：apply_pending_changes（compact/clear）期间置 True，
# guard_apply_change 检查它——锁定态尝试应用变更 → 拒绝 + 报错（防御未来"强制修改"逻辑）
_unlock_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("cap_unlock_ctx", default=False)


# 能力源 id → 人话标签（通知标题用：world-name-45 这种 id 世界 AI 看不出是什么）
_SOURCE_LABELS = {"forced-prompt": "强注入段", "ai-skills": "设计侧能力"}


def source_label(source: str) -> str:
    """能力源 id → 中文标签（world-prompt-45 → 世界AI提示词，world-name-45 → 世界AI昵称）"""
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    for prefix, name in (("world-prompt-", "世界AI提示词"), ("world-name-", "世界AI昵称")):
        if source.startswith(prefix):
            return name
    return source


def defs_hash(definitions: list) -> str:
    """工具定义列表 → 内容哈希（检测变更）"""
    return hashlib.sha256(
        json.dumps(definitions, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _def_by_name(definitions: list, name: str) -> dict | None:
    for d in definitions or []:
        fn = (d or {}).get("function") or {}
        if fn.get("name") == name:
            return d
    return None


def _diff_changelog(old_defs: list | None, new_defs: list) -> str:
    """自动 diff 两版定义 → 变更摘要（新增/移除/更新工具名；文本源用长度+首段差异）"""
    # 文本源：definitions = [{"type": "text", "content": "..."}]
    def _is_text(defs):
        return bool(defs) and all((d or {}).get("type") == "text" for d in defs)
    if _is_text(old_defs) or _is_text(new_defs):
        old_text = "".join((d or {}).get("content") or "" for d in (old_defs or []))
        new_text = "".join((d or {}).get("content") or "" for d in (new_defs or []))
        if old_text == new_text:
            return "内容无变化"
        # 短文本（昵称这类）：直接给「旧 → 新」；字数+首处差异看不懂（世界 AI 收到
        # 「6→3 字，首处差异：…群视界机器人… → …小傻福…」时没意识到那是它自己的新名字）
        if len(old_text) <= 60 and len(new_text) <= 60:
            return f"内容更新：「{old_text}」 → 「{new_text}」"
        # 长文本：找首个差异位置，展示前后各 40 字
        i = 0
        while i < min(len(old_text), len(new_text)) and old_text[i] == new_text[i]:
            i += 1
        old_snip = old_text[max(0, i - 20):i + 40].replace("\n", " ")
        new_snip = new_text[max(0, i - 20):i + 40].replace("\n", " ")
        return f"内容更新（{len(old_text)}→{len(new_text)} 字，首处差异：…{old_snip}… → …{new_snip}…）"
    old_names = {((d or {}).get("function") or {}).get("name") for d in (old_defs or [])}
    new_names = {((d or {}).get("function") or {}).get("name") for d in (new_defs or [])}
    added = new_names - old_names
    removed = old_names - new_names
    changed = {
        n for n in (new_names & old_names)
        if _def_by_name(new_defs, n) != _def_by_name(old_defs, n)
    }
    lines = []
    for n in sorted(added):
        lines.append(f"新增能力 {n}")
    for n in sorted(removed):
        lines.append(f"移除能力 {n}")
    for n in sorted(changed):
        lines.append(f"更新能力 {n}")
    return "\n".join(lines) or "能力定义更新（无名称变化）"


# ── 版本写入（启动/变更检测时调用） ──

async def ensure_source_version(
    cap_repo: CapabilityRepository, source: str, definitions: list, label: str,
) -> int:
    """对比该源最新版本：内容变了 → 写新版本（diff 生成 changelog）；没变 → 返回当前版本号"""
    from app.models.agent import CapabilityVersion

    h = defs_hash(definitions)
    latest = (await cap_repo.execute(
        select(CapabilityVersion)
        .where(CapabilityVersion.source == source)
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()

    if latest is not None and latest.content_hash == h:
        return latest.version

    new_version = (latest.version if latest else 0) + 1
    changelog = f"[{label} v{new_version}] " + _diff_changelog(
        latest.definitions if latest else None, definitions
    )
    cap_repo.add(CapabilityVersion(
        source=source, version=new_version, content_hash=h,
        changelog=changelog, definitions=definitions,
    ))
    await cap_repo.commit()
    logger.info(f"🧬 能力源 {source} 新版本 v{new_version}: {changelog[:120]}")
    return new_version


async def ensure_platform_version(cap_repo: CapabilityRepository) -> int:
    """平台内置工具版本化（启动时调用）"""
    from app.tools.base import ToolRegistry
    return await ensure_source_version(
        cap_repo, SOURCE_PLATFORM, ToolRegistry.get_all_definitions(), "平台工具"
    )


async def ensure_world_version(cap_repo: CapabilityRepository, world_id: int, skill_tools: list) -> int:
    """世界 skills 工具版本化（世界 AI 对话时调用）"""
    source = f"world-{world_id}"
    return await ensure_source_version(cap_repo, source, skill_tools, f"世界{world_id}")


# ── 查询 ──

async def get_version(cap_repo: CapabilityRepository, source: str, version: int):
    from app.models.agent import CapabilityVersion
    return (await cap_repo.execute(
        select(CapabilityVersion).where(
            CapabilityVersion.source == source,
            CapabilityVersion.version == version,
        )
    )).scalar_one_or_none()


async def get_latest_version(cap_repo: CapabilityRepository, source: str):
    from app.models.agent import CapabilityVersion
    return (await cap_repo.execute(
        select(CapabilityVersion)
        .where(CapabilityVersion.source == source)
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()


# ── AI 进度读写 ──

def _holder_map(holder, key: str) -> dict:
    """holder 可以是 agent（有属性）或 dict（如 worlds.config）"""
    if isinstance(holder, dict):
        return dict(holder.get(key) or {})
    return dict(getattr(holder, key, None) or {})


def _set_holder_map(holder, key: str, m: dict) -> None:
    if isinstance(holder, dict):
        holder[key] = m
    else:
        setattr(holder, key, m)


async def get_effective_definitions(
    cap_repo: CapabilityRepository, holder, source: str, fallback_definitions: list,
) -> list:
    """请求用的工具定义：按 effective 版本取快照；无记录（新 AI/新源）→ 用当前定义并同步 effective"""
    ver = _holder_map(holder, "cap_effective_versions").get(source)
    if ver is not None:
        row = await get_version(cap_repo, source, ver)
        if row is not None and row.definitions is not None:
            return row.definitions
    # 新 AI：直接用当前（latest）定义，并把 effective 对齐最新版本
    latest = await get_latest_version(cap_repo, source)
    if latest is not None:
        e = _holder_map(holder, "cap_effective_versions")
        e[source] = latest.version
        _set_holder_map(holder, "cap_effective_versions", e)
        if latest.definitions is not None:
            return latest.definitions
    return fallback_definitions


async def build_change_notice(cap_repo: CapabilityRepository, agent, sources: list[str]) -> str | None:
    """增量变更通知：对比 known vs latest，落后则拼 changelog（只含新变化），并更新 known。

    返回通知文本（追加进 system 尾部）；无变化返回 None。
    注意：调用方需把通知真正放进 messages 后再提交（known 更新与注入同事务）。
    """
    from app.models.agent import CapabilityVersion

    lines: list[str] = []
    changed = False
    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is None:
            continue
        known = _holder_map(agent, "cap_known_versions").get(source, 0)
        if known >= latest.version:
            continue
        # 取 known+1 .. latest 的 changelog（增量：只注入新变化）
        rows = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(
                CapabilityVersion.source == source,
                CapabilityVersion.version > known,
                CapabilityVersion.version <= latest.version,
            )
            .order_by(CapabilityVersion.version.asc())
        )).scalars().all()
        parts = [f"[{r.changelog}]" for r in rows if r.changelog]
        if parts:
            lines.append(f"【能力变更通知 · {source_label(source)} v{known}→v{latest.version}】\n" + "\n".join(parts))
            k = _holder_map(agent, "cap_known_versions")
            k[source] = latest.version
            _set_holder_map(agent, "cap_known_versions", k)
            changed = True
    if not changed:
        return None
    # 版本链保证「前缀缓存稳定」：强注入段/工具定义的新版本要等 compact / 清空上下文才整体生效，
    # 但 changelog 是当轮就到的。世界 AI 曾经看到通知 v10 却仍按旧段去找不存在的工具
    # （2026-09-18 反馈：「不只费 token，我会去找不存在的工具、白烧轮次」）→ 明确谁说了算。
    head = ("【能力变更通知】以下变更**立即生效**：与系统提示 / 工具描述里的旧表述冲突时以本通知为准；"
            "系统提示全文会在下次 compact / 清空上下文时整体刷新。\n")
    return head + "\n\n".join(lines)


async def mark_effective_latest(cap_repo: CapabilityRepository, agent, sources: list[str]) -> None:
    """compact 后调用：effective 全部对齐最新（工具定义直接用最新的）"""
    from app.models.agent import CapabilityVersion

    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None:
            e = _holder_map(agent, "cap_effective_versions")
            e[source] = latest.version
            _set_holder_map(agent, "cap_effective_versions", e)


async def mark_known_latest(cap_repo: CapabilityRepository, agent, sources: list[str]) -> None:
    """把「已知版本」也对齐最新：内容已经进了生效前缀时，不必再发一次变更通知。

    与 mark_effective_latest 配对（会话起点解锁：既生效、也已"告知"，避免下一轮多一条
    描述"已经生效的内容"的通知）。
    """
    from app.models.agent import CapabilityVersion

    for source in sources:
        latest = (await cap_repo.execute(
            select(CapabilityVersion)
            .where(CapabilityVersion.source == source)
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest is not None:
            k = _holder_map(agent, "cap_known_versions")
            k[source] = latest.version
            _set_holder_map(agent, "cap_known_versions", k)


# ═══════════════════════════════════════════════════════════════
# 前缀文本源版本化（所有进前缀的内容必须保证缓存命中）
# ═══════════════════════════════════════════════════════════════
# 用户 system_prompt / 强注入段 / 昵称等文本也走 capability_versions 版本链：
# - 变更（用户改提示词 / 系统更新强注入）→ 写新版本 + 尾部 changelog 告知，不碰前缀
# - compact / clear（解锁）→ effective 对齐最新，前缀用新内容

def text_defs(text: str) -> list:
    """文本内容 → definitions 存储格式（与工具定义同表，type=text 区分）"""
    return [{"type": "text", "content": text}]


def defs_to_text(definitions: list | None) -> str | None:
    """definitions 快照 → 文本（type=text 源）"""
    if not definitions:
        return None
    parts = []
    for d in definitions:
        if isinstance(d, dict) and d.get("type") == "text":
            parts.append(d.get("content") or "")
    return "".join(parts) if parts else None


async def ensure_text_source_version(
    cap_repo: CapabilityRepository, source: str, text: str, label: str,
) -> int:
    """文本内容版本化：内容变了 → 写新版本（复用 ensure_source_version + 文本 diff）"""
    return await ensure_source_version(cap_repo, source, text_defs(text), label)


async def get_effective_text(
    cap_repo: CapabilityRepository, holder, source: str, fallback_text: str,
) -> str:
    """锁定态取 effective 快照文本；无记录（新源）→ 用当前文本并同步 effective（与工具同语义）"""
    ver = _holder_map(holder, "cap_effective_versions").get(source)
    if ver is not None:
        row = await get_version(cap_repo, source, ver)
        if row is not None:
            t = defs_to_text(row.definitions)
            if t is not None:
                return t
    latest = await get_latest_version(cap_repo, source)
    if latest is not None:
        e = _holder_map(holder, "cap_effective_versions")
        e[source] = latest.version
        _set_holder_map(holder, "cap_effective_versions", e)
        t = defs_to_text(latest.definitions)
        if t is not None:
            return t
    return fallback_text


async def apply_pending_changes(cap_repo: CapabilityRepository, holder, sources: list[str]) -> None:
    """解锁（compact / clear = 新对话）时调用：effective 全部对齐最新，变更正式生效。

    这是唯一合法的"应用变更"入口：它先进入解锁上下文再操作。
    锁定态直接调用 mark_effective_latest / 改 effective → guard_apply_change 拒绝 + 报错。
    """
    token = _unlock_ctx.set(True)
    try:
        guard_apply_change(sources)  # 解锁上下文内校验通过（防御：万一 future 代码绕路）
        await mark_effective_latest(cap_repo, holder, sources)
    finally:
        _unlock_ctx.reset(token)


def guard_apply_change(sources: list[str]) -> None:
    """防御性守卫：非解锁上下文（对话进行中）尝试应用变更 → 拒绝 + 记录报错。

    设计口径："只要有在解锁之前尝试应用变更的都拒绝并记录报错"。
    目前代码都是动态读取拼接（不会强制修改），此守卫防止未来出现"强制修改"逻辑破坏不变式。
    """
    if not _unlock_ctx.get():
        logger.error(
            f"⛔ 拒绝在锁定态应用能力变更 {sources}："
            f"前缀必须保持缓存稳定，变更只能经 compact/clear 解锁后生效"
        )
        raise RuntimeError(
            f"锁定态禁止应用变更 {sources}（需 compact/clear 解锁）"
        )
