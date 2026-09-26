"""会话历史账本纯函数 —— 无 IO（见 docs/dev/conversation_history.md）

账本 = 模型看过的上下文的完整账本：**段内只追加，只在 compact / 超时压缩（解锁点）重写**。

- 每条 content 都是"渲染好的最终字节"（渲染即落库）→ 每轮重拼字节一致 → 前缀缓存命中；
- 三类来源按时间交织：self=我干了什么 / user=用户干了什么 / world=外界带来了什么；
- **缺口事件写在这批消息之前、与它们同一批写入**：缺口说的是"这批之前还有 N 条没列出来"，
  事后再插就是改中段 → 断缓存；
- 缺口**只报条数**：要读原文，AI 用 read_conversation 往回翻（补看机制明确不做，2026-09-26）。
"""
from __future__ import annotations

# 谁做的（决定投影成哪种 role）
ACTORS = ("self", "user", "world", "system")

# 这条是什么
KINDS = (
    "message",     # 群/私信里的一条消息
    "gap",         # 缺口：更早还有 N 条没列出来（只报条数；读原文用 read_conversation）
    "tool",        # 工具调用与结果（"我干过什么"）
    "note",        # 便签投递 / 撤下
    "notice",      # 平台通知（能力变更等）
    "handoff",     # 轮末交接：留给后面自己的关键信息（end_turn.key_note）
    "suggestion",  # 给用户的建议回复
    "summary",     # compact 产物
    "thinking",    # 保留的思考（仅 keep_thinking=true 时）
)

# actor → LLM role
ROLE_BY_ACTOR = {"self": "assistant", "user": "user", "world": "user", "system": "system"}

# 事件类：压缩时**原样搬运**，不揉进摘要（揉了就等于丢契约/丢"有个洞"）
NEVER_COMPRESSIBLE = ("gap", "note", "notice", "handoff")


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


def revoked_text() -> str:
    """被撤回的消息在账本里长什么样（唯一来源：条目渲染只认它）。

    撤回前的原文只留在库里（审计/排障），**任何渲染都不再显示**——所以"撤回后才进历史"的那些
    条目天然只会是这句占位，不需要另外判断。
    """
    return "（这条消息已被撤回）"


def revoked_notice(speaker: str, message_id: int) -> dict:
    """撤回通知：给**已经看过那条**的 AI 补一条账本条目。

    为什么不重写原来那条：账本段内只追加（改中段就断前缀缓存，§0）。所以"撤回"的语义是
    **再补一条作废通知**——AI 那句话还在它的上下文里，但下一轮它会知道那条不算数了。
    通知里**不重复被撤内容**：撤回本来就是不想让那句话继续传播。
    """
    name = (speaker or "").strip() or "有人"
    return make_entry(
        "notice",
        f"【撤回】「{name}」撤回了 1 条消息（msg_id={message_id}）：你之前看到的那条内容已作废，"
        f"不要再引用、不要追问它。",
        actor="system",
        ref=f"revoked:{message_id}",
    )


# 长消息折叠：超过 limit 时留**前 75% + 后 25%**，中间写明省略了多少、要展开
FOLD_LIMIT = 2048          # 群设置 max_msg_display_len 的默认值（0 = 不折叠）
FOLD_HEAD_RATIO = 0.75


def fold_text(content: str, *, limit: int = FOLD_LIMIT, expand_id: int | None = None) -> str:
    """长消息折成「前 75% + 省略标记 + 后 25%」。

    为什么两头都留：AI 常把结论/追问放在最后一段。2026-09-25 线上实测——它自己的回复被截在
    256 字，"你 60 多分"落在 400 字之后，它**真的没看见**，于是反过来否认自己说过这话。
    只留开头会让它对自己的话失忆，只留结尾会丢掉前因，所以两头都给。

    limit<=0 = 不折叠（群设置里 0 就是这个意思）；expand_id 给了才写 [展开 id=N]
    （那个标记由 expand_message 工具负责展开）。
    """
    if limit <= 0 or not content or len(content) <= limit:
        return content
    head_n = int(limit * FOLD_HEAD_RATIO)
    tail_n = limit - head_n
    marker = f"…（中间省略 {len(content) - limit} 字；引用或否认某句话之前，先用 expand_message 展开核对）"
    if expand_id is not None:
        marker += f"[展开 id={expand_id}]"
    return content[:head_n] + "\n" + marker + "\n" + content[-tail_n:]


def gap_text(count: int) -> str:
    """缺口事件文字（唯一来源：主站群聊截断提示也读这里，避免两处各写一遍）。"""
    return (f"（更早还有 {int(count)} 条未读消息没有列在上面；要看原文用 read_conversation 往回翻"
            "（view_unread 只给未读概览），别当成群里只有这几条。）")


def gap_entry(count: int, *, ref: str = "") -> dict:
    """缺口事件：**必须排在这批消息前面**，与它们同一批 append。"""
    return make_entry("gap", gap_text(count), actor="system", ref=ref)


def take_newest_within(entries: list[dict], max_chars: int) -> list[dict]:
    """按**渲染后的字数**从最旧端丢条目，保留最新的（正序进、正序出）；至少留最新一条。

    为什么按渲染后算：预算说的是"进上下文多少字"——一条 3 万字的原文折成 2048 + 省略标记后
    只占 2100 上下，按原文算（旧口径）会把前面那些本来装得下的消息白白挤成缺口。
    为什么至少留一条：单条就超预算时（群设置 0 = 不折叠、且消息极长）全丢会让水位永远不动、
    同步卡死；多带那一条只多花它一份钱。
    """
    kept: list[dict] = []
    total = 0
    for entry in reversed(entries or []):
        size = len(entry.get("content") or "")
        if kept and total + size > max_chars:
            break
        kept.append(entry)
        total += size
    return list(reversed(kept))


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


def tool_ledger_note(result: dict | None) -> str:
    """一次工具调用在账本中留下的括注：失败记原因，工具自报 __note 则记该摘要，其余为 ok。

    账本若只记工具名与结果，后续轮次无从判断当时的调用语义：`silence_member(ok)`
    曾被理解为「对方已被禁言」。状态变更类工具因此以 __note 自报一句摘要。
    __note 仅供账本使用，在此一并摘除——返回给模型的工具响应已有 message。
    """
    if not isinstance(result, dict):
        return "ok"
    note = str(result.pop("__note", "") or "").strip()
    if result.get("error") or result.get("success") is False:
        reason = str(result.get("message") or result.get("error") or "失败")[:60]
        return f"失败：{reason}"
    return note or "ok"


def tools_entry(items: list[dict]) -> dict:
    """本轮工具调用一笔总账（**我干了什么**）——一条条目，不是每个调用一条：

    逐个调用落账本会被工具淹没（一轮十几个调用），细节本来就在 ConversationLog 里。
    """
    parts = []
    for it in items or []:
        name = (it.get("name") or "?").strip()
        note = (it.get("note") or "ok").strip()
        parts.append(f"{name}({note})")
    return make_entry("tool", "[本轮工具] " + "；".join(parts), actor="self")


def handoff_entry(note: str) -> dict:
    """轮末交接（end_turn.key_note）：留给后面自己的关键信息——压缩时原样搬运，不揉进摘要。"""
    return make_entry("handoff", f"[上一轮交接] {note.strip()}", actor="system")


def thinking_entry(text: str) -> dict:
    """保留的思考（end_turn keep_thinking=true）：整段推理进历史，供后面的自己复用。"""
    return make_entry("thinking", f"[本轮思考] {text.strip()}", actor="self")


def note_entry(note_id: str, text: str) -> dict:
    """一条跨状态便签的**投递条目**——ref 记 note id，账本里有了就不再投（幂等）。

    便签是"只活到解锁"的东西：`drop_on_unlock` 让解锁重写时把它连同副本一起清掉。
    """
    return make_entry("note", text, actor="system", ref=f"note:{note_id}",
                      flags={"drop_on_unlock": True})


def delivered_note_ids(entries: list[dict]) -> set[str]:
    """账本里已经投过的便签 id（投递幂等的唯一依据：AI 看过就算投过）。"""
    ids: set[str] = set()
    for e in entries or []:
        ref = e.get("ref") or ""
        if ref.startswith("note:"):
            ids.add(ref[len("note:"):])
    return ids


def is_compressible(entry: dict) -> bool:
    """这条能不能被摘要吃掉：事件类永不（缺口/便签/通知），其余默认可以。"""
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
