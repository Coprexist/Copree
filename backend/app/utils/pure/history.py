"""会话历史账本纯函数 —— 无 IO（见 docs/dev/conversation_history.md）

账本 = 模型看过的上下文的完整账本：**段内只追加，只在 compact / 超时压缩（解锁点）重写**。

- 每条 content 都是"渲染好的最终字节"（渲染即落库）→ 每轮重拼字节一致 → 前缀缓存命中；
- 三类来源按时间交织：self=我干了什么 / user=用户干了什么 / world=外界带来了什么；
- **缺口事件写在这批消息之前、与它们同一批写入**：缺口说的是"这批之前还有 N 条没列出来"，
  事后再插就是改中段 → 断缓存；
- 补看走 append，带 [补] 抬头（顺序按写入位置，不是发生时间），所以每条都要带 ref/时间锚点。
"""
from __future__ import annotations

# 谁做的（决定投影成哪种 role）
ACTORS = ("self", "user", "world", "system")

# 这条是什么
KINDS = (
    "message",     # 群/私信里的一条消息
    "gap",         # 缺口：更早还有 N 条没列出来
    "backfill",    # 补看：后来把缺口补进来
    "tool",        # 工具调用与结果（"我干过什么"）
    "note",        # 便签投递 / 撤下
    "notice",      # 平台通知（能力变更等）
    "suggestion",  # 给用户的建议回复
    "summary",     # compact 产物
    "thinking",    # 保留的思考（仅 keep_thinking=true 时）
)

# actor → LLM role
ROLE_BY_ACTOR = {"self": "assistant", "user": "user", "world": "user", "system": "system"}

# 事件类：压缩时**原样搬运**，不揉进摘要（揉了就等于丢契约/丢"有个洞"）
NEVER_COMPRESSIBLE = ("gap", "backfill", "note", "notice")


def make_entry(kind: str, content: str, *, actor: str = "system",
               ref: str = "", flags: dict | None = None) -> dict:
    """构建一条账本条目（seq 由服务层分配，这里不管）。"""
    return {
        "kind": kind if kind in KINDS else "message",
        "actor": actor if actor in ACTORS else "system",
        "content": content,
        "ref": ref or "",
        "flags": dict(flags or {}),
    }


def gap_text(count: int) -> str:
    """缺口事件文字（唯一来源：主站群聊截断提示也读这里，避免两处各写一遍）。"""
    return (f"（更早还有 {int(count)} 条未读消息没有列在上面；需要时用 view_unread 查看，"
            "别当成群里只有这几条。）")


def gap_entry(count: int, *, ref: str = "") -> dict:
    """缺口事件：**必须排在这批消息前面**，与它们同一批 append。"""
    return make_entry("gap", gap_text(count), actor="system", ref=ref)


def backfill_header(count: int, *, first_ref: str = "", last_ref: str = "", at: str = "") -> str:
    """补看批次的开头：说清这些是更早的消息、现在才补进来、顺序按位置不按时间。"""
    span = f"（#{first_ref}..#{last_ref}）" if first_ref and last_ref else ""
    when = f"，补于 {at}" if at else ""
    return (f"（补看{span}：这 {int(count)} 条是更早的消息，之前没列出来过{when}；"
            "它们排在后面是因为只能追加，不代表刚发生。）")


def latest_message_ref(entries: list[dict]) -> int:
    """账本里最后渲染过的**真实消息 id**（水位）——只追加的幂等锚点。

    水位从账本自己推（message 条目的 ref = 消息 id），不另立游标表：
    多一份状态就多一处漂移，而且回滚时游标和账本会不一致。
    """
    latest = 0
    for e in entries or []:
        if (e.get("kind") or "") != "message":
            continue
        try:
            latest = max(latest, int(e.get("ref") or 0))
        except (TypeError, ValueError):
            continue
    return latest


def is_compressible(entry: dict) -> bool:
    """这条能不能被摘要吃掉：事件类永不（缺口/补看/便签/通知），其余默认可以。"""
    flags = entry.get("flags") or {}
    if "compressible" in flags:
        return bool(flags["compressible"])
    return entry.get("kind") not in NEVER_COMPRESSIBLE


def entries_to_messages(entries: list[dict]) -> list[dict]:
    """账本 → 发给模型的 messages（投影；顺序就是 seq 顺序）。"""
    out: list[dict] = []
    for e in entries or []:
        content = e.get("content") or ""
        if not content:
            continue
        out.append({"role": ROLE_BY_ACTOR.get(e.get("actor") or "system", "system"), "content": content})
    return out
