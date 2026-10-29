"""
通道对话额度 — 计费口径与剩余量的唯一口径（无 IO、无 DB 依赖）。

两种口径（用户可选，落在 channel_token_policy.meter_mode）：
  total    : 这一次调用用掉的**全部** token（prompt 含缓存命中 + completion）
  billable : 只算真花钱的那部分 —— prompt 里未命中的 + completion

为什么必须是纯函数：剩余额度、对话列表、拦下来的那一刻三处都要算这个数。
各算一遍，就一定会出现「面板说还剩 3 万、对话里却说已用完」这种对不上的账。
"""
from __future__ import annotations

METER_TOTAL = "total"
METER_BILLABLE = "billable"
METER_MODES = (METER_BILLABLE, METER_TOTAL)
# 默认口径用交涉 token（用户提的默认值就是它）：缓存命中不算钱，算进额度里会让人白掏一笔
DEFAULT_METER_MODE = METER_BILLABLE
DEFAULT_LIMIT_TOKENS = 1_000_000

# 超限后同一个对话的自动回复节流：每 10 分钟最多一句。
# 不节流的话，一个被刷屏的群会每来一条就回一句"额度用完"，等于自己在刷自己的屏。
BLOCKED_NOTICE_INTERVAL_SECONDS = 600

# 被拦下时默认发出去的官方口吻说明（用户可换成自己的，policy.blocked_reply_text）
DEFAULT_BLOCKED_REPLY = (
    "您好，本对话的可用额度已用完，暂时无法继续回复。"
    "如需继续使用，请联系该机器人的管理员调整额度，感谢理解。"
)


def normalize_mode(value: object) -> str:
    """认不出的口径一律按默认（billable）：宁可按便宜的那个算，也不悄悄换成更贵的一种"""
    mode = str(value or "").strip().lower()
    return mode if mode in METER_MODES else DEFAULT_METER_MODE


def normalize_limit(value: object) -> int:
    """额度只接受非负整数；解析不了就退回默认值，不把脏数据写进账里"""
    try:
        limit = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_TOKENS
    return max(0, limit)


def charged_tokens(mode: object, usage: dict | None) -> int:
    """这一次调用按 mode 记多少额度。

    usage 是 normalize_usage() 归一后的顶层键（prompt/completion/cached/total）。
    total 口径兜底用 prompt+completion 求和：老接口不一定回 total_tokens，
    缺了就当它等于两项之和，总比记 0 强。
    """
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if normalize_mode(mode) == METER_TOTAL:
        total = int(usage.get("total_tokens") or 0)
        return max(total, prompt + completion)
    cached = int(usage.get("cached_tokens") or 0)
    # 缓存命中比 prompt 还大只可能是上游字段错位：按 0 计，别把额度算成负的
    return max(0, prompt - cached) + completion


def used_tokens(mode: object, used_total: int | None, used_billable: int | None) -> int:
    """账上两个计数器都留着，按当前口径取其中一个（换口径不丢历史）"""
    if normalize_mode(mode) == METER_TOTAL:
        return max(0, int(used_total or 0))
    return max(0, int(used_billable or 0))


def remaining(limit: object, used: int | None) -> int:
    return max(0, normalize_limit(limit) - max(0, int(used or 0)))


def exhausted(limit: object, used: int | None) -> bool:
    return remaining(limit, used) <= 0


def blocked_reply_text(custom: object) -> str:
    """自动回复文案：用户填了就用他的，留空就是官方那句（不拼接、不猜语气）"""
    text = str(custom or "").strip()
    return text or DEFAULT_BLOCKED_REPLY


def conversation_key(kind: str, target: int | str) -> str:
    """对话键：group:<Copro 群 id> / dm:<会话 id>。

    用会话自己的 id 而不是 QQ 那边的 openid：额度是"这个对话"的账，
    换绑、改名、迁移都不该把它清零。
    """
    prefix = "group" if str(kind) == "group" else "dm"
    return f"{prefix}:{target}"
