"""qq-channel 的公共常量与纯函数

限流窗口、去重容量、召回周期这些常量，以及"同一句只落一条"的排队锁、群消息模式文案
这类小工具，三块（inbound / outbound / identity）都要用；放在任何一侧都会让另一侧反向
依赖，所以单独一个文件。
"""
from __future__ import annotations

import logging
from typing import Any

from .format import _MENTION_TOKEN_RE


logger = logging.getLogger(__name__)

# ── 本通道自己的常量（时限、频控、缓存上限）──
GROUP_WINDOW, GROUP_MAX = 300, 5           # 被动回复窗口：群 5 分钟/5 次
DM_WINDOW, DM_MAX = 3600, 4                # 单聊 60 分钟/4 次
UNREACHABLE_NOTICE_BASE = 300              # "发不出去"提示的起始回避间隔：5 分钟
UNREACHABLE_NOTICE_MAX = 5 * 86400         # 一直发不出去就翻倍回避，最高 5 天一条
UNION_TTL_SECONDS = 86400                  # 补拉到的 union 缓存一天
UNION_CACHE_MAX = 2000                     # 缓存条目上限：按群成员数增长
MIRROR_WINDOW_SECONDS = 3                  # 同一句 QQ 消息被两台机器人分别送进来的认亲窗口
MIRROR_SCAN = 20                           # 认亲时回看多少条：渲染后的令牌各台不同，SQL 比不了
GROUP_PER_MINUTE = 20                      # 单群频控 20/qpm（主动消息）
BOT_PER_MINUTE = 60                        # Bot 维度 60/qpm
PAIR_NOTIFY_INTERVAL = 60                  # 陌生人反复私聊时，配对码最多 60 秒提醒一次
PAIR_NOTIFY_CACHE_MAX = 256                # 通知表涨到这么大才清一次过期条目
DM_ROUTE_MAX = 500                         # 私信路由上限：按会话记
DEDUP_SIZE = 500                           # 相同 msg_id 可能重复推送，按 id 去重
GROUP_FACTS_MAX = 50                       # 记住群信息的群数上限


async def _serialize_same_text(db: Any, kind: str, landing: int, content: str) -> None:
    """同一句人话的入站排队过：认亲是"先查后写"，几台机器人并行时谁也看不见谁

    用事务级咨询锁（同一个库，跨进程也算）：正文摘掉令牌后求成一个键，先到的先落库，
    后到的等它提交再扫——这样"同一句只落一条"才立得住。没有它，几台机器人各扫各的，
    都查不到对方还没提交的那条，于是同一句话落好几遍、同一个人各建一个账号
    （2026-10-18 真机：群 #77；2026-10-31 真机：同一条落三遍）。

    键用**落点群**而不是 QQ 群 openid：同一个 QQ 群在每台机器人那里的 openid 不一样
    （腾讯按 appid 隔离），拿它做键等于每台各锁各的，一点排队作用都没有。
    """
    from sqlalchemy import text as sa_text

    key = f"{kind}:{int(landing)}:{_MENTION_TOKEN_RE.sub('', str(content or '')).strip()[:120]}"
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(hashtext(:key)::bigint)"), {"key": key})


# 业务幂等锚点写在条目的 ref 上（flags 留给平台语义位：drop_on_unlock / seen / compressible）
_CHANNEL_MODE_REF = "channel_mode:"


def _channel_mode(full: bool) -> str:
    """模式取值（写进 ref、也按它判，两处各写一遍迟早漂）"""
    return "full" if full else "at"


def _last_channel_mode(entries: list[dict]) -> str | None:
    """账本里最后一次"通道推送模式"通知说的是哪个模式（没说 → None）"""
    for entry in reversed(entries or []):
        ref = str(entry.get("ref") or "")
        if ref.startswith(_CHANNEL_MODE_REF):
            return ref[len(_CHANNEL_MODE_REF):]
        # 兼容改造前投的旧条目（当时锚点塞在 flags 里）——不认它就白重投一次
        legacy = (entry.get("flags") or {}).get("channel_mode")
        if legacy:
            return str(legacy)
    return None


def _channel_mode_notice(full: bool) -> str:
    """模式翻转通知的正文（唯一来源）。

    只说"能收到什么"，不说"会不会叫醒你"：后者由平台的意愿模型决定，不归通道管
    （说了就是给 AI 一个不成立的承诺，2026-09-26）。
    """
    if full:
        return (
            "【通道变更】这个 QQ 群开始推送群内全部消息（群主在 QQ 群设置里开了「获取群内全部消息」，"
            "也就是全量模式）：群里每个人说的话都会进 Copree，你都能看到；"
            "别人 @ 别人会在正文里显示成 `<@!id>`，正文不再在 @ 处断掉。"
        )
    return (
        "【通道变更】这个 QQ 群停止推送全部消息（群主关掉了「获取群内全部消息」，回到只喂点名）："
        "从此只有点名到你的消息会进 Copree，正文可能在 @ 别人处断掉。"
    )


def _msg_type_of(raw: Any) -> int | None:
    """配置里的"正文格式" → 协议里的 msg_type。

    配置里写的是**意图**（plain / markdown），不是协议数字：官方发送侧只有 0=文本、2=Markdown、
    7=富媒体（3 文档没写清），把数字暴露到界面上只会让人猜。留空 = 默认（先 Markdown，没权限降级）。
    数字串照样认（老配置/手工填的值）。
    """
    text = str(raw or "").strip().lower()
    if text in ("plain", "text", "0"):
        return 0
    if text in ("markdown", "md", "2"):
        return 2
    return None


def _split_list(raw: Any) -> set[str]:
    """逗号分隔（中英文逗号都认）→ 集合"""
    text = str(raw or "").replace("，", ",")
    return {item.strip() for item in text.split(",") if item.strip()}