"""
上下文压缩服务

当 _tool_call_loop 中的消息列表增长到接近模型上下文窗口时，
自动压缩中间消息为摘要，保留 system prompt（维持 prompt cache 命中）
和最近 N 条消息。

压缩策略：
- 保留 messages[0]（system prompt）—— 不变动以最大化 prompt cache 命中
- 保留 messages[-K:]（最近 K 条消息，默认 5）
- 中间部分 → 调用 LLM 生成摘要
- 摘要以 system 角色注入到 system prompt 之后
"""

import logging
import math
import re
from typing import NamedTuple, Optional

from app.repositories.memory_repo import MemoryRepository, SQLAlchemyMemoryRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyMemoryRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyMemoryRepository(db_or_repo)
    return db_or_repo


# 默认上下文窗口（DeepSeek V4 为 128K）
DEFAULT_CONTEXT_WINDOW = 128_000
# 压缩阈值：达到窗口的 N% 时触发压缩（管理员可覆盖，DB 配置优先）
COMPRESSION_THRESHOLD = 0.60
# 压缩目标范围（管理员可覆盖）
# 建议压缩到 触发值 × COMPRESSION_TARGET_MIN ~ COMPRESSION_TARGET_MAX
COMPRESSION_TARGET_MIN = 0.05   # 建议不低于触发值的 5%
COMPRESSION_TARGET_MAX = 0.20   # 建议不超过触发值的 20%
# 压缩后至少保留的最近消息数
DEFAULT_KEEP_LAST_N = 20
# 压缩用摘要的最大 token 数（摘要正文预算；思考已显式关闭，见 _request_summary）
SUMMARY_MAX_TOKENS = 1500
# 首答为空时的重试预算：思考关不掉的第三方模型/超长 prompt 仍可能把预算吃满，
# 留一次"加大预算重试"，别让用户看到"LLM 返回空摘要"
SUMMARY_RETRY_MAX_TOKENS = 6000
# messages 总数低于此值不压缩
MIN_MESSAGES_FOR_COMPRESSION = 8


class CompressionThresholds(NamedTuple):
    """三档阈值（占同一个上下文窗口的比例）——都从热阈值推出，见 docs/dev/conversation_history.md §6"""

    post: float  # T_post：压完能落到多小的地板（摘要 ≤ 热阈值 × COMPRESSION_TARGET_MAX）
    idle: float  # T_idle：久未活跃（缓存经济）触发线，取在 (T_post, T_hot) 内部
    hot: float   # T_hot：热触发线，到了必须压（不压迟早爆窗口）


# T_idle 在 [T_post, T_hot] 上的插值系数 = 1/e：从 T_post 起覆盖带宽 63.2%，一个单位衰减尺度。
# 它**不是推导结论**（缓存经济学解不出 e 来），定盘星是 cached_tokens 实测；
# 系数落在 (0, 1) 内就自动满足 T_post < T_idle < T_hot——贴 T_post 等于每次闲置都白跑一遍压缩，
# 贴 T_hot 就退化成只有一个阈值。
IDLE_THRESHOLD_FRACTION = 1 / math.e


def compression_thresholds(hot: float) -> CompressionThresholds:
    """由热阈值推出三档：压后地板 / 冷触发线 / 热触发线。

    只用热阈值一个自变量——三档数值别在调用点各自乘系数，口径只留这一份。
    """
    post = hot * COMPRESSION_TARGET_MAX
    return CompressionThresholds(post=post, idle=post + IDLE_THRESHOLD_FRACTION * (hot - post), hot=hot)


# 中文（CJK/假名/全角）与其它字符的 token 密度差 2~3 倍：一律按 4 字符/token 会把
# 中文 prompt 低估约 2.7 倍，阈值该触发时不触发（世界 AI 实测单轮顶到 17 万 token）
_CJK_RE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
_CJK_CHARS_PER_TOKEN = 1.5   # DeepSeek 中文约 1.5 字符/token
_OTHER_CHARS_PER_TOKEN = 4   # 英文/数字/JSON 约 4 字符/token


def _message_text(m: dict) -> str:
    """一条消息里真正进 payload 的文本：role + content + reasoning_content + tool_calls。

    两处都不能漏：tool_calls 的 arguments 常常是整份文件内容；reasoning_content
    （思考模式要求回传）实测占长请求的两三成——漏算它估算就永远偏低。
    """
    parts = []
    for key in ("role", "content", "reasoning_content"):
        val = m.get(key)
        if isinstance(val, str):
            parts.append(val)
    for tc in m.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        parts.append(str(fn.get("name") or ""))
        parts.append(str(fn.get("arguments") or ""))
    return "".join(parts)


def estimate_tokens(messages: list[dict]) -> int:
    """粗略 token 估算：中文按 1.5 字符/token，其余按 4 字符/token。

    比"总字符数 / 4"准得多（中英混排实测误差 ~5%），且不需要 tiktoken 依赖。
    旧实现一律 /4，对中文 prompt 低估约 2.7 倍——阈值迟迟不触发，
    等发现时单轮已经顶到 17 万 token。
    """
    total_chars = 0
    cjk_chars = 0
    for m in messages:
        text = _message_text(m)
        total_chars += len(text)
        cjk_chars += len(_CJK_RE.findall(text))
    other_chars = total_chars - cjk_chars
    return int(cjk_chars / _CJK_CHARS_PER_TOKEN + other_chars / _OTHER_CHARS_PER_TOKEN)


async def get_compression_threshold(db) -> float:
    """从 DB 配置读取压缩阈值（0.0-1.0），回退到硬编码默认值"""
    db = _ensure_repo(db)
    try:
        from sqlalchemy import select
        from app.models.conversation_log import ConversationLogConfig
        result = await db.execute(select(ConversationLogConfig.compression_threshold).where(ConversationLogConfig.id == 1))
        val = result.scalar_one_or_none()
        if val is not None and val > 0:
            return val / 100.0
    except Exception:
        pass
    return COMPRESSION_THRESHOLD


def should_compress(
    messages: list[dict],
    context_window: int = DEFAULT_CONTEXT_WINDOW,
    threshold: float = COMPRESSION_THRESHOLD,
    min_messages: int = MIN_MESSAGES_FOR_COMPRESSION,
) -> bool:
    """判断是否需要压缩上下文"""
    if len(messages) < min_messages:
        return False
    estimated = estimate_tokens(messages)
    return estimated >= int(context_window * threshold)


def build_compression_prompt(
    messages_to_compress: list[dict],
    trigger_tokens: int = 0,
) -> str:
    """
    构建压缩提示词。要求 LLM 将中间消息总结为简洁的对话摘要。

    trigger_tokens: 触发压缩时的 token 估算值，用于计算建议范围。
    """
    min_target = int(trigger_tokens * COMPRESSION_TARGET_MIN) if trigger_tokens > 0 else 0
    max_target = int(trigger_tokens * COMPRESSION_TARGET_MAX) if trigger_tokens > 0 else 0

    # 将待压缩消息格式化为可读文本
    conversation_text_parts = []
    for m in messages_to_compress:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if isinstance(content, str) and content.strip():
            truncated = content[:2000] + "…" if len(content) > 2000 else content
            label = {"user": "用户", "assistant": "AI", "tool": "工具结果", "system": "系统"}.get(role, role)
            conversation_text_parts.append(f"[{label}] {truncated}")
        elif m.get("tool_calls"):
            tc_names = [tc.get("function", {}).get("name", "?") for tc in m.get("tool_calls", [])]
            conversation_text_parts.append(f"[AI 调用工具] {', '.join(tc_names)}")

    conversation_text = "\n".join(conversation_text_parts)

    suggestion = ""
    if min_target > 0 and max_target > 0:
        suggestion = (
            f"建议压缩到 {min_target}-{max_target} tokens 之间，"
            f"最多不超过 {max_target} tokens。\n"
        )

    return (
        "请将以下对话历史压缩为一份简洁的摘要。摘要应包含：\n"
        "1. 讨论了哪些话题\n"
        "2. AI 执行了哪些关键操作（工具调用及其结果）\n"
        "3. 做出了哪些决定\n"
        "4. 当前未完成的事项（如有）\n\n"
        f"{suggestion}"
        "=== 对话历史 ===\n"
        f"{conversation_text}\n"
        "=== 结束 ===\n\n"
        "请输出摘要："
    )


def _not_compressed(messages: list[dict], reason: str) -> dict:
    """未压缩（无需压缩 / 摘要失败）时的统一统计形状。

    成功路径的字段这里全给齐（compressed_count=0、ratio=0），
    调用方只需看 compressed，不必再对缺失字段做 `get(k, 0)` 兜底。
    """
    tokens = estimate_tokens(messages)
    n = len(messages)
    return {
        "compressed": False,
        "reason": reason,
        "before_count": n,
        "after_count": n,
        "compressed_count": 0,
        "before_tokens": tokens,
        "after_tokens": tokens,
        "compression_ratio_pct": 0,
    }


async def _request_summary(
    compression_messages: list[dict],
    *,
    model: str,
    api_base_url: str,
    api_key: str | None,
    user_id: str | None = None,
) -> str:
    """摘要调用（唯一入口）：显式关思考 + 首答为空就加大预算重试一次。

    **为什么必须显式关思考**（2026-09-17 用户报"上下文压缩失败：摘要生成失败: LLM 返回空摘要"）：
    DeepSeek v4 默认就思考，思考 token 与正文抢**同一个** max_tokens 预算。实测同一份 22k tokens
    的压缩输入：budget=800 → completion=800/reasoning=800/finish_reason=length/content 空；
    显式 `thinking: disabled` 后 reasoning=0、几十个 token 就出摘要。
    """
    from app.ai.llm import chat_completion   # 延迟导入：避免 ai.llm ↔ services 循环

    last = ""
    for budget in (SUMMARY_MAX_TOKENS, SUMMARY_RETRY_MAX_TOKENS):
        response = await chat_completion(
            messages=compression_messages,
            model=model,
            api_base_url=api_base_url,
            api_key=api_key,
            temperature=0.3,           # 低温度，保持准确
            max_tokens=budget,
            user_id=user_id,
            stream=False,
            thinking_enabled=False,    # 摘要是短输出：思考只会抢预算、多花钱
        )
        summary = (response.get("content") or "").strip()
        if summary:
            return summary
        last = f"finish_reason={response.get('finish_reason')}, completion={((response.get('usage') or {}).get('completion_tokens'))}"
        logger.warning(f"上下文压缩：摘要为空（max_tokens={budget}, {last}）→ 加大预算重试")
    raise ValueError(f"LLM 返回空摘要（{last}）")


def split_for_compression(
    messages: list[dict], keep_system: bool = True, keep_last_n: int = DEFAULT_KEEP_LAST_N
) -> tuple[int, int]:
    """把消息切成「保留的头 + 待压缩的中间 + 保留的尾」，返回 (start_idx, end_idx)。

    **顺序约定：调用方给的列表必须是正序（旧 → 新）**，所以"保留最后 N 条"就是
    保留**最新的** N 条。2026-09-25 的事故就是这条约定被破坏：群/私聊历史当时按
    新→旧注入，压缩于是吃掉了最新的那几条（包括触发消息），AI 看不到当轮的消息，
    只能回上一条（用户实测"答上一条"漂移）。
    """
    start_idx = 1 if (keep_system and messages and messages[0].get("role") == "system") else 0
    return start_idx, max(start_idx, len(messages) - keep_last_n)


async def compress_messages(
    messages: list[dict],
    api_base_url: str,
    api_key: str,
    model: str,
    keep_system: bool = True,
    keep_last_n: int = DEFAULT_KEEP_LAST_N,
    user_id: str | None = None,
) -> tuple[list[dict], dict]:
    """
    压缩消息列表。

    参数:
        messages: 当前消息列表
        api_base_url: LLM API 地址
        api_key: API 密钥
        model: 压缩用的模型（建议用工作模型）
        keep_system: 是否保留第一条 system 消息
        keep_last_n: 保留最近 N 条消息
        user_id: DeepSeek API user_id（用于 prompt cache 命名空间）

    返回:
        (new_messages, stats) — 压缩后的消息列表和统计信息
    """
    original_count = len(messages)
    original_tokens = estimate_tokens(messages)

    # 确定保留范围
    start_idx, end_idx = split_for_compression(
        messages, keep_system=keep_system, keep_last_n=keep_last_n
    )

    if end_idx <= start_idx:
        # 没有可压缩的内容
        logger.info(f"上下文压缩跳过：无可压缩消息（total={original_count}, keep_last={keep_last_n}）")
        return messages, _not_compressed(messages, "无可压缩消息")

    # 中间部分需要压缩
    messages_to_compress = messages[start_idx:end_idx]
    logger.info(
        f"上下文压缩：总消息 {original_count} 条，"
        f"压缩中间 {len(messages_to_compress)} 条，"
        f"保留后 {keep_last_n} 条，"
        f"估算 token: {original_tokens}"
    )

    # 构建压缩请求（传入触发 token 数以计算建议范围）
    compression_prompt = build_compression_prompt(messages_to_compress, trigger_tokens=original_tokens)
    compression_messages = [
        {"role": "user", "content": compression_prompt},
    ]

    try:
        summary = await _request_summary(
            compression_messages, model=model, api_base_url=api_base_url, api_key=api_key,
            user_id=user_id,
        )
    except Exception as e:
        logger.error(f"上下文压缩失败（LLM 摘要调用出错）: {e}")
        return messages, _not_compressed(messages, f"摘要生成失败: {e}")

    # 组装新消息列表
    new_messages = []
    if keep_system and messages and messages[0].get("role") == "system":
        new_messages.append(messages[0])  # system prompt 不变，保持 cache 命中

    # 注入摘要消息（system 角色，放在 system prompt 之后）
    summary_msg = {
        "role": "system",
        "content": f"[上下文摘要 — 以下是之前对话的压缩版本]\n{summary}",
    }
    new_messages.append(summary_msg)

    # 保留最近 N 条
    new_messages.extend(messages[end_idx:])

    new_tokens = estimate_tokens(new_messages)
    compression_ratio = round((1 - new_tokens / max(original_tokens, 1)) * 100)

    stats = {
        "compressed": True,
        "before_count": original_count,
        "after_count": len(new_messages),
        "compressed_count": len(messages_to_compress),
        "before_tokens": original_tokens,
        "after_tokens": new_tokens,
        "compression_ratio_pct": compression_ratio,
        "summary_length": len(summary),
        # 摘要文本本身：解锁点要拿它写账本条目（不留的话账本重写只能写空摘要）
        "summary": summary,
    }

    logger.info(
        f"上下文压缩完成：{original_count} → {len(new_messages)} 条消息，"
        f"token 估算 {original_tokens} → {new_tokens}（压缩 {compression_ratio}%），"
        f"摘要长度 {len(summary)} 字符"
    )

    return new_messages, stats


def inline_compress(
    messages: list[dict],
    keep_system: bool = True,
    keep_last_n: int = DEFAULT_KEEP_LAST_N,
) -> tuple[list[dict], dict]:
    """
    内联压缩——不调 API，直接截断中间消息，复用前缀保持 cache 命中。

    策略：
    - 保留 system prompt + 已有摘要
    - 保留最近 N 条消息
    - 中间消息用一条 system note 替代
    """
    original_count = len(messages)
    original_tokens = estimate_tokens(messages)

    if original_count <= keep_last_n + 2:
        return messages, {"compressed": False, "reason": "消息太少无需压缩"}

    # 保留：system prompt + 摘要类消息（role=system 且非第一条）
    head = []
    tail_start = max(1, original_count - keep_last_n)

    if keep_system and messages and messages[0].get("role") == "system":
        head.append(messages[0])

    # 保留中间的 system 消息（已有的摘要）
    for m in messages[1:tail_start]:
        if m.get("role") == "system" and "摘要" in m.get("content", ""):
            head.append(m)

    # 截断提示
    compressed_count = tail_start - len(head)
    if compressed_count > 0:
        head.append({
            "role": "system",
            "content": f"[上下文压缩] 中间 {compressed_count} 条消息已折叠。用 expand_context 工具可展开。",
        })

    # 拼接
    new_messages = head + messages[tail_start:]
    new_tokens = estimate_tokens(new_messages)

    logger.info(
        f"内联压缩：{original_count} → {len(new_messages)} 条，"
        f"token {original_tokens} → {new_tokens}"
    )

    return new_messages, {
        "compressed": True,
        "inline": True,
        "before_count": original_count,
        "after_count": len(new_messages),
        "before_tokens": original_tokens,
        "after_tokens": new_tokens,
        # 没调模型也要给账本一个交代（同一句文案，别再写一遍）
        "summary": head[-1]["content"] if compressed_count > 0 else "",
    }
